"""
Scoop -- Database layer (AWS RDS Postgres via asyncpg)

Direct Postgres connection using a shared connection pool.
Connection string comes from DATABASE_URL.

Public function signatures are unchanged from the previous Supabase REST
implementation, so callers (main.py, run_digest.py, digest.py, enrich.py)
need no changes beyond imports that were already db.* functions.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "")

_pool: Optional[asyncpg.Pool] = None


def _uuid(value: object) -> UUID:
    """Normalize API/string IDs for asyncpg UUID parameters."""
    return value if isinstance(value, UUID) else UUID(str(value))


async def get_pool() -> asyncpg.Pool:
    """Lazily create and return a shared asyncpg pool."""
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set")
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            command_timeout=60,
            # RDS uses TLS; asyncpg negotiates SSL when the server requires it.
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _row_to_user(row: asyncpg.Record, companies: list[str]) -> dict:
    """Shape a users row + company names into the dict callers expect.

    The old Supabase code returned `companies` as a list of {"name": ...}
    (from the `companies(name)` embed). Preserve that exact shape.
    """
    d = dict(row)
    d["companies"] = [{"name": c} for c in companies]
    return d


# -- Users ------------------------------------

async def get_all_users() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        users = await conn.fetch("select * from users")
        comps = await conn.fetch("select user_id, name from companies")
    by_user: dict[str, list[str]] = {}
    for c in comps:
        by_user.setdefault(str(c["user_id"]), []).append(c["name"])
    return [_row_to_user(u, by_user.get(str(u["id"]), [])) for u in users]


async def get_user_by_email(email: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        u = await conn.fetchrow("select * from users where email = $1", email)
        if not u:
            return None
        comps = await conn.fetch(
            "select name from companies where user_id = $1", u["id"]
        )
    return _row_to_user(u, [c["name"] for c in comps])


async def _sync_user_canonical_companies(
    conn: asyncpg.Connection, user_id: object,
) -> None:
    """Reconcile one user's legacy rows into canonical subscriptions."""
    await conn.execute(
        "update subscriber_companies set active=false where user_id=$1", user_id
    )
    rows = await conn.fetch(
        "select id, name from companies where user_id=$1 order by created_at", user_id
    )
    for row in rows:
        lookup = _lookup_company_name(row["name"])
        if not lookup:
            continue
        await conn.execute("select pg_advisory_xact_lock(hashtext($1))", lookup)
        company = await conn.fetchrow(
            "select id from canonical_companies where lookup_name=$1", lookup
        )
        if not company:
            company = await conn.fetchrow(
                """insert into canonical_companies(canonical_name, lookup_name)
                   values($1,$2) on conflict(lookup_name) do nothing returning id""",
                row["name"].strip(), lookup,
            )
            if not company:
                company = await conn.fetchrow(
                    "select id from canonical_companies where lookup_name=$1", lookup
                )
        await conn.execute(
            """insert into company_aliases(company_id,alias,lookup_alias)
               values($1,$2,$3) on conflict(company_id,lookup_alias) do nothing""",
            company["id"], row["name"], lookup,
        )
        await conn.execute(
            """insert into subscriber_companies(user_id,company_id,display_name,active)
               values($1,$2,$3,true)
               on conflict(user_id,company_id) do update
                 set display_name=excluded.display_name,active=true""",
            user_id, company["id"], row["name"],
        )
        await conn.execute(
            """insert into subscriber_company_legacy_links
                 (legacy_company_id,user_id,company_id)
               values($1,$2,$3) on conflict(legacy_company_id) do nothing""",
            row["id"], user_id, company["id"],
        )


async def create_user(email: str, product: str, companies: list[str]) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            u = await conn.fetchrow(
                "insert into users (email, product) values ($1, $2) returning *",
                email,
                product,
            )
            if companies:
                await conn.executemany(
                    "insert into companies (user_id, name) values ($1, $2)",
                    [(u["id"], c) for c in companies],
                )
            if await conn.fetchval("select to_regclass('subscriber_companies') is not null"):
                await _sync_user_canonical_companies(conn, u["id"])
    return dict(u)


