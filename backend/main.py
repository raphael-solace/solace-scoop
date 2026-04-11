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


# -- Endpoints ---------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/subscribe")
async def subscribe(req: SubscribeRequest):
    if not req.companies:
        raise HTTPException(400, "At least one company is required")

    if os.getenv("SUPABASE_URL"):
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
