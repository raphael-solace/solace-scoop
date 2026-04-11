"""
Scoop Digest Pipeline -- Solace Internal

Four-pass approach:
  1. Solace context (hardcoded, no API call).
  2. For each customer account, run 8 parallel queries:
     people moves, business initiatives, hiring velocity,
     partnerships/vendors, financial events, risk signals,
     competitive moves, regulatory/compliance.
  3. Rank by urgency and impact, return top N.
  4. Week-over-week dedup via cheap LLM.

Provider priority:
  - SAP AI Core (if configured) for Perplexity and GPT calls
  - Direct Perplexity API (fallback)
  - LiteLLM proxy (fallback for dedup)
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import date, timedelta
from typing import Optional

import httpx

from config import cfg

PPLX_API_URL = "https://api.perplexity.ai/chat/completions"

SIGNAL_CATEGORIES = {
    "leadership_change": {"tag": "People Move", "color": "red"},
    "technical_leader": {"tag": "Tech Leader", "color": "red"},
    "champion_change": {"tag": "Champion Update", "color": "red"},
    "stakeholder_move": {"tag": "Stakeholder Move", "color": "red"},
    "tech_initiative": {"tag": "Tech Initiative", "color": "blue"},
    "platform_change": {"tag": "Platform Change", "color": "blue"},
    "tech_stack": {"tag": "Tech Stack", "color": "blue"},
    "architecture": {"tag": "Architecture", "color": "blue"},
    "cloud_migration": {"tag": "Cloud Migration", "color": "blue"},
    "digital_transformation": {"tag": "Transformation", "color": "blue"},
    "integration": {"tag": "Integration", "color": "blue"},
    "strategic": {"tag": "Strategic", "color": "blue"},
    "hiring": {"tag": "Hiring Signal", "color": "green"},
    "partnership": {"tag": "Partnership", "color": "green"},
    "partner_activity": {"tag": "Partner Activity", "color": "green"},
    "expansion": {"tag": "Expansion", "color": "green"},
    "funding": {"tag": "Funding", "color": "green"},
    "financial_event": {"tag": "Financial", "color": "green"},
    "earnings_language": {"tag": "Earnings", "color": "green"},
    "risk_layoffs": {"tag": "Risk: Layoffs", "color": "red"},
    "risk_reorg": {"tag": "Risk: Reorg", "color": "red"},
    "risk_churn": {"tag": "Risk: Churn", "color": "red"},
    "restructuring": {"tag": "Restructuring", "color": "red"},
    "competitive": {"tag": "Competitive", "color": "amber"},
    "competitor_win": {"tag": "Competitor", "color": "amber"},
    "competitor_launch": {"tag": "Competitor", "color": "amber"},
    "acquisition": {"tag": "M&A", "color": "amber"},
    "regulatory": {"tag": "Regulation", "color": "amber"},
    "regulation": {"tag": "Regulation", "color": "amber"},
    "compliance_deadline": {"tag": "Compliance", "color": "red"},
}

ALL_TAGS = ", ".join(SIGNAL_CATEGORIES.keys())


# -- Model call with fallback chain -----------

async def _call_model(
    system: str,
    prompt: str,
    max_tokens: int | None = None,
    provider: str = "pplx",
) -> dict:
    """Call a model with fallback: AI Core -> direct API -> error.

    provider: "pplx" for Perplexity sonar-pro, "gpt" for GPT-4o-mini (dedup).
    Returns the full API response dict.
    """
    pplx_cfg = cfg["perplexity"]
    tokens = max_tokens or pplx_cfg["max_tokens"]
    temp = pplx_cfg["temperature"]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    max_retries = pplx_cfg["max_retries"]
    base_delay = pplx_cfg["retry_base_delay_sec"]

    # Try SAP AI Core first
    deployment_env = "AICORE_DEPLOYMENT_PPLX" if provider == "pplx" else "AICORE_DEPLOYMENT_GPT4O_MINI"
    deployment_id = os.getenv(deployment_env, "")
    model = "sonar-pro" if provider == "pplx" else "gpt-4o-mini"

    if deployment_id:
        import aicore
        if aicore.is_configured():
            for attempt in range(max_retries):
                try:
                    return await aicore.chat_completion(
                        deployment_id=deployment_id,
                        model=model,
                        messages=messages,
                        max_tokens=tokens,
                        temperature=temp,
                    )
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        wait = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        print(f"      [ai-core rate-limited] waiting {wait:.0f}s...")
                        await asyncio.sleep(wait)
                        continue
                    raise
                except Exception as e:
                    print(f"      [ai-core error] {e}, falling back to direct API")
                    break

    # Fallback: direct Perplexity API (for pplx provider)
    if provider == "pplx":
        api_key = os.getenv("PPLX_KEY", "")
        if api_key:
            for attempt in range(max_retries):
                try:
                    async with httpx.AsyncClient(timeout=60) as client:
                        resp = await client.post(
                            PPLX_API_URL,
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": "sonar-pro",
                                "messages": messages,
                                "max_tokens": tokens,
                                "temperature": temp,
                            },
                        )
                        if resp.status_code == 429:
                            wait = base_delay * (2 ** attempt) + random.uniform(0, 1)
                            print(f"      [pplx rate-limited] waiting {wait:.0f}s...")
                            await asyncio.sleep(wait)
                            continue
                        resp.raise_for_status()
                    return resp.json()
                except httpx.HTTPStatusError:
                    raise
                except Exception as e:
                    print(f"      [pplx error] {e}")
                    break

    # Fallback: LiteLLM proxy (for gpt provider / dedup)
    if provider == "gpt":
        api_url = os.getenv("LITELLM_URL", "")
        api_key = os.getenv("LITELLM_KEY", "")
        if api_url and api_key:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{api_url.rstrip('/')}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": os.getenv("LITELLM_MODEL", "azure-gpt-4o-mini"),
                        "messages": messages,
                        "max_tokens": tokens,
                        "temperature": temp,
                    },
                )
                resp.raise_for_status()
            return resp.json()

    raise RuntimeError(f"No provider available for {provider}")


def _parse_json(content: str) -> dict | list:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(content)


# -- Solace context ----------------------------

def get_solace_context() -> dict:
    s = cfg["solace"]
    return {
        "company_summary": s["product"],
        "buyer_personas": s["buyer_personas"],
        "use_cases": s["use_cases"],
        "buying_triggers": s["buying_triggers"],
        "competitors": s["competitors"],
        "keywords": s["keywords"],
        "partners": s["partners"],
        "product_category": s.get("product_category", "Event-driven messaging and integration platform"),
    }


# -- 8 query types per company ----------------

QUERY_TYPES = [
    {
        "name": "people_moves",
        "days": 7,
        "prompt": """Find recent role changes, appointments, departures, or promotions at {company} in the LAST 7 DAYS ONLY.
