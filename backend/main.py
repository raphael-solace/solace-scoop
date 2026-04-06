"""
Scoop API Server

FastAPI backend that powers the digest pipeline.
Deploy to Railway, Render, Fly.io, or any Docker host.

Endpoints:
  POST /api/preview   — Generate a sample digest for onboarding
  POST /api/digest    — Generate a full weekly digest (called by cron)
  GET  /health        — Health check

Future endpoints (stubbed):
  POST /api/auth/magic-link   — Send magic link email
  POST /api/webhooks/stripe   — Stripe webhook handler
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

from digest import generate_digest_preview, generate_full_digest

load_dotenv()

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://noptus.github.io,http://localhost:3000").split(",")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify API key is configured
    if not os.getenv("PPLX_KEY"):
        raise RuntimeError("PPLX_KEY environment variable is required")
    yield
    # Shutdown: nothing to clean up


app = FastAPI(
    title="Scoop API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ── Request / Response Models ────────────────

class PreviewRequest(BaseModel):
    email: EmailStr
    product: str
    companies: list[str]


class DigestItem(BaseModel):
    company: str
    tag: str
    tag_color: str
    headline: str
    why: str
    sources: list[str] = []


class PreviewResponse(BaseModel):
    date: str
    company_count: int
    items: list[DigestItem]


class FullDigestRequest(BaseModel):
    """Triggered by cron. Processes all users or a specific user."""
    user_email: Optional[str] = None
    api_secret: str  # Simple auth for cron trigger


# ── Endpoints ────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "scoop-api"}


@app.post("/api/preview", response_model=PreviewResponse)
async def preview(req: PreviewRequest):
    """Generate a sample digest for the onboarding flow."""
    if not req.companies:
        raise HTTPException(400, "At least one company is required")

    # Limit to first 3 for preview
    companies = [c.strip() for c in req.companies if c.strip()][:3]
    items = await generate_digest_preview(companies, req.product)

    return PreviewResponse(
        date=_today_formatted(),
        company_count=len(req.companies),
        items=[DigestItem(**item) for item in items],
    )


@app.post("/api/digest")
async def run_digest(req: FullDigestRequest):
    """
    Run the full weekly digest pipeline.
    Called by a cron job (GitHub Actions, Railway cron, etc.).
    """
    expected_secret = os.getenv("CRON_SECRET", "")
    if not expected_secret or req.api_secret != expected_secret:
        raise HTTPException(403, "Invalid API secret")

    result = await generate_full_digest(user_email=req.user_email)
    return {"status": "ok", "processed": result["processed"]}


# ── Future: Auth & Payments (stubs) ──────────

# POST /api/auth/magic-link
# - Accepts { email }
# - Generates a one-time token, stores in DB
# - Sends email via Resend with magic link
# - On click: validates token, sets session cookie / JWT
#
# POST /api/webhooks/stripe
# - Validates Stripe signature
# - Handles checkout.session.completed → activate user
# - Handles customer.subscription.deleted → deactivate user
# - Handles invoice.payment_failed → notify user


# ── Helpers ──────────────────────────────────

def _today_formatted() -> str:
    from datetime import date
    d = date.today()
    months = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    return f"Preview — {months[d.month - 1]} {d.day}, {d.year}"
