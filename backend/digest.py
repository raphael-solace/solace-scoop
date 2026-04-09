"""
Scoop Digest Pipeline

Four-pass approach:
  1. Deep research on the seller (product, buyers, triggers, deal killers, urgency drivers).
  2. Website intel: understand what each target company actually does, their industry, and tech stack.
  3. For each target account, run 8 parallel queries tuned to mid-level sellers:
     website/tech intel, people (technical leaders, not C-suite), IT initiatives,
     hiring velocity, partnerships, financial events, risk signals, competitive moves.
  4. Rank by urgency and impact, deduplicate, return top signals.

Designed for mid-level B2B salespeople who talk to Directors, VPs, and technical leaders
-- not the CEO or board. Signals focus on technology, architecture, and IT operations.

At $3/1000 Perplexity queries, thoroughness is cheap. Bad intel is expensive.
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
    "expansion": {"tag": "Expansion", "color": "green"},
    "funding": {"tag": "Funding", "color": "green"},
    # Financial
    "financial_event": {"tag": "Financial", "color": "green"},
    "earnings_language": {"tag": "Earnings", "color": "green"},
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

async def _pplx_query(system: str, prompt: str, max_tokens: int = 700) -> dict:
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


# ── Pass 1: Deep seller research ─────────────

async def research_seller(product: str) -> dict:
    data = await _pplx_query(
        system="You are a B2B sales intelligence analyst. Return only valid JSON.",
        prompt=f"""Research "{product}" thoroughly as a B2B product/company.
I need to understand this deeply for sales intelligence purposes.
IMPORTANT: The salesperson using this tool is a mid-level rep who talks to Directors, VPs, and technical leaders -- NOT the CEO or board. Focus the buyer personas on technical decision-makers.

Respond in this exact JSON format (no markdown, no code fences):
{{
    "company_summary": "<2-3 sentences on what this company/product does>",
    "industry": "<the seller's primary industry>",
    "buyer_personas": "<comma-separated list of 5-8 MID-LEVEL job titles who evaluate and buy this product. Focus on Directors, VPs, Heads of departments, Enterprise Architects, Solution Architects, Engineering Managers, Platform Leads -- NOT CEO, CTO, CFO>",
    "use_cases": "<the top 5 technical problems this product solves, comma-separated>",
    "buying_triggers": "<7-10 specific technical/operational events that would create a sales opportunity, e.g. 'cloud migration project started', 'legacy middleware end-of-life', 'real-time data initiative launched', comma-separated>",
    "deal_killers": "<5-7 events or situations that would KILL a deal or indicate the prospect is NOT a buyer. e.g., 'just signed a 3-year contract with competitor X', 'hiring freeze in IT department', 'recently failed implementation of similar tool'>",
    "urgency_drivers": "<What creates URGENCY to buy NOW vs. next quarter? Compliance deadlines, technology end-of-life, migration timelines, competitive pressure, project deadlines, etc.>",
    "competitors": "<top 3-5 competitors, comma-separated>",
    "keywords": "<10 technical keywords and phrases a prospect would use when they need this product, comma-separated. Include technology terms, architecture patterns, and integration concepts.>",
    "product_category": "<one short phrase describing the category, e.g. 'event-driven messaging', 'observability platform', 'API management'>",
    "industries_served": "<top 5 industries where this product is most commonly sold, comma-separated>"
}}""",
        max_tokens=700,
    )
    content = data["choices"][0]["message"]["content"]
    return _parse_json(content)


# ── Pass 2: 8 query types per company ────────

QUERY_TYPES = [
    {
        "name": "website_intel",
        "prompt": """Research {company} website and online presence to understand what this company actually does.
Look for:
- What products or services does {company} offer?
- What industry are they in? (IT, finance, healthcare, manufacturing, retail, logistics, etc.)
- What technology stack, platforms, or technical architecture do they use or sell?
- What are their main use cases and who are THEIR customers?
- What technical challenges are they likely working on? (cloud migration, API modernization, real-time data, microservices, event-driven architecture, etc.)
- Company size, headquarters, and key markets
- Blog posts, case studies, or technical documentation that reveal their technology direction

This context is critical -- we need to understand {company}'s business so other queries can find RELEVANT signals for selling {product_category} to them.
If nothing found, say so clearly.""",
    },
    {
        "name": "people_moves",
        "prompt": """Find recent role changes, appointments, departures, or promotions
at {company} in the last 30 days. Focus on MID-LEVEL TECHNICAL LEADERS, not the CEO or board:
- Directors, VPs, and Heads of IT, Engineering, Architecture, Integration, Data, or Platform teams
- Enterprise Architects, Solution Architects, Integration Leads
- Engineering Managers, Platform Engineering Leads, DevOps/SRE leaders
- Roles like: {buyer_personas}

