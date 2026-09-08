"""
Scoop API Server

Endpoints:
  POST /api/subscribe    - Sign up from landing page
  POST /api/digest       - Run digest pipeline (cron)
  GET  /api/unsubscribe  - One-click unsubscribe (HMAC-signed)
  GET  /health           - Health check
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, field_validator

load_dotenv()

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:8080",
).split(",")

CRON_SECRET = os.getenv("CRON_SECRET", "")
if not CRON_SECRET:
    raise RuntimeError("CRON_SECRET must be set")


def _hmac_sign(email: str) -> str:
    return hmac.new(CRON_SECRET.encode(), email.encode(), hashlib.sha256).hexdigest()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Scoop API", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "Accept"],
)


# -- Security headers middleware ---------------

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# -- Models ------------------------------------

class SubscribeRequest(BaseModel):
    email: EmailStr
    product: str
    companies: list[str]

    @field_validator("product")
    @classmethod
    def product_length(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError("Product description must be under 500 characters")
        return v.strip()

    @field_validator("companies")
    @classmethod
    def companies_valid(cls, v: list[str]) -> list[str]:
        cleaned = [c.strip() for c in v if c.strip()][:10]
        for c in cleaned:
            if len(c) > 200:
                raise ValueError("Company name must be under 200 characters")
        return cleaned


class DigestRequest(BaseModel):
    user_email: Optional[str] = None
    api_secret: str


class ChatRequest(BaseModel):
    question: str
    email: str = ""
    accounts: str = ""


class OTPRequest(BaseModel):
    email: EmailStr


class VerifyOTPRequest(BaseModel):
    email: EmailStr
    code: str


class VerifySessionRequest(BaseModel):
    email: EmailStr
    token: str


class MeRequest(BaseModel):
    email: EmailStr
    token: str


class SaveAccountsRequest(BaseModel):
    email: EmailStr
    token: str
    product: str = ""
    companies: list[str]

    @field_validator("product")
    @classmethod
    def product_length(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError("Product description must be under 500 characters")
        return v.strip()

    @field_validator("companies")
    @classmethod
    def companies_valid(cls, v: list[str]) -> list[str]:
        cleaned = [c.strip() for c in v if c.strip()][:25]
        for c in cleaned:
            if len(c) > 200:
                raise ValueError("Company name must be under 200 characters")
        return cleaned


class AddPersonRequest(BaseModel):
    email: EmailStr
    token: str
    name: str
    company: str
    title: str = ""
    linkedin: str = ""

    @field_validator("name", "company")
    @classmethod
    def required_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name and company are required")
        if len(v) > 200:
            raise ValueError("Value too long")
        return v

    @field_validator("title", "linkedin")
    @classmethod
    def optional_length(cls, v: str) -> str:
        v = v.strip()
        if len(v) > 500:
            raise ValueError("Value too long")
        return v


class DeletePersonRequest(BaseModel):
    email: EmailStr
    token: str
    person_id: str


# -- Auth helper -------------------------------

async def _require_session(email: str, token: str) -> dict:
    """Validate the OTP session token and return the user, or raise 401."""
    from db import check_session, get_user_by_email

    if not await check_session(email, token):
        raise HTTPException(401, "Session expired. Please sign in again.")
    user = await get_user_by_email(email)
    if not user:
        raise HTTPException(404, "Account not found")
    return user


# -- Endpoints ---------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/subscribe")
async def subscribe(req: SubscribeRequest):
    if not req.companies:
        raise HTTPException(400, "At least one company is required")

    if os.getenv("DATABASE_URL"):
        from db import create_user, get_user_by_email
        existing = await get_user_by_email(req.email)
        if not existing:
            await create_user(req.email, req.product, req.companies)

    from send_email import send_welcome_email
    await send_welcome_email(req.email)

    return {"status": "ok"}


@app.post("/api/digest")
async def run_digest(req: DigestRequest):
    if not hmac.compare_digest(req.api_secret, CRON_SECRET):
        raise HTTPException(403, "Invalid API secret")

    from db import get_all_users, get_user_by_email, save_digest
    from digest import generate_digest_for_user
    from send_email import send_digest_email

    if req.user_email:
        user = await get_user_by_email(req.user_email)
        users = [user] if user else []
    else:
        users = await get_all_users()

    sent = 0
    for user in users:
        items = await generate_digest_for_user(user)
        if items:
            await send_digest_email(user, items)
            await save_digest(user["id"], items)
            sent += 1

    return {"status": "ok", "processed": sent}


@app.get("/api/unsubscribe")
async def unsubscribe(email: str, token: str):
    """HMAC-signed one-click unsubscribe."""
    expected = _hmac_sign(email)
    if not hmac.compare_digest(token, expected):
        raise HTTPException(403, "Invalid unsubscribe link")

    from db import get_user_by_email, delete_user
    user = await get_user_by_email(email)
    if user:
        await delete_user(user["id"])

    return {"status": "unsubscribed"}


# -- Feedback ----------------------------------

@app.get("/api/feedback")
async def feedback(digest_id: str, idx: int, vote: str, sig: str):
    """HMAC-signed vote on a signal. Called from email link clicks."""
    expected = _hmac_sign(f"{digest_id}:{idx}:{vote}")
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(403, "Invalid feedback link")

    if vote not in ("up", "down"):
        raise HTTPException(400, "Vote must be 'up' or 'down'")

    from db import save_feedback, get_digest_owner

    # Find user_id from digest
    user_id = await get_digest_owner(digest_id)
    if not user_id:
        raise HTTPException(404, "Digest not found")
    await save_feedback(digest_id, idx, user_id, vote)

    emoji = "👍" if vote == "up" else "👎"
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>body{{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f8fafc;}}.card{{text-align:center;padding:40px;border-radius:12px;background:#fff;box-shadow:0 4px 24px rgba(0,0,0,0.06);}}</style>
</head><body><div class="card"><p style="font-size:48px;margin:0 0 16px;">{emoji}</p><p style="font-size:18px;color:#0f172a;font-weight:600;">Thanks for the feedback!</p><p style="font-size:14px;color:#64748b;">This helps us improve your weekly Scoops.</p></div></body></html>""")


