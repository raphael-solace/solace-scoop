"""Hermetic security and data-contract tests for the admin API."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest

os.environ.setdefault("CRON_SECRET", "test-cron-secret")

sys.modules.setdefault(
    "asyncpg",
    types.SimpleNamespace(Pool=object, Connection=object, create_pool=None, connect=None),
)

import admin_auth
import db
import main


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


PASSWORD = "correct horse battery staple"
SALT = b"0123456789abcdef"
DIGEST = hashlib.scrypt(PASSWORD.encode(), salt=SALT, n=16384, r=8, p=1, dklen=32)
ADMIN_ENV = {
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD_HASH": (
        f"scrypt$v=1$n=16384$r=8$p=1${_b64(SALT)}${_b64(DIGEST)}"
    ),
    "ADMIN_SESSION_SECRET": "s" * 32,
    "CRON_SECRET": "test-cron-secret",
}


@pytest.fixture(autouse=True)
def _admin_config():
    with patch.dict(os.environ, ADMIN_ENV, clear=False):
        admin_auth.login_rate_limiter.clear()
        yield
        admin_auth.login_rate_limiter.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="https://api.example.test",
    )


async def _login(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/admin/login", json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["expires_in"] == 1800
    assert response.json()["token_type"] == "Bearer"
    return response.json()["access_token"]


def test_test_module_configures_cron_secret_before_main_import() -> None:
    assert os.environ.get("CRON_SECRET")


def test_credentials_and_short_lived_token() -> None:
    assert admin_auth.verify_admin_credentials("admin", PASSWORD)
    assert not admin_auth.verify_admin_credentials("wrong", PASSWORD)
    assert not admin_auth.verify_admin_credentials("admin", "wrong")
    with patch("admin_auth.hashlib.scrypt", wraps=hashlib.scrypt) as derive:
        admin_auth.verify_admin_credentials("wrong", PASSWORD)
        admin_auth.verify_admin_credentials("admin", "wrong")
    assert derive.call_count == 2

    token = admin_auth.issue_admin_token("admin", now=1_000)
    claims = admin_auth.validate_admin_token(token, now=1_001)
    assert claims["sub"] == "admin"
    assert claims["exp"] - claims["iat"] == 1800
    with pytest.raises(admin_auth.InvalidAdminToken):
        admin_auth.validate_admin_token(token + "x", now=1_001)
    with pytest.raises(admin_auth.InvalidAdminToken):
        admin_auth.validate_admin_token(token, now=2_800)


def test_missing_and_malformed_configuration_fail_closed() -> None:
    with patch.dict(os.environ, {"ADMIN_PASSWORD_HASH": "raw-password"}, clear=False):
        with pytest.raises(admin_auth.AdminConfigurationError):
            admin_auth.verify_admin_credentials("admin", PASSWORD)
    with patch.dict(os.environ, {"ADMIN_SESSION_SECRET": "short"}, clear=False):
        with pytest.raises(admin_auth.AdminConfigurationError):
            admin_auth.issue_admin_token("admin")


def test_unauthorized_admin_requests_never_touch_database_and_do_not_cache() -> None:
    async def run() -> None:
        calls = AsyncMock()
        with patch("db.get_admin_summary", calls):
            async with _client() as client:
                for auth in (None, "Basic abc", "Bearer malformed"):
                    headers = {"Authorization": auth} if auth else {}
                    response = await client.get("/api/admin/summary", headers=headers)
                    assert response.status_code == 401
                    assert response.headers["cache-control"] == "no-store, private"
                    assert response.headers["pragma"] == "no-cache"
        calls.assert_not_awaited()

    asyncio.run(run())


def test_login_rate_limit_retry_after_and_success_reset() -> None:
    async def run() -> None:
        async with _client() as client:
            for _ in range(4):
                response = await client.post(
                    "/api/admin/login",
                    json={"username": "admin", "password": "wrong"},
                )
                assert response.status_code == 401
            fifth = await client.post(
                "/api/admin/login", json={"username": "admin", "password": "wrong"}
            )
            assert fifth.status_code == 401
            assert 1 <= int(fifth.headers["retry-after"]) <= 900
            locked = await client.post(
                "/api/admin/login", json={"username": "admin", "password": PASSWORD}
            )
            assert locked.status_code == 429

            admin_auth.login_rate_limiter.clear()
            assert (await _login(client))
            assert admin_auth.login_rate_limiter.entry_count == 0

    asyncio.run(run())


def test_limiter_registry_is_bounded_and_username_cannot_bypass_lockout() -> None:
    limiter = admin_auth.LoginRateLimiter(max_entries=3)
    for index in range(5):
        limiter.record_failure(f"user-{index}", "127.0.0.1")
    assert limiter.retry_after("admin", "127.0.0.1") > 0

    for index in range(10):
        limiter.record_failure("admin", f"192.0.2.{index}")
    assert limiter.entry_count == 3


def test_login_runs_password_hash_off_event_loop() -> None:
    async def run() -> None:
        async with _client() as client:
            with patch("main.asyncio.to_thread", AsyncMock(return_value=True)) as worker:
                response = await client.post(
                    "/api/admin/login", json={"username": "admin", "password": PASSWORD}
                )
        assert response.status_code == 200
        worker.assert_awaited_once()

    asyncio.run(run())


def test_safe_endpoint_contracts_and_cors() -> None:
    async def run() -> None:
        summary = {
            "generated_at": "2026-09-22T00:00:00+00:00",
            "period": {"timezone": "UTC", "week_start": "2026-09-21T00:00:00+00:00", "weeks": 12},
            "totals": {"subscribers": 1, "tracked_accounts": 2, "delivered_emails": 3, "articles_this_week": 4, "emails_this_week": 1},
            "weekly_articles": [],
        }
        subscribers = {"items": [], "next_cursor": None}
        deliveries = {"items": [], "next_cursor": None}
        with patch("db.get_admin_summary", AsyncMock(return_value=summary)), \
             patch("db.get_admin_subscribers", AsyncMock(return_value=subscribers)), \
             patch("db.get_admin_deliveries", AsyncMock(return_value=deliveries)):
            async with _client() as client:
                token = await _login(client)
                headers = {"Authorization": f"Bearer {token}"}
                for path, expected in (
                    ("/api/admin/summary", summary),
                    ("/api/admin/subscribers", subscribers),
                    ("/api/admin/deliveries", deliveries),
                ):
                    response = await client.get(path, headers=headers)
                    assert response.status_code == 200
                    assert response.json() == expected
                    assert response.headers["cache-control"] == "no-store, private"
                preflight = await client.options(
                    "/api/admin/summary",
                    headers={
                        "Origin": "http://localhost:8080",
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "Authorization",
                    },
                )
                assert preflight.status_code == 200
                assert "Authorization" in preflight.headers["access-control-allow-headers"]

    asyncio.run(run())


def _fake_pool(fetchrow_result=None, fetch_results=None):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    conn.fetch = AsyncMock(side_effect=fetch_results or [])
    tx = MagicMock()
    tx.__aenter__ = AsyncMock()
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction.return_value = tx
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = acquire
    return pool, conn, tx


def test_summary_uses_utc_monday_and_sent_delivery_truth() -> None:
    now = datetime(2026, 9, 23, 14, tzinfo=timezone.utc)
    monday = datetime(2026, 9, 21, tzinfo=timezone.utc)
    totals = {
        "subscribers": 2,
        "tracked_accounts": 3,
        "unique_tracked_accounts": 2,
        "delivered_emails": 4,
        "articles_this_week": 5,
        "delivered_emails_this_week": 1,
    }
    pool, conn, tx = _fake_pool(fetchrow_result=totals, fetch_results=[[]])
    with patch("db.get_pool", AsyncMock(return_value=pool)):
        result = asyncio.run(db.get_admin_summary(12, now=now))
    assert result["period"]["week_start"] == monday.isoformat()
    assert conn.fetchrow.call_args.args[1] == monday
    totals_sql = conn.fetchrow.call_args.args[0].lower()
    weekly_sql = conn.fetch.call_args.args[0].lower()
    assert "delivery_status = 'sent'" in totals_sql
    assert "delivered_at" in totals_sql and "item_count" in totals_sql
    assert "delivery_status = 'sent'" in weekly_sql
    tx.__aenter__.assert_awaited_once()
    assert conn.transaction.call_args.kwargs == {"readonly": True}


def test_subscriber_pagination_uses_companies_and_safe_fields() -> None:
    created = datetime(2026, 9, 22, tzinfo=timezone.utc)
    row_id = UUID("00000000-0000-0000-0000-000000000001")
    rows = [
        {
            "id": row_id,
            "email": "person@example.com",
            "product": "Events",
            "plan": "free",
            "created_at": created,
            "accounts": ["Acme"],
            "articles_this_week": 2,
            "delivered_emails": 3,
            "last_delivered_at": created,
        },
        {"id": UUID(int=2), "created_at": created - timedelta(seconds=1)},
    ]
    pool, conn, _ = _fake_pool(fetch_results=[rows, []])
    with patch("db.get_pool", AsyncMock(return_value=pool)):
        result = asyncio.run(db.get_admin_subscribers(1))
    assert set(result["items"][0]) == {
        "email", "product", "plan", "created_at", "accounts",
        "articles_this_week", "delivered_email_count", "last_delivered_at",
    }
    assert result["items"][0]["accounts"] == ["Acme"]
    assert result["next_cursor"]
    sql = conn.fetch.call_args.args[0].lower()
    assert "left join companies" in sql
    assert "select *" not in sql and "items" not in sql
    assert "delivery_status = 'sent'" in sql
    assert conn.fetch.call_args.args[1] == 2

    with patch("db.get_pool", AsyncMock(return_value=pool)):
        asyncio.run(db.get_admin_subscribers(1, result["next_cursor"]))
    assert conn.fetch.call_args.args[2] == created
    assert conn.fetch.call_args.args[3] == row_id
    with pytest.raises(ValueError):
        asyncio.run(db.get_admin_subscribers(50, "not-a-cursor"))


def test_delivery_contract_pagination_and_query_allowlist() -> None:
    delivered = datetime(2026, 9, 22, tzinfo=timezone.utc)
    row_id = UUID("00000000-0000-0000-0000-000000000003")
    rows = [{
        "id": row_id, "email": "person@example.com", "delivered_at": delivered,
        "item_count": 4, "is_breaking": False, "delivery_status": "sent",
    }]
    pool, conn, _ = _fake_pool(fetch_results=[rows])
    with patch("db.get_pool", AsyncMock(return_value=pool)):
        result = asyncio.run(db.get_admin_deliveries(100))
    delivery = result["items"][0]
    assert set(delivery) == {
        "id", "subscriber_email", "delivered_at", "item_count",
        "is_breaking", "status",
    }
    assert delivery["is_breaking"] is False
    assert result["next_cursor"] is None
    sql = conn.fetch.call_args.args[0].lower()
    assert "delivery_status = 'sent'" in sql
    assert "delivered_at" in sql and "item_count" in sql
    for forbidden in ("select *", "items", "delivery_error", "stripe", "auth_tokens"):
        assert forbidden not in sql
    assert conn.fetch.call_args.args[1] == 101
