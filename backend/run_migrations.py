"""Apply versioned SQL migrations to RDS under a database advisory lock."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from digest import _hard_keys

load_dotenv()

MIGRATIONS = [
    "migrate_delivery_state.sql",
    "migrate_signal_ledger.sql",
    "migrate_company_pool.sql",
    "migrate_auth_rate_limits.sql",
    "migrate_submitted_signals.sql",
]


async def main() -> None:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    root = Path(__file__).parent
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute("select pg_advisory_lock(hashtext('scoop-schema-migrations'))")
        if not await conn.fetchval("select to_regclass('users') is not null"):
            await conn.execute((root / "schema_rds.sql").read_text())
            print("[bootstrap] schema_rds.sql")
        await conn.execute(
            """create table if not exists schema_migrations (
                 name text primary key,
                 checksum text not null,
                 applied_at timestamptz not null default now()
               )"""
        )
        for name in MIGRATIONS:
            sql = (root / name).read_text()
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            row = await conn.fetchrow(
                "select checksum from schema_migrations where name=$1", name
            )
            if row:
                if row["checksum"] != checksum:
                    raise RuntimeError(f"Applied migration changed: {name}")
                print(f"[skip] {name}")
                continue
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "insert into schema_migrations(name,checksum) values($1,$2)",
                    name, checksum,
                )
            print(f"[applied] {name}")

        done = {
            row["digest_id"] for row in await conn.fetch(
                "select distinct digest_id from sent_signal_keys where digest_id is not null"
            )
        }
        digests = await conn.fetch(
            """select id,user_id,items,sent_at from digests
               where delivery_status='sent' order by sent_at"""
        )
        inserted = 0
        for digest in digests:
            if digest["id"] in done:
                continue
            items = digest["items"]
            if isinstance(items, str):
                import json
                items = json.loads(items)
            batch = []
            for item in items or []:
                headline = item.get("headline", "") if isinstance(item, dict) else ""
                company = item.get("company", "") if isinstance(item, dict) else ""
                for fp in _hard_keys(headline, company) if headline else []:
                    batch.append((digest["user_id"], digest["id"], fp,
                                  headline, company, digest["sent_at"]))
            if batch:
                await conn.executemany(
                    """insert into sent_signal_keys
                         (user_id,digest_id,fp,headline,company,sent_at)
                       values($1,$2,$3,$4,$5,$6)""",
                    batch,
                )
                inserted += len(batch)
        print(f"[ledger] inserted {inserted} delivered-history key(s)")
    finally:
        try:
            await conn.execute("select pg_advisory_unlock(hashtext('scoop-schema-migrations'))")
        finally:
            await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
