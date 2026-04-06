"""
Scoop — Reply Handler

Polls the Gmail inbox for replies to digest emails. When a user
replies with a question ("Tell me more about the Renault CTO move"),
Perplexity researches the answer and sends a contextual reply back.

Limits: 5 total emails per thread (1 digest + 2 back-and-forths).
Thread state is stored in Supabase so it persists across Actions runs.

Run via cron every 15 minutes:
  python reply_handler.py
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import json
import os
import sys
from datetime import datetime, timedelta
from email.header import decode_header
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx

from send_email import send_raw_email

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
PPLX_API_URL = "https://api.perplexity.ai/chat/completions"
PPLX_MODEL = "sonar-pro"

MAX_EMAILS_PER_THREAD = 5  # 1 digest + 2 back-and-forths


# ── Thread tracking via Supabase ─────────────
# Table: reply_threads (thread_id text PK, user_email text, count int, context text)

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


async def _get_thread(thread_id: str) -> dict | None:
    if not SUPABASE_URL:
        return None
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/reply_threads",
            headers=_sb_headers(),
            params={"thread_id": f"eq.{thread_id}"},
        )
        rows = r.json() if r.status_code == 200 else []
        return rows[0] if rows else None


async def _upsert_thread(thread_id: str, user_email: str, count: int, context: str) -> None:
    if not SUPABASE_URL:
        return
    async with httpx.AsyncClient() as c:
        await c.post(
            f"{SUPABASE_URL}/rest/v1/reply_threads",
            headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            json={"thread_id": thread_id, "user_email": user_email, "count": count, "context": context[:4000]},
        )


# ── Email parsing helpers ────────────────────

def _decode_subject(msg) -> str:
    raw = msg.get("Subject", "")
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _get_text_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def _strip_quoted_text(body: str) -> str:
    lines = body.split("\n")
    clean = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if stripped.startswith("On ") and stripped.endswith("wrote:"):
            break
        if stripped == "--":
            break
        if "Original Message" in stripped:
            break
        clean.append(line)
    return "\n".join(clean).strip()


def _get_thread_id(msg) -> str | None:
    references = msg.get("References", "").strip()
    in_reply_to = msg.get("In-Reply-To", "").strip()
    if references:
        return references.split()[0]
    return in_reply_to or None


# ── Perplexity research ──────────────────────

async def _research_question(question: str, context: str) -> str:
    api_key = os.getenv("PPLX_KEY")
    if not api_key:
        raise RuntimeError("PPLX_KEY not set")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            PPLX_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": PPLX_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": f"""You are Scoop, a B2B sales intelligence assistant.
You previously sent the user a digest email. They replied with a follow-up question.

Context from the original digest:
{context}

Answer their question with specific, actionable intelligence.
Include names, titles, dates, and sources where possible.
Keep the response concise (3-5 paragraphs max).
Write in a warm but professional tone. Sign off as "— Scoop 🐶🗞️".""",
                    },
                    {"role": "user", "content": question},
                ],
                "max_tokens": 800,
                "temperature": 0.2,
            },
        )
        resp.raise_for_status()

    data = resp.json()
    answer = data["choices"][0]["message"]["content"].strip()

    citations = data.get("citations", [])
    if citations:
        answer += "\n\nSources:\n" + "\n".join(f"- {url}" for url in citations[:5])

    return answer


# ── Gmail IMAP ───────────────────────────────

def fetch_replies() -> list[dict]:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("Gmail credentials not set, skipping.")
        return []

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select("INBOX")

    since = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
    _, msg_ids = mail.search(None, f'(UNSEEN SINCE {since})')

    replies = []
    if not msg_ids[0]:
        mail.logout()
        return []

    for msg_id in msg_ids[0].split():
        _, msg_data = mail.fetch(msg_id, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        thread_id = _get_thread_id(msg)
        if not thread_id:
            continue

        from_addr = email.utils.parseaddr(msg.get("From", ""))[1]
        if from_addr.lower() == GMAIL_ADDRESS.lower():
            continue

        subject = _decode_subject(msg)
        question = _strip_quoted_text(_get_text_body(msg))
        if not question.strip():
            continue

        replies.append({
            "msg_id": msg_id,
            "thread_id": thread_id,
            "from": from_addr,
            "subject": subject,
            "question": question,
        })

        mail.store(msg_id, "+FLAGS", "\\Seen")

    mail.logout()
    return replies


# ── Main loop ────────────────────────────────

async def process_replies() -> int:
    replies = fetch_replies()
    if not replies:
        print("No new replies.")
        return 0

    processed = 0

    for reply in replies:
        thread_id = reply["thread_id"]
        thread = await _get_thread(thread_id)
        count = thread["count"] if thread else 1
        context = thread["context"] if thread else ""

        if count >= MAX_EMAILS_PER_THREAD:
            print(f"  Thread with {reply['from']} hit limit, skipping.")
            continue

        print(f"  Reply from {reply['from']}: {reply['question'][:80]}...")

        answer = await _research_question(reply["question"], context)

        subject = reply["subject"]
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        html_answer = answer.replace("\n", "<br>")
        html = f"""<div style="font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size:15px; line-height:1.6; color:#0f172a;">
{html_answer}
</div>"""

        send_raw_email(reply["from"], subject, html)
        print(f"  Replied to {reply['from']}")

        new_context = context + f"\nUser asked: {reply['question']}\nScoop answered: {answer[:500]}"
        await _upsert_thread(thread_id, reply["from"], count + 2, new_context)
        processed += 1

    return processed


async def main():
    print(f"Checking replies at {datetime.now().strftime('%H:%M:%S')}...")
    n = await process_replies()
    print(f"Processed {n} replies.")


if __name__ == "__main__":
    asyncio.run(main())
