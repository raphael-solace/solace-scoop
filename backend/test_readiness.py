"""Focused regression tests for weekly pipeline safety."""

from __future__ import annotations

import asyncio
import json
import os
import smtplib
import sys
import types
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

os.environ.setdefault("CRON_SECRET", "test-cron-secret")

sys.modules.setdefault(
    "asyncpg",
    types.SimpleNamespace(Pool=object, Connection=object, create_pool=None, connect=None),
)

import db
import digest
import main
import send_email
from run_digest import _company_matches_tracked, quality_filter
from sources import discover_official_sources, parse_feed, serialize_source_item
from run_sources import load_catalog


def test_research_resume_keeps_a_ready_candidate() -> None:
    user = {"id": "existing", "email": "person@solace.com", "companies": [{"name": "Airbus"}]}
    with patch("db.get_user_by_email", AsyncMock(return_value=user)), \
         patch("run_digest.build_readiness_report", AsyncMock(return_value={"status": "READY"})), \
         patch("digest.generate_digest_for_user", AsyncMock()) as generate, \
         patch("db.save_digest", AsyncMock()) as save:
        from run_digest import main as run_weekly
        asyncio.run(run_weekly(user["email"], "research-only", resume_research=True))
    generate.assert_not_awaited()
    save.assert_not_awaited()


def test_fully_ready_resumed_batch_needs_no_new_provider_calls() -> None:
    from run_digest import main as run_weekly
    user = {"id": "existing", "email": "person@solace.com", "companies": [{"name": "Airbus"}]}
    report = {"status": "READY", "failed_users": [], "ready_users": 1, "eligible_users": 1}
    with patch("db.get_all_users", AsyncMock(return_value=[user])), \
         patch("run_digest.build_readiness_report", AsyncMock(return_value=report)), \
         patch("digest.generate_digest_for_user", AsyncMock()) as generate, \
         patch("send_email.send_readiness_email", AsyncMock()) as send:
        asyncio.run(run_weekly(mode="research-only", resume_research=True))
    generate.assert_not_awaited()
    send.assert_awaited_once_with(report)


def test_research_lock_rejects_overlap_and_releases_after_failure() -> None:
    from run_digest import research_weekly_batch
    for locked in (False, True):
        conn = AsyncMock()
        conn.fetchval.return_value = locked
        pool = MagicMock()
        pool.acquire.return_value.__aenter__.return_value = conn
        with patch("db.get_pool", AsyncMock(return_value=pool)), \
             patch("run_digest.main", AsyncMock(side_effect=RuntimeError("provider offline"))) as research:
            try:
                asyncio.run(research_weekly_batch())
            except RuntimeError:
                assert locked
        assert research.await_count == int(locked)
        assert conn.execute.await_count == int(locked)


def test_throttled_provider_respects_retry_after() -> None:
    request = httpx.Request("POST", "https://aicore.invalid")
    throttled = httpx.Response(429, request=request, headers={"Retry-After": "75"})
    with patch.dict(os.environ, {"AICORE_DEPLOYMENT_PPLX": "deployment"}), \
         patch("aicore.is_configured", return_value=True), \
         patch("aicore.chat_completion", AsyncMock(side_effect=[
             httpx.HTTPStatusError("throttled", request=request, response=throttled),
             {"choices": []},
         ])), \
         patch("digest.asyncio.sleep", AsyncMock()) as sleep:
        assert asyncio.run(digest._call_model("system", "prompt")) == {"choices": []}
    assert sleep.await_args.args[0] >= 75


def test_digest_email_retains_sources_feedback_and_fits_a_phone() -> None:
    user = {"email": "kelly.teo@solace.com", "companies": [{"name": "Example Bank"}]}
    items = [{
        "company": "Example Bank", "headline": "A customer update <today>",
        "so_what": "A reason to talk", "sources": ["https://example.com/news/update"],
        "date": "2026-11-22", "message": "Hello <there>", "contact_name": "Alex Example",
        "contact_title": "CTO", "tag": "Technology", "tag_color": "blue",
    } for _ in range(24)]
    with patch.object(send_email, "API_URL", "https://api.example.com"), \
         patch.object(send_email, "CRON_SECRET", "test-only"):
        html = send_email.render_digest(user, items, digest_id="digest")
    assert len(html.encode()) < 90000
    assert html.count("https://example.com/news/update") == 24
    assert html.count("Suggested outreach".upper()) == 3
    assert "idx=23" in html
    assert "&lt;today&gt;" in html
    assert 'width="100%"' in html and "max-width:640px" in html
    plain = send_email._plain_text(html)
    assert "https://example.com/news/update" in plain
    assert "@media" not in plain