async def set_user_accounts(user_id: str, product: str, companies: list[str]) -> None:
    """Replace a user's focus area (product) and tracked companies.

    Mirrors the account page's old Supabase save: PATCH product, then
    DELETE + re-INSERT companies. Runs in one transaction so a partial
    failure never leaves the user with an empty account list.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "update users set product = $1, updated_at = now() where id = $2",
                product,
                user_id,
            )
            await conn.execute("delete from companies where user_id = $1", user_id)
            if companies:
                await conn.executemany(
                    "insert into companies (user_id, name) values ($1, $2)",
                    [(user_id, c) for c in companies],
                )
            if await conn.fetchval("select to_regclass('subscriber_companies') is not null"):
                await _sync_user_canonical_companies(conn, user_id)


# -- Canonical companies and shared signal pool ------------------------------


def _lookup_company_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


async def backfill_canonical_companies() -> dict:
    """Idempotently link exact case-insensitive company names to shared UUIDs."""
    pool = await get_pool()
    created = linked = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """select c.id, c.user_id, c.name
                   from companies c
                   left join subscriber_company_legacy_links l
                     on l.legacy_company_id = c.id
                   where l.legacy_company_id is null
                   order by lower(c.name), c.created_at"""
            )
            for row in rows:
                lookup = _lookup_company_name(row["name"])
                if not lookup:
                    continue
                company = await conn.fetchrow(
                    "select id from canonical_companies where lookup_name = $1",
                    lookup,
                )
                if not company:
                    company = await conn.fetchrow(
                        """insert into canonical_companies
                             (canonical_name, lookup_name)
                           values ($1, $2)
                           on conflict (lookup_name) do nothing
                           returning id""",
                        row["name"].strip(), lookup,
                    )
                    if company:
                        created += 1
                    else:
                        company = await conn.fetchrow(
                            "select id from canonical_companies where lookup_name=$1",
                            lookup,
                        )
                await conn.execute(
                    """insert into company_aliases
                         (company_id, alias, lookup_alias, verified)
                       values ($1, $2, $3, false)
                       on conflict (company_id, lookup_alias) do nothing""",
                    company["id"], row["name"], lookup,
                )
                await conn.execute(
                    """insert into subscriber_companies
                         (user_id, company_id, display_name)
                       values ($1, $2, $3)
                       on conflict (user_id, company_id) do update
                         set display_name = excluded.display_name, active = true""",
                    row["user_id"], company["id"], row["name"],
                )
                await conn.execute(
                    """insert into subscriber_company_legacy_links
                         (legacy_company_id, user_id, company_id)
                       values ($1, $2, $3)
                       on conflict (legacy_company_id) do nothing""",
                    row["id"], row["user_id"], company["id"],
                )
                linked += 1
    return {"canonical_created": created, "subscriptions_linked": linked}


async def get_distinct_subscribed_companies() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select cc.id, cc.canonical_name, cc.primary_domain, cc.status,
                      count(sc.user_id) subscriber_count
               from canonical_companies cc
               join subscriber_companies sc on sc.company_id = cc.id and sc.active
               where cc.status in ('active','provisional')
               group by cc.id
               order by cc.canonical_name"""
        )
    return [dict(row) for row in rows]


