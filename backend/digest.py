"""
Scoop Digest Pipeline — Solace Internal

Four-pass approach:
  1. Skip seller research (hardcoded Solace context).
  2. For each customer account, run 6 parallel queries focused on:
     champions/stakeholders, EDA/integration signals, IT initiatives,
     partner activity, recent news (2 weeks only), risks & competitive.
  3. Rank by urgency and impact, deduplicate, return top signals.

Designed for Solace colleagues tracking their customer accounts.
Signals focus on event-driven architecture, integration, messaging,
champions (IT Architects, Integration Leads, CTOs), and partner activity.

At $3/1000 Perplexity queries, thoroughness is cheap. Bad intel is expensive.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

import httpx

from config import cfg

PPLX_API_URL = "https://api.perplexity.ai/chat/completions"
PPLX_MODEL = cfg["perplexity"]["model"]

SIGNAL_CATEGORIES = {
    # People (mid-level focus)
    "leadership_change": {"tag": "People Move", "color": "red"},
    "technical_leader": {"tag": "Tech Leader", "color": "red"},
    # Technology & Architecture
    "tech_initiative": {"tag": "Tech Initiative", "color": "blue"},
    "platform_change": {"tag": "Platform Change", "color": "blue"},
    "tech_stack": {"tag": "Tech Stack", "color": "blue"},
    "architecture": {"tag": "Architecture", "color": "blue"},
    "cloud_migration": {"tag": "Cloud Migration", "color": "blue"},
    "digital_transformation": {"tag": "Transformation", "color": "blue"},
    "integration": {"tag": "Integration", "color": "blue"},
    "strategic": {"tag": "Strategic", "color": "blue"},
    # Growth
    "hiring": {"tag": "Hiring Signal", "color": "green"},
    "partnership": {"tag": "Partnership", "color": "green"},
    "partner_activity": {"tag": "Partner Activity", "color": "green"},
    "expansion": {"tag": "Expansion", "color": "green"},
    "funding": {"tag": "Funding", "color": "green"},
    # Financial
    "financial_event": {"tag": "Financial", "color": "green"},
    "earnings_language": {"tag": "Earnings", "color": "green"},
    # Champions
    "champion_change": {"tag": "Champion Update", "color": "red"},
    "stakeholder_move": {"tag": "Stakeholder Move", "color": "red"},
    # Risk
    "risk_layoffs": {"tag": "Risk: Layoffs", "color": "red"},
    "risk_reorg": {"tag": "Risk: Reorg", "color": "red"},
    "risk_churn": {"tag": "Risk: Churn", "color": "red"},
    "restructuring": {"tag": "Restructuring", "color": "red"},
    # Competitive
    "competitive": {"tag": "Competitive", "color": "amber"},
    "competitor_win": {"tag": "Competitor", "color": "amber"},
    "competitor_launch": {"tag": "Competitor", "color": "amber"},
    "acquisition": {"tag": "M&A", "color": "amber"},
    # Regulatory
    "regulatory": {"tag": "Regulation", "color": "amber"},
    "regulation": {"tag": "Regulation", "color": "amber"},
    "compliance_deadline": {"tag": "Compliance", "color": "red"},
}

ALL_TAGS = ", ".join(SIGNAL_CATEGORIES.keys())


# ── Perplexity helpers ───────────────────────

async def _pplx_query(system: str, prompt: str, max_tokens: int | None = None) -> dict:
    api_key = os.getenv("PPLX_KEY")
    if not api_key:
        raise RuntimeError("PPLX_KEY not set")

    pplx_cfg = cfg["perplexity"]
    payload = {
        "model": PPLX_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens or pplx_cfg["max_tokens"],
        "temperature": pplx_cfg["temperature"],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    max_retries = pplx_cfg["max_retries"]
    base_delay = pplx_cfg["retry_base_delay_sec"]
    for attempt in range(max_retries):
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(PPLX_API_URL, headers=headers, json=payload)
            if resp.status_code == 429:
                wait = 2 ** attempt * base_delay
                print(f"      [rate-limited] waiting {wait}s before retry...")
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
        return resp.json()

    raise RuntimeError(f"Perplexity API rate limit exceeded after {max_retries} retries")


def _parse_json(content: str) -> dict | list:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(content)


# ── Solace context (hardcoded) ────────────────

def get_solace_context() -> dict:
    """Return the Solace seller context from config.yaml (no API call needed)."""
    s = cfg["solace"]
    return {
        "company_summary": s["product"],
        "industry": "Enterprise middleware, integration, and event-driven architecture",
        "buyer_personas": s["buyer_personas"],
        "use_cases": s["use_cases"],
        "buying_triggers": s["buying_triggers"],
        "deal_killers": s.get("deal_killers", ""),
        "urgency_drivers": s.get("urgency_drivers", ""),
        "competitors": s["competitors"],
        "keywords": s["keywords"],
        "product_category": s.get("product_category", "Event-driven messaging and integration platform"),
        "industries_served": s.get("industries_served", ""),
        "partners": s["partners"],
    }


# ── Pass 2: 8 query types per company ────────

QUERY_TYPES = [
    {
        "name": "champions_stakeholders",
        "prompt": """Find the KEY PEOPLE at {company} who are champions or stakeholders for integration, middleware, and event-driven architecture decisions.
