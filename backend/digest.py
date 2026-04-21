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

CONTEXT: The reader is a Solace colleague who covers {company}. Solace is a people-first company that values trust, humility, and genuine human connection. The reader wants 2-3 high-confidence signals they can act on, not 8 where they filter out 5.

SOLACE PROOF POINTS BY VERTICAL (weave the relevant one into your message naturally):
- Aviation/Transport: "Solace powers FAA SWIM for real-time flight data across US airspace"
- Financial Services/Banking: "60% of the world's largest investment banks use Solace for real-time trading"
- Manufacturing/CPG: "Danone connects 100+ factories via Solace event mesh for real-time production data"
- Retail: "Major retailers use Solace to connect POS, inventory, and e-commerce in real-time"
- Telecom: "Solace processes 1.5M connected vehicles in Singapore via real-time event streaming"
- Healthcare/Pharma: "Roche uses Solace for real-time lab data integration across global sites"
- Energy/Utilities: "Solace enables real-time grid monitoring and smart meter data processing"
- Defense/Government: "NATO uses Solace for secure real-time C2 data distribution"

[
  {{
    "company": "{company}",
    "tag": "<one of: {all_tags}>",
    "date": "<YYYY-MM-DD of when the EVENT happened (not when the article was published). MUST be after {cutoff}. If you cannot determine the event date, do NOT include this signal.>",
    "headline": "<What happened, in one clear sentence with names, dates, concrete numbers. Never use em dashes.>",
    "signal_strength": "<integer 1-5. 5=crisis/RFP/funded project with deadline. 4=new leadership in buying role or major platform decision. 3=strategic partnership or M&A affecting IT. 2=earnings commentary or hiring patterns. 1=PR announcement or generic news.>",
    "persona_fit": "<integer 1-5. 5=CIO/CTO/Head of Architecture/Head of Integration (direct buyer). 4=VP Engineering/Enterprise Architect/Head of Platform. 3=CDO/Head of Data/Head of Cloud. 2=CFO/COO/business leader. 1=marketing/brand/HR or no IT relevance.>",
    "so_what": "<In 2-3 sentences, explain why this matters. Write like telling a friend over coffee. Be warm and specific to THIS event. Never generic.>",
    "contact_name": "<ALWAYS find a real person at {company}. Search for: CTO, CIO, CDO, CISO, VP Engineering, VP Architecture, Head of Integration, Head of Platform, Head of Middleware, Enterprise Architect, Chief Architect, DSI, Architecte d'Entreprise. You MUST try hard. Only empty string if truly impossible.>",
    "contact_title": "<Their title at {company}.>",
    "contact_linkedin": "<LinkedIn URL ONLY if actually found. Do NOT guess.>",
    "message": "<Warm, human, 2-sentence message for LinkedIn DM. First sentence: acknowledge the news with warmth. Second sentence: connect to Solace using a relevant proof point from the list above for {company}'s vertical. Sound like a trusted advisor sharing an insight. Example: 'Congrats on the new AI initiative. We helped Danone connect 100+ factories with event mesh for exactly this kind of real-time data flow, happy to share what worked.' Never be pushy.>",
    "risk_or_opportunity": "<opportunity | risk | both>",
    "sources": ["<source URLs>"]
  }}
]

RULES:
- Only include signals with signal_strength >= 3. Skip PR fluff and generic news.
- ALWAYS find a contact_name with persona_fit >= 3 (IT/architecture buyer).
- The "message" must include a relevant Solace proof point for {company}'s industry.
- Return [] if nothing high-quality found. That is the correct answer.
- Never use em dashes (--). Never invent signals. Quality over quantity."""


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
        company=company, all_tags=ALL_TAGS, cutoff=cutoff.isoformat(),
    )

    data = await _call_model(
        system=f"""You are a B2B sales intelligence analyst. Today is {today.isoformat()}.

STRICT DATE RULE: Only report events where the EVENT ITSELF happened after {cutoff.isoformat()}.
- The EVENT date is when something actually occurred (appointment started, deal signed, product launched, results announced).
- The ARTICLE date is when a news outlet wrote about it. These are NOT the same thing.
- Example: An article published in February 2026 about someone who took a role in December 2025 is OLD NEWS. Do not include it.
- If the event happened before {cutoff.isoformat()}, do NOT include it even if the article is recent.
- Every signal MUST include the EVENT date in YYYY-MM-DD format. If you cannot determine when the event happened, do NOT include the signal.

Do NOT report background info as news. Do NOT fabricate dates.
If nothing genuinely happened in the last 14 days, return []. That is the correct answer.
Return only valid JSON.

