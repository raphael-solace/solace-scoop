"""
Scoop API Server

Endpoints:
  POST /api/subscribe    - Sign up from landing page
  POST /api/digest       - Run digest pipeline (cron)
  GET  /api/unsubscribe  - One-click unsubscribe (HMAC-signed)
  GET  /health           - Process liveness check
  GET  /ready            - RDS readiness check
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

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
    allow_headers=["Content-Type", "Accept", "Authorization"],
)


# -- Security headers middleware ---------------

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/admin/"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
    return response


def _clean_company_names(values: list[str]) -> list[str]:
    cleaned = {}
    for value in values:
        name = " ".join(value.split())
        if not name:
            continue
        if len(name) > 200:
            raise ValueError("Company name must be under 200 characters")
        cleaned.setdefault(name.casefold(), name)
    return list(cleaned.values())


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
        cleaned = _clean_company_names(v)
        if len(cleaned) > 10:
            raise ValueError("Choose up to 10 companies when signing up")
        return cleaned


class DigestRequest(BaseModel):
    user_email: Optional[str] = None
    api_secret: str


class ChatRequest(BaseModel):
    question: str
    email: EmailStr
    token: str
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


class SubmitSignalRequest(MeRequest):
    company: str = Field(min_length=1, max_length=200)
    linkedin_url: str = Field(min_length=1, max_length=1500)
    note: str = Field(min_length=1, max_length=500)


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
        return _clean_company_names(v)


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


class AdminLoginRequest(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=1024)


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


async def _require_admin(
    authorization: Optional[str] = Header(default=None),
) -> dict:
    """Validate a strict admin bearer token before any database access."""
    if not authorization or len(authorization) > 4096:
        raise HTTPException(
            401,
            "Invalid or expired admin session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme != "Bearer" or not token or " " in token:
        raise HTTPException(
            401,
            "Invalid or expired admin session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    from admin_auth import AdminConfigurationError, InvalidAdminToken, validate_admin_token

    try:
        return validate_admin_token(token)
    except (AdminConfigurationError, InvalidAdminToken):
        raise HTTPException(
            401,
            "Invalid or expired admin session",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


# -- Endpoints ---------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Check RDS connectivity and the schema required by OTP verification."""
    try:
        from db import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("select attempts from auth_tokens limit 0")
    except Exception as exc:
        raise HTTPException(503, "Database unavailable") from exc
    return {"status": "ready"}


