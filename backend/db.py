"""
Scoop — Database layer (Supabase)

Simple async wrapper around the Supabase REST API.
Uses httpx directly to keep dependencies minimal.
Swap to the official supabase-py client when you need
realtime or auth helpers.
"""

from __future__ import annotations

import os
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