IMPORTANT: For every signal, also search for a senior IT/tech leader at {company} to recommend as a contact. Search for: CTO, CIO, CDO, CISO, VP Engineering, VP Architecture, Head of Integration, Head of Platform, Head of Middleware, Enterprise Architect, Chief Architect, Solution Architect, DSI (Directeur des Systemes d'Information), Architecte d'Entreprise at {company}. Include their name, title, and LinkedIn URL if found.""",
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

    # Hard date filter: STRICT — drop signals with no date or date older than cutoff
    valid = []
    dropped_no_date = 0
    dropped_old = 0
    for item in items:
        if not item.get("headline"):
            continue
        d = item.get("date", "")
        if not d:
            dropped_no_date += 1
            continue  # no date = not verifiably recent = drop
        try:
            signal_date = date.fromisoformat(d)
            if signal_date < cutoff:
                dropped_old += 1
                continue  # event happened before the window
        except ValueError:
            dropped_no_date += 1
            continue  # unparseable date = drop
        valid.append(item)

    if dropped_no_date or dropped_old:
        print(f"    [date-filter] Dropped {dropped_old} old + {dropped_no_date} undated signals")

    return valid


async def _enrich_contact(item: dict) -> dict:
    """For a signal missing a contact, run a dedicated search to find one."""
    company = item.get("company", "")
    headline = item.get("headline", "")
    tag = item.get("tag", "")
    if not company:
        return item

    try:
        data = await _call_model(
            system="You are a LinkedIn research specialist. Return only valid JSON.",
            prompt=f"""Find ONE senior IT or technology leader at {company} who would be the right person to discuss this news with:

"{headline}"

Search for real people at {company} on LinkedIn. Look for these roles:
- CTO, CIO, CDO, CISO, Chief Digital Officer, Chief Data Officer
- VP of Engineering, VP of IT, VP of Technology, VP of Architecture
- Head of Integration, Head of Platform, Head of Data, Head of Middleware
- Enterprise Architect, Chief Architect, Solution Architect, Integration Architect
- Head of Digital Transformation, Head of Cloud, Head of Infrastructure
- Directeur des Systemes d'Information (DSI), Directeur Technique
- Architecte d'Entreprise, Architecte SI, Responsable Integration

Return JSON (no markdown):
{{
  "name": "<full name of a real person you found>",
  "title": "<their current title at {company}>",
  "linkedin": "<their LinkedIn profile URL, ONLY if you actually found it, else empty string>"
}}

You MUST find someone. Search harder. Look at {company}'s leadership page, LinkedIn company page, recent press releases, conference speakers. If you cannot find the exact URL, still return the name and title.""",
            max_tokens=200,
            provider="pplx",
        )
        content = data["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        contact = json.loads(content)
        if contact.get("name"):
            item["contact_name"] = contact["name"]
            item["contact_title"] = contact.get("title", "")
            item["contact_linkedin"] = contact.get("linkedin", "")
    except Exception as e:
        pass  # keep the signal, just without a contact

    return item


async def _enrich_all_contacts(items: list[dict]) -> list[dict]:
    """Enrich signals that are missing contacts with a dedicated search."""
    tasks = []
    for item in items:
        if not item.get("contact_name"):
            tasks.append(_enrich_contact(item))
        else:
            tasks.append(asyncio.coroutine(lambda i=item: i)() if False else None)

    # Run enrichment for items missing contacts, with pacing
    for item in items:
        if not item.get("contact_name"):
            print(f"    [contact search] {item.get('company', '?')}...")
            await _enrich_contact(item)
            await asyncio.sleep(1)  # pace to avoid rate limits

    found = sum(1 for i in items if i.get("contact_name"))
    print(f"    [contacts] {found}/{len(items)} signals have contacts")
    return items


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


# -- Score-based ranking (deterministic, no LLM call) --

def _rank_signals(items: list[dict]) -> list[dict]:
    """Rank signals by score, filter by quality threshold, enforce diversity."""
    max_signals = cfg["digest"].get("max_signals_per_email", 5)
    min_strength = cfg["digest"].get("min_signal_strength", 3)
    min_persona = cfg["digest"].get("min_persona_fit", 3)
    max_risk = 2
    max_per_company = 2

    def _score(item):
        try:
            strength = int(item.get("signal_strength", 1))
        except (ValueError, TypeError):
            strength = 1
        try:
            persona = int(item.get("persona_fit", 1))
        except (ValueError, TypeError):
            persona = 1
        item["_strength"] = strength
        item["_persona"] = persona
        return strength * 2 + persona

    # Score and sort
    for item in items:
        item["_composite"] = _score(item)
    items.sort(key=lambda x: x["_composite"], reverse=True)

    # Filter by quality threshold
    qualified = [i for i in items if i["_strength"] >= min_strength and i["_persona"] >= min_persona]

    # If too few qualify, relax strength to 2
    if len(qualified) < 2:
        qualified = [i for i in items if i["_strength"] >= (min_strength - 1) and i["_persona"] >= (min_persona - 1)]

    # Diversity caps
    result = []
    company_count = {}
    risk_count = 0
    seen_headlines = set()

    for item in qualified:
        company = item.get("company", "")
        is_risk = item.get("risk_or_opportunity") in ("risk", "both")
        headline_key = item.get("headline", "")[:60]

        # Dedup within batch
        if headline_key in seen_headlines:
            continue
        # Max per company
        if company_count.get(company, 0) >= max_per_company:
            continue
        # Max risk
        if is_risk and risk_count >= max_risk:
            continue

        seen_headlines.add(headline_key)
        company_count[company] = company_count.get(company, 0) + 1
        if is_risk:
            risk_count += 1
        result.append(item)

        if len(result) >= max_signals:
            break

    # Clean up internal scoring fields
    for item in result:
        item.pop("_composite", None)
        item.pop("_strength", None)
        item.pop("_persona", None)

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
    ranked = _rank_signals(all_items)
    print(f"  [ranked] {len(ranked)} signals")

    # Enrich signals missing contacts with dedicated search
    ranked = await _enrich_all_contacts(ranked)

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