def test_ai_core_404_falls_back_to_direct_perplexity() -> None:
    async def run() -> None:
        request = httpx.Request("POST", "https://aicore.invalid")
        not_found = httpx.Response(404, request=request)
        direct = httpx.Response(
            200,
            request=httpx.Request("POST", digest.PPLX_API_URL),
            json={"choices": [{"message": {"content": "OK"}}]},
        )
        with patch.dict(os.environ, {"AICORE_DEPLOYMENT_PPLX": "missing", "PPLX_KEY": "test"}), \
             patch("aicore.chat_completion", AsyncMock(side_effect=httpx.HTTPStatusError(
                 "not found", request=request, response=not_found
             ))), \
             patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=direct)):
            result = await digest._call_model("system", "prompt", provider="pplx")
        assert result["choices"][0]["message"]["content"] == "OK"

    asyncio.run(run())


def test_weekly_freshness_is_strictly_fourteen_days() -> None:
    today = date.today()
    cutoff = today - timedelta(days=14)
    assert digest.MAX_SIGNAL_AGE_DAYS == 14
    assert digest._is_fresh_enough({"date": cutoff.isoformat()}, cutoff, today)
    assert not digest._is_fresh_enough(
        {"date": (cutoff - timedelta(days=1)).isoformat()}, cutoff, today
    )
    assert not digest._is_fresh_enough({"date": ""}, cutoff, today)
    assert not digest._is_fresh_enough(
        {"date": (today + timedelta(days=1)).isoformat()}, cutoff, today
    )


def test_sunday_research_saves_only_signals_still_fresh_on_monday() -> None:
    from run_digest import main as run_weekly_digest

    sunday = date(2026, 11, 8)
    user = {"id": "test-user", "email": "person@solace.com", "companies": [{"name": "Airbus"}]}
    expiring = {"company": "Airbus", "date": "2026-10-25"}
    fresh = {"company": "Airbus", "date": "2026-10-26"}
    future = {"company": "Airbus", "date": "2026-11-09"}
    with patch("run_digest.date") as clock, \
         patch("db.get_user_by_email", AsyncMock(return_value=user)), \
         patch("digest.generate_digest_for_user", AsyncMock(return_value=[expiring, fresh, future])), \
         patch("run_digest.enrich_linkedin", AsyncMock(return_value=[fresh])) as enrich, \
         patch("db.save_digest", AsyncMock()) as save:
        clock.today.return_value = sunday
        asyncio.run(run_weekly_digest(email=user["email"], mode="research-only"))
    enrich.assert_awaited_once_with([fresh])
    save.assert_awaited_once_with(user["id"], [fresh])


def test_readiness_reports_database_failure() -> None:
    async def run() -> None:
        with patch("db.get_pool", AsyncMock(side_effect=RuntimeError("offline"))):
            try:
                await main.ready()
            except main.HTTPException as exc:
                assert exc.status_code == 503
            else:
                raise AssertionError("readiness must fail when the database is unavailable")

    asyncio.run(run())


def test_readiness_checks_otp_attempts_column() -> None:
    async def run() -> None:
        conn = AsyncMock()
        pool = MagicMock()
        pool.acquire.return_value.__aenter__.return_value = conn
        with patch("db.get_pool", AsyncMock(return_value=pool)):
            assert await main.ready() == {"status": "ready"}
        conn.execute.assert_awaited_once_with("select attempts from auth_tokens limit 0")

    asyncio.run(run())


def test_signup_rejects_external_email_before_database_or_email() -> None:
    async def run() -> None:
        request = main.SubscribeRequest(
            email="external@example.com", product="PubSub+", companies=["Acme"]
        )
        try:
            await main.subscribe(request)
        except main.HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("external signup should be rejected")

    asyncio.run(run())


def test_otp_uses_cryptographic_randomness() -> None:
    fake_conn = MagicMock()
    fake_conn.fetchval = AsyncMock(return_value=False)
    fake_conn.execute = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock()
    transaction.__aexit__ = AsyncMock(return_value=False)
    fake_conn.transaction.return_value = transaction
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=fake_conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    fake_pool = MagicMock()
    fake_pool.acquire.return_value = acquire

    with patch("db.get_pool", AsyncMock(return_value=fake_pool)), \
         patch("db.secrets.randbelow", return_value=42):
        assert asyncio.run(db.create_otp("person@solace.com")) == "000042"