Focus on IT Architects, Enterprise Architects, Integration Leads, CTOs, VPs of Engineering, Heads of Platform, Directors of IT.
Include the person's NAME, TITLE, where they came from, and the DATE of the change.
If nothing found in the last 7 days, return an empty array.""",
    },
    {
        "name": "business_initiatives",
        "days": 14,
        "prompt": """Find IT and technology initiatives at {company} in the LAST 14 DAYS.
Focus on: cloud migration, digital transformation, event-driven architecture adoption, middleware modernization (TIBCO, IBM MQ replacement), microservices, IoT, AI/ML data pipelines, real-time integration projects.
Include project names, budgets, and timelines if mentioned.
If nothing found, return an empty array.""",
    },
    {
        "name": "hiring_velocity",
        "days": 14,
        "prompt": """Analyze HIRING PATTERNS at {company} in the LAST 14 DAYS.
Look for clusters of roles in IT, integration, middleware, platform, DevOps, architecture.
Keywords to match: {keywords}
A pattern of 5+ related postings is a signal. A single posting is noise.
If nothing significant found, return an empty array.""",
    },
    {
        "name": "partnerships_vendors",
        "days": 14,
        "prompt": """Find technology partnerships, vendor selections, or SI engagements at {company} in the LAST 14 DAYS.
Look for: Accenture, Deloitte, Capgemini working with {company}. Competitor deployments (Confluent, TIBCO, IBM MQ).
Include vendor names and engagement scope.
If nothing found, return an empty array.""",
    },
    {
        "name": "financial_events",
        "days": 14,
        "prompt": """Find financial events at {company} in the LAST 14 DAYS relevant to IT spend.
Look for: funding rounds, earnings language about technology investment, budget announcements, M&A affecting IT.
If nothing found, return an empty array.""",
    },
    {
        "name": "risk_signals",
        "days": 14,
        "prompt": """Find NEGATIVE or WARNING signals at {company} in the LAST 14 DAYS.
