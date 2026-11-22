"""
SAP AI Core — OAuth2 client + chat completion helper.

Handles token caching using the issuer's lifetime, deployment-based routing,
and provider-specific URL patterns.

Provider priority (set in digest.py):
  1. SAP AI Core (if AICORE_DEPLOYMENT_* env vars are set)
  2. Direct Perplexity API (PPLX_KEY)
  3. LiteLLM proxy (LITELLM_URL + LITELLM_KEY)
"""

from __future__ import annotations

import base64
import asyncio
import math
import os
import time

import httpx

# ── Token cache ──────────────────────────────

_cached_token: str = ""
_token_expires_at: float = 0.0
_token_lock = asyncio.Lock()


async def _get_token(rejected_token: str | None = None) -> str:
    """Get a valid OAuth2 access token, refreshing if expired."""
    async with _token_lock:
        return await _get_token_locked(rejected_token)


async def _get_token_locked(rejected_token: str | None) -> str:
    global _cached_token, _token_expires_at

    if rejected_token is not None and rejected_token == _cached_token:
        _token_expires_at = 0.0
    if _cached_token and time.time() < _token_expires_at:
        return _cached_token

    auth_url = os.getenv("SAP_AICORE_AUTH_URL", "")
    client_id = os.getenv("SAP_AICORE_CLIENT_ID", "")
    client_secret = os.getenv("SAP_AICORE_CLIENT_SECRET", "")

    if not all([auth_url, client_id, client_secret]):
        raise RuntimeError("SAP AI Core credentials not configured")

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    requested_at = time.time()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            auth_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()

    data = resp.json()
    lifetime = float(data.get("expires_in", 300))
    if not math.isfinite(lifetime) or lifetime <= 0:
        raise RuntimeError("SAP AI Core returned an invalid token lifetime")
    _cached_token = data["access_token"]
    # Refresh before expiry, allowing for clock skew and request latency.
    _token_expires_at = requested_at + lifetime - min(60, lifetime * 0.1)
    return _cached_token


# ── Chat completion ──────────────────────────

async def chat_completion(
    deployment_id: str,
    model: str,
    messages: list[dict],
    max_tokens: int = 700,
    temperature: float = 0.1,
) -> dict:
    """Call a model via SAP AI Core deployment.

    Returns the full API response dict (OpenAI-compatible format).
    """
    token = await _get_token()
    base_url = os.getenv("SAP_AICORE_BASE_URL", "")
    resource_group = os.getenv("SAP_AICORE_RESOURCE_GROUP", "default")

    # GPT models need ?api-version=latest
    url = f"{base_url}/v2/inference/deployments/{deployment_id}/chat/completions"
    if "gpt" in model.lower():
        url += "?api-version=latest"

    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(2):
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "AI-Resource-Group": resource_group,
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            if resp.status_code != 401 or attempt == 1:
                break
            token = await _get_token(rejected_token=token)
        resp.raise_for_status()

    return resp.json()


def is_configured() -> bool:
    """Check if SAP AI Core is configured."""
    return bool(
        os.getenv("SAP_AICORE_AUTH_URL")
        and os.getenv("SAP_AICORE_CLIENT_ID")
        and os.getenv("SAP_AICORE_CLIENT_SECRET")
        and os.getenv("SAP_AICORE_BASE_URL")
    )
