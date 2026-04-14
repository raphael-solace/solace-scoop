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
        "days": 14,
        "prompt": """Find recent executive changes, senior appointments, departures, or promotions at {company} in the LAST 14 DAYS.
Include C-suite, VPs, Directors, Heads of departments, Chief Architects, CTO, CIO, CDO.
Include the person's NAME, new TITLE, previous role, and DATE.
If nothing found, return an empty array.""",
    },
    {
        "name": "business_news",
        "days": 14,
        "prompt": """Find the most important business news about {company} in the LAST 14 DAYS.
Include: strategic announcements, M&A activity, major contracts won or lost, product launches, market expansions, earnings surprises, CEO statements about strategy, organizational changes.
Prioritize news that would be useful for a salesperson to reference in a conversation.
If nothing found, return an empty array.""",
    },
    {
        "name": "technology_initiatives",
        "days": 14,
        "prompt": """Find technology and IT initiatives at {company} in the LAST 14 DAYS.
Include: cloud migration, digital transformation, AI/ML projects, platform modernization, new technology partnerships, IT infrastructure investments, data strategy announcements, cybersecurity initiatives.
Also look for: event-driven architecture, middleware changes, real-time data, microservices, integration projects.
If nothing found, return an empty array.""",
    },
    {
        "name": "partnerships_deals",
        "days": 14,
        "prompt": """Find new partnerships, vendor selections, consulting engagements, or major deals involving {company} in the LAST 14 DAYS.
Include: technology vendor selections, SI engagements (Accenture, Deloitte, Capgemini, etc.), strategic alliances, joint ventures, supply chain partnerships.
If nothing found, return an empty array.""",
    },
    {
        "name": "financial_signals",
        "days": 14,
        "prompt": """Find financial news about {company} in the LAST 14 DAYS that signals IT spending direction.
Include: quarterly results with technology commentary, capex/opex guidance, investment announcements, cost optimization programs, budget reallocations, fundraising.
If nothing found, return an empty array.""",
    },
    {
        "name": "risk_signals",
        "days": 14,
        "prompt": """Find WARNING signals at {company} in the LAST 14 DAYS.
Include: layoffs (specify which departments), restructuring, executive departures, profit warnings, regulatory fines, cybersecurity incidents, supply chain disruptions.
Be specific about scope and impact.
If nothing concerning, return an empty array.""",
    },
    {
        "name": "competitive_intel",
        "days": 14,
        "prompt": """Find competitive intelligence about {company} in the LAST 14 DAYS relevant to enterprise software and integration.
Has {company} selected, evaluated, or expanded use of: Kafka, Confluent, TIBCO, IBM MQ, MuleSoft, AWS EventBridge, Azure Service Bus, or other messaging/integration platforms?
Also: job postings mentioning competitor technologies, conference talks about their tech stack, migration projects.
If nothing found, return an empty array.""",
    },
    {
        "name": "industry_regulatory",
        "days": 14,
        "prompt": """Find industry developments or regulatory changes affecting {company} in the LAST 14 DAYS.
Include: new regulations, compliance deadlines, industry trends affecting {company}'s sector, government policy changes, trade developments.
Focus on developments that create urgency for technology investment.
If nothing found, return an empty array.""",
    },
]

SIGNAL_OUTPUT_INSTRUCTION = """Return a JSON array of signals (0 to 3 items). No markdown, no code fences.
[
  {{
    "company": "{company}",
    "tag": "<one of: {all_tags}>",
    "date": "<YYYY-MM-DD format. MUST be a real date from your sources. If you cannot determine the exact date, use empty string. NEVER guess a date.>",
    "headline": "<one specific sentence with names, dates, concrete details. Never use em dashes.>",
    "why": "<one sentence explaining why a salesperson covering this account should care. Focus on the business impact, not on any specific product. What does this mean for the relationship?>",
    "urgency": "<IMMEDIATE | THIS_WEEK | THIS_MONTH | THIS_QUARTER>",
    "window": "<why timing matters and when it closes>",
    "opening_line": "<natural conversation starter referencing this news, no jargon, no selling>",
    "risk_or_opportunity": "<opportunity | risk | both>",
    "suggested_action": "<who to contact, what channel, what to say>",
    "confidence": "<HIGH | MEDIUM | LOW>",
    "sources": ["<source URLs from your research>"]
  }}
]

RULES:
- Every signal MUST have a specific name, date, or number
- The "date" field MUST be YYYY-MM-DD format (e.g. 2026-04-08). If unsure of exact date, leave empty.
- The "why" should explain business relevance, NOT force a connection to any specific product
- The "opening_line" should be something you'd naturally say to a contact, not a sales pitch
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

    # Hard date filter: drop signals with parseable dates older than cutoff
    valid = []
    for item in items:
        if not item.get("headline"):
            continue
        d = item.get("date", "")
        if d:
            try:
                signal_date = date.fromisoformat(d)
                if signal_date < cutoff:
                    continue  # too old
            except ValueError:
                pass  # unparseable date, keep it
        valid.append(item)

    return valid


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
        system="You are a B2B sales intelligence curator. Return only valid JSON.",
        prompt=f"""Select the best signals for a salesperson's weekly account intelligence digest.