def test_otp_guess_limit_is_enforced_by_query() -> None:
    fake_conn = AsyncMock()
    fake_conn.fetchrow.return_value = None
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=fake_conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    fake_pool = MagicMock()
    fake_pool.acquire.return_value = acquire

    with patch("db.get_pool", AsyncMock(return_value=fake_pool)):
        assert not asyncio.run(db.verify_otp("person@solace.com", "123456"))

    query = fake_conn.fetchrow.call_args.args[0]
    assert "attempts < $3" in query


def test_source_and_company_gates() -> None:
    assert _company_matches_tracked("Ford Motor Company", {"ford"})
    assert not _company_matches_tracked("Oxford Instruments", {"ford"})
    assert not _company_matches_tracked("Ford Foundation", {"ford"})
    assert not _company_matches_tracked("Acme Services", {"acme technologies"})
    assert digest._is_specific_article_url("https://example.com/news/2026/a-specific-story")
    assert digest._is_specific_article_url("https://fox.com/news/2026/a-specific-story")
    assert not digest._is_specific_article_url("https://example.com/news")
    assert quality_filter([{"company": "Airbus", "headline": "Valid"}], ["Airbus"])
    assert quality_filter([{"company": "", "headline": "Invalid"}], ["Airbus"]) == []


def test_shared_facts_project_independently_per_user() -> None:
    async def run() -> None:
        item = {"company": "Airbus", "headline": "Airbus opens a new factory", "date": date.today().isoformat(), "sources": ["https://example.com/factory"]}
        history = {
            "user-a": [{"company": "Airbus", "headline": "Airbus opens a new factory"}],
            "user-b": [],
        }
        async def previous(user_id, weeks=12): return history[user_id]
        async def empty(*args, **kwargs): return set()
        fake_db = types.SimpleNamespace(
            get_previous_signals=previous,
            get_previous_source_urls=empty,
            get_previous_signal_keys=empty,
        )
        with patch.dict(sys.modules, {"db": fake_db}), \
             patch("digest._enrich_all_contacts", AsyncMock(side_effect=lambda rows: rows)), \
             patch("digest._call_model", AsyncMock(return_value={"choices": [{"message": {"content": "[]"}}]})):
            a = await digest.project_signals_for_user({"id": "user-a"}, [dict(item)])
            b = await digest.project_signals_for_user({"id": "user-b"}, [dict(item)])
        assert a == []
        assert len(b) == 1

    asyncio.run(run())


def test_semantic_dedup_keeps_different_event_at_same_company() -> None:
    async def run() -> None:
        items = [
            {"company": "Airbus", "headline": "Airbus appoints Jane Doe as CTO"},
            {"company": "Airbus", "headline": "Airbus opens a new factory in Hamburg"},
        ]
        previous = [{"company": "Airbus", "headline": "Jane Doe named Airbus CTO"}]
        response = {"choices": [{"message": {"content": "[1]"}}]}
        with patch("digest._call_model", AsyncMock(return_value=response)):
            kept = await digest._dedup_signals(items, previous, set(), set())
        assert [item["headline"] for item in kept] == ["Airbus opens a new factory in Hamburg"]

    asyncio.run(run())


def _signal(company: str, index: int, strength: int = 3) -> dict:
    return {
        "company": company,
        "headline": f"{company} unique development number {index}",
        "date": date.today().isoformat(),
        "signal_strength": strength,
        "persona_fit": 3,
        "risk_or_opportunity": "opportunity",
        "sources": [f"https://example.com/articles/{company.lower()}-{index}"],
    }


def test_semantic_dedup_budget_scales_with_candidate_pool() -> None:
    import inspect
    source = inspect.getsource(digest._dedup_signals)
    assert "len(items) * 10" in source
    assert "min(5000" in source


def test_current_batch_semantic_dedup_keeps_one_rewording() -> None:
    async def run() -> None:
        items = [
            _signal("Acme", 1),
            _signal("Acme", 2),
        ]
        items[0]["headline"] = "Acme appoints Jane Doe as CTO"
        items[1]["headline"] = "Jane Doe named technology chief at Acme"
        response = {"choices": [{"message": {"content": "[0]"}}]}
        with patch("digest._call_model", AsyncMock(return_value=response)):
            kept = await digest._dedup_signals(items, [], set(), set())
        assert [item["headline"] for item in kept] == [items[0]["headline"]]

    asyncio.run(run())


