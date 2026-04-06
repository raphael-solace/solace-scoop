"""
Scoop Digest Pipeline

Queries Perplexity sonar-pro for each company, then synthesizes
the results into a ranked, contextualized digest.

Usage:
  items = await generate_digest_preview(["Snowflake", "Stripe"], "data platform")
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import httpx

PPLX_API_URL = "https://api.perplexity.ai/chat/completions"
PPLX_MODEL = "sonar-pro"

SIGNAL_CATEGORIES = {
    "executive_change": {"tag": "Executive Change", "color": "red"},
    "funding": {"tag": "Funding", "color": "green"},
    "acquisition": {"tag": "M&A", "color": "amber"},
    "layoffs": {"tag": "Restructuring", "color": "red"},
    "product_launch": {"tag": "Product Launch", "color": "blue"},
    "partnership": {"tag": "Partnership", "color": "green"},
    "regulatory": {"tag": "Regulatory", "color": "amber"},
    "strategic": {"tag": "Strategic", "color": "blue"},
    "competitive": {"tag": "Competitive", "color": "amber"},
    "expansion": {"tag": "Expansion", "color": "green"},
}


async def generate_digest_preview(
    companies: list[str],
    product: str,
) -> list[dict]:
    """Generate digest items for a preview (onboarding flow)."""
    tasks = [_query_company(company, product) for company in companies]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items = []
    for company, result in zip(companies, results):
        if isinstance(result, Exception):
            continue
        if result:
            items.append(result)

    return items


async def generate_digest_for_user(user: dict) -> list[dict]:
    """
    Generate a full digest for a single user.
    user = {"email": ..., "product": ..., "companies": [{"name": ...}, ...]}
    """
    companies = [c["name"] for c in user.get("companies", [])]
    if not companies:
        return []

    product = user.get("product", "our product")
    tasks = [_query_company(company, product) for company in companies]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items = []
    for result in results:
        if isinstance(result, Exception):
            continue
        if result:
            items.append(result)

    return items


async def _query_company(company: str, product: str) -> Optional[dict]:
    """Query Perplexity for recent news about a company and synthesize."""
    api_key = os.getenv("PPLX_KEY")
    if not api_key:
        raise RuntimeError("PPLX_KEY not set")

    prompt = f"""Find the single most important recent news or development about {company}
from the last 7 days. Focus on: executive changes, M&A, funding rounds, layoffs,
product launches, partnerships, regulatory actions, or strategic pivots.

If there is no significant recent news, look for the most recent noteworthy development.

Respond in this exact JSON format (no markdown, no code fences):
{{
  "company": "{company}",
  "tag": "<one of: executive_change, funding, acquisition, layoffs, product_launch, partnership, regulatory, strategic, competitive, expansion>",
  "headline": "<one sentence describing what happened>",
  "why": "<one sentence explaining why this matters to someone selling {product}>"
}}"""

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            PPLX_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": PPLX_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a concise business intelligence analyst. Return only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 300,
                "temperature": 0.1,
            },
        )
        resp.raise_for_status()

    data = resp.json()
    content = data["choices"][0]["message"]["content"].strip()

    # Parse the JSON response
    import json

    # Handle potential markdown code fences
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    parsed = json.loads(content)

    # Map the tag to display values
    tag_key = parsed.get("tag", "strategic")
    category = SIGNAL_CATEGORIES.get(tag_key, SIGNAL_CATEGORIES["strategic"])

    return {
        "company": parsed.get("company", company),
        "tag": category["tag"],
        "tag_color": category["color"],
        "headline": parsed.get("headline", ""),
        "why": parsed.get("why", ""),
        "sources": data.get("citations", []),
    }
