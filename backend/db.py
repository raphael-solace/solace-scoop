"""
Scoop - Database layer (Supabase)

Simple async wrapper around the Supabase REST API.
Uses httpx directly to keep dependencies minimal.
Swap to the official supabase-py client when you need
realtime or auth helpers.
"""

from __future__ import annotations

import os
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


# ── Users ─────────────────────────────────────

async def get_all_users() -> list[dict]:
    """Fetch all users with their companies."""
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
    """Create a user and their tracked companies in one go."""
    async with httpx.AsyncClient() as client:
        # Create user
        resp = await client.post(
            _url("users"),
            headers=_headers(),
            json={"email": email, "product": product},
        )
        resp.raise_for_status()
        user = resp.json()[0]

        # Create companies
        if companies:
            rows = [{"user_id": user["id"], "name": c} for c in companies]
            resp = await client.post(
                _url("companies"),
                headers=_headers(),
                json=rows,
            )
            resp.raise_for_status()

        return user


# ── Digests ───────────────────────────────────

async def save_digest(user_id: str, items: list[dict]) -> None:
    """Save a sent digest for history/dedup."""
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


async def get_previous_headlines(user_id: str, weeks: int = 4) -> list[str]:
    """Get headlines from previous digests for dedup (last N weeks)."""
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


# ── Auth tokens ──────────────────────────────────

async def create_auth_token(email: str, expires_minutes: int = 15) -> str:
    """Create a magic-link token. Returns the token string."""
    token = secrets.token_hex(16)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)).isoformat()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _url("auth_tokens"),
            headers=_headers(),
            json={"email": email, "token": token, "expires_at": expires_at},
        )
        resp.raise_for_status()
    return token


async def verify_auth_token(token: str) -> Optional[str]:
    """Verify a magic-link token. Returns email if valid, None otherwise. Marks as used."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("auth_tokens"),
            headers=_headers(),
            params={
                "token": f"eq.{token}",
                "used_at": "is.null",
                "select": "id,email,expires_at",
            },
        )
        resp.raise_for_status()
        rows = resp.json()

    if not rows:
        return None

    row = rows[0]
    expires_at = datetime.fromisoformat(row["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        return None

    # Mark as used
    async with httpx.AsyncClient() as client:
        await client.patch(
            _url("auth_tokens") + f"?id=eq.{row['id']}",
            headers=_headers(),
            json={"used_at": datetime.now(timezone.utc).isoformat()},
        )

    return row["email"]


async def create_session_token(email: str, expires_hours: int = 24) -> str:
    """Create a longer-lived session token. Returns the token string."""
    token = secrets.token_hex(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).isoformat()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _url("auth_tokens"),
            headers=_headers(),
            json={"email": email, "token": token, "expires_at": expires_at},
        )
        resp.raise_for_status()
    return token


async def get_session_email(token: str) -> Optional[str]:
    """Validate a session token. Returns email if valid, None otherwise."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("auth_tokens"),
            headers=_headers(),
            params={
                "token": f"eq.{token}",
                "select": "email,expires_at",
            },
        )
        resp.raise_for_status()
        rows = resp.json()

    if not rows:
        return None

    row = rows[0]
    expires_at = datetime.fromisoformat(row["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        return None

    return row["email"]


# ── Account management ───────────────────────────

async def get_user_full(email: str) -> Optional[dict]:
    """Fetch user with companies and digest count."""
    async with httpx.AsyncClient() as client:
        # User + companies
        resp = await client.get(
            _url("users"),
            headers=_headers(),
            params={
                "select": "id, email, product, plan, created_at, companies(id, name)",
                "email": f"eq.{email}",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        user = rows[0]

        # Digest count
        resp = await client.get(
            _url("digests"),
            headers=_headers(),
            params={
                "user_id": f"eq.{user['id']}",
                "select": "id",
            },
        )
        resp.raise_for_status()
        user["digest_count"] = len(resp.json())

    return user


async def update_user_account(email: str, product: str, companies: list[str]) -> Optional[dict]:
    """Update a user's product description and replace their company list."""
    async with httpx.AsyncClient() as client:
        # Find user
        resp = await client.get(
            _url("users"),
            headers=_headers(),
            params={"email": f"eq.{email}", "select": "id"},
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        user_id = rows[0]["id"]

        # Update product
        h = _headers()
        h["Prefer"] = "return=representation,resolution=merge-duplicates"
        await client.post(
            _url("users") + "?on_conflict=email",
            headers=h,
            json={"email": email, "product": product},
        )

        # Delete old companies
        await client.delete(
            _url("companies") + f"?user_id=eq.{user_id}",
            headers=_headers(),
        )

        # Insert new companies
        if companies:
            company_rows = [{"user_id": user_id, "name": c} for c in companies]
            await client.post(
                _url("companies"),
                headers=_headers(),
                json=company_rows,
            )

    return await get_user_full(email)