I work at Solace and need to know who my champions and stakeholders are at {company}.
Find:
- IT Architects, Enterprise Architects, Solution Architects, Integration Architects
- CTOs, VPs of Engineering, Heads of Integration, Heads of Platform
- Directors of IT, Engineering Managers for integration/middleware/messaging teams
- Anyone with "integration", "middleware", "event-driven", "messaging", "API", or "platform" in their title
- Recent hires or role changes in these positions (last 6 months)
- Their background: where they came from, what technologies they know
Include the person's NAME, their TITLE, and background if known.
IMPORTANT: Only include news/moves from the last 2 weeks. Background info can be older.
If no relevant people found, say so clearly.""",
    },
    {
        "name": "eda_integration",
        "prompt": """Research the EVENT-DRIVEN ARCHITECTURE and INTEGRATION landscape at {company}.
I work at Solace and need to understand how {company} handles integration and messaging.
Find:
- What messaging, middleware, or event broker technologies does {company} use? (Kafka, MQ, TIBCO, RabbitMQ, Solace, etc.)
- Are they doing event-driven architecture, microservices, or real-time data streaming?
- What cloud providers do they use? (AWS, Azure, GCP, hybrid/multi-cloud)
- Any integration platforms (MuleSoft, Boomi, Informatica, SAP PI/PO)?
- Job postings mentioning: event-driven, messaging, middleware, integration, Kafka, MQ, pub/sub, streaming, API gateway
- Blog posts, conference talks, or case studies about their integration architecture
- Keywords in job descriptions that match: {keywords}
IMPORTANT: Only include news/announcements from the last 2 weeks. Technical stack info can be older.
If nothing found, say so clearly.""",
    },
    {
        "name": "it_initiatives",
        "prompt": """Find current IT and technology initiatives at {company} relevant to event-driven architecture and integration.
I work at Solace and need to know what {company} is building that could use event brokers, messaging, or integration middleware.
Find:
- Cloud migration projects (hybrid cloud, multi-cloud strategies)
- Digital transformation programs involving real-time data or event-driven patterns
- Modernization from legacy middleware (TIBCO, IBM MQ, MuleSoft) to cloud-native
- IoT or edge computing initiatives that need event streaming
- AI/ML data pipeline projects needing real-time event feeds
- Microservices or API modernization programs
- Budget announcements or investment areas in integration/middleware
- Anything matching these buying triggers: {buying_triggers}
IMPORTANT: Only include information from the last 2 weeks. Skip anything older.
If nothing found, say so clearly.""",
    },
    {
        "name": "partner_activity",
        "prompt": """Research PARTNER AND SYSTEMS INTEGRATOR ACTIVITY at {company}.
I work at Solace and need to know which consulting firms and technology partners are active at {company}.
Find:
- Is Accenture, Deloitte, Capgemini, Wipro, TCS, Infosys, or Cognizant working with {company} on integration or digital transformation?
- Any recent consulting engagements, RFPs, or project awards involving integration, middleware, or event-driven architecture?
- Technology partnerships with Confluent, TIBCO, IBM, MuleSoft, Boomi, SAP, Informatica?
- Any systems integrator hiring specifically for {company} projects?
- Conference co-presentations or joint case studies between {company} and any consulting firm or tech vendor?
IMPORTANT: Only include news from the last 2 weeks. Skip anything older.
If nothing relevant found, say so clearly.""",
    },
    {
        "name": "recent_news",
        "prompt": """Find the latest news about {company} from the LAST 2 WEEKS ONLY.