async def claim_research_job(
    company_id: str, window_start: date, window_end: date,
    prompt_version: str, worker_id: str, lease_minutes: int = 90,
) -> Optional[dict]:
    """Claim one canonical company/window job across all ECS workers."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """insert into research_jobs
                     (company_id, window_start, window_end, prompt_version)
                   values ($1, $2, $3, $4)
                   on conflict (company_id, window_start, window_end, prompt_version)
                   do nothing""",
                _uuid(company_id), window_start, window_end, prompt_version,
            )
            claim_token = secrets.token_hex(16)
            row = await conn.fetchrow(
                """update research_jobs
                   set status = 'running', claimed_by = $5, claim_token = $7,
                       claimed_until = now() + ($6 * interval '1 minute'),
                       started_at = coalesce(started_at, now()), attempts = attempts + 1
                   where company_id = $1 and window_start = $2 and window_end = $3
                     and prompt_version = $4
                     and (status in ('pending','partial','failed')
                          or (status = 'running' and claimed_until < now()))
                   returning id""",
                _uuid(company_id), window_start, window_end, prompt_version,
                worker_id, lease_minutes, claim_token,
            )
    return {"id": str(row["id"]), "token": claim_token} if row else None


async def complete_research_job(
    job_id: str, claim_token: str, items: list[dict], *, partial: bool = False
) -> None:
    """Persist neutral validated facts and mark a shared job complete."""
    import hashlib
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            job = await conn.fetchrow(
                """select company_id from research_jobs
                   where id=$1 and status='running' and claim_token=$2
                     and claimed_until >= now()
                   for update""",
                _uuid(job_id), claim_token,
            )
            if not job:
                raise RuntimeError("Research job lease is no longer owned")
            for item in items:
                neutral_item = {
                    key: value for key, value in item.items()
                    if key not in {
                        "so_what", "why", "message", "opening_line",
                        "contact_name", "contact_title", "contact_linkedin",
                        "contact_email", "salesforce_url",
                    }
                }
                event_date = date.fromisoformat(neutral_item["date"])
                headline = neutral_item.get("headline", "").strip()
                event_key = hashlib.sha256(
                    f"{job['company_id']}|{event_date.isoformat()}|{headline.lower()}".encode()
                ).hexdigest()
                signal = await conn.fetchrow(
                    """insert into shared_signals
                         (company_id, event_key, event_date, category, headline,
                          factual_summary, signal_strength, research_job_id,
                          raw_payload, expires_at)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
                       on conflict (company_id, event_key) do update set
                         last_seen_at = now(), raw_payload = excluded.raw_payload
                       returning id""",
                    job["company_id"], event_key, event_date,
                    neutral_item.get("tag"), headline,
                    "",
                    int(neutral_item.get("signal_strength", 1) or 1), _uuid(job_id),
                    json.dumps(neutral_item),
                    datetime.combine(event_date + timedelta(days=15), datetime.min.time(), tzinfo=timezone.utc),
                )
                for source_url in neutral_item.get("sources", []) or []:
                    await conn.execute(
                        """insert into signal_sources
                             (signal_id, source_type, source_url, canonical_url)
                           values ($1, 'model_web', $2, $3)
                           on conflict (signal_id, canonical_url) do nothing""",
                        signal["id"], source_url, source_url,
                    )
            result = await conn.execute(
                """update research_jobs set status = $3, completed_at = now(),
                     claimed_by = null, claim_token = null, claimed_until = null,
                     error_text = null
                   where id=$1 and status='running' and claim_token=$2
                     and claimed_until >= now()""",
                _uuid(job_id), claim_token, "partial" if partial else "complete",
            )
            if not result.endswith("1"):
                raise RuntimeError("Research job lease expired before completion")


async def fail_research_job(
    job_id: str, claim_token: str, error: str
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """update research_jobs set status='failed', completed_at=now(),
                 claimed_by=null, claim_token=null, claimed_until=null, error_text=$3
               where id=$1 and status='running' and claim_token=$2
                 and claimed_until >= now()""",
            _uuid(job_id), claim_token, error[:500],
        )


async def get_shared_signals_for_user(user_id: str, window_start: date) -> list[dict]:
    """Load fresh shared facts for a user's canonical subscriptions."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select s.id, s.raw_payload
               from shared_signals s
               join subscriber_companies sc on sc.company_id=s.company_id
                 and sc.user_id=$1 and sc.active
               where s.event_date >= $2 and s.expires_at > now()
               order by s.event_date desc, s.signal_strength desc""",
            user_id, window_start,
        )
    result = []
    for row in rows:
        item = row["raw_payload"]
        if isinstance(item, str):
            item = json.loads(item)
        item = dict(item)
        item["shared_signal_id"] = str(row["id"])
        result.append(item)
    return result


async def seed_source_catalog(entries: list[dict]) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for entry in entries:
            await conn.execute(
                """insert into source_catalog
                     (name, adapter_type, endpoint_template, terms_url, robots_url,
                      licence, storage_mode, enabled, review_status, poll_minutes,
                      max_requests_per_second, config)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
                   on conflict (name) do update set
                     adapter_type=excluded.adapter_type,
                     endpoint_template=excluded.endpoint_template,
                     terms_url=excluded.terms_url, robots_url=excluded.robots_url,
                     licence=excluded.licence, storage_mode=excluded.storage_mode,
                     enabled=excluded.enabled, review_status=excluded.review_status,
                     poll_minutes=excluded.poll_minutes,
                     max_requests_per_second=excluded.max_requests_per_second,
                     config=source_catalog.config || excluded.config,
                     updated_at=now()""",
                entry["name"], entry["adapter"], entry["endpoint"],
                entry.get("terms_url"), entry.get("robots_url"),
                entry.get("licence"), entry.get("storage_mode", "metadata_excerpt_link"),
                bool(entry.get("enabled", False)), entry.get("review_status", "pending"),
                int(entry.get("poll_minutes", 1440)),
                float(entry.get("max_requests_per_second", 0.5)),
                json.dumps(entry.get("config", {})),
            )
    return len(entries)


async def get_enabled_company_sources(adapter_type: str) -> list[dict]:
    """Resolve enabled company-scoped source endpoints from verified identifiers."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select sc.id source_id, sc.adapter_type, sc.endpoint_template,
                      cc.id company_id, cc.canonical_name,
                      coalesce(cs.external_id, ci.value) external_id,
                      cs.etag, cs.last_modified
               from source_catalog sc
               join company_identifiers ci
                 on ci.scheme = sc.config->>'identifier_scheme'
               join canonical_companies cc on cc.id=ci.company_id
               left join company_sources cs
                 on cs.company_id=cc.id and cs.source_id=sc.id
               where sc.enabled and sc.review_status='approved'
                 and sc.adapter_type=$1
                 and coalesce(cs.enabled,true)""",
            adapter_type,
        )
    return [dict(row) for row in rows]


async def get_enabled_global_sources() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select * from source_catalog
               where enabled and review_status='approved'
                 and coalesce(config->>'scope','global')='global'
               order by name"""
        )
    return [dict(row) for row in rows]


async def upsert_source_items(
    source_id: str, items: list[dict], company_id: str | None = None,
) -> int:
    if not items:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in items:
                await conn.execute(
                    """insert into source_items
                         (source_id, company_id, source_item_id, source_url, title,
                          excerpt, published_at, source_updated_at, content_hash,
                          raw_payload)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                       on conflict (source_id, source_item_id) do update set
                         source_url=excluded.source_url, title=excluded.title,
                         excerpt=excluded.excerpt,
                         published_at=excluded.published_at,
                         source_updated_at=excluded.source_updated_at,
                         processing_status = case
                           when source_items.content_hash is distinct from excluded.content_hash
                           then 'pending' else source_items.processing_status end,
                         content_hash=excluded.content_hash,
                         raw_payload=excluded.raw_payload,
                         last_seen_at=now()""",
                    _uuid(source_id), _uuid(company_id) if company_id else None,
                    item["source_item_id"], item["url"], item["title"],
                    item.get("summary", "")[:1000], item.get("published_at"),
                    item.get("updated_at"), item.get("content_hash"),
                    json.dumps(item.get("raw", {}), default=str),
                )
    return len(items)