SELECTION CRITERIA (in order of importance):
1. DIVERSITY: include a mix of signal types. Do NOT select more than 3 risk/layoff signals. A good digest has people moves, partnerships, strategic news, AND risk signals, not just one type.
2. ACTIONABILITY: prefer signals the salesperson can reference in a conversation or act on this week.
3. FRESHNESS: prefer signals with specific recent dates over vague ones.
4. COMPANY SPREAD: try to cover different companies, not 5 signals from the same company.
5. CONFIDENCE: prefer HIGH confidence signals with named sources.

{signals_json}

Return a JSON array of the "i" values of the best {n}, in priority order: [0, 3, 7, ...]""",
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
            result = ranked[:n] if ranked else items[:n]
            return _cap_risk_signals(result)
    except (json.JSONDecodeError, ValueError):
        pass

    return _cap_risk_signals(items[:n])


def _cap_risk_signals(items: list[dict], max_risk: int = 3) -> list[dict]:
    """Ensure no more than max_risk risk/layoff signals in a digest."""
    result = []
    risk_count = 0
    deferred_non_risk = []

    for item in items:
        is_risk = item.get("risk_or_opportunity") in ("risk", "both")
        if is_risk:
            if risk_count < max_risk:
                result.append(item)
                risk_count += 1
            # else skip
        else:
            result.append(item)

    return result


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


# -- People research ---------------------------

async def research_person(person: dict) -> list[dict]:
    """Research recent activity for a tracked person.

    person: dict with name, title, company, linkedin
    Returns list of signal dicts (same format as company signals).
    """
    name = person.get("name", "")
    title = person.get("title", "")
    company = person.get("company", "")
    linkedin = person.get("linkedin", "")
    if not name:
        return []

    today = date.today()
    cutoff = today - timedelta(days=14)

    linkedin_hint = f"\nTheir LinkedIn profile is at {linkedin}" if linkedin else ""

    data = await _call_model(
        system=f"""You are a B2B sales intelligence analyst. Today is {today.isoformat()}.
Only report activity AFTER {cutoff.isoformat()}. Return only valid JSON.""",
        prompt=f"""Research recent public activity by {name}, {title} at {company}.{linkedin_hint}

Find:
- LinkedIn posts or articles they published or shared
- Conference talks, webinars, or podcast appearances
- Blog posts or thought leadership articles
- Press quotes or media mentions
- Job changes or role updates
- Public comments on industry trends

Return a JSON array of findings (0 to 3 items). No markdown, no code fences.
[
  {{
    "company": "{company}",
    "tag": "person_activity",
    "person_name": "{name}",
    "date": "<YYYY-MM-DD if known, else empty string>",
    "headline": "<what they did or said, one specific sentence>",
    "why": "<why a salesperson should care about this>",
    "opening_line": "<natural conversation opener referencing this>",
    "sources": ["<URL if found>"],
    "risk_or_opportunity": "opportunity",
    "confidence": "<HIGH if from named source, MEDIUM if inferred>"
  }}
]

If nothing found, return []. Do NOT invent activity.""",
        provider="pplx",
    )

    content = data["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        items = _parse_json(content)
        if isinstance(items, dict):
            items = [items]
    except (json.JSONDecodeError, ValueError):
        return []

    return [i for i in items if isinstance(i, dict) and i.get("headline")]


async def generate_people_signals(people: list[dict]) -> list[dict]:
    """Research all tracked people. Returns list of signals."""
    if not people:
        return []

    all_items = []
    for i, person in enumerate(people):
        if i > 0:
            await asyncio.sleep(cfg["digest"]["pace_between_companies_sec"])
        name = person.get("name", "?")
        company = person.get("company", "?")
        print(f"  [person] {name} at {company}")
        items = await research_person(person)
        if items:
            print(f"    {len(items)} signal(s)")
            for item in items:
                item["tag"] = "Person Activity"
                item["tag_color"] = "blue"
        else:
            print(f"    nothing found")
        all_items.extend(items)

    return all_items
