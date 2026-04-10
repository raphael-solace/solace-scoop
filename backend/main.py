"""
Scoop API Server

Endpoints:
  POST /api/subscribe       - Sign up a new user (from landing page)
  POST /api/digest          - Run weekly digest (called by cron)
  POST /api/auth/magic-link - Send a magic link email
  POST /api/auth/verify     - Verify a magic link token
  GET  /api/account         - Get account data (authenticated)
  POST /api/account/update  - Update account (authenticated)
  GET  /health              - Health check
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

load_dotenv()

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://noptus.github.io,http://localhost:8080",
).split(",")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("PPLX_KEY"):
        raise RuntimeError("PPLX_KEY environment variable is required")
    yield


app = FastAPI(title="Scoop API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ── Models ───────────────────────────────────

class SubscribeRequest(BaseModel):
    email: EmailStr
    product: str
    companies: list[str]


class DigestRequest(BaseModel):
    user_email: Optional[str] = None
    api_secret: str


class MagicLinkRequest(BaseModel):
    email: EmailStr


class VerifyTokenRequest(BaseModel):
    token: str


class UpdateAccountRequest(BaseModel):
    product: str
    companies: list[str]


# ── Endpoints ────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/subscribe")
async def subscribe(req: SubscribeRequest):
    """Sign up a new user from the landing page."""
    companies = [c.strip() for c in req.companies if c.strip()][:10]
    if not companies:
        raise HTTPException(400, "At least one company is required")

    # Save to Supabase if configured
    if os.getenv("SUPABASE_URL"):
        from db import create_user, get_user_by_email

        existing = await get_user_by_email(req.email)
        if not existing:
            await create_user(req.email, req.product, companies)

    # Send welcome email via Gmail
    from send_email import send_welcome_email

    await send_welcome_email(req.email)

    return {"status": "ok"}


@app.post("/api/digest")
async def run_digest(req: DigestRequest):
    """Run the weekly digest pipeline. Called by GitHub Actions cron."""
    expected = os.getenv("CRON_SECRET", "")
    if not expected or req.api_secret != expected:
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


# ── Auth ────────────────────────────────────

async def _get_auth_email(request: Request) -> str:
    """Extract and validate session token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing authorization")
    token = auth[7:]
    from db import get_session_email
    email = await get_session_email(token)
    if not email:
        raise HTTPException(401, "Invalid or expired session")
    return email


@app.post("/api/auth/magic-link")
async def send_magic_link(req: MagicLinkRequest):
    """Send a magic link email. Always returns ok (don't leak if email exists)."""
    from db import get_user_by_email, create_auth_token
    from send_email import send_magic_link_email

    user = await get_user_by_email(req.email)
    if user:
        token = await create_auth_token(req.email)
        app_url = os.getenv("APP_URL", "http://localhost:8080")
        link = f"{app_url}/account.html?token={token}"
        await send_magic_link_email(req.email, link)

    return {"status": "ok"}


@app.post("/api/auth/verify")
async def verify_token(req: VerifyTokenRequest):
    """Verify a magic link token and return a session token."""
    from db import verify_auth_token, create_session_token

    email = await verify_auth_token(req.token)
    if not email:
        raise HTTPException(401, "Invalid or expired token")

    session_token = await create_session_token(email)
    return {"email": email, "session_token": session_token}


# ── Account management ──────────────────────

@app.get("/api/account")
async def get_account(request: Request):
    """Get the authenticated user's account data."""
    email = await _get_auth_email(request)
    from db import get_user_full
    user = await get_user_full(email)
    if not user:
        raise HTTPException(404, "Account not found")
    return user


@app.post("/api/account/update")
async def update_account(req: UpdateAccountRequest, request: Request):
    """Update the authenticated user's account."""
    email = await _get_auth_email(request)
    companies = [c.strip() for c in req.companies if c.strip()]
    from db import update_user_account
    user = await update_user_account(email, req.product, companies)
    if not user:
        raise HTTPException(404, "Account not found")
    return user