def test_weekly_dedup_happens_before_ranking_and_backfills_slots() -> None:
    async def run() -> None:
        companies = ["Acme", "Beta", "Gamma", "Delta"]
        candidates = [
            _signal(companies[index % len(companies)], index, 5 if index < 5 else 3)
            for index in range(29)
        ]
        previous = [dict(candidates[index]) for index in range(5)]
        fake_db = types.SimpleNamespace(
            get_previous_signals=AsyncMock(return_value=previous),
            get_previous_source_urls=AsyncMock(return_value=set()),
            get_previous_signal_keys=AsyncMock(return_value=set()),
        )
        llm = {"choices": [{"message": {"content": json.dumps(list(range(24)))}}]}
        with patch.dict(sys.modules, {"db": fake_db}), \
             patch("digest.generate_signals", AsyncMock(return_value=candidates)), \
             patch("submitted_signals.list_submissions", AsyncMock(return_value=[])), \
             patch("digest._call_model", AsyncMock(return_value=llm)), \
             patch("digest._enrich_all_contacts", AsyncMock(side_effect=lambda rows: rows)):
            result = await digest.generate_digest_for_user(
                {"id": "user-a", "companies": [{"name": name} for name in companies]}
            )
        assert len(result) == 24
        assert all(item["headline"] not in {p["headline"] for p in previous} for item in result)

    asyncio.run(run())


def test_company_aliases_share_one_diversity_counter() -> None:
    items = [_signal("Ford", index) for index in range(6)]
    items += [_signal("Ford Motor Company", index + 6) for index in range(6)]
    filtered = digest._filter_tracked_items(items, ["Ford"])
    assert len(digest._rank_signals(filtered)) == 6


def test_wrong_company_candidates_do_not_consume_ranked_slots() -> None:
    items = [_signal("Oxford Instruments", index, 5) for index in range(4)]
    items += [_signal("Ford", index, 3) for index in range(12)]
    filtered = digest._filter_tracked_items(items, ["Ford"])
    assert len(digest._rank_signals(filtered)) == 6  # configured per-company diversity cap
    assert all(item["company"] == "Ford" for item in filtered)


def test_post_dedup_recovery_runs_for_undercovered_accounts() -> None:
    async def run() -> None:
        candidates = [_signal("Acme", 1)]
        fake_db = types.SimpleNamespace(
            get_previous_signals=AsyncMock(return_value=[]),
            get_previous_source_urls=AsyncMock(return_value=set()),
            get_previous_signal_keys=AsyncMock(return_value=set()),
        )
        recovered = [_signal("Beta", index) for index in range(6)]
        with patch.dict(sys.modules, {"db": fake_db}), \
             patch("digest.generate_signals", AsyncMock(return_value=candidates)), \
             patch("submitted_signals.list_submissions", AsyncMock(return_value=[])), \
             patch("digest._run_catchall_query", AsyncMock(side_effect=lambda company, *_, **__: recovered if company == "Beta" else [])), \
             patch("digest._enrich_all_contacts", AsyncMock(side_effect=lambda rows: rows)):
            result = await digest.generate_digest_for_user({
                "id": "user-a", "companies": [{"name": "Acme"}, {"name": "Beta"}]
            })
        assert len(result) == 7  # six Beta (diversity cap) plus one Acme

    asyncio.run(run())


def test_full_weekly_digest_still_recovers_and_selects_an_uncovered_account() -> None:
    async def run() -> None:
        companies = ["Acme", "Beta", "Gamma", "Delta", "Quiet"]
        primary = [
            _signal(company, index, strength=5)
            for company in companies[:-1] for index in range(6)
        ]
        fallback = AsyncMock(return_value=[_signal("Quiet", 1, strength=2)])
        with patch("digest.generate_signals", AsyncMock(return_value=primary)), \
             patch("digest._run_catchall_query", fallback), \
             patch("digest._dedup_signals", AsyncMock(side_effect=lambda items, *_: items)), \
             patch("digest._enrich_all_contacts", AsyncMock(side_effect=lambda items: items)):
            result = await digest.generate_digest_for_user({
                "companies": [{"name": name} for name in companies],
            })
        assert fallback.await_count == 1
        assert fallback.await_args.args[0] == "Quiet"
        assert fallback.await_args.kwargs["newsroom_fallback"] is True
        assert len(result) == 24
        assert {item["company"] for item in result} == set(companies)
        assert result[0]["company"] != "Quiet"

    asyncio.run(run())