# -- OTP Auth ----------------------------------

@app.post("/api/auth/send-otp")
async def send_otp(req: OTPRequest):
    """Send a 6-digit OTP to the user's email via Gmail."""
    from db import get_user_by_email, create_otp
    from send_email import send_otp_email, send_no_account_email

    user = await get_user_by_email(req.email)
    if user:
        code = await create_otp(req.email)
        await send_otp_email(req.email, code)
    else:
        # No account yet: invite them to create one instead of silently doing nothing.
        await send_no_account_email(req.email)

    # Always return the same response (don't leak account existence in the HTTP response).
    return {"status": "ok"}


@app.post("/api/auth/verify-otp")
async def verify_otp(req: VerifyOTPRequest):
    """Verify a 6-digit OTP and return a session token."""
    from db import verify_otp, create_session

    valid = await verify_otp(req.email, req.code)
    if not valid:
        return {"error": "Invalid or expired code. Try again."}

    token = await create_session(req.email)
    return {"status": "ok", "token": token}


@app.post("/api/auth/verify-session")
async def verify_session(req: VerifySessionRequest):
    """Check if a session token is still valid."""
    from db import check_session
    valid = await check_session(req.email, req.token)
    return {"valid": valid}


# -- Account dashboard (RDS-backed) ------------

@app.post("/api/me")
async def me(req: MeRequest):
    """Hydrate the dashboard in one call: profile, companies, people, stats, digests.

    Reads from RDS — the same store the weekly digest reads and writes — so what
    the user sees and edits here is exactly what the Monday email uses.
    """
    user = await _require_session(req.email, req.token)
    from db import get_people, get_recent_digests, get_digest_count

    companies = [c["name"] for c in user.get("companies", [])]
    people = await get_people(user["id"])
    digests = await get_recent_digests(user["id"], limit=4)
    digest_count = await get_digest_count(user["id"])

    return {
        "email": user["email"],
        "product": user.get("product") or "",
        "companies": companies,
        "people": people,
        "digests": digests,
        "stats": {"accounts": len(companies), "digests": digest_count},
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
    }


@app.post("/api/accounts")
async def save_accounts(req: SaveAccountsRequest):
    """Replace the user's focus area + tracked companies (writes to RDS)."""
    user = await _require_session(req.email, req.token)
    from db import set_user_accounts

    await set_user_accounts(user["id"], req.product, req.companies)
    return {"status": "ok", "companies": req.companies, "product": req.product}


@app.post("/api/people/add")
async def people_add(req: AddPersonRequest):
    """Add a tracked contact (writes to RDS)."""
    user = await _require_session(req.email, req.token)
    from db import add_person

    person = await add_person(
        user["id"],
        {"name": req.name, "company": req.company,
         "title": req.title, "linkedin": req.linkedin},
    )
    return {"status": "ok", "person": person}


@app.post("/api/people/delete")
async def people_delete(req: DeletePersonRequest):
    """Delete a tracked contact, scoped to the signed-in user (writes to RDS)."""
    user = await _require_session(req.email, req.token)
    from db import delete_person

    ok = await delete_person(user["id"], req.person_id)
    if not ok:
        raise HTTPException(404, "Contact not found")
    return {"status": "ok"}


# -- Chat --------------------------------------

@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Answer questions about accounts using Perplexity web search."""
    if not req.question or len(req.question) > 1000:
        return {"error": "Question too long or empty"}

    from digest import _call_model

    # Build context from user's accounts
    accounts_ctx = ""
    if req.accounts:
        accounts_ctx = f"\nThe user covers these accounts: {req.accounts}."

    # Get recent signals for context
    signals_ctx = ""
    if req.email:
        try:
            from db import get_user_by_email, get_last_digest
            user = await get_user_by_email(req.email)
            if user:
                digest = await get_last_digest(user["id"])
                if digest and digest.get("items"):
                    headlines = [f"- {i['company']}: {i['headline']}" for i in digest["items"][:5] if i.get("headline")]
                    if headlines:
                        signals_ctx = "\n\nRecent signals from their last digest:\n" + "\n".join(headlines)
        except Exception:
            pass

    try:
        data = await _call_model(
            system=f"""You are Scoop, a helpful sales intelligence assistant for Solace colleagues.
You help salespeople prepare for calls, understand their accounts, and find relevant information.
Be warm, concise, and actionable. Use markdown for formatting.{accounts_ctx}{signals_ctx}

Solace sells PubSub+ Event Broker, Event Portal, and Agent Mesh for event-driven architecture.
When relevant, connect insights to how Solace could help, but don't force it.""",
            prompt=req.question,
            max_tokens=800,
            provider="pplx",
        )
        answer = data["choices"][0]["message"]["content"]
        sources = data.get("citations", [])
        return {"answer": answer, "sources": sources}
    except Exception as e:
        return {"error": str(e)}