async def get_pending_source_items(limit: int = 200) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select si.*, sc.name source_name
               from source_items si join source_catalog sc on sc.id=si.source_id
               where si.processing_status='pending'
               order by si.published_at desc nulls last, si.first_seen_at
               limit $1""",
            limit,
        )
    result = []
    for row in rows:
        item = dict(row)
        if isinstance(item.get("raw_payload"), str):
            item["raw_payload"] = json.loads(item["raw_payload"])
        result.append(item)
    return result


async def match_global_source_items(limit: int = 200) -> dict:
    """Conservatively link global feed items to exactly one canonical alias."""
    import re
    pool = await get_pool()
    linked = ambiguous = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            aliases = await conn.fetch(
                """select ca.company_id, ca.alias
                   from company_aliases ca
                   join canonical_companies cc on cc.id=ca.company_id
                   where cc.status='active' and ca.verified
                     and length(ca.alias) >= 4"""
            )
            rows = await conn.fetch(
                """select id,title,excerpt from source_items
                   where processing_status='pending' and company_id is null
                   order by published_at desc nulls last limit $1
                   for update skip locked""",
                limit,
            )
            for row in rows:
                haystack = f"{row['title']} {row['excerpt'] or ''}".lower()
                matches = {
                    alias["company_id"] for alias in aliases
                    if re.search(
                        rf"(?<![a-z0-9]){re.escape(alias['alias'].lower())}(?![a-z0-9])",
                        haystack,
                    )
                }
                if len(matches) == 1:
                    await conn.execute(
                        "update source_items set company_id=$2 where id=$1",
                        row["id"], next(iter(matches)),
                    )
                    linked += 1
                elif len(matches) > 1:
                    ambiguous += 1
    return {"linked": linked, "ambiguous": ambiguous}


async def classify_company_source_items(limit: int = 200) -> dict:
    """Promote company-linked official items into neutral shared signals.

    Global regulator feeds intentionally remain pending until entity matching is
    explicit; this function never guesses a company from title text.
    """
    import hashlib
    pool = await get_pool()
    classified = ignored = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """select si.*, cc.canonical_name
                   from source_items si
                   left join canonical_companies cc on cc.id=si.company_id
                   where si.processing_status='pending' and si.company_id is not null
                   order by si.published_at desc nulls last, si.first_seen_at
                   limit $1 for update skip locked""",
                limit,
            )
            for row in rows:
                if not row["company_id"]:
                    continue
                if not row["published_at"]:
                    await conn.execute(
                        "update source_items set processing_status='ignored' where id=$1",
                        row["id"],
                    )
                    ignored += 1
                    continue
                event_date = row["published_at"].date()
                if (date.today() - event_date).days > 14 or event_date > date.today():
                    await conn.execute(
                        "update source_items set processing_status='ignored' where id=$1",
                        row["id"],
                    )
                    ignored += 1
                    continue
                event_key = hashlib.sha256(
                    f"{row['company_id']}|{row['source_id']}|{row['source_item_id']}".encode()
                ).hexdigest()
                raw = {
                    "company": row["canonical_name"] or "",
                    "date": event_date.isoformat(),
                    "headline": row["title"],
                    "so_what": row["excerpt"] or "",
                    "tag": "Official Update",
                    "tag_color": "blue",
                    "signal_strength": 2,
                    "persona_fit": 2,
                    "sources": [row["source_url"]],
                }
                signal = await conn.fetchrow(
                    """insert into shared_signals
                         (company_id,event_key,event_date,category,headline,
                          factual_summary,signal_strength,raw_payload,expires_at)
                       values($1,$2,$3,'official_source',$4,$5,2,$6::jsonb,$7)
                       on conflict(company_id,event_key) do update set
                         last_seen_at=now(),raw_payload=excluded.raw_payload
                       returning id""",
                    row["company_id"], event_key, event_date, row["title"],
                    row["excerpt"] or "", json.dumps(raw),
                    datetime.combine(event_date + timedelta(days=15), datetime.min.time(), tzinfo=timezone.utc),
                )
                await conn.execute(
                    """insert into signal_sources
                         (signal_id,source_type,source_item_id,source_url,
                          canonical_url,title,excerpt,published_at,
                          updated_at,retrieved_at,raw_payload_hash)
                       values($1,'official',$2,$3,$3,$4,$5,$6,$7,now(),$8)
                       on conflict(signal_id,canonical_url) do nothing""",
                    signal["id"], f"{row['source_id']}:{row['source_item_id']}", row["source_url"],
                    row["title"], row["excerpt"], row["published_at"],
                    row["source_updated_at"], row["content_hash"],
                )
                await conn.execute(
                    "update source_items set processing_status='classified' where id=$1",
                    row["id"],
                )
                classified += 1
    return {"classified": classified, "ignored": ignored}


async def mark_source_item(item_id: str, status: str) -> None:
    if status not in {"classified", "ignored", "failed"}:
        raise ValueError("invalid source item status")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "update source_items set processing_status=$2,last_seen_at=now() where id=$1",
            _uuid(item_id), status,
        )


async def update_global_source_fetch(
    source_id: str, etag: str | None, last_modified: str | None,
    error: str | None = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        config = {
            "etag": etag,
            "last_modified": last_modified,
            "last_error": error,
            "last_fetch_at": datetime.now(timezone.utc).isoformat(),
        }
        await conn.execute(
            """update source_catalog set
                 config = config || $2::jsonb,
                 updated_at = now()
               where id = $1""",
            _uuid(source_id), json.dumps(config),
        )


# -- Digests ----------------------------------

async def save_digest(user_id: str, items: list[dict]) -> str:
    """Save a digest and return its ID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serialize weekly candidate creation per user so overlapping research
            # jobs cannot leave multiple pending rows.
            await conn.execute("select pg_advisory_xact_lock(hashtext($1))", str(user_id))
            active_send = await conn.fetchval(
                """select exists(
                       select 1 from digests
                       where user_id = $1 and is_breaking = false
                         and delivery_status = 'sending'
                         and sent_at >= now() - interval '24 hours'
                   )""",
                user_id,
            )
            if active_send:
                raise RuntimeError("A weekly digest delivery is already in progress")
            await conn.execute(
                """update digests set delivery_status = 'superseded'
                   where user_id = $1 and is_breaking = false
                     and delivery_status in ('pending', 'failed')""",
                user_id,
            )
            row = await conn.fetchrow(
                """insert into digests (user_id, item_count, items)
                   values ($1, $2, $3::jsonb) returning id""",
                user_id,
                len(items),
                json.dumps(items),
            )
    return str(row["id"]) if row else ""