Look for: layoffs in IT/engineering, hiring freezes, budget cuts, key departures (architects, integration leads), restructuring in tech teams.
Be specific about scope. "Laid off 50 in engineering" is different from "laid off 50 in marketing."
If nothing concerning, return an empty array.""",
    },
    {
        "name": "competitive_moves",
        "days": 14,
        "prompt": """Find competitive intelligence at {company} in the LAST 14 DAYS relevant to selling event-driven messaging.
Has {company} adopted or evaluated: {competitors}?
Job postings mentioning competitor products? Migration away from current platform?
If nothing found, return an empty array.""",
    },
    {
        "name": "regulatory_compliance",
        "days": 14,
        "prompt": """Find regulatory or compliance developments affecting {company} in the LAST 14 DAYS.
Focus on regulations requiring real-time data processing, event-driven patterns, or integration modernization.
Include deadlines and fines if mentioned.
If nothing found, return an empty array.""",
    },
]

SIGNAL_OUTPUT_INSTRUCTION = """Return a JSON array of signals (0 to 3 items). No markdown, no code fences.
[
  {{
    "company": "{company}",
    "tag": "<one of: {all_tags}>",
    "date": "<the date this event happened, e.g. 'Apr 8'. MUST be a real date from your sources. If no date, use empty string.>",
    "headline": "<one specific sentence with names, dates, concrete details. Never use em dashes.>",
    "why": "<one sentence connecting this to Solace (event broker, EDA, Agent Mesh)>",
    "urgency": "<IMMEDIATE | THIS_WEEK | THIS_MONTH | THIS_QUARTER>",
    "window": "<why timing matters and when it closes>",
    "opening_line": "<natural conversation starter, no jargon>",
    "risk_or_opportunity": "<opportunity | risk | both>",
    "suggested_action": "<who to contact, what channel, what to say>",
    "confidence": "<HIGH | MEDIUM | LOW>",
    "sources": ["<source URLs from your research>"]
  }}
]

RULES:
- Every signal MUST have a specific name, date, or number
- Return [] if nothing concrete found. Never invent signals.
- Never use em dashes (--) in any field"""


async def _run_company_query(
    query_type: dict,
    company: str,
    seller_context: dict,
) -> list[dict]:
    today = date.today()
    cutoff = today - timedelta(days=query_type["days"])
    ctx = {
        "company": company,
        "keywords": seller_context.get("keywords", ""),
        "competitors": seller_context.get("competitors", ""),
        "buyer_personas": seller_context.get("buyer_personas", ""),
        "buying_triggers": seller_context.get("buying_triggers", ""),
        "partners": seller_context.get("partners", ""),
    }
    prompt = query_type["prompt"].format(**ctx)
    output_instruction = SIGNAL_OUTPUT_INSTRUCTION.format(
        company=company, all_tags=ALL_TAGS,
    )

    data = await _call_model(
        system=f"""You are a B2B sales intelligence analyst. Today is {today.isoformat()}.
Only report events AFTER {cutoff.isoformat()}. Include specific dates.
Do NOT report background info as news. Do NOT fabricate dates.
If nothing recent, return []. Return only valid JSON.""",
        prompt=f"{prompt}\n\n{output_instruction}",
        provider="pplx",
    )

    content = data["choices"][0]["message"]["content"]
    try:
        items = _parse_json(content)
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(items, list):
        items = [items] if isinstance(items, dict) and items.get("headline") else []

    sources = data.get("citations", [])
    for item in items:
        if not item.get("sources"):
            item["sources"] = sources

    return [i for i in items if i.get("headline")]


async def _research_company(
    company: str,
    seller_context: dict,
) -> list[dict]:
    tasks = [_run_company_query(qt, company, seller_context) for qt in QUERY_TYPES]
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


# -- Rank signals ------------------------------

async def _rank_signals(
    items: list[dict],
    seller_context: dict,
) -> list[dict]:
    n = cfg["digest"]["signals_per_email"]
    if len(items) <= 3:
        return items

    signals_json = json.dumps(
        [{"i": i, "company": item["company"], "tag": item.get("tag", ""),
          "headline": item["headline"], "urgency": item.get("urgency", ""),
          "risk_or_opportunity": item.get("risk_or_opportunity", "opportunity")}
         for i, item in enumerate(items)],
        indent=2,
    )

    data = await _call_model(
        system="You are a B2B sales prioritization expert. Return only valid JSON.",
        prompt=f"""Rank these signals for a Solace colleague. Criteria:
