"""One-shot in-VPC migration: create sent_signal_keys + backfill from digest history.

Idempotent:
  - CREATE TABLE / INDEX use IF NOT EXISTS.
  - Backfill skips any digest whose id already appears in sent_signal_keys.digest_id.

Run as an ECS one-off task inside the VPC (RDS is private):
  command override: ["python", "migrate_ledger.py"]
"""
import asyncio
import json

from db import get_pool
from digest import _hard_keys

DDL = """
create table if not exists sent_signal_keys (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  digest_id uuid,
  fp text not null,
  headline text,
  company text,
  sent_at timestamptz not null default now()
);
create index if not exists idx_sent_keys_user_fp on sent_signal_keys(user_id, fp);
create index if not exists idx_sent_keys_user_sent on sent_signal_keys(user_id, sent_at);
create index if not exists idx_sent_keys_digest on sent_signal_keys(digest_id);
"""


async def main() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1) schema
        await conn.execute(DDL)
        print("[migrate] schema ensured")

        # 2) which digests are already backfilled?
        done_rows = await conn.fetch(
            "select distinct digest_id from sent_signal_keys where digest_id is not null"
        )
        done = {str(r["digest_id"]) for r in done_rows}
        print(f"[migrate] {len(done)} digest(s) already in ledger")

        # 3) walk all digests, oldest first (preserve chronology)
        digests = await conn.fetch(
            "select id, user_id, items, sent_at from digests order by sent_at asc"
        )
        print(f"[migrate] {len(digests)} digest(s) total")

        inserted_rows = 0
        inserted_digests = 0
        for d in digests:
            did = str(d["id"])
            if did in done:
                continue
            items = d["items"]
            if isinstance(items, str):
                items = json.loads(items)
            batch = []
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                headline = item.get("headline", "")
                company = item.get("company", "")
                if not headline:
                    continue
                for fp in _hard_keys(headline, company):
                    batch.append(
                        (d["user_id"], d["id"], fp, headline, company, d["sent_at"])
                    )
            if not batch:
                continue
            await conn.executemany(
                """insert into sent_signal_keys
                   (user_id, digest_id, fp, headline, company, sent_at)
                   values ($1, $2, $3, $4, $5, $6)""",
                batch,
            )
            inserted_rows += len(batch)
            inserted_digests += 1

        total = await conn.fetchval("select count(*) from sent_signal_keys")
        print(
            f"[migrate] backfilled {inserted_digests} digest(s), "
            f"{inserted_rows} key row(s). ledger total now {total}."
        )


if __name__ == "__main__":
    asyncio.run(main())