async def get_previous_signals(user_id: str, weeks: int = 12) -> list[dict]:
    """Return previously-sent signals as {headline, company} dicts, so dedup can
    fingerprint headline AND company consistently on both sides."""
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select items from digests
               where user_id = $1 and sent_at >= $2
                 and delivery_status = 'sent'
               order by sent_at desc""",
            user_id,
            cutoff,
        )
    out: list[dict] = []
    for r in rows:
        items = r["items"]
        if isinstance(items, str):
            items = json.loads(items)
        for item in items or []:
            if isinstance(item, dict) and item.get("headline"):
                out.append({
                    "headline": item["headline"],
                    "company": item.get("company", ""),
                    "date": item.get("date", ""),
                })
    return out


async def get_previous_signal_keys(user_id: str, weeks: int = 12) -> set[str]:
    """Load the hard fingerprint ledger for a user within the lookback window."""
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select sk.fp from sent_signal_keys sk
               join digests d on d.id = sk.digest_id
               where sk.user_id = $1 and sk.sent_at >= $2
                 and d.delivery_status = 'sent'""",
            user_id,
            cutoff,
        )
    return {r["fp"] for r in rows}


async def record_sent_signal_keys(user_id: str, keys: list[dict]) -> None:
    """Append shipped signals' fingerprints to the ledger.

    keys: list of {"fp": str, "headline": str, "company": str, "digest_id": str}.
    """
    if not keys:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """insert into sent_signal_keys (user_id, digest_id, fp, headline, company)
               values ($1, $2, $3, $4, $5)""",
            [(user_id, _uuid(k["digest_id"]), k["fp"], k.get("headline", ""),
              k.get("company", "")) for k in keys],
        )


async def get_last_digest(user_id: str) -> Optional[dict]:
    """Get the most recent saved digest for a user (dashboard/history use)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """select id, items, sent_at, item_count, is_breaking,
                      delivery_status, delivered_at
               from digests where user_id = $1
               order by sent_at desc limit 1""",
            user_id,
        )
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    d["items"] = json.loads(d["items"]) if isinstance(d["items"], str) else d["items"]
    return d


async def get_last_delivered_weekly_digest(user_id: str) -> Optional[dict]:
    """Get the latest successfully delivered non-breaking digest."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """select id, items, sent_at, item_count, delivery_status, delivered_at
               from digests
               where user_id = $1 and is_breaking = false
                 and delivery_status = 'sent'
               order by delivered_at desc nulls last, sent_at desc limit 1""",
            user_id,
        )
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    d["items"] = json.loads(d["items"]) if isinstance(d["items"], str) else d["items"]
    return d


async def get_pending_weekly_digests(user_id: str, max_age_hours: int = 18) -> list[dict]:
    """Return all fresh, unsent, non-breaking weekly candidates for a user."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select id, items, sent_at, item_count, delivery_status
               from digests
               where user_id = $1
                 and is_breaking = false
                 and delivery_status = 'pending'
                 and sent_at >= $2
               order by sent_at desc""",
            user_id,
            cutoff,
        )
    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        d["items"] = json.loads(d["items"]) if isinstance(d["items"], str) else d["items"]
        result.append(d)
    return result


async def get_pending_weekly_digest(user_id: str, max_age_hours: int = 18) -> Optional[dict]:
    """Get exactly one fresh, unsent, non-breaking weekly digest.

    Fails closed: an older, duplicate, or already claimed/sent digest is never reused.
    """
    rows = await get_pending_weekly_digests(user_id, max_age_hours=max_age_hours)
    return rows[0] if len(rows) == 1 else None


async def claim_digest_for_send(digest_id: str) -> bool:
    """Atomically claim a pending digest so concurrent/repeated jobs cannot resend it."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """update digests set delivery_status = 'sending', delivery_error = null
               where id = $1 and delivery_status = 'pending'""",
            _uuid(digest_id),
        )
    return result.endswith("1")


async def complete_digest_send(digest_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """update digests
               set delivery_status = 'sent', delivered_at = now(), sent_at = now(),
                   delivery_error = null
               where id = $1 and delivery_status = 'sending'""",
            _uuid(digest_id),
        )


async def fail_digest_send(digest_id: str, error: str) -> None:
    """Release a definite pre-send failure for a later retry."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """update digests
               set delivery_status = 'pending', delivery_error = $2
               where id = $1 and delivery_status = 'sending'""",
            _uuid(digest_id),
            error[:500],
        )


async def get_previous_headlines(user_id: str, weeks: int = 4) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select items from digests
               where user_id = $1 and sent_at >= $2
                 and delivery_status = 'sent'
               order by sent_at desc""",
            user_id,
            cutoff,
        )
    headlines: list[str] = []
    for r in rows:
        items = r["items"]
        if isinstance(items, str):
            items = json.loads(items)
        for item in items or []:
            if isinstance(item, dict) and item.get("headline"):
                headlines.append(item["headline"])
    return headlines