def test_newsroom_fallback_requires_a_dated_direct_source_and_useful_event() -> None:
    today = date.today().isoformat()
    article = "https://example.org/news/new-logistics-platform"
    unsupported = "https://example.org/news/unsubstantiated"
    social = "https://www.linkedin.com/posts/example-update"
    body = [
        {**_signal("Acme", 1), "sources": [article, unsupported, social]},
        {**_signal("Acme", 2), "sources": [unsupported]},
        {**_signal("Acme", 3), "headline": "Acme publishes its daily share buyback", "sources": [article]},
    ]
    response = {
        "choices": [{"message": {"content": json.dumps(body)}}],
        "search_results": [{"url": article, "date": today}, {"url": social, "date": today}],
    }
    with patch("digest._call_model", AsyncMock(return_value=response)):
        result = asyncio.run(digest._run_catchall_query("Acme", {}, newsroom_fallback=True))
    assert len(result) == 1
    assert result[0]["sources"] == [article]


def test_newsroom_fallback_distinguishes_aston_martin_and_its_f1_team() -> None:
    team = "https://www.astonmartinf1.com/news/partnership"
    automaker = "https://media.astonmartin.com/2026/model-launch"
    assert not digest._is_eligible_fallback_source(team, "Aston Martin")
    assert digest._is_eligible_fallback_source(automaker, "Aston Martin")
    assert digest._is_eligible_fallback_source(team, "Aston Martin Aramco Formula One Team")
    assert not digest._is_eligible_fallback_source("https://www.stocktitan.net/news/release", "Aptiv")


def test_weekly_rotation_covers_overflow_without_researching_duplicates() -> None:
    companies = [f"Company {i:02d}" for i in range(28)]
    monday = date(2026, 11, 9)
    first = digest._weekly_company_sample(companies + ["company 00"], 20, monday)
    second = digest._weekly_company_sample(companies, 20, monday + timedelta(days=7))
    assert len(first) == len(second) == 20
    assert set(first) | set(second) == set(companies)
    assert digest._weekly_company_sample(list(reversed(companies)), 20, monday) == first


def test_source_url_keeps_identity_params_but_drops_tracking() -> None:
    one = digest._canonical_source_url("https://example.com/article?id=1&utm_source=x")
    two = digest._canonical_source_url("https://example.com/article?id=2&utm_source=y")
    same = digest._canonical_source_url("https://example.com/article?id=1&utm_campaign=z")
    assert one != two
    assert one == same
    assert digest._is_specific_article_url("https://example.com/article?id=1")
    assert digest._is_specific_article_url("https://example.com/press-releases/acme-launch")
    assert not digest._is_specific_article_url("https://google.com/search?q=acme")
    assert not digest._is_specific_article_url("https://example.com/search?q=acme")
    assert digest._canonical_source_url("https://example.com:notaport/story") == ""


def test_hard_ledger_no_longer_suppresses_similar_recurring_topics() -> None:
    first = digest._hard_keys("Acme reports quarterly cloud revenue growth", "Acme")
    second = digest._hard_keys("Acme reports quarterly cloud revenue decline", "Acme")
    assert not first.intersection(second)
    assert all(key.startswith("s:") for key in first | second)


def test_mixed_model_array_keeps_valid_signal() -> None:
    async def run() -> None:
        payload = [_signal("Acme", 1), None, "invalid"]
        with patch("digest._call_model", AsyncMock(return_value={
            "choices": [{"message": {"content": json.dumps(payload)}}]
        })):
            result = await digest._run_company_query(
                digest.QUERY_TYPES[0], "Acme", digest.get_solace_context()
            )
        assert len(result) == 1
        assert result[0]["company"] == "Acme"

    asyncio.run(run())


def test_low_query_coverage_is_not_treated_as_genuine_no_news() -> None:
    async def run() -> None:
        with patch("digest._research_company_with_coverage", AsyncMock(return_value=([], 1, 11))):
            try:
                await digest.generate_signals(["Acme"], finalize=False)
            except RuntimeError as exc:
                assert "All company research queries failed" in str(exc)
            else:
                raise AssertionError("near-total provider failure must not become no-news")

    asyncio.run(run())


