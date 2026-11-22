"""
CLI entrypoint for the digest pipeline.

Usage:
  python run_digest.py                     # Research + quality filter + LinkedIn + send (all users)
  python run_digest.py --email x@y.com     # Research + quality filter + LinkedIn + send (one user)
  python run_digest.py --research-only     # Research and save to DB only
  python run_digest.py --send-only         # Send last saved digest only

Auto-detect: if run on Sunday, defaults to --research-only.
             if run on Monday, defaults to --send-only.

Weekly pipeline (default "both" mode):
  1. Research signals via Perplexity/AI Core (8 queries per company)
  2. Quality filter: remove low-value signals, wrong companies, bad dates
  3. LinkedIn enrichment: find real profile URLs for contacts
  4. Save to DB
  5. Send emails with subject: "X Scoops this week: Company A, Company B"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()


# -- Quality filter ----------------------------------------------------------

def _signal_strength(item: dict) -> int:
    try:
        return int(item.get("signal_strength", 0))
    except (TypeError, ValueError):
        return 0


def _eligible_user(user: dict) -> bool:
    return bool(
        user
        and user.get("email", "").lower().endswith("@solace.com")
        and user.get("companies")
    )


def _company_matches_tracked(company: str, tracked: set[str]) -> bool:
    from digest import _company_matches_tracked as matches
    return matches(company, tracked)


def quality_filter(items: list[dict], user_companies: list[str]) -> list[dict]:
    """Remove wrong-company signals and clean bad contacts. Permissive — let ranking decide quality."""
    tracked = {c.lower() for c in user_companies}
    filtered = []

    for item in items:
        headline = item.get("headline", "")
        company = item.get("company", "")
        contact = item.get("contact_name", "")
        title = item.get("contact_title", "")

        if not _company_matches_tracked(company, tracked):
            print(f"    [quality] Dropped wrong or missing company: {company}")
            continue

        # Drop only the most obviously useless patterns
        LOW_VALUE = ["SEC Form", "recall affecting"]
        if any(p in headline for p in LOW_VALUE):
            print(f"    [quality] Dropped low-value: {headline[:60]}")
            continue

        # Clean bad contacts (single name, no title, press contacts)
        if contact:
            if " " not in contact:
                item["contact_name"] = ""
                item["contact_title"] = ""
            elif "Press" in title or "press" in title:
                item["contact_name"] = ""
                item["contact_title"] = ""

        filtered.append(item)

    return filtered


# -- LinkedIn enrichment -----------------------------------------------------

async def enrich_linkedin(items: list[dict]) -> list[dict]:
    """Find real LinkedIn profile URLs for contacts."""
    deployment_id = os.getenv("AICORE_DEPLOYMENT_PPLX", "")
    if not deployment_id:
        return items

    import aicore
    if not aicore.is_configured():
        return items

    # Collect unique contacts needing LinkedIn
    contacts = {}
    for item in items:
        name = item.get("contact_name", "")
        if name and not item.get("contact_linkedin"):
            key = f"{name}|{item.get('company', '')}"
            if key not in contacts:
                contacts[key] = {
                    "name": name,
                    "title": item.get("contact_title", ""),
                    "company": item.get("company", ""),
                }

    if not contacts:
        return items

    print(f"    [linkedin] Searching for {len(contacts)} contacts...")
    linkedin_map = {}

    for key, c in contacts.items():
        try:
            data = await aicore.chat_completion(
                deployment_id=deployment_id,
                model="sonar-pro",
                messages=[
                    {"role": "system", "content": "Return ONLY a valid LinkedIn profile URL. Nothing else. If you cannot find the exact profile, return empty string."},
                    {"role": "user", "content": f"Find the LinkedIn profile URL for {c['name']}, {c['title']} at {c['company']}. Return ONLY the URL like https://www.linkedin.com/in/username or empty string if not found."},
                ],
                max_tokens=100,
                temperature=0.0,
            )
            content = data["choices"][0]["message"]["content"].strip()
            if "linkedin.com/in/" in content:
                for word in content.split():
                    if "linkedin.com/in/" in word:
                        url = word.strip("\"'.,;")
                        if url.startswith("http") and _is_legit_linkedin(url):
                            linkedin_map[key] = url
                            break
        except Exception:
            pass
        await asyncio.sleep(1.5)

    # Apply found URLs
    found = 0
    for item in items:
        name = item.get("contact_name", "")
        if name and not item.get("contact_linkedin"):
            key = f"{name}|{item.get('company', '')}"
            if key in linkedin_map:
                item["contact_linkedin"] = linkedin_map[key]
                found += 1

    print(f"    [linkedin] Found {found} verified profiles")
    return items


def _is_legit_linkedin(url: str) -> bool:
    """Reject fabricated LinkedIn URLs."""
    if "/in/" not in url:
        return False
    slug = url.split("/in/")[-1].rstrip("/")

    # Reject hex suffixes (unverifiable old-format or fabricated)
    if re.search(r"-[0-9a-f]{6,}$", slug):
        return False

    # Reject company names in slug (LinkedIn doesn't do this)
    company_keywords = [
        "shell", "ikea", "thales", "renault", "airbus", "bmw", "siemens",
        "tesco", "nike", "unilever", "roche", "danone", "engie", "edf",
        "sncf", "bnp", "hsbc", "barclays", "vodafone", "open-fiber",
    ]
    slug_lower = slug.lower()
    for kw in company_keywords:
        if kw in slug_lower and slug_lower != kw:
            return False

    return True


# -- Main pipeline -----------------------------------------------------------

async def main(
    email: str | None = None,
    mode: str = "both",
    *,
    resume_research: bool = False,
) -> None:
    from db import (
        claim_digest_for_send,
        complete_digest_send,
        fail_digest_send,
        get_all_users,
        get_last_delivered_weekly_digest,
        get_pending_weekly_digest,
        get_user_by_email,
        save_digest,
        record_sent_signal_keys,
    )
    from digest import _hard_keys
    from digest import (MAX_SIGNAL_AGE_DAYS, _is_fresh_enough, generate_digest_for_user,
                        get_provider_metrics, reset_provider_metrics)
    from send_email import send_digest_email, send_readiness_email

    research_date = date.today()
    delivery_date = research_date
    if mode == "research-only" and research_date.weekday() == 6:
        # Sunday's research is delivered Monday morning.
        delivery_date += timedelta(days=1)
    cutoff = delivery_date - timedelta(days=MAX_SIGNAL_AGE_DAYS)

    if email:
        user = await get_user_by_email(email)
        users = [user] if user else []
    else:
        users = await get_all_users()

    # Only users with tracked companies participate in weekly research/delivery.
    users = [user for user in users if _eligible_user(user)]

    if not users:
        print("No eligible users to process.")
        if mode == "send-only":
            await preflight(email)  # raises NOT READY for an empty population
        if mode == "research-only" and email is None:
            report = await build_readiness_report(delivery_date=delivery_date)
            await send_readiness_email(report)
            raise RuntimeError("Sunday research found no eligible users")
        return

    # Validate every candidate but quarantine only invalid users. One stale item
    # must not cancel delivery for every other ready subscriber.
    preflight_report = None
    quarantined_emails: set[str] = set()
    if mode == "send-only":
        preflight_report = await preflight(email, strict=False)
        quarantined_emails = {
            row["email"].lower() for row in preflight_report.get("failed_users", [])
        }

    print(f"Processing {len(users)} user(s), mode={mode}...")
    if mode in ("both", "research-only"):
        reset_provider_metrics()
    failures = 0
    research_attempts = 0

    for user in users:
        try:
            if mode == "research-only" and resume_research:
                readiness = await build_readiness_report(
                    user["email"], delivery_date=delivery_date
                )
                if readiness["status"] != "NOT READY":
                    print(f"  [skip] {user['email']}: current weekly digest is ready")
                    continue
            if mode == "send-only" and user["email"].lower() in quarantined_emails:
                print(f"  [quarantine] {user['email']}: candidate failed readiness; not sending")
                failures += 1
                continue
            # GUARD: skip user if they already received a digest in "both" mode (prevents double-sends).
            # In "send-only" mode we skip this guard because the research was saved earlier (e.g. Sunday)
            # and sent_at defaults to the INSERT time — the gap is by design, not a duplicate.
            if mode == "both":
                existing = await get_last_delivered_weekly_digest(user["id"])
                if existing and existing.get("sent_at"):
                    from datetime import datetime, timezone
                    try:
                        raw_sent_at = existing["sent_at"]
                        sent_at = (
                            datetime.fromisoformat(raw_sent_at.replace("Z", "+00:00"))
                            if isinstance(raw_sent_at, str)
                            else raw_sent_at
                        )
                        hours_ago = (datetime.now(timezone.utc) - sent_at).total_seconds() / 3600
                        if hours_ago < 20:
                            print(f"  [skip] {user['email']} already got a digest {hours_ago:.0f}h ago")
                            continue
                    except (ValueError, TypeError):
                        pass

            if mode in ("both", "research-only"):
                research_attempts += 1
                print(f"\n  Researching {user['email']}...")
                items = [
                    item for item in await generate_digest_for_user(user, delivery_date=delivery_date)
                    if _is_fresh_enough(item, cutoff, research_date)
                ]
                if not items:
                    digest_id = await save_digest(user["id"], [])
                    print(f"  Saved no-fresh-news digest for {user['email']}")
                    continue

                # Quality filter
                user_companies = [c["name"] for c in user.get("companies", [])]
                items = quality_filter(items, user_companies)
                if not items:
                    digest_id = await save_digest(user["id"], [])
                    print(f"  Saved no-fresh-news digest for {user['email']} after quality filtering")
                    continue

                # LinkedIn enrichment
                items = await enrich_linkedin(items)

                # Save as pending. Fingerprints are recorded only after SMTP
                # accepts the message, so failed/blocked sends do not suppress news.
                digest_id = await save_digest(user["id"], items)
                print(f"  Saved {len(items)} pending signals to DB")

            if mode in ("both", "send-only"):
                if mode == "send-only":
                    digest = await get_pending_weekly_digest(user["id"])
                    if not digest:
                        print(f"  No fresh pending weekly digest for {user['email']}; refusing stale/repeat send")
                        failures += 1
                        continue
                    items = digest.get("items", [])
                    digest_id = digest.get("id", "")
                else:
                    digest_id = digest_id if "digest_id" in dir() else ""

                if not await claim_digest_for_send(digest_id):
                    print(f"  Digest {digest_id} is not pending; refusing duplicate send")
                    failures += 1
                    continue

                try:
                    await send_digest_email(user, items, digest_id=digest_id)
                    await complete_digest_send(digest_id)
                    ledger = []
                    for item in items:
                        headline, company = item.get("headline", ""), item.get("company", "")
                        for fp in _hard_keys(headline, company):
                            ledger.append({"fp": fp, "headline": headline,
                                           "company": company, "digest_id": digest_id})
                    try:
                        await record_sent_signal_keys(user["id"], ledger)
                    except Exception as ledger_exc:
                        print(f"  [ledger] Delivery succeeded but ledger update failed: {ledger_exc}", file=sys.stderr)
                    print(f"  Sent digest to {user['email']} ({len(items)} items)")
                except Exception as exc:
                    await fail_digest_send(digest_id, str(exc))
                    raise

        except Exception as e:
            failures += 1
            print(f"  Error processing {user['email']}: {e}", file=sys.stderr)

    print(f"\nDone. failures={failures}")
    if mode == "research-only" and email is None:
        provider_metrics = get_provider_metrics()
        provider_successes = sum(
            values.get("successes", 0) for values in provider_metrics.values()
        )
        if research_attempts and provider_successes == 0:
            failures += 1
            print("No successful AI provider calls were recorded", file=sys.stderr)
        report = await build_readiness_report(delivery_date=delivery_date)
        if failures and report["status"] != "NOT READY":
            report["status"] = "NOT READY"
            report["pipeline_failures"] = failures
        try:
            await send_readiness_email(report)
            print(f"Sunday readiness email sent: {report['status']}")
        except Exception as report_error:
            failures += 1
            print(
                f"Sunday readiness email failed: {type(report_error).__name__}",
                file=sys.stderr,
            )
    if failures:
        raise RuntimeError(f"Digest pipeline failed for {failures} user(s)")


async def research_weekly_batch(email: str | None = None) -> None:
    """Resume missing candidates, serializing scheduled research and recovery."""
    from db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        locked = await conn.fetchval(
            "select pg_try_advisory_lock(hashtext('scoop-weekly-research'))"
        )
        if not locked:
            print("Weekly research is already running; this invocation will exit")
            return
        try:
            await main(email, "research-only", resume_research=True)
        finally:
            await conn.execute(
                "select pg_advisory_unlock(hashtext('scoop-weekly-research'))"
            )


async def build_readiness_report(
    email: str | None = None, *, delivery_date: date | None = None
) -> dict:
    """Validate the current weekly batch and return structured, read-only metrics."""
    from config import cfg
    from db import get_all_users, get_pending_weekly_digests, get_user_by_email
    from digest import (MAX_SIGNAL_AGE_DAYS, _is_fresh_enough, _is_specific_article_url,
                        get_provider_metrics)
    from send_email import render_digest

    users = [await get_user_by_email(email)] if email else await get_all_users()
    internal = [u for u in users if u and u.get("email", "").lower().endswith("@solace.com")]
    eligible = [u for u in internal if u.get("companies")]
    excluded_no_company = [u["email"] for u in internal if not u.get("companies")]
    ready = []
    failed = []
    article_counts = []
    validation_date = date.today()
    cutoff = (delivery_date or validation_date) - timedelta(days=MAX_SIGNAL_AGE_DAYS)

    for user in eligible:
        candidates = await get_pending_weekly_digests(user["id"])
        errors = []
        item_count = 0
        if len(candidates) != 1:
            errors.append(f"expected 1 pending digest, found {len(candidates)}")
        else:
            digest = candidates[0]
            items = digest.get("items", [])
            item_count = len(items)
            tracked = {c["name"].lower() for c in user.get("companies", [])}
            if not 0 <= item_count <= cfg["digest"].get("max_signals_per_email", 24):
                errors.append(f"invalid item count {item_count}")
            for idx, item in enumerate(items):
                company = item.get("company", "").lower()
                sources = item.get("sources") or []
                if not _company_matches_tracked(company, tracked):
                    errors.append(f"item {idx}: untracked company")
                if not _is_fresh_enough(item, cutoff, validation_date):
                    errors.append(f"item {idx}: stale, future, or invalid date")
                if not sources or not all(_is_specific_article_url(src) for src in sources):
                    errors.append(f"item {idx}: missing or non-specific source")
            try:
                html = render_digest(user, items, digest_id=digest["id"])
                if not html or "</html>" not in html:
                    errors.append("HTML rendering failed")
            except Exception as exc:
                errors.append(f"HTML rendering failed: {type(exc).__name__}")

        if errors:
            failed.append({"email": user["email"], "errors": errors})
        else:
            ready.append({"email": user["email"], "articles": item_count})
            article_counts.append(item_count)

    provider_metrics = get_provider_metrics()
    provider_issues = sum(
        value for values in provider_metrics.values() for key, value in values.items()
        if key != "successes"
    )
    status = (
        "NOT READY" if not eligible or failed
        else ("DEGRADED" if provider_issues else "READY")
    )
    total_articles = sum(article_counts)
    return {
        "status": status,
        "eligible_users": len(eligible),
        "ready_users": len(ready),
        "failed_users": failed,
        "excluded_no_company": excluded_no_company,
        "total_articles": total_articles,
        "average_articles_per_person": round(total_articles / len(ready), 2) if ready else 0,
        "minimum_articles": min(article_counts) if article_counts else 0,
        "maximum_articles": max(article_counts) if article_counts else 0,
        "zero_article_digests": sum(1 for n in article_counts if n == 0),
        "ready": ready,
        "provider_metrics": provider_metrics,
    }


async def preflight(email: str | None = None, *, strict: bool = True) -> dict:
    """Print readiness; strict mode is for diagnostics, not bulk Monday delivery."""
    report = await build_readiness_report(email)
    failed_by_email = {row["email"]: row["errors"] for row in report["failed_users"]}
    for row in report["ready"]:
        print(f"  [OK] {row['email']}: {row['articles']} items")
    for address, errors in failed_by_email.items():
        print(f"  [FAIL] {address}: {'; '.join(errors)}")
    print(f"Preflight complete: users={report['eligible_users']} failures={len(report['failed_users'])}")
    if strict and report["status"] == "NOT READY":
        raise RuntimeError(f"Preflight failed for {len(report['failed_users'])} user(s)")
    return report


# -- Daily scan for breaking scoops ------------------------------------------

async def daily_scan() -> None:
    """Fast daily scan for critical signals (strength >= 5).

    Runs Tue-Fri. If a truly urgent signal is found, sends a breaking
    scoop email immediately. Max 1 per user per week.
    """
    from db import (
        claim_digest_for_send,
        complete_digest_send,
        fail_digest_send,
        get_all_users,
        get_breaking_scoops_this_week,
        get_previous_headlines,
        get_previous_source_urls,
        record_sent_signal_keys,
        save_breaking_digest,
    )
    from digest import _hard_keys, generate_signals
    from send_email import send_breaking_scoop_email

    from config import cfg
    bs_cfg = cfg.get("breaking_scoop", {})
    if not bs_cfg.get("enabled", False):
        print("Breaking scoop disabled in config.")
        return

    min_strength = bs_cfg.get("min_strength", 5)
    max_per_week = bs_cfg.get("max_per_user_per_week", 1)
    companies_to_scan = bs_cfg.get("companies_to_scan", 5)

    users = await get_all_users()
    users = [u for u in users if u.get("email", "").endswith("@solace.com")]

    print(f"Daily scan for {len(users)} users (strength >= {min_strength})...")
    failures = 0

    for user in users:
        try:
            # Check if user already got a breaking scoop this week
            sent_count = await get_breaking_scoops_this_week(user["id"])
            if sent_count >= max_per_week:
                continue

            companies = [c["name"] for c in user.get("companies", [])][:companies_to_scan]
            if not companies:
                continue

            # Research (fast — only top companies)
            items = await generate_signals(companies)
            if not items:
                continue

            # Breaking alerts must be both strong and genuinely recent.
            recency_hours = bs_cfg.get("recency_hours", 24)
            cutoff_date = date.today() - timedelta(days=max(1, (recency_hours + 23) // 24))
            critical = [
                item for item in items
                if _signal_strength(item) >= min_strength
                and item.get("date")
                and date.fromisoformat(item["date"]) >= cutoff_date
            ]
            if not critical:
                continue

            # Dedup against recent headlines AND source URLs
            previous = await get_previous_headlines(user["id"], weeks=4)
            prev_sources = await get_previous_source_urls(user["id"], weeks=4)
            if previous:
                prev_set = set(h.lower()[:50] for h in previous)
                critical = [i for i in critical if i.get("headline", "").lower()[:50] not in prev_set]
            if prev_sources:
                critical = [
                    i for i in critical
                    if not any(
                        (src.split("?")[0].split("#")[0].rstrip("/").lower()) in prev_sources
                        for src in i.get("sources", [])
                        if isinstance(src, str) and src.startswith("http")
                    )
                ]

            if not critical:
                continue

            # Take the single best signal
            best = critical[0]
            print(f"\n  BREAKING for {user['email']}: {best.get('headline', '')[:60]}")

            # Save, atomically claim, send, then record as delivered.
            digest_id = await save_breaking_digest(user["id"], [best], max_per_week)
            if not digest_id:
                continue  # another task used the remaining quota
            if not await claim_digest_for_send(digest_id):
                raise RuntimeError(f"Could not claim breaking digest {digest_id}")
            try:
                await send_breaking_scoop_email(user, best, digest_id=digest_id)
                await complete_digest_send(digest_id)
                ledger = [
                    {"fp": fp, "headline": best.get("headline", ""),
                     "company": best.get("company", ""), "digest_id": digest_id}
                    for fp in _hard_keys(best.get("headline", ""), best.get("company", ""))
                ]
                try:
                    await record_sent_signal_keys(user["id"], ledger)
                except Exception as ledger_exc:
                    print(f"  [ledger] Breaking delivery succeeded but ledger update failed: {ledger_exc}", file=sys.stderr)
                print(f"  Sent breaking scoop to {user['email']}")
            except Exception as exc:
                await fail_digest_send(digest_id, str(exc))
                raise

        except Exception as e:
            failures += 1
            print(f"  Error (daily scan) {user.get('email', '?')}: {e}", file=sys.stderr)

    print(f"\nDaily scan done. failures={failures}")
    if failures:
        raise RuntimeError(f"Daily scan failed for {failures} user(s)")


# -- Brand monitoring ----------------------------------------------------------

async def brand_monitor() -> None:
    """Search for external mentions of Solace across the public web.

    Sends a 'Solace in the Wild' digest to the configured recipients list.
    """
    from digest import search_brand_mentions
    from send_email import send_brand_monitor_email
    from config import cfg

    bm_cfg = cfg.get("brand_monitor", {})
    if not bm_cfg.get("enabled", False):
        print("Brand monitor disabled.")
        return

    recipients = bm_cfg.get("recipients", [])
    if not recipients:
        print("No recipients configured for brand monitor.")
        return

    print("Brand monitor: searching for Solace mentions...")
    mentions = await search_brand_mentions()

    if not mentions:
        print("  No mentions found today.")
        return

    print(f"  Found {len(mentions)} mention(s). Sending to {recipients}")
    await send_brand_monitor_email(recipients, mentions)
    print("  Brand monitor email sent.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Scoop digest pipeline")
    parser.add_argument("--email", help="Process a specific user")
    parser.add_argument("--research-only", action="store_true", help="Research and save to DB only")
    parser.add_argument("--send-only", action="store_true", help="Send the fresh pending weekly digest only")
    parser.add_argument("--preflight", action="store_true", help="Validate the pending weekly batch without sending")
    parser.add_argument("--readiness-email", action="store_true", help="Validate and email the Sunday readiness report")
    parser.add_argument("--daily-scan", action="store_true", help="Daily scan for breaking scoops")
    parser.add_argument("--brand-monitor", action="store_true", help="Search for Solace mentions")
    args = parser.parse_args()

    if args.brand_monitor:
        asyncio.run(brand_monitor())
    elif args.daily_scan:
        asyncio.run(daily_scan())
    elif args.preflight:
        asyncio.run(preflight(args.email))
    elif args.readiness_email:
        from send_email import send_readiness_email
        async def send_report() -> None:
            report = await build_readiness_report(args.email)
            await send_readiness_email(report)
            print(f"Readiness email sent: {report['status']}")
        asyncio.run(send_report())
    elif args.research_only:
        asyncio.run(research_weekly_batch(args.email))
    elif args.send_only:
        mode = "send-only"
        asyncio.run(main(args.email, mode))
    else:
        today = date.today()
        if today.weekday() == 6:
            mode = "research-only"
            print("Auto-detected Sunday: research-only mode")
        elif today.weekday() == 0:
            mode = "send-only"
            print("Auto-detected Monday: send-only mode")
        else:
            mode = "both"
        if mode == "research-only":
            asyncio.run(research_weekly_batch(args.email))
        else:
            asyncio.run(main(args.email, mode))