async def get_previous_source_urls(user_id: str, weeks: int = 6) -> set[str]:
    """Get all source URLs from previously sent digests to prevent re-sending the same article."""
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select items from digests
               where user_id = $1 and sent_at >= $2
                 and delivery_status = 'sent'
               order by sent_at desc""",
            user_id,
            cutoff,
        )
    urls: set[str] = set()
    for r in rows:
        items = r["items"]
        if isinstance(items, str):
            items = json.loads(items)
        for item in items or []:
            if not isinstance(item, dict):
                continue
            for src in item.get("sources", []):
                if isinstance(src, str) and src.startswith("http"):
                    from digest import _canonical_source_url
                    normalized = _canonical_source_url(src)
                    if normalized:
                        urls.add(normalized)
    return urls


async def get_digest_owner(digest_id: str) -> Optional[str]:
    """Return the user_id that owns a digest, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select user_id from digests where id = $1", _uuid(digest_id)
        )
    return str(row["user_id"]) if row else None


# -- Delete -----------------------------------

async def delete_user(user_id: str) -> None:
    """Delete a user and all their data (cascade deletes companies, digests)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("delete from users where id = $1", user_id)


# -- People -----------------------------------

async def get_people(user_id: str) -> list[dict]:
    """Get tracked people for a user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select id, company, name, title, email, linkedin, salesforce_url
               from people where user_id = $1""",
            user_id,
        )
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        out.append(d)
    return out


async def add_person(user_id: str, person: dict) -> dict:
    """Add a tracked contact for a user. Returns the created row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """insert into people (user_id, name, company, title, linkedin)
               values ($1, $2, $3, $4, $5)
               returning id, company, name, title, email, linkedin, salesforce_url""",
            user_id,
            person.get("name", ""),
            person.get("company", ""),
            person.get("title", ""),
            person.get("linkedin", ""),
        )
    d = dict(row)
    d["id"] = str(d["id"])
    return d


async def delete_person(user_id: str, person_id: str) -> bool:
    """Delete a contact, scoped to the owning user. Returns True if a row was removed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "delete from people where id = $1 and user_id = $2",
            _uuid(person_id),
            user_id,
        )
    # asyncpg returns e.g. "DELETE 1"
    return result.endswith("1")


# -- Digests (dashboard reads) ----------------

async def get_recent_digests(user_id: str, limit: int = 4) -> list[dict]:
    """Most recent digests for the News tab, newest first: [{items, sent_at}]."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select items, sent_at from digests
               where user_id = $1 and delivery_status = 'sent'
               order by sent_at desc limit $2""",
            user_id,
            limit,
        )
    out = []
    for r in rows:
        items = r["items"]
        if isinstance(items, str):
            items = json.loads(items)
        out.append({"items": items or [], "sent_at": r["sent_at"].isoformat()})
    return out


async def get_digest_count(user_id: str) -> int:
    """Total digests sent to a user (for the stats bar)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "select count(*) from digests where user_id = $1 and delivery_status = 'sent'", user_id
        )


# -- OTP Auth ---------------------------------