I work at Solace and need conversation openers with IT architects and integration leaders at {company}.
Focus on:
- Technology announcements, platform changes, architecture decisions
- Industry news affecting {company} (regulations, market shifts, M&A)
- Hiring or restructuring in IT, engineering, or architecture teams
- Partnerships, vendor selections, or integration project announcements
- Conference appearances or thought leadership by {company} tech leaders
- Anything related to event-driven architecture, messaging, integration, real-time data, microservices, or cloud migration
STRICT RULE: Every item MUST have happened in the last 2 weeks. If nothing recent, say "No news in the last 2 weeks" -- do NOT backfill with older news.
If nothing found, say so clearly.""",
    },
    {
        "name": "risks_competitive",
        "prompt": """Find RISK SIGNALS and COMPETITIVE MOVES at {company} relevant to Solace.
I work at Solace and need to protect my accounts and spot competitive threats.
Find:
- Has {company} recently adopted or evaluated Confluent/Kafka, IBM MQ, TIBCO, RabbitMQ, AWS EventBridge, Azure Service Bus, or Google Pub/Sub?
- Job postings at {company} mentioning competitor product names (Kafka, TIBCO, IBM MQ, Confluent)
- Layoffs, hiring freezes, or budget cuts in IT/engineering at {company}
- Key departures: architects, integration leads, or engineering managers leaving
- Restructuring or org changes in technology teams
- Signals that {company} is consolidating or switching middleware/messaging vendors
- Any complaints or migration away from current integration platform
IMPORTANT: Only include events from the last 2 weeks.
If nothing concerning found, say "No risk signals detected."
If nothing found, say so clearly.""",
    },
]


# ── Enhanced signal output schema ────────────

SIGNAL_OUTPUT_INSTRUCTION = """Based on what you found, return a JSON array of signals (0 to 3 items).
Each signal must contain concrete, verified information.

Return this exact format (no markdown, no code fences):
[
  {{
    "company": "{company}",
    "tag": "<one of: {all_tags}>",
    "headline": "<one specific sentence with names, dates, and concrete details>",
    "date_mentioned": "<YYYY-MM-DD of when this event happened or was published. If you cannot determine a specific date, use empty string. Do NOT guess or invent dates.>",
    "signal_type": "<one of: event, person_move, job_posting, announcement, background. Use 'background' ONLY for tech stack or company context that is not tied to a specific recent event.>",
    "why": "<one sentence connecting this to Solace (event broker, EDA, Agent Mesh), referencing which stakeholder (IT Architect, Integration Lead, CTO, Head of Integration) would care and why this changes the deal dynamic>",
    "urgency": "<one of: IMMEDIATE, THIS_WEEK, THIS_MONTH, THIS_QUARTER>",
    "window": "<Why this timing matters. e.g., 'New CTO has 90 days to audit the stack.' or 'Compliance deadline is June 30.'>",
    "opening_line": "<A natural, non-salesy sentence the rep can use to start a conversation referencing this signal. e.g., 'I saw [name] just joined as CTO. Congrats on the hire.'>",
    "risk_or_opportunity": "<one of: opportunity, risk, both>",
    "suggested_action": "<Specific next step. Include: who to contact (IT Architect, Integration Lead, CTO, Head of Integration), what channel (email/LinkedIn/phone), and what to say.>",
    "confidence": "<HIGH if based on named sources and dates, MEDIUM if inferred from patterns, LOW if speculative>",
    "source_url": "<URL of the most relevant source for this signal, e.g. a news article, blog post, LinkedIn profile, or job posting. If no URL available, use empty string.>"
  }}
]

RULES:
- Every signal MUST have a specific name, date, or number. No vague statements.
- The "date_mentioned" MUST be a real date you found in your sources. If the source does not contain a date, leave it as empty string. NEVER fabricate a date.
- The "opening_line" must sound like a human, not a salesperson. No jargon.
- The "window" must explain WHY timing matters and WHEN it closes.
- If a signal is a RISK (layoffs, reorg, competitor adoption), mark it clearly.
- Confidence HIGH = you found a named source with a date. MEDIUM = pattern inference. LOW = speculative.
- Return an empty array [] if nothing concrete was found. Never invent signals.
- Prefer returning FEWER high-quality signals over many vague ones. One real signal with a source beats three generic ones."""


async def _run_company_query(
    query_type: dict,
    company: str,
    product: str,
    seller_context: dict,
) -> list[dict]:
    """Run one query type for one company, return 0-3 signal items."""
    ctx = {
        "company": company,
        "product": product,
        "product_category": seller_context.get("product_category", product),
        "keywords": seller_context.get("keywords", ""),
        "competitors": seller_context.get("competitors", ""),
        "buyer_personas": seller_context.get("buyer_personas", "IT Architects, Integration Leads, CTOs"),
        "use_cases": seller_context.get("use_cases", ""),
        "buying_triggers": seller_context.get("buying_triggers", ""),
        "partners": seller_context.get("partners", "Accenture, Deloitte, Capgemini"),
    }

    prompt = query_type["prompt"].format(**ctx)

    output_instruction = SIGNAL_OUTPUT_INSTRUCTION.format(
        company=company,
        product=product,
        all_tags=ALL_TAGS,
    )

    from datetime import date, timedelta
    today = date.today()
    cutoff = today - timedelta(days=cfg["digest"]["news_recency_days"])

    data = await _pplx_query(
        system=f"""You are a sales intelligence analyst helping a Solace colleague stay informed about their customer accounts.
