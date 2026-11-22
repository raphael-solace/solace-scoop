"""Regressions for token expiry during long weekly research runs."""

import asyncio
import os
from unittest.mock import patch

import httpx
import pytest

import aicore


ENV = {
    "SAP_AICORE_AUTH_URL": "https://auth.example/token",
    "SAP_AICORE_CLIENT_ID": "test",
    "SAP_AICORE_CLIENT_SECRET": "test",
    "SAP_AICORE_BASE_URL": "https://inference.example",
}


def run_with_transport(handler, scenario):
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    with patch.dict(os.environ, ENV), \
         patch.object(aicore, "_cached_token", ""), \
         patch.object(aicore, "_token_expires_at", 0), \
         patch.object(aicore, "_token_lock", asyncio.Lock()), \
         patch.object(aicore.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(
             transport=transport, **kwargs
         )):
        asyncio.run(scenario())


def test_refreshes_using_issuer_expiry_during_long_run():
    requests = []
    clock = [1000.0]

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"access_token": f"token-{len(requests)}", "expires_in": 3600})

    async def scenario():
        with patch.object(aicore.time, "time", side_effect=lambda: clock[0]):
            assert await aicore._get_token() == "token-1"
            clock[0] = 4500
            assert await aicore._get_token() == "token-1"
            clock[0] = 4541
            assert await aicore._get_token() == "token-2"
        assert len(requests) == 2

    run_with_transport(handler, scenario)


@pytest.mark.parametrize("second_status", [200, 401])
def test_retries_an_unauthorized_completion_once(second_status):
    auth_calls = []
    bearer_tokens = []

    def handler(request):
        if request.url.host == "auth.example":
            auth_calls.append(request)
            return httpx.Response(200, json={"access_token": f"token-{len(auth_calls)}", "expires_in": 3600})
        bearer_tokens.append(request.headers["authorization"])
        status = 401 if len(bearer_tokens) == 1 else second_status
        return httpx.Response(status, json={"choices": []})

    async def scenario():
        if second_status == 401:
            with pytest.raises(httpx.HTTPStatusError):
                await aicore.chat_completion("deployment", "sonar-pro", [])
        else:
            assert await aicore.chat_completion("deployment", "sonar-pro", []) == {"choices": []}
        assert len(auth_calls) == 2
        assert bearer_tokens == ["Bearer token-1", "Bearer token-2"]

    run_with_transport(handler, scenario)


def test_concurrent_rejections_share_one_token_refresh():
    auth_calls = []

    async def handler(request):
        auth_calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"access_token": "refreshed", "expires_in": 3600})

    async def scenario():
        aicore._cached_token = "expired"
        aicore._token_expires_at = float("inf")
        tokens = await asyncio.gather(*(aicore._get_token(rejected_token="expired") for _ in range(12)))
        assert tokens == ["refreshed"] * 12
        assert len(auth_calls) == 1

    run_with_transport(handler, scenario)
