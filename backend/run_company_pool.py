"""Shared company research pool and shadow projection runner.

Usage:
  python run_company_pool.py --backfill
  python run_company_pool.py --research
  python run_company_pool.py --shadow-report
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from config import cfg

DEFAULT_PROMPT_VERSION = "company-neutral-v1"


async def backfill() -> None:
    from db import backfill_canonical_companies

    result = await backfill_canonical_companies()
    print(
        f"Canonical backfill: created={result['canonical_created']} "
        f"linked={result['subscriptions_linked']}"
    )


async def research() -> None:
    if not cfg.get("company_pool", {}).get("enabled", False):
        print("Company pool disabled in config; no research performed.")
        return
    from db import (
        backfill_canonical_companies,
        claim_research_job,
        complete_research_job,
        fail_research_job,
        get_distinct_subscribed_companies,
    )
    from digest import research_company_once, reset_provider_metrics

    await backfill_canonical_companies()
    companies = await get_distinct_subscribed_companies()
    today = date.today()
    window_start = today - timedelta(days=14)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    prompt_version = cfg.get("company_pool", {}).get(
        "prompt_version", DEFAULT_PROMPT_VERSION
    )
    reset_provider_metrics()
    semaphore = asyncio.Semaphore(3)

    async def process(company: dict) -> str:
        async with semaphore:
            claim = await claim_research_job(
                str(company["id"]), window_start, today,
                prompt_version, worker_id,
            )
            if not claim:
                return "skipped"
            job_id, claim_token = claim["id"], claim["token"]
            try:
                print(
                    f"[{company['canonical_name']}] ({company['status']}) researching once for "
                    f"{company['subscriber_count']} subscriber(s)"
                )
                items, coverage = await research_company_once(
                    company["canonical_name"], include_coverage=True
                )
                is_partial = coverage["failed"] > 0
                await complete_research_job(
                    job_id, claim_token, items, partial=is_partial
                )
                return "partial" if is_partial else "completed"
            except Exception as exc:
                await fail_research_job(job_id, claim_token, str(exc))
                print(f"[{company['canonical_name']}] failed: {type(exc).__name__}")
                return "failed"

    results = await asyncio.gather(*(process(company) for company in companies))
    completed = results.count("completed")
    partial = results.count("partial")
    skipped = results.count("skipped")
    failed = results.count("failed")
    print(
        f"Pool research complete: companies={len(companies)} "
        f"completed={completed} partial={partial} "
        f"skipped={skipped} failed={failed}"
    )
    if failed:
        raise RuntimeError(f"Shared research failed for {failed} company job(s)")


async def shadow_report() -> None:
    from config import cfg
    from db import (
        backfill_canonical_companies,
        get_all_users,
        get_shared_signals_for_user,
    )
    from digest import project_signals_for_user

    await backfill_canonical_companies()
    users = [
        user for user in await get_all_users()
        if user.get("email", "").lower().endswith("@solace.com")
        and user.get("companies")
    ]
    cutoff = date.today() - timedelta(days=cfg["digest"].get("news_recency_days", 14))
    total = zero = 0
    for user in users:
        shared = await get_shared_signals_for_user(user["id"], cutoff)
        projected = await project_signals_for_user(user, shared)
        count = len(projected)
        total += count
        zero += count == 0
        print(f"{user['email']}: shared={len(shared)} projected={count}")
    average = round(total / len(users), 2) if users else 0
    print(
        f"Shadow projection: users={len(users)} total={total} "
        f"average={average} zero={zero}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run central company signal pool")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--research", action="store_true")
    parser.add_argument("--shadow-report", action="store_true")
    args = parser.parse_args()
    if args.backfill:
        asyncio.run(backfill())
    elif args.research:
        asyncio.run(research())
    elif args.shadow_report:
        asyncio.run(shadow_report())
    else:
        parser.error("choose --backfill, --research, or --shadow-report")
