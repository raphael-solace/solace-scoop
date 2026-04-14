"""
Scoop -- Database layer (Supabase REST API)

Uses httpx directly against the PostgREST API.
Service role key for backend operations (bypasses RLS).
"""

from __future__ import annotations

import os
import random
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def _headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


# -- Users ------------------------------------

async def get_all_users() -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("users"),
            headers=_headers(),
            params={"select": "*, companies(name)"},
        )
        resp.raise_for_status()
        return resp.json()


async def get_user_by_email(email: str) -> Optional[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("users"),
            headers=_headers(),
            params={
                "select": "*, companies(name)",
                "email": f"eq.{email}",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None


async def create_user(email: str, product: str, companies: list[str]) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _url("users"),
            headers=_headers(),
            json={"email": email, "product": product},
        )
        resp.raise_for_status()
        user = resp.json()[0]

        if companies:
            rows = [{"user_id": user["id"], "name": c} for c in companies]
            resp = await client.post(
                _url("companies"),
                headers=_headers(),
                json=rows,
            )
            resp.raise_for_status()

        return user


# -- Digests ----------------------------------

async def save_digest(user_id: str, items: list[dict]) -> None:
    async with httpx.AsyncClient() as client:
        await client.post(
            _url("digests"),
            headers=_headers(),
            json={
                "user_id": user_id,
                "item_count": len(items),
                "items": items,
            },
        )


async def get_last_digest(user_id: str) -> Optional[dict]:
    """Get the most recent saved digest for a user."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("digests"),
            headers=_headers(),
            params={
                "user_id": f"eq.{user_id}",
                "select": "items,sent_at,item_count",
                "order": "sent_at.desc",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None


async def get_previous_headlines(user_id: str, weeks: int = 4) -> list[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks)).isoformat()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("digests"),
            headers=_headers(),
            params={
                "user_id": f"eq.{user_id}",
                "sent_at": f"gte.{cutoff}",
                "select": "items",
                "order": "sent_at.desc",
            },
        )
        resp.raise_for_status()
        digests = resp.json()

    headlines = []
    for digest in digests:
        for item in digest.get("items", []):
            if isinstance(item, dict) and item.get("headline"):
                headlines.append(item["headline"])
    return headlines


# -- Delete -----------------------------------

async def delete_user(user_id: str) -> None:
    """Delete a user and all their data (cascade deletes companies, digests)."""
    async with httpx.AsyncClient() as client:
        await client.delete(
            _url("users") + f"?id=eq.{user_id}",
            headers=_headers(),
        )


# -- OTP Auth ---------------------------------

async def create_otp(email: str) -> str:
    """Create a 6-digit OTP, store in auth_tokens, return the code."""
    code = f"{random.randint(0, 999999):06d}"
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    async with httpx.AsyncClient() as client:
        # Clean up old OTPs for this email
        await client.delete(
            _url("auth_tokens") + f"?email=eq.{email}",
            headers=_headers(),
        )
        await client.post(
            _url("auth_tokens"),
            headers=_headers(),
            json={"email": email, "token": code, "expires_at": expires},
        )
    return code


async def verify_otp(email: str, code: str) -> bool:
    """Verify a 6-digit OTP. Returns True if valid. Deletes on success."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("auth_tokens"),
            headers=_headers(),
            params={
                "email": f"eq.{email}",
                "token": f"eq.{code}",
                "used_at": "is.null",
                "select": "id,expires_at",
            },
        )
        resp.raise_for_status()
        rows = resp.json()

    if not rows:
        return False

    row = rows[0]
    expires = datetime.fromisoformat(row["expires_at"])
    if datetime.now(timezone.utc) > expires:
        return False

    # Mark as used
    async with httpx.AsyncClient() as client:
        await client.patch(
            _url("auth_tokens") + f"?id=eq.{row['id']}",
            headers=_headers(),
            json={"used_at": datetime.now(timezone.utc).isoformat()},
        )
    return True


async def create_session(email: str) -> str:
    """Create a session token (24h), return it."""
    token = secrets.token_hex(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    async with httpx.AsyncClient() as client:
        await client.post(
            _url("auth_tokens"),
            headers=_headers(),
            json={"email": email, "token": token, "expires_at": expires},
        )
    return token


async def check_session(email: str, token: str) -> bool:
    """Check if a session token is valid."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("auth_tokens"),
            headers=_headers(),
            params={
                "email": f"eq.{email}",
                "token": f"eq.{token}",
                "select": "expires_at",
            },
        )
        resp.raise_for_status()
        rows = resp.json()

    if not rows:
        return False

    expires = datetime.fromisoformat(rows[0]["expires_at"])
    return datetime.now(timezone.utc) < expires