Include the person's NAME, their new TITLE, and where they came from if known.
Skip C-suite roles unless they directly manage the department that buys {product_category}.
A new VP of Engineering or Director of Platform is more relevant than a new CEO.
If no relevant people moves found, say so clearly.""",
    },
    {
        "name": "tech_stack_signals",
        "prompt": """Research the TECHNOLOGY STACK and ARCHITECTURE decisions at {company}.
Look for:
- What platforms, middleware, cloud providers, or integration tools does {company} use?
- Job postings mentioning specific technologies (Kafka, MQ, API gateways, microservices, event-driven, Kubernetes, etc.)
- Blog posts, conference talks, or case studies by {company} employees about their tech stack
- Open-source contributions or GitHub repositories from {company}
- Technology partnerships or certified integrations
- Architecture decisions or migration projects mentioned publicly
- Keywords in job descriptions that match: {keywords}

The insight we need: What is {company}'s current technology landscape, and where does {product_category} fit or compete?
If nothing found, say so clearly.""",
    },
    {
        "name": "it_initiatives",
        "prompt": """Find recent IT and technology initiatives at {company} in the last 30 days.
Look for:
- Digital transformation programs, cloud migration projects, modernization efforts
- New platform builds, architecture overhauls, API strategy initiatives
- Integration projects, data pipeline work, real-time processing initiatives
- Programs related to: {use_cases}
- Budget increases for IT, engineering, or platform teams
- RFPs, vendor evaluations, or procurement processes for technology
- Anything matching these buying triggers: {buying_triggers}

Include specific details: project scope, technologies mentioned, timeline.
Focus on operational and technical initiatives that a Director of IT or VP of Engineering would own.
If nothing found, say so clearly.""",
    },
    {
        "name": "hiring_velocity",
        "prompt": """Analyze the HIRING PATTERNS at {company}. Not just individual job postings, but the VOLUME and VELOCITY of hiring.
Look for:
- Total number of open roles in IT, engineering, architecture, integration, data, platform, DevOps
- CLUSTERS of similar roles (e.g., "12 data engineers posted in 2 weeks" = building something big)
- New team formation signals (e.g., first-ever "Head of Platform Engineering" = new department)
- Seniority patterns (hiring a VP before ICs = early stage; hiring 20 ICs = scaling)
- Keywords in job descriptions that match: {keywords}
- Contractor/consultant postings (often precede major projects)

The insight we need is: WHAT IS {company} BUILDING and HOW FAST?

A single job posting is noise. A pattern of 10+ related postings in the same month is a signal.
Also note: hiring freezes or sudden job posting removals are RISK signals.
If nothing found beyond normal hiring activity, say so clearly.""",
    },
    {
        "name": "partnerships_vendors",
        "prompt": """Find recent technology partnerships, vendor selections, or platform decisions
at {company} in the last 30 days relevant to someone selling {product_category}. Look for:
- New technology vendor announcements (cloud, middleware, integration, data platforms)
- Strategic technology partnerships that change {company}'s architecture
- Competitor deployments (competitors include: {competitors})
- Conference presentations or case studies where {company} discussed their technology choices
- Integration partner announcements or marketplace listings

Include specific vendor names, technologies, and details.
If nothing relevant found, say so clearly.""",
    },
    {
        "name": "risk_signals",
        "prompt": """Find recent NEGATIVE or WARNING signals at {company} in the last 60 days.
This is about protecting existing deals and spotting churn risk.
Look for:
- Layoffs, hiring freezes, or headcount reductions -- especially in IT/engineering
- Restructuring, department mergers, or org chart changes in technical teams
- Key technical leader departures (Directors, VPs of Engineering, Architects)
- Budget cuts or "cost optimization" language targeting IT spend
- Office closures or team consolidation
- Failed technology projects, outages, or public technical issues
- Customer complaints about {company}'s own product reliability

Be specific about the SCOPE of the negative signal. "Laid off 50 in engineering"
is very different from "laid off 50 in marketing."

If nothing concerning found, explicitly state "No risk signals detected" --
that itself is useful information for a sales rep.""",
    },
    {
        "name": "competitive_moves",
        "prompt": """Find recent competitive intelligence relevant to selling {product_category} to {company}.
Look for:
- Has {company} recently adopted, evaluated, or mentioned any of these competitors: {competitors}?
- Job postings at {company} that mention competitor product names or related technologies
- Conference talks or blog posts by {company} employees about tools/platforms they use in the {product_category} space
- Case studies or testimonials where {company} is featured as a customer of a competitor
- Architecture decisions that favor or disfavor {product_category}
- Any public statements about switching, replacing, or consolidating vendors

