"""Regression tests for verification and private LinkedIn tip submission."""

import asyncio
import json
import os
import sys
import types
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("CRON_SECRET", "test-cron-secret")
sys.modules.setdefault("asyncpg", types.SimpleNamespace(Pool=object, Connection=object))

import digest
import main
import submitted_signals as tips


POST_ID = "1234567890123456789"
LINK = f"https://www.linkedin.com/posts/test_activity-{POST_ID}-demo?utm_source=copy"
COMPANY = "John Lewis Partnership"
SOURCE = "https://www.johnlewispartnership.co.uk/media-centre/latest-news/2026/23967"
NOTE = "John Lewis Money appointed Charles Offord as Director of Financial Services."


@pytest.mark.parametrize("value", [
    "https://www.linkedin.com/company/john-lewis-partnership/",
    LINK.replace("www.linkedin.com", "linkedin.com.evil.example"),
    LINK.replace("https:", "http:"),
])
def test_link_requires_a_linkedin_post(value):
    with pytest.raises(ValueError):
        tips.linkedin_permalink(value)


def test_link_normalizes_tracking_and_reads_post_id():
    post_id, url = tips.linkedin_permalink(LINK)
    assert post_id == POST_ID
    assert url == LINK.split("?")[0]
    assert tips.linkedin_permalink(f"https://uk.linkedin.com/feed/update/urn%3Ali%3Aactivity%3A{POST_ID}/")[0] == POST_ID


@pytest.mark.parametrize("index_date,found", [
    (date.today().isoformat(), True),
    (None, False),
    ((date.today() - timedelta(days=20)).isoformat(), False),
])
def test_verification_requires_matching_recent_publication_date(index_date, found):
    reply = {
        "choices": [{"message": {"content": json.dumps({
            "source_url": SOURCE, "source_kind": "company", "published_on": date.today().isoformat(),
        })}}],
        "search_results": [{"url": SOURCE, "date": index_date, "title": NOTE}],
    }
    with patch("digest._call_model", AsyncMock(return_value=reply)) as lookup:
        signal = asyncio.run(tips.verify_submission(COMPANY, NOTE))
    assert bool(signal) == found
    if found:
        assert signal["source_url"] == SOURCE
    assert "linkedin.com" not in lookup.await_args.kwargs["prompt"]


def test_submission_saves_only_corroborated_tips_for_tracked_accounts():
    async def run():
        user = {"id": "user-a", "companies": [{"name": COMPANY}]}
        row = {"company": COMPANY, "headline": NOTE, "source_url": SOURCE, "source_published_on": date.today()}
        payload = {"email": "test@example.org", "token": "test", "company": COMPANY, "linkedin_url": LINK, "note": NOTE}
        with patch("main._require_session", AsyncMock(return_value=user)), \
             patch("submitted_signals.find_submission", AsyncMock(return_value=None)) as existing, \
             patch("submitted_signals.verify_submission", AsyncMock(return_value=None)) as verify, \
             patch("submitted_signals.save_submission", AsyncMock(return_value=(row, True))) as save:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test") as client:
                wrong = await client.post("/api/signals/submit", json={**payload, "company": "Another account"})
                assert wrong.status_code == 422
                verify.assert_not_awaited()
                no_evidence = await client.post("/api/signals/submit", json=payload)
                assert no_evidence.status_code == 422
                save.assert_not_awaited()
                verify.return_value = {"headline": NOTE, "source_url": SOURCE}
                saved = await client.post("/api/signals/submit", json=payload)
                assert saved.json()["status"] == "verified"
                save.assert_awaited_once()
                existing.return_value = row
                duplicate = await client.post("/api/signals/submit", json=payload)
                assert duplicate.json()["status"] == "existing"
                assert save.await_count == 1

    asyncio.run(run())


def test_verified_tip_enters_digest_without_private_note_or_link():
    async def run():
        row = {
            "company": COMPANY, "headline": NOTE, "source_url": SOURCE,
            "source_published_on": date.today(), "note": "private note", "linkedin_url": LINK,
        }
        fake_db = types.SimpleNamespace(
            get_previous_signals=AsyncMock(return_value=[]),
            get_previous_source_urls=AsyncMock(return_value=set()),
            get_previous_signal_keys=AsyncMock(return_value=set()),
        )
        user = {"id": "user-a", "companies": [{"name": COMPANY}]}
        with patch.dict(sys.modules, {"db": fake_db}), \
             patch("submitted_signals.list_submissions", AsyncMock(return_value=[row])), \
             patch("digest.generate_signals", AsyncMock(return_value=[])), \
             patch("digest._run_catchall_query", AsyncMock(return_value=[])), \
             patch("digest._enrich_all_contacts", AsyncMock(side_effect=lambda x: x)):
            result = await digest.generate_digest_for_user(user)
        assert len(result) == 1 and result[0]["sources"] == [SOURCE]
        assert "note" not in result[0] and "linkedin_url" not in result[0]

    asyncio.run(run())