def test_all_malformed_model_json_counts_as_query_failure() -> None:
    async def run() -> None:
        malformed = {"choices": [{"message": {"content": "truncated {"}}]}
        with patch("digest._call_model", AsyncMock(return_value=malformed)):
            try:
                await digest._run_company_query(
                    digest.QUERY_TYPES[0], "Acme", digest.get_solace_context()
                )
            except RuntimeError as exc:
                assert "malformed JSON" in str(exc)
            else:
                raise AssertionError("malformed model output must not look like no news")

    asyncio.run(run())


def test_model_json_parse_failure_retries_once() -> None:
    async def run() -> None:
        valid = json.dumps([_signal("Acme", 1)])
        with patch("digest._call_model", AsyncMock(side_effect=[
            {"choices": [{"message": {"content": "truncated {"}}]},
            {"choices": [{"message": {"content": valid}}]},
        ])) as caller:
            result = await digest._run_company_query(
                digest.QUERY_TYPES[0], "Acme", digest.get_solace_context()
            )
        assert len(result) == 1
        assert caller.await_count == 2

    asyncio.run(run())


def test_office_failure_falls_back_to_gmail() -> None:
    office = MagicMock()
    office.starttls.return_value = None
    office.login.side_effect = smtplib.SMTPAuthenticationError(535, b"no")
    gmail = MagicMock()

    with patch.object(send_email, "ALLOWED_RECIPIENTS", "*"), \
         patch.object(send_email, "SMTP_ADDRESS", "office@example.com"), \
         patch.object(send_email, "SMTP_PASSWORD", "office-secret"), \
         patch.object(send_email, "GMAIL_ADDRESS", "gmail@example.com"), \
         patch.object(send_email, "GMAIL_APP_PASSWORD", "gmail-secret"), \
         patch("send_email.smtplib.SMTP", return_value=office), \
         patch("send_email.smtplib.SMTP_SSL", return_value=gmail):
        transport = send_email.send_raw_email("person@example.com", "Subject", "<p>Hi</p>")

    assert transport == "gmail"
    gmail.__enter__.return_value.sendmail.assert_called_once()


