"""
Scoop Digest Pipeline

Three-pass approach:
  1. Research the seller's company deeply.
  2. For each target account, run multiple parallel queries across
     different signal types (people moves, tech initiatives, hiring,
     partnerships, etc.)
  3. Deduplicate, rank, and format the results.

We deliberately use many Perplexity calls per company. At $3/1000
queries, thoroughness is cheap. Bad intel is expensive.
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
    "leadership_change": {"tag": "People Move", "color": "red"},
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
    "strategic": {"tag": "Strategic", "color": "blue"},
}


# ── Perplexity helpers ───────────────────────

async def _pplx_query(system: str, prompt: str, max_tokens: int = 600) -> dict:
    api_key = os.getenv("PPLX_KEY")
    if not api_key:
        raise RuntimeError("PPLX_KEY not set")

    async with httpx.AsyncClient(timeout=60) as client:
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


def _parse_json(content: str) -> dict | list:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(content)


# ── Pass 1: Deep research on the seller ──────

async def research_seller(product: str) -> dict:
    data = await _pplx_query(
        system="You are a B2B sales intelligence analyst. Return only valid JSON.",
        prompt=f"""Research "{product}" thoroughly as a B2B product or service.

I need to understand:
- What this company/product does (could be software, services, insurance, consulting, manufacturing, logistics, anything B2B)
- Who the typical buyer is inside a target company (specific job titles at the level that evaluates and signs off)
- What problems it solves for the buyer
- What events at a prospect company would create a buying opportunity
- What industry or industries this product serves

Respond in this exact JSON format (no markdown, no code fences):
{{
  "company_summary": "<2-3 sentences on what this company/product does>",
  "industry": "<the seller's primary industry: software, insurance, consulting, manufacturing, logistics, financial services, healthcare, etc.>",
  "buyer_personas": "<comma-separated list of 5-8 job titles who evaluate and buy this product. Be specific to the industry. Not just C-suite, include director/VP/manager-level roles.>",
  "use_cases": "<the top 5 problems this product solves for buyers, comma-separated>",
  "buying_triggers": "<7-10 specific events at a prospect company that would create a sales opportunity, comma-separated. Be specific to this product and industry.>",
  "competitors": "<top 3-5 competitors, comma-separated>",
  "keywords": "<10 keywords and phrases a prospect would use when they need this product, comma-separated>"
}}""",
        max_tokens=600,
    )
    content = data["choices"][0]["message"]["content"]
    return _parse_json(content)


# ── Pass 2: Multi-query per company ──────────

# Each query type searches for a different kind of signal.
# They run in parallel for each company.

QUERY_TYPES = [
    {
        "name": "people_moves",
        "prompt": """Find recent role changes, appointments, departures, or promotions
at {company} in the last 30 days. Focus on people in roles relevant to buying {product}:
- Roles like: {buyer_personas}
- Any leadership change at the VP, Director, or Head-of level in departments that would buy {product}
- New hires into senior roles in relevant departments

Include the person's NAME, their new TITLE, and where they came from if known.
Skip roles that have no connection to buying {product}.
If no relevant people moves found, say so clearly.""",
    },
    {
        "name": "business_initiatives",
        "prompt": """Find recent initiatives, programs, or strategic projects at {company}
in the last 30 days that would be relevant to someone selling {product}.

Look for:
- Programs related to: {use_cases}
- Budget increases or new investment announcements in relevant areas
- RFPs, tenders, or vendor selection processes
- Transformation programs, modernization efforts, or expansion plans
- Anything matching these buying triggers: {buying_triggers}

Include specific details: project scope, budget if mentioned, timeline.
If nothing found, say so clearly.""",
    },
    {
        "name": "hiring_signals",
        "prompt": """Find evidence that {company} is actively hiring for roles
that suggest they need {product}. Look for:
- Job postings related to: {keywords}
- New teams or departments being formed in relevant areas
- Rapid headcount growth in departments that buy {product}
- Job descriptions that mention problems {product} solves

Include specific job titles and what they suggest about the company's direction.
If nothing relevant found, say so clearly.""",
    },
    {
        "name": "partnerships_vendors",
        "prompt": """Find recent partnerships, vendor selections, or strategic relationships
at {company} in the last 30 days relevant to someone selling {product}. Look for:
- New vendor announcements in areas where {product} competes
- Strategic partnerships that change how {company} operates
- Competitor deployments (competitors include: {competitors})
- Industry events or conferences where {company} presented as a buyer

Include specific names and details.
If nothing relevant found, say so clearly.""",
    },
]


async def _run_company_query(
    query_type: dict,
    company: str,
    product: str,
    seller_context: dict,
) -> list[dict]:
    """Run one query type for one company, return 0-3 signal items."""
    keywords = seller_context.get("keywords", "")
    competitors = seller_context.get("competitors", "")
    buyer_personas = seller_context.get("buyer_personas", "decision-makers")
    use_cases = seller_context.get("use_cases", "")
    buying_triggers = seller_context.get("buying_triggers", "")

    prompt = query_type["prompt"].format(
        company=company,
        product=product,
        keywords=keywords,
        competitors=competitors,
        buyer_personas=buyer_personas,
        use_cases=use_cases,
        buying_triggers=buying_triggers,
    )

    data = await _pplx_query(
        system=f"""You are a B2B sales intelligence analyst.