IMPORTANT: Also look for signals that {company} is UNHAPPY with their current
solution. Migration projects, "replacing legacy systems", hiring for skills
associated with switching platforms -- all gold for a mid-level sales rep.
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
    "why": "<one sentence connecting this to selling {product}, referencing which mid-level technical leader (Director, VP, Architect) would care and why this changes the deal dynamic>",
    "urgency": "<one of: IMMEDIATE, THIS_WEEK, THIS_MONTH, THIS_QUARTER>",
    "window": "<Why this timing matters. e.g., 'New CTO has 90 days to audit the stack.' or 'Compliance deadline is June 30.'>",
    "opening_line": "<A natural, non-salesy sentence the rep can use to start a conversation referencing this signal. e.g., 'I saw [name] just joined as CTO. Congrats on the hire.'>",
    "risk_or_opportunity": "<one of: opportunity, risk, both>",
    "suggested_action": "<Specific next step. Include: who to contact (mid-level title like Director/VP/Architect, NOT CEO/CTO), what channel (email/LinkedIn/phone), and what to say.>",
    "confidence": "<HIGH if based on named sources and dates, MEDIUM if inferred from patterns, LOW if speculative>"
  }}
]

RULES:
- Every signal MUST have a specific name, date, or number. No vague statements.
- The "opening_line" must sound like a human, not a salesperson. No jargon.
- The "window" must explain WHY timing matters and WHEN it closes.
- If a signal is a RISK (layoffs, reorg, competitor adoption), mark it clearly.
- Confidence HIGH = you found a named source. MEDIUM = pattern inference. LOW = speculation.
- Return an empty array [] if nothing concrete was found. Never invent signals."""


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
        "buyer_personas": seller_context.get("buyer_personas", "decision-makers"),
        "use_cases": seller_context.get("use_cases", ""),
        "buying_triggers": seller_context.get("buying_triggers", ""),
    }

    prompt = query_type["prompt"].format(**ctx)

    output_instruction = SIGNAL_OUTPUT_INSTRUCTION.format(
        company=company,
        product=product,
        all_tags=ALL_TAGS,
    )

    data = await _pplx_query(
        system=f"""You are a B2B sales intelligence analyst helping a mid-level salesperson.
The salesperson sells: {product} ({ctx['product_category']})
Their product solves: {ctx['use_cases']}
Their buyers are mid-level technical leaders: {ctx['buyer_personas']}
NOTE: The salesperson does NOT talk to the CEO/CTO/Board. They sell to Directors, VPs, Architects, and Engineering Managers.

Extract concrete, specific facts. Include names, titles, dates, and numbers.
Focus on technology, architecture, IT operations, and integration signals.
Do not speculate or invent information. If you can't find anything, say so.
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
        system="You are a B2B sales prioritization expert. Return only valid JSON.",
        prompt=f"""You are ranking sales intelligence signals by urgency and impact.

Given these signals for a salesperson who sells {product}:
Their buyers are: {buyer_personas}

{signals_json}

Rank them from most to least urgent/impactful. The ranking criteria:
1. RISK signals (protecting existing revenue) always rank above opportunities
2. IMMEDIATE urgency ranks above THIS_WEEK, etc.
3. Signals with HIGH confidence rank above MEDIUM and LOW
4. Signals that affect the PRIMARY buyer persona rank above secondary
5. Signals where the "window" is closing soonest rank highest

Return a JSON array of the indices (the "i" field) of the top 5 signals, in priority order:
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
            return ranked[:5] if ranked else items[:5]
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
    print(f"    Category: {seller_context.get('product_category', '')}")
    print(f"    Buyers: {seller_context.get('buyer_personas', '')[:120]}")
    print(f"    Triggers: {seller_context.get('buying_triggers', '')[:120]}")
    print(f"    Deal killers: {seller_context.get('deal_killers', '')[:120]}")
    print(f"    Urgency: {seller_context.get('urgency_drivers', '')[:120]}")
    print(f"    Competitors: {seller_context.get('competitors', '')[:120]}")

    # Pass 2: 8 queries per company in parallel
    all_items = []
    for company in companies:
        print(f"  [{company}] (8 queries)")
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
    print(f"  [done] {len(ranked)} final signals")

    return ranked


async def generate_digest_for_user(user: dict) -> list[dict]:
    """Generate a full digest for a single user."""
    companies = [c["name"] for c in user.get("companies", [])]
    if not companies:
        return []
    product = user.get("product", "our product")
    return await generate_digest_preview(companies, product)
