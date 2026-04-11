"""
SAP AI Core — OAuth2 client + chat completion helper.

Handles token caching (~11h lifetime), deployment-based routing,
and provider-specific URL patterns.

Provider priority (set in digest.py):
  1. SAP AI Core (if AICORE_DEPLOYMENT_* env vars are set)
  2. Direct Perplexity API (PPLX_KEY)
  3. LiteLLM proxy (LITELLM_URL + LITELLM_KEY)
"""

from __future__ import annotations

import base64
import os
import time

import httpx

# ── Token cache ──────────────────────────────

_cached_token: str = ""
_token_expires_at: float = 0.0
_TOKEN_TTL = 11 * 3600  # cache for 11 hours (tokens valid ~12h)


async def _get_token() -> str:
    """Get a valid OAuth2 access token, refreshing if expired."""
    global _cached_token, _token_expires_at

    if _cached_token and time.time() < _token_expires_at:
        return _cached_token

    auth_url = os.getenv("SAP_AICORE_AUTH_URL", "")
    client_id = os.getenv("SAP_AICORE_CLIENT_ID", "")
    client_secret = os.getenv("SAP_AICORE_CLIENT_SECRET", "")

    if not all([auth_url, client_id, client_secret]):
        raise RuntimeError("SAP AI Core credentials not configured")

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

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

    _cached_token = resp.json()["access_token"]
    _token_expires_at = time.time() + _TOKEN_TTL
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