The salesperson sells: {product}
Their product solves: {use_cases}
Their buyers are: {buyer_personas}

Extract concrete, specific facts. Include names, titles, dates, and numbers.
Do not speculate or invent information. If you can't find anything, say so.
Return only valid JSON.""",

        prompt=f"""{prompt}

Based on what you found, return a JSON array of signals (0 to 3 items).
Each signal must contain concrete, verified information.

Return this exact format (no markdown, no code fences):
[
  {{
    "company": "{company}",
    "tag": "<one of: leadership_change, tech_initiative, platform_change, digital_transformation, hiring, partnership, expansion, funding, acquisition, competitive, regulatory, restructuring, strategic>",
    "headline": "<one specific sentence with names, dates, and concrete details>",
    "why": "<one sentence connecting this to selling {product}, referencing which buyer persona would care>",
    "suggested_action": "<one concrete next step: who to contact and what to say>"
  }}
]

If nothing relevant was found, return an empty array: []""",
        max_tokens=600,
    )

    content = data["choices"][0]["message"]["content"]
    try:
        items = _parse_json(content)
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(items, list):
        items = [items] if isinstance(items, dict) and items.get("headline") else []

    # Tag each item with sources
    sources = data.get("citations", [])
    for item in items:
        item["sources"] = sources

    return [i for i in items if i.get("headline")]


async def _research_company(
    company: str,
    product: str,
    seller_context: dict,
) -> list[dict]:
    """Run all query types for a single company in parallel."""
    tasks = [
        _run_company_query(qt, company, product, seller_context)
        for qt in QUERY_TYPES
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items = []
    for query_type, result in zip(QUERY_TYPES, results):
        if isinstance(result, Exception):
            print(f"    [{query_type['name']}] Error: {result}")
            continue
        if result:
            print(f"    [{query_type['name']}] {len(result)} signal(s)")
            all_items.extend(result)
        else:
            print(f"    [{query_type['name']}] nothing found")

    return all_items


# ── Pass 3: Deduplicate and rank ─────────────

async def _rank_signals(
    items: list[dict],
    product: str,
    seller_context: dict,
) -> list[dict]:
    """Use Perplexity to pick the most actionable signals and remove duplicates."""
    if len(items) <= 3:
        return items

    buyer_personas = seller_context.get("buyer_personas", "IT leaders")
    summaries = json.dumps(
        [{"company": i["company"], "headline": i["headline"], "tag": i["tag"]} for i in items],
        indent=2,
    )

    data = await _pplx_query(
        system="You are a B2B sales prioritization expert. Return only valid JSON.",
        prompt=f"""A salesperson selling {product} has these signals about their accounts.
Their buyers are: {buyer_personas}

Signals:
{summaries}

Pick the top 5 most actionable signals. Prioritize:
1. People moves (new IT leaders = immediate outreach opportunity)
2. Active technology projects or tenders (budget is allocated)
3. Hiring signals (team is growing = needs tools)
4. Partnerships or vendor changes (architecture is in flux)

Remove duplicates (same event described differently).

Return a JSON array of the indices (0-based) of the top signals, in priority order:
[0, 3, 7, ...]""",
        max_tokens=200,
    )

    content = data["choices"][0]["message"]["content"]
    try:
        indices = _parse_json(content)
        if isinstance(indices, list):
            ranked = []
            seen = set()
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(items):
                    key = items[idx]["headline"][:60]
                    if key not in seen:
                        seen.add(key)
                        ranked.append(items[idx])
            return ranked if ranked else items[:5]
    except (json.JSONDecodeError, ValueError):
        pass

    return items[:5]


# ── Public API ───────────────────────────────

async def generate_digest_preview(
    companies: list[str],
    product: str,
) -> list[dict]:
    """Generate digest items with deep multi-query research."""
    # Pass 1: understand the seller
    print("  [seller research]")
    seller_context = await research_seller(product)
    print(f"    Summary: {seller_context.get('company_summary', '')[:120]}")
    print(f"    Buyers: {seller_context.get('buyer_personas', '')[:120]}")
    print(f"    Triggers: {seller_context.get('buying_triggers', '')[:120]}")
    print(f"    Competitors: {seller_context.get('competitors', '')[:120]}")

    # Pass 2: multi-query each company in parallel
    all_items = []
    for company in companies:
        print(f"  [{company}]")
        company_items = await _research_company(company, product, seller_context)
        # Tag with formatted category
        for item in company_items:
            tag_key = item.get("tag", "strategic")
            category = SIGNAL_CATEGORIES.get(tag_key, SIGNAL_CATEGORIES["strategic"])
            item["tag"] = category["tag"]
            item["tag_color"] = category["color"]
        all_items.extend(company_items)

    if not all_items:
        print("  No signals found across any account.")
        return []

    print(f"  [ranking] {len(all_items)} raw signals")

    # Pass 3: rank and deduplicate
    ranked = await _rank_signals(all_items, product, seller_context)
    print(f"  [done] {len(ranked)} final signals")

    return ranked


async def generate_digest_for_user(user: dict) -> list[dict]:
    """Generate a full digest for a single user."""
    companies = [c["name"] for c in user.get("companies", [])]
    if not companies:
        return []
    product = user.get("product", "our product")
    return await generate_digest_preview(companies, product)