@app.post("/api/admin/login")
async def admin_login(req: AdminLoginRequest, request: Request):
    """Exchange configured admin credentials for a short-lived bearer token."""
    from admin_auth import (
        AdminConfigurationError,
        issue_admin_token,
        login_rate_limiter,
        verify_admin_credentials,
    )

    direct_ip = request.client.host if request.client else "unknown"
    retry_after = login_rate_limiter.retry_after(req.username, direct_ip)
    if retry_after:
        raise HTTPException(
            429,
            "Too many login attempts",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        valid = await asyncio.to_thread(
            verify_admin_credentials, req.username, req.password
        )
    except AdminConfigurationError as exc:
        raise HTTPException(503, "Admin authentication unavailable") from exc
    if not valid:
        retry_after = login_rate_limiter.record_failure(req.username, direct_ip)
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        raise HTTPException(401, "Invalid username or password", headers=headers)

    login_rate_limiter.record_success(req.username, direct_ip)
    return {
        "access_token": issue_admin_token(req.username),
        "token_type": "Bearer",
        "expires_in": 1800,
    }


@app.get("/api/admin/summary")
async def admin_summary(
    weeks: int = Query(default=12, ge=1, le=52),
    _admin: dict = Depends(_require_admin),
):
    from db import get_admin_summary

    return await get_admin_summary(weeks)


@app.get("/api/admin/subscribers")
async def admin_subscribers(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: Optional[str] = Query(default=None, max_length=512),
    _admin: dict = Depends(_require_admin),
):
    from db import get_admin_subscribers

    try:
        return await get_admin_subscribers(limit, cursor)
    except ValueError:
        raise HTTPException(400, "Invalid cursor") from None


@app.get("/api/admin/deliveries")
async def admin_deliveries(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: Optional[str] = Query(default=None, max_length=512),
    _admin: dict = Depends(_require_admin),
):
    from db import get_admin_deliveries

    try:
        return await get_admin_deliveries(limit, cursor)
    except ValueError:
        raise HTTPException(400, "Invalid cursor") from None


@app.post("/api/subscribe")
async def subscribe(req: SubscribeRequest):
    email = str(req.email).lower()
    if not email.endswith("@solace.com"):
        raise HTTPException(403, "Scoop is currently available for Solace colleagues only")
    if not req.companies:
        raise HTTPException(400, "At least one company is required")
    if not os.getenv("DATABASE_URL"):
        raise HTTPException(503, "Database is not configured")

    from db import create_user, get_user_by_email

    existing = await get_user_by_email(email)
    if not existing:
        await create_user(email, req.product, req.companies)

        from send_email import send_welcome_email
        await send_welcome_email(email)

    return {"status": "ok"}


@app.post("/api/digest")
async def run_digest(req: DigestRequest):
    if not hmac.compare_digest(req.api_secret, CRON_SECRET):
        raise HTTPException(403, "Invalid API secret")

    from db import (
        claim_digest_for_send,
        complete_digest_send,
        fail_digest_send,
        get_all_users,
        get_last_delivered_weekly_digest,
        get_user_by_email,
        record_sent_signal_keys,
        save_digest,
    )
    from digest import _hard_keys, generate_digest_for_user
    from send_email import send_digest_email

    if not req.user_email:
        raise HTTPException(400, "user_email is required; bulk sends run through the scheduled preflighted CLI")
    user = await get_user_by_email(req.user_email)
    users = [user] if user else []

    sent = 0
    for user in users:
        existing = await get_last_delivered_weekly_digest(user["id"])
        if existing and existing.get("delivered_at"):
            from datetime import datetime, timezone
            delivered_at = existing["delivered_at"]
            if isinstance(delivered_at, str):
                delivered_at = datetime.fromisoformat(delivered_at.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - delivered_at).total_seconds() < 20 * 3600:
                continue

        items = await generate_digest_for_user(user)
        if not items:
            continue

        digest_id = await save_digest(user["id"], items)
        if not await claim_digest_for_send(digest_id):
            raise HTTPException(409, "Digest could not be claimed for delivery")

        try:
            await send_digest_email(user, items, digest_id=digest_id)
            await complete_digest_send(digest_id)
            ledger = []
            for item in items:
                headline, company = item.get("headline", ""), item.get("company", "")
                for fp in _hard_keys(headline, company):
                    ledger.append({"fp": fp, "headline": headline,
                                   "company": company, "digest_id": digest_id})
            try:
                await record_sent_signal_keys(user["id"], ledger)
            except Exception:
                pass  # Delivery already succeeded; do not misclassify it as failed.
            sent += 1
        except Exception as exc:
            await fail_digest_send(digest_id, str(exc))
            raise

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
        try:
            code = await create_otp(req.email)
        except RuntimeError as exc:
            if "cooldown" in str(exc).lower():
                raise HTTPException(429, "Please wait before requesting another code") from exc
            raise
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
    from submitted_signals import list_submissions

    companies = [c["name"] for c in user.get("companies", [])]
    people = await get_people(user["id"])
    digests = await get_recent_digests(user["id"], limit=4)
    digest_count = await get_digest_count(user["id"])
    submitted_signals = await list_submissions(user["id"], date.today() - timedelta(days=30))

    return {
        "email": user["email"],
        "product": user.get("product") or "",
        "companies": companies,
        "people": people,
        "digests": digests,
        "submitted_signals": submitted_signals,
        "stats": {"accounts": len(companies), "digests": digest_count},
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
    }


@app.post("/api/signals/submit")
async def submit_signal(req: SubmitSignalRequest):
    """Save a LinkedIn lead once a dated company or trade source corroborates it."""
    user = await _require_session(req.email, req.token)
    from submitted_signals import find_submission, linkedin_permalink, save_submission, verify_submission

    company = next(
        (c["name"].strip() for c in user.get("companies", [])
         if c["name"].strip().lower() == req.company.strip().lower()),
        None,
    )
    note = req.note.strip()
    if company is None or not note:
        raise HTTPException(422, "Choose a tracked account and add a short note.")
    try:
        post_id, linkedin_url = linkedin_permalink(req.linkedin_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    existing = await find_submission(user["id"], post_id)
    if existing:
        if existing["company"].lower() != company.lower():
            raise HTTPException(409, "You already saved this post for another account.")
        return {"status": "existing", "signal": existing}

    try:
        async with asyncio.timeout(22):
            evidence = await verify_submission(company, note)
    except (TimeoutError, RuntimeError) as exc:
        raise HTTPException(503, "Source verification is temporarily unavailable. Try again.") from exc
    if evidence is None:
        raise HTTPException(422, "Scoop couldn't confirm this tip in a dated company or trade source from the last 14 days.")
    signal, created = await save_submission(user["id"], company, post_id, linkedin_url, note, evidence)
    if signal is None:
        raise HTTPException(409, "The tracked account changed during verification. Refresh and try again.")
    if signal["company"].lower() != company.lower():
        raise HTTPException(409, "You already saved this post for another account.")
    return {"status": "verified" if created else "existing", "signal": signal}


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
    """Answer authenticated questions about the caller's accounts."""
    if not req.question or len(req.question) > 1000:
        return {"error": "Question too long or empty"}

    await _require_session(req.email, req.token)
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