Today's date is {today.isoformat()}.
Solace sells: PubSub+ Event Broker, Event Portal, and Agent Mesh for event-driven architecture and real-time integration.
Key use cases: {ctx['use_cases']}
Key buyer personas at customer accounts: {ctx['buyer_personas']}
Competitors: {ctx['competitors']}
Key partners: Accenture, Deloitte, Capgemini, Boomi, SAP, Informatica

CRITICAL RULES:
- Only report events, announcements, or changes from AFTER {cutoff.isoformat()}.
- Every fact must include the SPECIFIC DATE it happened (YYYY-MM-DD format).
- If you cannot find the exact date of an event, say so explicitly — do NOT guess.
- Do NOT report general company background as if it were news.
- Do NOT fabricate dates. An undated fact is better than a fake-dated one.
- If you find nothing recent, return an empty array []. That is the correct answer.
Focus on event-driven architecture, integration, messaging, middleware, and real-time data signals.
Return only valid JSON.""",

        prompt=f"""{prompt}

{output_instruction}""",
        max_tokens=700,
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
        item["sources"] = sources

    return [i for i in items if i.get("headline")]


async def _research_company(
    company: str,
    product: str,
    seller_context: dict,
) -> list[dict]:
    """Run all 8 query types for a single company in parallel."""
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


# ── Pass 3: Rank by urgency and impact ───────

async def _rank_signals(
    items: list[dict],
    product: str,
    seller_context: dict,
) -> list[dict]:
    if len(items) <= 3:
        return items

    buyer_personas = seller_context.get("buyer_personas", "decision-makers")
    signals_json = json.dumps(
        [
            {
                "i": i,
                "company": item["company"],
                "tag": item["tag"],
                "headline": item["headline"],
                "urgency": item.get("urgency", "THIS_QUARTER"),
                "risk_or_opportunity": item.get("risk_or_opportunity", "opportunity"),
                "confidence": item.get("confidence", "MEDIUM"),
            }
            for i, item in enumerate(items)
        ],
        indent=2,
    )

    data = await _pplx_query(
        system="You are a B2B sales prioritization expert for Solace (event-driven messaging). Return only valid JSON.",
        prompt=f"""You are ranking sales intelligence signals for a Solace colleague.
Solace sells: PubSub+ Event Broker, Event Portal, and Agent Mesh.
Their key stakeholders at customers are: {buyer_personas}

{signals_json}

Rank them from most to least urgent/impactful. The ranking criteria:
1. RISK signals (protecting existing accounts, competitive threats from Confluent/Kafka/TIBCO/IBM MQ) always rank above opportunities
2. Champion/stakeholder changes (new IT Architect, Integration Lead departure) rank very high
3. IMMEDIATE urgency ranks above THIS_WEEK, etc.
4. Signals with HIGH confidence rank above MEDIUM and LOW
5. Partner activity (Accenture, Deloitte) at the account is highly relevant
6. Signals where the "window" is closing soonest rank highest

