"""
Scoop API Server

Endpoints:
  POST /api/subscribe  — Sign up a new user (from landing page)
  POST /api/digest     — Run weekly digest (called by cron)
  GET  /health         — Health check
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
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

    from db import get_all_users, get_user_by_email
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
            sent += 1

    return {"status": "ok", "processed": sent}
