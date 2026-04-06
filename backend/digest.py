"""
Scoop Digest Pipeline

Two-pass approach:
  1. Research the seller's company to understand what they actually sell,
     who their buyers are, and what signals create opportunities.
  2. For each target account, search for news that matches those
     specific buying signals.

This ensures the digest surfaces IT architecture decisions, platform
migrations, and technical initiatives rather than CEO reshuffles
and sponsorship deals.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

import httpx

PPLX_API_URL = "https://api.perplexity.ai/chat/completions"
PPLX_MODEL = "sonar-pro"

SIGNAL_CATEGORIES = {
    "tech_initiative": {"tag": "Tech Initiative", "color": "blue"},
    "platform_change": {"tag": "Platform Change", "color": "blue"},
    "digital_transformation": {"tag": "Transformation", "color": "blue"},
    "hiring": {"tag": "Hiring Signal", "color": "green"},
    "partnership": {"tag": "Partnership", "color": "green"},
    "expansion": {"tag": "Expansion", "color": "green"},
    "funding": {"tag": "Funding", "color": "green"},
    "acquisition": {"tag": "M&A", "color": "amber"},
    "competitive": {"tag": "Competitive", "color": "amber"},
    "regulatory": {"tag": "Compliance", "color": "amber"},
    "restructuring": {"tag": "Restructuring", "color": "red"},
    "leadership_change": {"tag": "IT Leadership", "color": "red"},
    "strategic": {"tag": "Strategic", "color": "blue"},
}


async def _pplx_query(system: str, prompt: str, max_tokens: int = 500) -> dict:
    """Make a single Perplexity API call."""
    api_key = os.getenv("PPLX_KEY")
    if not api_key:
        raise RuntimeError("PPLX_KEY not set")

    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(
            PPLX_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": PPLX_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.1,
            },
        )
        resp.raise_for_status()

    return resp.json()


def _parse_json(content: str) -> dict:
    """Extract JSON from a response that might have markdown fences."""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(content)


# ── Pass 1: Research the seller ──────────────

async def research_seller(product: str) -> dict:
    """
    Research what the seller's company actually does, who buys it,
    and what signals indicate a sales opportunity.
    """
    data = await _pplx_query(
        system="You are a B2B sales intelligence analyst. Return only valid JSON.",
        prompt=f"""Research "{product}" as a B2B product/company.

Respond in this exact JSON format (no markdown, no code fences):
{{
  "company_summary": "<what this company/product does in 1-2 sentences>",
  "buyer_personas": "<who actually buys this: job titles like Enterprise Architect, VP Engineering, IT Director, etc.>",
  "use_cases": "<the top 3 technical problems this product solves, comma-separated>",
  "buying_triggers": "<5 specific events at a target company that would create a sales opportunity, comma-separated. Focus on IT and technology decisions, not general business news.>"
}}""",
        max_tokens=400,
    )

    content = data["choices"][0]["message"]["content"]
    return _parse_json(content)


# ── Pass 2: Query each account ───────────────

async def _query_company(
    company: str,
    product: str,
    seller_context: dict,
) -> Optional[dict]:
    """
    Query for news about a target company, filtered through the lens
    of what would actually matter to the seller.
    """
    buyer_personas = seller_context.get("buyer_personas", "IT leaders")
    use_cases = seller_context.get("use_cases", "enterprise integration")
    buying_triggers = seller_context.get("buying_triggers", "platform migrations, cloud adoption")

    data = await _pplx_query(
        system=f"""You are a B2B sales intelligence analyst helping someone who sells {product}.

Their product solves: {use_cases}
Their buyers are: {buyer_personas}

You must find news that is relevant to a mid-market software sale. Ignore:
- CEO/board-level changes (unless it's a CTO/CIO/VP Engineering)
- Sponsorships, sports, marketing campaigns
- Stock price movements
- General HR/culture news
- Anything that wouldn't matter to an enterprise architect or IT director

Only surface signals that could affect IT budgets, architecture decisions,
technology vendor evaluations, or digital transformation initiatives.

Return only valid JSON.""",

        prompt=f"""Find the most important recent IT, technology, or digital transformation
news about {company} from the last 14 days.

Look specifically for:
- Cloud migrations, platform modernization, or infrastructure changes
- New IT leadership (CTO, CIO, VP Engineering, Chief Architect)
- Technology partnerships or vendor selections
- Digital transformation programs or IT budget changes
- Hiring for roles like: {buyer_personas}
- Regulatory or compliance changes that drive IT spending
- Any event matching these buying triggers: {buying_triggers}

If none of these apply, look for broader technology strategy changes.
If there is truly nothing IT-relevant, say so.

Respond in this exact JSON format (no markdown, no code fences):
{{
  "company": "{company}",
  "relevant": true,
  "tag": "<one of: tech_initiative, platform_change, digital_transformation, hiring, partnership, expansion, funding, acquisition, competitive, regulatory, restructuring, leadership_change, strategic>",
  "headline": "<one sentence: what specifically happened, with concrete details>",
  "why": "<one sentence: why this creates an opportunity to sell {product}, referencing the specific buyer persona who would care>",
  "suggested_action": "<one sentence: what the salesperson should do next, e.g. 'Reach out to their VP of Integration about...'>"
}}

If nothing IT-relevant was found, return:
{{
  "company": "{company}",
  "relevant": false,
  "headline": "",
  "why": "",
  "tag": "strategic",
  "suggested_action": ""
}}""",
        max_tokens=400,
    )

    content = data["choices"][0]["message"]["content"]
    parsed = _parse_json(content)

    if not parsed.get("relevant", True) or not parsed.get("headline"):
        return None

    tag_key = parsed.get("tag", "strategic")
    category = SIGNAL_CATEGORIES.get(tag_key, SIGNAL_CATEGORIES["strategic"])

    return {
        "company": parsed.get("company", company),
        "tag": category["tag"],
        "tag_color": category["color"],
        "headline": parsed.get("headline", ""),
        "why": parsed.get("why", ""),
        "suggested_action": parsed.get("suggested_action", ""),
        "sources": data.get("citations", []),
    }


# ── Public API ───────────────────────────────

async def generate_digest_preview(
    companies: list[str],
    product: str,
) -> list[dict]:
    """Generate digest items for a preview (onboarding) or a full run."""
    # Pass 1: understand the seller
    seller_context = await research_seller(product)
    print(f"  Seller context: {seller_context.get('company_summary', '')[:100]}")
    print(f"  Buyers: {seller_context.get('buyer_personas', '')[:100]}")
    print(f"  Triggers: {seller_context.get('buying_triggers', '')[:100]}")

    # Pass 2: query each account through that lens
    tasks = [_query_company(c, product, seller_context) for c in companies]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items = []
    for company, result in zip(companies, results):
        if isinstance(result, Exception):
            print(f"  Error for {company}: {result}")
            continue
        if result:
            items.append(result)
        else:
            print(f"  No IT-relevant signals for {company}")

    return items


async def generate_digest_for_user(user: dict) -> list[dict]:
    """Generate a full digest for a single user."""
    companies = [c["name"] for c in user.get("companies", [])]
    if not companies:
        return []

    product = user.get("product", "our product")
    return await generate_digest_preview(companies, product)
