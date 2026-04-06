"""
Scoop — Reply Handler

Polls the Gmail inbox for replies to digest emails. When a user
replies with a question ("Tell me more about the Renault CTO move"),
Perplexity researches the answer and sends a contextual reply back.

Limits: 5 total emails per thread (1 digest + 2 back-and-forths).

Run via cron every 5 minutes:
  python reply_handler.py

Or as a GitHub Actions workflow on schedule.
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from email.header import decode_header
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from send_email import send_raw_email

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
PPLX_API_URL = "https://api.perplexity.ai/chat/completions"
PPLX_MODEL = "sonar-pro"

# Track threads: { message_id: { "count": N, "context": str, "user_email": str } }
THREADS_FILE = Path(__file__).resolve().parent / ".reply_threads.json"
MAX_EMAILS_PER_THREAD = 5  # 1 digest + 2 back-and-forths


def _load_threads() -> dict:
    if THREADS_FILE.exists():
        return json.loads(THREADS_FILE.read_text())
    return {}


def _save_threads(threads: dict) -> None:
    THREADS_FILE.write_text(json.dumps(threads, indent=2))


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
    """Extract plain text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def _strip_quoted_text(body: str) -> str:
    """Remove quoted reply text (lines starting with >) and signatures."""
    lines = body.split("\n")
    clean = []
    for line in lines:
        # Stop at common reply markers
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
    """Find the original thread ID from In-Reply-To or References headers."""
    in_reply_to = msg.get("In-Reply-To", "").strip()
    references = msg.get("References", "").strip()

    # Use the first reference (original message) as thread key
    if references:
        return references.split()[0]
    return in_reply_to or None


async def _research_question(question: str, context: str) -> str:
    """Use Perplexity to answer the user's follow-up question."""
    import httpx

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

    # Append sources if available
    citations = data.get("citations", [])
    if citations:
        answer += "\n\nSources:\n" + "\n".join(f"- {url}" for url in citations[:5])

    return answer


def fetch_replies() -> list[dict]:
    """Connect to Gmail IMAP and fetch unread replies to Scoop digests."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("Gmail credentials not set, skipping.")
        return []

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select("INBOX")

    # Search for unread emails that are replies (have In-Reply-To header)
    # and received in the last 7 days
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

        # Only process replies (has In-Reply-To or References)
        thread_id = _get_thread_id(msg)
        if not thread_id:
            continue

        from_addr = email.utils.parseaddr(msg.get("From", ""))[1]
        subject = _decode_subject(msg)
        body = _get_text_body(msg)
        question = _strip_quoted_text(body)
        message_id = msg.get("Message-ID", "")

        if not question.strip():
            continue

        # Don't process our own outgoing messages
        if from_addr.lower() == GMAIL_ADDRESS.lower():
            continue

        replies.append({
            "msg_id": msg_id,
            "message_id": message_id,
            "thread_id": thread_id,
            "from": from_addr,
            "subject": subject,
            "question": question,
        })

        # Mark as seen
        mail.store(msg_id, "+FLAGS", "\\Seen")

    mail.logout()
    return replies


async def process_replies() -> int:
    """Fetch replies, research answers, send responses."""
    replies = fetch_replies()
    if not replies:
        print("No new replies.")
        return 0

    threads = _load_threads()
    processed = 0

    for reply in replies:
        thread_id = reply["thread_id"]
        thread = threads.get(thread_id, {"count": 1, "context": "", "user_email": reply["from"]})

        # Check limit
        if thread["count"] >= MAX_EMAILS_PER_THREAD:
            print(f"  Thread with {reply['from']} hit {MAX_EMAILS_PER_THREAD}-email limit, skipping.")
            continue

        print(f"  Reply from {reply['from']}: {reply['question'][:80]}...")

        # Research the answer
        answer = await _research_question(reply["question"], thread.get("context", ""))

        # Build reply subject
        subject = reply["subject"]
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        # Send reply
        html_answer = answer.replace("\n", "<br>")
        html = f"""<div style="font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size:15px; line-height:1.6; color:#0f172a;">
{html_answer}
</div>"""

        send_raw_email(reply["from"], subject, html)
        print(f"  Replied to {reply['from']}")

        # Update thread state
        thread["count"] = thread.get("count", 1) + 2  # +1 for their reply, +1 for our response
        thread["context"] += f"\nUser asked: {reply['question']}\nScoop answered: {answer[:500]}"
        threads[thread_id] = thread
        processed += 1

    _save_threads(threads)
    return processed


async def main():
    print(f"Checking replies at {datetime.now().strftime('%H:%M:%S')}...")
    n = await process_replies()
    print(f"Processed {n} replies.")


if __name__ == "__main__":
    asyncio.run(main())