1. RISK signals rank above opportunities
2. People changes (new architect, key departure) rank high
3. IMMEDIATE urgency above THIS_WEEK, etc.
4. HIGH confidence above MEDIUM/LOW

{signals_json}

Return a JSON array of the "i" values of the top {n}, in priority order: [0, 3, 7, ...]""",
        max_tokens=cfg["perplexity"]["ranking_max_tokens"],
        provider="pplx",
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
            return ranked[:n] if ranked else items[:n]
    except (json.JSONDecodeError, ValueError):
        pass

    return items[:n]


# -- Dedup against previous week ---------------

async def _dedup_signals(
    items: list[dict],
    previous_headlines: list[str],
) -> list[dict]:
    if not items or not previous_headlines:
        return items

    signals_for_review = [
        {"i": i, "headline": item.get("headline", "")}
        for i, item in enumerate(items)
    ]
    prev_list = "\n".join(f"- {h}" for h in previous_headlines[:30])

    try:
        data = await _call_model(
            system="You are a strict dedup reviewer. Return only valid JSON arrays.",
            prompt=f"""Compare these new signals against last week's headlines.
DROP a signal if it describes the SAME event (same person, same announcement, same deal) even if worded differently.
KEEP signals that are follow-up developments or genuinely new events.

LAST WEEK'S HEADLINES:
{prev_list}

NEW SIGNALS:
{json.dumps(signals_for_review, indent=2)}

Return a JSON array of the "i" values to KEEP: [0, 2, 5, ...]
If all are duplicates, return []. No explanation.""",
            max_tokens=500,
            provider="gpt",
        )
        content = data["choices"][0]["message"]["content"]
        indices = _parse_json(content)
        if isinstance(indices, list):
            kept = [items[idx] for idx in indices if isinstance(idx, int) and 0 <= idx < len(items)]
            dropped = len(items) - len(kept)
            if dropped:
                print(f"    [dedup] Dropped {dropped} duplicate(s)")
            return kept
    except Exception as e:
        print(f"    [dedup] Failed ({e}), keeping all signals")

    return items


# -- Public API --------------------------------

async def generate_signals(
    companies: list[str],
) -> list[dict]:
    """Research signals for a list of companies. Returns ranked, deduped items."""
    seller_context = get_solace_context()
    product = cfg["solace"]["product"]

    all_items = []
    for i, company in enumerate(companies):
        if i > 0:
            await asyncio.sleep(cfg["digest"]["pace_between_companies_sec"])
        print(f"  [{company}] (8 queries)")
        company_items = await _research_company(company, seller_context)
        for item in company_items:
            tag_key = item.get("tag", "strategic")
            category = SIGNAL_CATEGORIES.get(tag_key, SIGNAL_CATEGORIES.get("strategic", {"tag": "Strategic", "color": "blue"}))
            item["tag"] = category["tag"]
            item["tag_color"] = category["color"]
        all_items.extend(company_items)

    if not all_items:
        print("  No signals found across any account.")
        return []

    print(f"  [ranking] {len(all_items)} raw signals")
    ranked = await _rank_signals(all_items, seller_context)
    print(f"  [ranked] {len(ranked)} signals")

    return ranked


async def generate_digest_for_user(user: dict) -> list[dict]:
    """Generate a full digest for a single user, with dedup against previous week."""
    companies = [c["name"] for c in user.get("companies", [])]
    if not companies:
        return []

    n = cfg["digest"]["companies_per_user"]
    sample = companies[:n]

    # Research
    items = await generate_signals(sample)
    if not items:
        return []

    # Dedup against previous digests
    user_id = user.get("id", "")
    if user_id:
        try:
            from db import get_previous_headlines
            lookback = cfg.get("litellm", {}).get("dedup_lookback_weeks", 4)
            previous = await get_previous_headlines(user_id, weeks=lookback)
            if previous:
                print(f"  [dedup] Comparing against {len(previous)} previous headlines")
                items = await _dedup_signals(items, previous)
        except Exception as e:
            print(f"  [dedup] Could not fetch previous headlines: {e}")

    print(f"  [done] {len(items)} final signals")
    return items