async def create_otp(email: str, cooldown_seconds: int = 60) -> str:
    """Create a 6-digit OTP unless one was issued too recently."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(hashtext($1))", email)
            recent = await conn.fetchval(
                """select exists(
                     select 1 from auth_tokens
                     where email = $1 and length(token) = 6
                       and created_at > now() - make_interval(secs => $2)
                   )""",
                email,
                cooldown_seconds,
            )
            if recent:
                raise RuntimeError("OTP request cooldown is active")
            await conn.execute(
                "delete from auth_tokens where email = $1 and length(token) = 6", email
            )
            await conn.execute(
                "insert into auth_tokens (email, token, expires_at) values ($1, $2, $3)",
                email,
                code,
                expires,
            )
    return code


async def verify_otp(email: str, code: str, max_attempts: int = 5) -> bool:
    """Verify a 6-digit OTP with a bounded number of guesses."""
    if len(code) != 6 or not code.isdigit():
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """update auth_tokens
               set attempts = attempts + 1,
                   used_at = case when token = $2 then now() else used_at end
               where id = (
                 select id from auth_tokens
                 where email = $1 and used_at is null and expires_at > now()
                   and length(token) = 6 and attempts < $3
                 order by created_at desc limit 1
                 for update
               )
               returning token = $2 as valid""",
            email,
            code,
            max_attempts,
        )
    return bool(row and row["valid"])


async def create_session(email: str) -> str:
    """Create a session token (24h), return it."""
    token = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into auth_tokens (email, token, expires_at) values ($1, $2, $3)",
            email,
            token,
            expires,
        )
    return token


async def check_session(email: str, token: str) -> bool:
    """Check if a 64-character session token is valid."""
    if len(token) != 64:
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select expires_at from auth_tokens where email = $1 and token = $2",
            email,
            token,
        )
    if not row:
        return False
    return datetime.now(timezone.utc) < row["expires_at"]


# -- Feedback ---------------------------------

async def save_feedback(digest_id: str, signal_index: int, user_id: str, vote: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """insert into signal_feedback (digest_id, signal_index, user_id, vote)
               values ($1, $2, $3, $4)""",
            _uuid(digest_id),
            signal_index,
            user_id,
            vote,
        )


# -- Breaking Scoop tracking ------------------

async def get_breaking_scoops_this_week(user_id: str) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """select count(*) from digests
               where user_id = $1 and is_breaking = true and sent_at >= $2
                 and delivery_status = 'sent'""",
            user_id,
            cutoff,
        )
    return int(count or 0)


async def save_breaking_digest(
    user_id: str, items: list[dict], max_per_week: int
) -> str:
    """Create a breaking draft only if the delivered weekly quota has room."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(hashtext($1))", str(user_id))
            count = await conn.fetchval(
                """select count(*) from digests
                   where user_id = $1 and is_breaking = true and sent_at >= $2
                     and delivery_status in ('sending', 'sent')""",
                user_id,
                cutoff,
            )
            if int(count or 0) >= max_per_week:
                return ""
            row = await conn.fetchrow(
                """insert into digests (user_id, item_count, items, is_breaking)
                   values ($1, $2, $3::jsonb, true) returning id""",
                user_id,
                len(items),
                json.dumps(items),
            )
    return str(row["id"]) if row else ""


# -- Admin dashboard (narrow read-only queries) -------------------------------

_ADMIN_MAX_PAGE_SIZE = 100
_ADMIN_MAX_WEEKS = 52