Return a JSON array of the indices (the "i" field) of the top {cfg['digest']['signals_per_email']} signals, in priority order:
[0, 3, 7, ...]""",
        max_tokens=cfg["perplexity"]["ranking_max_tokens"],
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
            n = cfg["digest"]["signals_per_email"]
            return ranked[:n] if ranked else items[:n]
    except (json.JSONDecodeError, ValueError):
        pass

    return items[:cfg["digest"]["signals_per_email"]]


# ── Pass 4: Freshness & dedup review ─────────
#
# Three layers of quality control:
#   Layer 1 (programmatic): hard date filter — drop signals with dates older than the window
#   Layer 2 (programmatic): headline dedup — drop signals whose headline is >80% similar to a previous one
#   Layer 3 (LLM): semantic review — a cheap model reviews remaining signals for
#                  vague/undated content and semantic duplicates the string match missed


def _date_filter(items: list[dict], max_age_days: int) -> tuple[list[dict], int]:
    """Drop signals whose date_mentioned is outside the freshness window.

    Signals with no date_mentioned are kept (handled by LLM review later).
    Background-type signals are dropped outright unless they're risk signals.
    """
    from datetime import date, timedelta

    cutoff = date.today() - timedelta(days=max_age_days)
    kept = []
    dropped = 0

    for item in items:
        # Background info without a date is almost always stale filler
        if item.get("signal_type") == "background" and item.get("risk_or_opportunity") != "risk":
            dropped += 1
            continue

        date_str = item.get("date_mentioned", "")
        if date_str:
            try:
                event_date = date.fromisoformat(date_str)
                if event_date < cutoff:
                    dropped += 1
                    continue
            except ValueError:
                pass  # unparseable date — let LLM review decide

        kept.append(item)

    return kept, dropped


def _headline_dedup(items: list[dict], previous_headlines: list[str]) -> tuple[list[dict], int]:
    """Drop signals whose headline is very similar to a previously sent headline.

    Uses simple token overlap (Jaccard similarity) — fast and deterministic.
    """
    if not previous_headlines:
        return items, 0

    def _tokens(text: str) -> set[str]:
        return set(text.lower().split())

    prev_token_sets = [_tokens(h) for h in previous_headlines]

    kept = []
    dropped = 0
    for item in items:
        headline_tokens = _tokens(item.get("headline", ""))
        if not headline_tokens:
            kept.append(item)
            continue

        is_dup = False
        for prev_tokens in prev_token_sets:
            if not prev_tokens:
                continue
            intersection = headline_tokens & prev_tokens
            union = headline_tokens | prev_tokens
            similarity = len(intersection) / len(union) if union else 0
            if similarity > 0.5:  # 50% token overlap = likely same event
                is_dup = True
                break

        if is_dup:
            dropped += 1
        else:
            kept.append(item)

    return kept, dropped


async def _llm_quality_review(
    items: list[dict],
    previous_headlines: list[str],
) -> list[dict]:
    """Final LLM review pass — catches what programmatic filters miss.

    The cheap LLM sees each signal's headline, date, signal_type, sources,
    and the previous headlines list. It drops:
    - Signals that read like background/Wikipedia info (no recent event)
    - Signals with vague timing ("recently", "in recent months", "has been")
    - Semantic duplicates of previous headlines the string match missed
    - Signals that are clearly speculative or lack a verifiable source
    """
    if not items:
        return items

    llm_cfg = cfg["litellm"]
    api_url = os.getenv("LITELLM_URL", "")
    api_key = os.getenv("LITELLM_KEY", "")

    if not api_url or not api_key:
        print("    [llm-review] LITELLM not configured, skipping")
        return items

    signals_for_review = []
    for i, item in enumerate(items):
        signals_for_review.append({
            "i": i,
            "company": item.get("company", ""),
            "headline": item.get("headline", ""),
            "date_mentioned": item.get("date_mentioned", ""),
            "signal_type": item.get("signal_type", ""),
            "tag": item.get("tag", ""),
            "risk_or_opportunity": item.get("risk_or_opportunity", "opportunity"),
            "confidence": item.get("confidence", ""),
            "source_url": item.get("source_url", ""),
        })

    prev_section = ""
    if previous_headlines:
        prev_list = "\n".join(f"- {h}" for h in previous_headlines[:50])
        prev_section = f"""
PREVIOUSLY SENT HEADLINES (do NOT repeat these, even if reworded):
{prev_list}
"""

    today_str = __import__("datetime").date.today().isoformat()

    prompt = f"""Today is {today_str}. You are a strict quality gate for a weekly account intelligence email sent to salespeople.
Every signal that passes your review will land in someone's inbox. Bad signals destroy trust.

Review each signal and decide: KEEP or DROP.

