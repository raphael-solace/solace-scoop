"""
One-shot migration: Supabase (PostgREST) -> AWS RDS Postgres (asyncpg).

Runs inside the VPC (as an ECS task) so it can reach the private RDS.
Idempotent: applies schema with `if not exists`, and upserts rows by id.

Reads from SUPABASE_URL / SUPABASE_KEY.
Writes to DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

import asyncpg
import httpx


def _ts(v):
    """Parse a Supabase ISO-8601 timestamp string into a datetime for asyncpg."""
    if v is None or isinstance(v, datetime):
        return v
    s = str(v)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]

SB_HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

SCHEMA_FILE = Path(__file__).parent / "schema_rds.sql"


async def fetch_all(client: httpx.AsyncClient, table: str, select: str = "*") -> list[dict]:
    """Fetch all rows from a Supabase table with pagination."""
    out: list[dict] = []
    offset = 0
    page = 1000
    while True:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SB_HEADERS, "Range-Unit": "items", "Range": f"{offset}-{offset+page-1}"},
            params={"select": select},
        )
        if r.status_code == 400:
            # table doesn't exist in Supabase
            print(f"  [{table}] not present in Supabase (skip): {r.text[:120]}")
            return []
        r.raise_for_status()
        rows = r.json()
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out


async def main() -> None:
    print("=== Scoop DB migration: Supabase -> RDS ===", flush=True)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # 1. Apply schema
        print("Applying schema_rds.sql ...", flush=True)
        await conn.execute(SCHEMA_FILE.read_text())
        print("  schema applied.", flush=True)

        async with httpx.AsyncClient(timeout=60) as client:
            # 2. Users (parents first)
            users = await fetch_all(client, "users")
            print(f"users: {len(users)}", flush=True)
            for u in users:
                await conn.execute(
                    """insert into users (id, email, product, plan, stripe_customer_id, created_at, updated_at)
                       values ($1,$2,$3,coalesce($4,'free'),$5,coalesce($6,now()),coalesce($7,now()))
                       on conflict (id) do update set
                         email=excluded.email, product=excluded.product, plan=excluded.plan,
                         stripe_customer_id=excluded.stripe_customer_id, updated_at=excluded.updated_at""",
                    u["id"], u["email"], u.get("product"), u.get("plan"),
                    u.get("stripe_customer_id"), _ts(u.get("created_at")), _ts(u.get("updated_at")),
                )

            # 3. Companies
            companies = await fetch_all(client, "companies")
            print(f"companies: {len(companies)}", flush=True)
            for c in companies:
                await conn.execute(
                    """insert into companies (id, user_id, name, created_at)
                       values ($1,$2,$3,coalesce($4,now()))
                       on conflict (id) do update set name=excluded.name""",
                    c["id"], c["user_id"], c["name"], _ts(c.get("created_at")),
                )

            # 4. Digests (items is jsonb)
            digests = await fetch_all(client, "digests")
            print(f"digests: {len(digests)}", flush=True)
            for d in digests:
                items = d.get("items") or []
                await conn.execute(
                    """insert into digests
                         (id, user_id, sent_at, item_count, items, is_breaking,
                          delivery_status, delivered_at)
                       values ($1,$2,coalesce($3,now()),$4,$5::jsonb,coalesce($6,false),
                               'sent',coalesce($3,now()))
                       on conflict (id) do update set
                         item_count=excluded.item_count, items=excluded.items""",
                    d["id"], d["user_id"], _ts(d.get("sent_at")),
                    d.get("item_count") or len(items),
                    json.dumps(items), d.get("is_breaking", False),
                )

            # 5. People
            people = await fetch_all(client, "people")
            print(f"people: {len(people)}", flush=True)
            for p in people:
                await conn.execute(
                    """insert into people (id, user_id, company, name, title, email, linkedin, salesforce_url, created_at)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,coalesce($9,now()))
                       on conflict (id) do nothing""",
                    p["id"], p["user_id"], p.get("company"), p.get("name"), p.get("title"),
                    p.get("email"), p.get("linkedin"), p.get("salesforce_url"), _ts(p.get("created_at")),
                )

            # 6. Auth tokens
            tokens = await fetch_all(client, "auth_tokens")
            print(f"auth_tokens: {len(tokens)}", flush=True)
            for t in tokens:
                await conn.execute(
                    """insert into auth_tokens (id, email, token, expires_at, used_at, created_at)
                       values ($1,$2,$3,$4,$5,coalesce($6,now()))
                       on conflict (id) do nothing""",
                    t["id"], t["email"], t["token"], _ts(t["expires_at"]),
                    _ts(t.get("used_at")), _ts(t.get("created_at")),
                )

            # 7. signal_feedback (usually empty)
            feedback = await fetch_all(client, "signal_feedback")
            print(f"signal_feedback: {len(feedback)}", flush=True)
            for f in feedback:
                await conn.execute(
                    """insert into signal_feedback (id, digest_id, signal_index, user_id, vote, comment, created_at)
                       values ($1,$2,$3,$4,$5,$6,coalesce($7,now()))
                       on conflict (id) do nothing""",
                    f["id"], f["digest_id"], f["signal_index"], f["user_id"],
                    f["vote"], f.get("comment"), _ts(f.get("created_at")),
                )

        # 8. Verify counts
        print("\n=== RDS row counts ===", flush=True)
        for t in ["users", "companies", "digests", "people", "auth_tokens", "signal_feedback"]:
            n = await conn.fetchval(f"select count(*) from {t}")
            print(f"  {t:16} {n}", flush=True)

        print("\n=== MIGRATION COMPLETE ===", flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