def _admin_week_start(now: datetime | None = None) -> datetime:
    """Return the UTC Monday 00:00 boundary containing ``now``."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    return (current - timedelta(days=current.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _admin_encode_cursor(created_at: datetime, row_id: object) -> str:
    import base64
    payload = json.dumps(
        [created_at.astimezone(timezone.utc).isoformat(), str(row_id)],
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _admin_decode_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    import base64
    import binascii
    if cursor is None:
        return None, None
    if not cursor or len(cursor) > 512 or "=" in cursor:
        raise ValueError("Invalid cursor")
    try:
        raw = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
        )
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) != 2:
            raise ValueError
        timestamp = datetime.fromisoformat(data[0])
        row_id = UUID(data[1])
        if timestamp.tzinfo is None:
            raise ValueError
        timestamp = timestamp.astimezone(timezone.utc)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        raise ValueError("Invalid cursor") from None
    return timestamp, row_id


def _admin_iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


async def get_admin_summary(
    weeks: int = 12, *, now: datetime | None = None
) -> dict:
    """Return aggregate admin metrics without reading payload or secret fields."""
    weeks = max(1, min(int(weeks), _ADMIN_MAX_WEEKS))
    current_week = _admin_week_start(now)
    first_week = current_week - timedelta(weeks=weeks - 1)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            totals = await conn.fetchrow(
                """with subscriber_totals as (
                     select count(*)::bigint as subscribers from users
                   ), account_totals as (
                     select count(*)::bigint as tracked_accounts,
                            count(distinct lower(trim(name)))::bigint
                              as unique_tracked_accounts
                     from companies
                   ), delivery_totals as (
                     select count(*)::bigint as delivered_emails,
                            coalesce(sum(item_count) filter (
                              where delivered_at >= $1
                            ), 0)::bigint as articles_this_week,
                            count(*) filter (
                              where delivered_at >= $1
                            )::bigint as delivered_emails_this_week
                     from digests
                     where delivery_status = 'sent'
                       and delivered_at is not null
                   )
                   select s.subscribers, a.tracked_accounts,
                          a.unique_tracked_accounts, d.delivered_emails,
                          d.articles_this_week, d.delivered_emails_this_week
                   from subscriber_totals s
                   cross join account_totals a
                   cross join delivery_totals d""",
                current_week,
            )
            weekly_rows = await conn.fetch(
                """select weeks.week_start,
                          coalesce(sum(d.item_count), 0)::bigint as articles,
                          count(d.id)::bigint as delivered_emails
                   from generate_series(
                     $1::timestamptz, $2::timestamptz, interval '1 week'
                   ) as weeks(week_start)
                   left join digests d
                     on d.delivery_status = 'sent'
                    and d.delivered_at is not null
                    and d.delivered_at >= weeks.week_start
                    and d.delivered_at < weeks.week_start + interval '1 week'
                   group by weeks.week_start
                   order by weeks.week_start""",
                first_week,
                current_week,
            )
    totals = totals or {}
    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    return {
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "period": {
            "timezone": "UTC",
            "week_start": current_week.isoformat(),
            "weeks": weeks,
        },
        "totals": {
            "subscribers": int(totals.get("subscribers", 0) or 0),
            "tracked_accounts": int(totals.get("tracked_accounts", 0) or 0),
            "delivered_emails": int(totals.get("delivered_emails", 0) or 0),
            "articles_this_week": int(totals.get("articles_this_week", 0) or 0),
            "emails_this_week": int(
                totals.get("delivered_emails_this_week", 0) or 0
            ),
        },
        "weekly_articles": [
            {
                "week_start": _admin_iso(row["week_start"]),
                "article_count": int(row["articles"] or 0),
                "email_count": int(row["delivered_emails"] or 0),
            }
            for row in weekly_rows
        ],
    }


async def get_admin_subscribers(
    limit: int = 50,
    cursor: str | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """Return a keyset page of subscriber accounts and delivery aggregates."""
    limit = max(1, min(int(limit), _ADMIN_MAX_PAGE_SIZE))
    cursor_time, cursor_id = _admin_decode_cursor(cursor)
    week_start = _admin_week_start(now)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            rows = await conn.fetch(
                """with selected_users as (
                     select u.id, u.email, u.product, u.plan, u.created_at
                     from users u
                     where ($2::timestamptz is null
                            or (u.created_at, u.id) < ($2::timestamptz, $3::uuid))
                     order by u.created_at desc, u.id desc
                     limit $1
                   )
                   select su.id, su.email, su.product, su.plan, su.created_at,
                          coalesce(
                            array_agg(c.name order by c.created_at, c.id)
                              filter (where c.id is not null),
                            array[]::text[]
                          ) as accounts,
                          coalesce(ds.articles_this_week, 0)::bigint
                            as articles_this_week,
                          coalesce(ds.delivered_emails, 0)::bigint
                            as delivered_emails,
                          ds.last_delivered_at
                   from selected_users su
                   left join companies c on c.user_id = su.id
                   left join lateral (
                     select coalesce(sum(d.item_count) filter (
                              where d.delivered_at >= $4
                            ), 0)::bigint as articles_this_week,
                            count(*)::bigint as delivered_emails,
                            max(d.delivered_at) as last_delivered_at
                     from digests d
                     where d.user_id = su.id
                       and d.delivery_status = 'sent'
                       and d.delivered_at is not null
                   ) ds on true
                   group by su.id, su.email, su.product, su.plan, su.created_at,
                            ds.articles_this_week, ds.delivered_emails,
                            ds.last_delivered_at
                   order by su.created_at desc, su.id desc""",
                limit + 1,
                cursor_time,
                cursor_id,
                week_start,
            )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        next_cursor = _admin_encode_cursor(page[-1]["created_at"], page[-1]["id"])
    return {
        "items": [
            {
                "email": row["email"],
                "product": row["product"] or "",
                "plan": row["plan"],
                "created_at": _admin_iso(row["created_at"]),
                "accounts": list(row["accounts"] or []),
                "articles_this_week": int(row["articles_this_week"] or 0),
                "delivered_email_count": int(row["delivered_emails"] or 0),
                "last_delivered_at": _admin_iso(row["last_delivered_at"]),
            }
            for row in page
        ],
        "next_cursor": next_cursor,
    }


async def get_admin_deliveries(
    limit: int = 50, cursor: str | None = None
) -> dict:
    """Return authoritative successful deliveries using stable keyset pagination."""
    limit = max(1, min(int(limit), _ADMIN_MAX_PAGE_SIZE))
    cursor_time, cursor_id = _admin_decode_cursor(cursor)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            rows = await conn.fetch(
                """select d.id, u.email, d.delivered_at, d.item_count,
                          d.is_breaking, d.delivery_status
                   from digests d
                   join users u on u.id = d.user_id
                   where d.delivery_status = 'sent'
                     and d.delivered_at is not null
                     and ($2::timestamptz is null
                          or (d.delivered_at, d.id) < ($2::timestamptz, $3::uuid))
                   order by d.delivered_at desc, d.id desc
                   limit $1""",
                limit + 1,
                cursor_time,
                cursor_id,
            )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        next_cursor = _admin_encode_cursor(page[-1]["delivered_at"], page[-1]["id"])
    return {
        "items": [
            {
                "id": str(row["id"]),
                "subscriber_email": row["email"],
                "delivered_at": _admin_iso(row["delivered_at"]),
                "item_count": int(row["item_count"] or 0),
                "is_breaking": bool(row["is_breaking"]),
                "status": row["delivery_status"],
            }
            for row in page
        ],
        "next_cursor": next_cursor,
    }