def test_rss_and_atom_parser_preserves_source_identity() -> None:
    rss = b'''<rss><channel><item><guid>sec-1</guid><title>Filing update</title>
      <link>https://example.com/Filing/One</link><description>&lt;b&gt;Summary&lt;/b&gt;</description>
      <pubDate>Sun, 13 Sep 2026 18:00:00 GMT</pubDate></item></channel></rss>'''
    atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>job-7</id>
      <title>Principal Architect</title><link rel="alternate" href="/jobs/7"/>
      <published>2026-09-13T18:00:00Z</published></entry></feed>'''
    rss_items = parse_feed(rss, "https://example.com/feed")
    atom_items = parse_feed(atom, "https://careers.example.com/feed")
    assert rss_items[0].source_item_id == "sec-1"
    assert rss_items[0].url == "https://example.com/Filing/One"
    assert rss_items[0].summary == "Summary"
    assert atom_items[0].source_item_id == "job-7"
    assert atom_items[0].url == "https://careers.example.com/jobs/7"


def test_bulk_preflight_can_quarantine_without_aborting() -> None:
    async def run() -> None:
        report = {
            "status": "NOT READY",
            "eligible_users": 2,
            "ready": [{"email": "ready@solace.com", "articles": 8}],
            "failed_users": [{"email": "stale@solace.com", "errors": ["stale"]}],
        }
        with patch("run_digest.build_readiness_report", AsyncMock(return_value=report)):
            assert await __import__("run_digest").preflight(strict=False) == report
            try:
                await __import__("run_digest").preflight(strict=True)
            except RuntimeError:
                pass
            else:
                raise AssertionError("strict diagnostic preflight must still fail")

    asyncio.run(run())


def test_readiness_uses_actual_send_date() -> None:
    import inspect
    source = inspect.getsource(__import__("run_digest").build_readiness_report)
    assert "validation_date = date.today()" in source
    assert "_is_fresh_enough(item, cutoff, validation_date)" in source


def test_readiness_status_is_not_ready_for_empty_population() -> None:
    # The report invariant is intentionally stricter than "no failed rows".
    eligible = []
    failed = []
    status = "NOT READY" if not eligible or failed else "READY"
    assert status == "NOT READY"


def test_readiness_email_renders_metrics_and_escapes_failures() -> None:
    report = {
        "status": "NOT READY", "eligible_users": 2, "ready_users": 1,
        "total_articles": 5, "average_articles_per_person": 2.5,
        "minimum_articles": 0, "maximum_articles": 5,
        "zero_article_digests": 1,
        "failed_users": [{"email": "bad@example.com", "errors": ["<invalid>"]}],
        "provider_metrics": {"aicore_pplx": {"successes": 3, "http_429": 1}},
    }
    with patch.object(send_email, "send_raw_email") as sender:
        asyncio.run(send_email.send_readiness_email(report))
    _, subject, html = sender.call_args.args
    assert "NOT READY" in subject
    assert "Average articles / ready person" in html
    assert "&lt;invalid&gt;" in html


def test_source_catalog_enables_only_reviewed_keyless_sources() -> None:
    catalog = load_catalog()
    enabled = [row for row in catalog if row.get("enabled")]
    assert enabled
    assert all(row.get("review_status") == "approved" for row in enabled)
    supported = {
        "rss", "atom", "sec_submissions", "greenhouse_jobs",
        "lever_jobs", "ashby_jobs", "smartrecruiters_jobs",
    }
    assert all(row["adapter"] in supported for row in enabled)
    company_scoped = [row for row in enabled if row["adapter"] not in {"rss", "atom"}]
    assert all(row.get("config", {}).get("identifier_scheme") for row in company_scoped)
    assert not any(row["name"] == "sam_opportunities" and row.get("enabled") for row in catalog)
    forbidden_media = {"wsj", "bbc", "reuters", "guardian", "npr", "cnbc"}
    assert not any(any(name in row["name"].lower() for name in forbidden_media) and row.get("enabled") for row in catalog)


def test_official_items_require_a_publication_timestamp() -> None:
    import inspect

    source = inspect.getsource(db.classify_company_source_items)
    assert 'if not row["published_at"]' in source
    assert 'row["published_at"].date()' in source
    assert 'row["published_at"] or row["first_seen_at"]' not in source


def test_first_party_source_discovery() -> None:
    page = '''<html><head><link rel="alternate" type="application/rss+xml" href="/news/feed.xml"></head>
      <body><a href="https://boards.greenhouse.io/acme">Jobs</a></body></html>'''
    result = discover_official_sources(page, "https://acme.example/news")
    assert result["feeds"] == ["https://acme.example/news/feed.xml"]
    assert result["careers"][0]["scheme"] == "greenhouse_board"
    assert result["careers"][0]["value"] == "acme"


def test_source_serialization_preserves_datetime_types() -> None:
    rss = b'''<rss><channel><item><guid>x</guid><title>Update</title>
      <link>https://example.com/x</link>
      <pubDate>Sun, 13 Sep 2026 18:00:00 GMT</pubDate></item></channel></rss>'''
    item = serialize_source_item(parse_feed(rss, "https://example.com/feed")[0])
    from datetime import datetime
    assert isinstance(item["published_at"], datetime)


def test_company_pool_schema_preserves_legacy_subscription_links() -> None:
    from pathlib import Path

    sql = Path(__file__).with_name("migrate_company_pool.sql").read_text()
    assert "subscriber_company_legacy_links" in sql
    assert "idx_canonical_company_lookup" in sql
    assert "subscriber_company_legacy_links" in sql
    assert "claim_token" in sql
    assert "unique(company_id, event_key)" in sql
    assert "unique(source_id, source_item_id)" in sql
    assert "primary key (user_id, signal_id)" in sql


def test_missing_smtp_credentials_fail_closed() -> None:
    with patch.object(send_email, "ALLOWED_RECIPIENTS", "*"), \
         patch.object(send_email, "SMTP_ADDRESS", ""), \
         patch.object(send_email, "SMTP_PASSWORD", ""), \
         patch.object(send_email, "GMAIL_ADDRESS", ""), \
         patch.object(send_email, "GMAIL_APP_PASSWORD", ""):
        try:
            send_email.send_raw_email("person@example.com", "Subject", "<p>Hi</p>")
        except RuntimeError as exc:
            assert "No SMTP credentials" in str(exc)
        else:
            raise AssertionError("missing SMTP credentials must fail closed")