DROP a signal if ANY of these are true:
- It has no date_mentioned AND reads like general background info ("Company X is a leader in...", "They use Kafka for...")
- The date_mentioned is empty and the headline uses vague time words: "recently", "in recent months", "has been", "continues to", "ongoing"
- The headline describes a well-known, long-standing fact rather than a recent event
- It is substantially the same event/topic as a previously sent headline (same person, same project, same announcement — even if the wording changed)
- The headline is generic enough to apply to almost any large enterprise ("investing in digital transformation", "modernizing IT infrastructure")
- It has confidence LOW and no source_url

KEEP a signal if:
- It describes a specific event with a verifiable date in the last 14 days
- It names a specific person, project, partner, or technology decision
- It is a risk signal (layoffs, competitive threat, key departure) even if the date is approximate
- It would make a salesperson say "I need to call my contact about this"
{prev_section}
SIGNALS:
{json.dumps(signals_for_review, indent=2)}

Return ONLY a JSON array of the "i" values to KEEP, in order. Example: [0, 2, 5]
If none pass, return []. No explanation, no markdown, just the array."""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{api_url.rstrip('/')}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": llm_cfg["model"],
                    "messages": [
                        {"role": "system", "content": "You are a strict quality gate. Return only valid JSON arrays. When in doubt, DROP."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": llm_cfg["max_tokens"],
                    "temperature": llm_cfg["temperature"],
                },
            )
            resp.raise_for_status()

        content = resp.json()["choices"][0]["message"]["content"]
        indices = _parse_json(content)

        if isinstance(indices, list):
            kept = [items[idx] for idx in indices if isinstance(idx, int) and 0 <= idx < len(items)]
            dropped = len(items) - len(kept)
            if dropped:
                print(f"    [llm-review] Dropped {dropped} signal(s)")
            return kept

    except Exception as e:
        print(f"    [llm-review] Failed ({e}), keeping all signals")

    return items


async def _review_freshness_and_dedup(
    items: list[dict],
    previous_headlines: list[str],
) -> list[dict]:
    """Three-layer quality gate: date filter → headline dedup → LLM review."""
    if not items:
        return items

    max_age = cfg["digest"]["news_recency_days"]

    # Layer 1: programmatic date filter
    items, dropped_date = _date_filter(items, max_age)
    if dropped_date:
        print(f"    [date-filter] Dropped {dropped_date} signal(s) older than {max_age} days or background")

    # Layer 2: programmatic headline dedup
    items, dropped_dedup = _headline_dedup(items, previous_headlines)
    if dropped_dedup:
        print(f"    [dedup] Dropped {dropped_dedup} signal(s) matching previous headlines")

    # Layer 3: LLM semantic review
    items = await _llm_quality_review(items, previous_headlines)

    return items


# ── Public API ───────────────────────────────

async def generate_digest_preview(
    companies: list[str],
    product: str,
    previous_headlines: list[str] | None = None,
) -> list[dict]:
    """Generate digest items with multi-query research for Solace colleagues."""
    # Pass 1: use hardcoded Solace context (no API call needed)
    print("  [solace context] Using hardcoded Solace seller context")
    seller_context = get_solace_context()

    # Pass 2: 6 queries per company in parallel
    all_items = []
    for i, company in enumerate(companies):
        if i > 0:
            await asyncio.sleep(cfg["digest"]["pace_between_companies_sec"])
        print(f"  [{company}] (6 queries)")
        company_items = await _research_company(company, product, seller_context)
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
    print(f"  [ranked] {len(ranked)} signals after ranking")

    # Pass 4: freshness & dedup review via cheap LLM
    print(f"  [review] Checking freshness & dedup ({len(previous_headlines or [])} previous headlines)")
    reviewed = await _review_freshness_and_dedup(ranked, previous_headlines or [])
    print(f"  [done] {len(reviewed)} final signals")

    return reviewed


async def generate_digest_for_user(user: dict) -> list[dict]:
    """Generate a full digest for a single Solace colleague."""
    companies = [c["name"] for c in user.get("companies", [])]
    if not companies:
        return []

    # Fetch previous headlines for dedup
    previous_headlines: list[str] = []
    user_id = user.get("id", "")
    if user_id:
        try:
            from db import get_previous_headlines
            lookback = cfg["litellm"]["dedup_lookback_weeks"]
            previous_headlines = await get_previous_headlines(user_id, weeks=lookback)
            if previous_headlines:
                print(f"  [dedup] Found {len(previous_headlines)} previous headlines to compare against")
        except Exception as e:
            print(f"  [dedup] Could not fetch previous headlines: {e}")

    # Always use Solace product context from config
    product = cfg["solace"]["product"]
    return await generate_digest_preview(companies, product, previous_headlines)
