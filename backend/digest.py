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
from collections import Counter
import json
import os
import random
import re
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from config import cfg

PPLX_API_URL = "https://api.perplexity.ai/chat/completions"

_PROVIDER_METRICS: dict[str, dict[str, int]] = {}


def reset_provider_metrics() -> None:
    """Reset in-process provider telemetry at the start of an operational run."""
    _PROVIDER_METRICS.clear()


def _record_provider_metric(route: str, metric: str) -> None:
    bucket = _PROVIDER_METRICS.setdefault(route, {})
    bucket[metric] = bucket.get(metric, 0) + 1


def get_provider_metrics() -> dict[str, dict[str, int]]:
    """Return a copy suitable for readiness reporting."""
    return {route: dict(values) for route, values in _PROVIDER_METRICS.items()}


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
                    result = await aicore.chat_completion(
                        deployment_id=deployment_id,
                        model=model,
                        messages=messages,
                        max_tokens=tokens,
                        temperature=temp,
                    )
                    _record_provider_metric(f"aicore_{provider}", "successes")
                    return result
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    retryable = status == 429 or status >= 500
                    _record_provider_metric(f"aicore_{provider}", f"http_{status}")
                    if retryable and attempt < max_retries - 1:
                        wait = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        if status == 429:
                            try:
                                retry_after = float(e.response.headers.get("Retry-After", 0))
                            except ValueError:
                                retry_after = 0
                            wait = max(wait, 30 * (attempt + 1), min(retry_after, 120))
                        print(f"      [ai-core HTTP {status}] retrying in {wait:.0f}s...")
                        await asyncio.sleep(wait)
                        continue
                    print(f"      [ai-core HTTP {status}] falling back")
                    break
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    _record_provider_metric(f"aicore_{provider}", "network_errors")
                    if attempt < max_retries - 1:
                        wait = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        print(f"      [ai-core {type(e).__name__}] retrying in {wait:.0f}s...")
                        await asyncio.sleep(wait)
                        continue
                    print(f"      [ai-core {type(e).__name__}] falling back")
                    break
                except Exception as e:
                    _record_provider_metric(f"aicore_{provider}", "errors")
                    print(f"      [ai-core error] {type(e).__name__}, falling back")
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
                    _record_provider_metric("direct_pplx", "successes")
                    return resp.json()
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    _record_provider_metric("direct_pplx", f"http_{status}")
                    if status >= 500 and attempt < max_retries - 1:
                        wait = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        print(f"      [pplx HTTP {status}] retrying in {wait:.0f}s...")
                        await asyncio.sleep(wait)
                        continue
                    raise
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    _record_provider_metric("direct_pplx", "network_errors")
                    if attempt < max_retries - 1:
                        wait = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        print(f"      [pplx {type(e).__name__}] retrying in {wait:.0f}s...")
                        await asyncio.sleep(wait)
                        continue
                    raise
                except Exception as e:
                    _record_provider_metric("direct_pplx", "errors")
                    print(f"      [pplx error] {type(e).__name__}")
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
            _record_provider_metric("litellm_gpt", "successes")
            return resp.json()

    _record_provider_metric(provider, "unavailable")
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

def _get_recency_days() -> int:
    return cfg["digest"].get("news_recency_days", 14)


# Absolute floor: no signal older than this may EVER ship, regardless of query
# window or retry path. Keep this aligned with the weekly freshness promise.
MAX_SIGNAL_AGE_DAYS = 14

# Accepted date formats from the model, in priority order. Anything that does
# not parse to a real date is treated as UNDATED and dropped.
_DATE_FORMATS = (
    "%Y-%m-%d",      # 2026-07-03  (required format)
    "%b %d, %Y",     # Oct 13, 2025
    "%B %d, %Y",     # October 13, 2025
    "%d %b %Y",      # 13 Oct 2025
    "%d %B %Y",      # 13 October 2025
    "%m/%d/%Y",      # 07/03/2026
    "%Y/%m/%d",      # 2026/07/03
)


def _parse_signal_date(raw) -> Optional[date]:
    """Parse a signal's date field leniently. Returns None if undated or
    unparseable (a YYYY string alone is NOT a usable date and returns None)."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    # ISO first (fast path, also handles 2026-07-03T09:37:42+00:00)
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _is_fresh_enough(item: dict, cutoff: date, today: Optional[date] = None) -> bool:
    """Single source of truth for freshness. A signal is fresh ONLY if it has a
    parseable date that is (a) not before the query cutoff, (b) within the
    absolute MAX_SIGNAL_AGE_DAYS floor, and (c) not in the future. Undated or
    unparseable signals are NOT fresh — they are dropped. This is a hard gate:
    an old event with a missing/garbled date must never ship."""
    today = today or date.today()
    d = _parse_signal_date(item.get("date", ""))
    if d is None:
        return False
    # normalize the stored date to canonical ISO so downstream ranking/age is correct
    item["date"] = d.isoformat()
    if d > today:
        return False  # future-dated → fabricated, drop
    if d < cutoff:
        return False
    if (today - d).days > MAX_SIGNAL_AGE_DAYS:
        return False
    return True


QUERY_TYPES = [
    {
        "name": "people_moves",
        "prompt": """Find ANY personnel changes at {company} in the LAST {days} DAYS.
Include ALL levels: C-suite, SVPs, VPs, Directors, Heads of departments, Senior Managers, Chief Architects, CTO, CIO, CDO, CISO.
Also include: board appointments, advisory roles, internal promotions, departures, retirements, leaves.
Include the person's NAME, new TITLE, previous role, and DATE.
Search in English AND the company's local language (Italian, Spanish, French, German, etc.).
Even small appointments matter — a new Head of Integration or Director of Architecture is very relevant.""",
    },
    {
        "name": "business_news",
        "prompt": """Find ALL recent business news about {company} in the LAST {days} DAYS, no matter how small.
Include: strategic announcements, M&A activity, contracts won or lost, product launches, market expansions, earnings updates, CEO/CFO statements, organizational changes, investor presentations, AGM outcomes, dividend announcements, sustainability targets, ESG reports.
Also include: press releases, corporate blog posts, investor day presentations, LinkedIn announcements by executives.
Search in English AND the company's local language. Even minor operational news is useful for a salesperson.""",
    },
    {
        "name": "technology_initiatives",
        "prompt": """Find ANY technology or IT activity at {company} in the LAST {days} DAYS.
Include: cloud migration, digital transformation, AI/ML projects, platform modernization, technology partnerships, IT investments, data strategy, cybersecurity initiatives, automation projects, IoT deployments, app modernization.
Also look for: event-driven architecture, middleware changes, real-time data, microservices, integration projects, API strategy, Kafka/messaging mentions, DevOps/platform engineering.
Also check: job postings for architects or integration roles, tech conference talks by their staff, GitHub activity, tech blog posts.
Search broadly in English and the company's local language.""",
    },
    {
        "name": "partnerships_deals",
        "prompt": """Find ANY partnerships, vendor selections, consulting engagements, or deals involving {company} in the LAST {days} DAYS.
Include: technology vendor selections, SI engagements (Accenture, Deloitte, Capgemini, TCS, Wipro, Infosys, etc.), strategic alliances, joint ventures, supply chain partnerships, research collaborations, university partnerships, startup investments.
Also look for: RFPs, tender announcements, procurement notices, vendor shortlists.
Search in English AND the company's local language.""",
    },
    {
        "name": "financial_signals",
        "prompt": """Find financial news about {company} in the LAST {days} DAYS.
Include: quarterly/annual results, capex/opex guidance, investment announcements, cost programs, budget changes, fundraising, debt issuance, credit rating changes, analyst upgrades/downgrades, share buybacks, dividend changes, profit warnings.
Also include: management commentary from earnings calls about technology spending, digital investment plans, operational efficiency programs.
Even routine financial news is useful as conversation context.""",
    },
    {
        "name": "risk_signals",
        "prompt": """Find ANY warning signals or disruptions at {company} in the LAST {days} DAYS.
Include: layoffs (any department), restructuring, reorganizations, executive departures, profit warnings, regulatory fines, cybersecurity incidents, supply chain problems, labor disputes, litigation, product recalls, reputational issues.
Also: delays in major projects, missed deadlines, leadership vacuum, activist investor pressure.
Be specific about scope and impact.""",
    },
    {
        "name": "competitive_intel",
        "prompt": """Find competitive intelligence about {company} in the LAST {days} DAYS relevant to enterprise software, integration, and messaging.
Has {company} selected, evaluated, or expanded use of: Kafka, Confluent, TIBCO, IBM MQ, MuleSoft, AWS EventBridge, Azure Service Bus, Google Pub/Sub, RabbitMQ, or other messaging/integration platforms?
Also look for: job postings mentioning competitor technologies, conference talks about their tech stack, migration projects, vendor evaluations, RFI/RFP for middleware or integration platforms.
Check job boards and LinkedIn for relevant hiring signals.""",
    },
    {
        "name": "linkedin_social",
        "prompt": """Find recent LinkedIn and social activity about {company} in the LAST {days} DAYS.
Search LinkedIn specifically:
- Posts from {company}'s official LinkedIn company page (product news, hiring, milestones, event participation).
- Posts, articles, or comments by {company} EXECUTIVES and senior technical leaders (CIO, CTO, CDO, Chief Architect, VP Engineering, Head of Integration/Platform/Data).
- Employees announcing new roles, projects, launches, certifications, or "we're hiring" for architecture/integration/platform/data roles.
- LinkedIn posts reacting to {company} announcements (partners, analysts, customers).
Also check X/Twitter, YouTube (conference talks, webinars), and the company's own blog/newsroom.
For each item, capture the person's NAME and TITLE if a specific individual posted it, and the EVENT date.
Only include posts genuinely from the last {days} days — not older posts resurfaced.""",
    },
    {
        "name": "industry_regulatory",
        "prompt": """Find industry or regulatory developments affecting {company} in the LAST {days} DAYS.
Include: new regulations, compliance deadlines, industry trends, government policy changes, trade developments, sustainability mandates, data privacy rules (GDPR enforcement, NIS2, DORA), AI regulation, industry association announcements.
Also: competitor announcements in the same sector, market shifts, supply chain regulations, tariffs, sanctions.
Focus on developments that could affect their technology decisions or create urgency for investment.""",
    },
    {
        "name": "procurement_contracts",
        "prompt": """Search official procurement, tender, award, and contract sources for {company} in the LAST {days} DAYS.
Look for government and enterprise tenders, contract awards, framework agreements, supplier selections, RFP/RFI notices, procurement portals, public-sector disclosures, and regulatory filings.
Search USAspending/SAM.gov, TED and national procurement portals where relevant, plus local-language sources.
Include both contracts won by {company} and procurements initiated by {company}. Capture amount, counterparty, scope, deadline, and award/notice date when available.""",
    },
    {
        "name": "engineering_hiring",
        "prompt": """Search deeply for engineering, architecture, integration, cloud, data, AI, cybersecurity, and platform hiring at {company} in the LAST {days} DAYS.
Use the official careers site and public ATS boards such as Greenhouse, Lever, Ashby, and SmartRecruiters, plus named job announcements.
Find new teams, clusters of related roles, senior appointments, technology keywords, locations, and project clues. Treat a newly published role or clearly new hiring campaign as a dated signal; do not report longstanding undated vacancies.""",
    },
    {
        "name": "regional_trade_news",
        "prompt": """Search regional, industry-specific, and local-language sources for developments involving {company} in the LAST {days} DAYS that broad English-language news may miss.
Include trade publications, local business journals, subsidiary newsrooms, regulator notices, conference agendas, association announcements, regional expansions, plant/site activity, customer wins, awards, and executive interviews.
Translate the key fact into English while preserving names, numbers, event date, and the specific source URL.""",
    },
]

SIGNAL_OUTPUT_INSTRUCTION = """Return a JSON array of signals (0 to 7 items per query). No markdown, no code fences.

CONTEXT: The reader is a Solace colleague who covers {company}. Solace values trust, humility, and genuine human connection. The reader wants actionable signals they can use in conversations.
IMPORTANT: Include ALL relevant findings you can find. Even small events (a mid-level hire, a minor partnership, a conference talk) are useful. We rank and filter downstream — your job is to FIND things, not filter them.

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
    "date": "<EXACT YYYY-MM-DD of when the EVENT happened (not when the article was published). MUST be on or after {cutoff} and not in the future. Month-name or partial dates are INVALID. If you cannot determine a specific recent event date, do NOT include this signal.>",
    "headline": "<What happened, in one clear sentence with names, dates, concrete numbers. Never use em dashes.>",
    "signal_strength": "<integer 1-5. 5=crisis/RFP/funded project with deadline. 4=new leadership in buying role or major platform decision. 3=strategic partnership or M&A affecting IT. 2=earnings commentary or hiring patterns. 1=PR announcement or generic news.>",
    "persona_fit": "<integer 1-5. 5=CIO/CTO/Head of Architecture/Head of Integration (direct buyer). 4=VP Engineering/Enterprise Architect/Head of Platform. 3=CDO/Head of Data/Head of Cloud. 2=CFO/COO/business leader. 1=marketing/brand/HR or no IT relevance.>",
    "so_what": "<In 2-3 sentences, explain why this matters. Write like telling a friend over coffee. Be warm and specific to THIS event. Never generic.>",
    "contact_name": "<If the news article NAMES a specific person at {company} (appointed, promoted, quoted, mentioned), use them. Otherwise leave empty. NEVER invent or guess a name. An empty contact is better than a wrong one.>",
    "contact_title": "<Their title at {company}.>",
    "contact_linkedin": "<LinkedIn URL ONLY if actually found. Do NOT guess.>",
    "message": "<Warm, human, 2-sentence message for LinkedIn DM. First sentence: acknowledge the news with warmth. Second sentence: connect to Solace using a relevant proof point from the list above for {company}'s vertical. Sound like a trusted advisor sharing an insight. Example: 'Congrats on the new AI initiative. We helped Danone connect 100+ factories with event mesh for exactly this kind of real-time data flow, happy to share what worked.' Never be pushy.>",
    "risk_or_opportunity": "<opportunity | risk | both>",
    "sources": ["<source URLs>"]
  }}
]

RULES:
- Include signals of ALL strength levels. Score them honestly. We rank downstream.
- Try to find a contact_name when possible, but do not skip a signal just because you cannot find one.
- The "message" must include a relevant Solace proof point for {company}'s industry.
- Return [] ONLY if genuinely nothing happened. Err on the side of including more, not less.
- Never use em dashes (--). Never invent signals.
- SOURCES: each URL must point to the SPECIFIC article or press release about THIS event, not a homepage, section index, tag/keyword page, or search-results page. Reject links like "site.com/news", "site.com/keywords/x", "newsnow.com/...", or a bare company press-release index. If you only have a generic listing URL, omit that signal's source rather than including a non-specific link. Prefer reputable primary sources (the company's own release, a named news outlet's article) over aggregators and social video."""


async def _run_company_query(
    query_type: dict,
    company: str,
    seller_context: dict,
    days_override: int | None = None,
) -> list[dict]:
    today = date.today()
    days = days_override or query_type.get("days") or _get_recency_days()
    cutoff = today - timedelta(days=days)
    ctx = {
        "company": company,
        "days": days,
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

    system = f"""You are a B2B sales intelligence analyst. Today is {today.isoformat()}.

DATE RULES (STRICT — violations cause the signal to be discarded):
- Only report events that happened RECENTLY (on or after {cutoff.isoformat()}). Today is {today.isoformat()}.
- Every signal MUST include a "date" field in EXACT YYYY-MM-DD format (e.g. "2026-07-03"). No other format is accepted. No month-name or partial dates.
- Use the EVENT date (when it actually happened), not the article publication date.
- If you cannot determine a specific event date on or after {cutoff.isoformat()}, DO NOT include the signal. An undated signal is worse than no signal.
- Do NOT include old events (outages, launches, deals from previous years) even if a recent article mentions, recaps, or links to them. The EVENT itself must be recent.
- Do NOT fabricate, guess, or approximate dates. Do NOT output a future date.
- If the article is a retrospective, anniversary, "on this day", or historical reference, SKIP it entirely.

Return only valid JSON. Search DEEPLY and WIDELY across source types, not just top news:
- News + trade/industry press (including regional and non-English outlets)
- LinkedIn: the company page, executive posts, and employee posts/role changes
- Company newsroom, blog, and investor-relations pages
- Job boards / careers pages (hiring signals for architecture, integration, platform, data roles)
- Conference talks, webinars, YouTube, podcasts, and GitHub/tech-community activity
Prefer the most SPECIFIC primary source for each event. Include non-English and non-US sources.
If nothing genuinely happened recently, return []. That is the correct answer.

IMPORTANT: If the news article NAMES a real person at {company} (appointed, quoted, mentioned), include them as contact_name. If nobody is explicitly named in your sources, leave contact_name empty. NEVER guess or invent names. Wrong contacts destroy trust with the salesperson."""
    user_prompt = f"{prompt}\n\n{output_instruction}"

    items = None
    for parse_attempt in range(2):
        data = await _call_model(system=system, prompt=user_prompt, provider="pplx")
        try:
            content = data["choices"][0]["message"]["content"]
            items = _parse_json(content)
            break
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError):
            _record_provider_metric("research_parse", "errors")
            if parse_attempt == 0:
                print("      [parse] malformed model JSON; retrying once")
    if items is None:
        raise RuntimeError("Model returned malformed JSON after retry")

    if not isinstance(items, list):
        items = [items] if isinstance(items, dict) and items.get("headline") else []

    items = [item for item in items if isinstance(item, dict)]
    for item in items:
        # Require item-specific sources. Copying all response citations onto every
        # item can attach unrelated articles to unsupported claims.
        _clean_sources(item)

    # HARD DATE + SOURCE FILTER: a signal ships ONLY if it has a parseable date within the
    # window and the absolute freshness floor. Undated / unparseable / stale /
    # future-dated signals are DROPPED (not kept). This is what prevents an old
    # event (e.g. a years-old outage) from shipping with a missing or garbled date.
    valid = []
    dropped_old = 0
    dropped_undated = 0
    for item in items:
        if not item.get("headline"):
            continue
        if not item.get("sources"):
            print(f"    [source-filter] Dropped unsourced signal: {item.get('headline', '')[:60]}")
            continue
        if _is_fresh_enough(item, cutoff, today):
            valid.append(item)
        elif _parse_signal_date(item.get("date", "")) is None:
            dropped_undated += 1
        else:
            dropped_old += 1

    if dropped_old or dropped_undated:
        print(f"    [date-filter] Dropped {dropped_old} old + {dropped_undated} undated/unparseable signal(s)")

    return valid


async def _enrich_contact(item: dict) -> dict:
    """For a signal missing a contact, run a dedicated search to find one.

    IMPORTANT: Only returns contacts that are verifiably real (mentioned in
    a source with a URL). Never fabricates names.
    """
    company = item.get("company", "")
    headline = item.get("headline", "")
    if not company:
        return item

    try:
        data = await _call_model(
            system="You are a research analyst. Return only valid JSON. NEVER invent or fabricate names.",
            prompt=f"""Find the CIO, CTO, or Head of IT at {company}.

CRITICAL RULES:
- Only return a person if you found them in a VERIFIABLE source (company website, press release, news article, annual report).
- Include the source URL where you found this person.
- If you CANNOT find a verifiable person, return {{"name": "", "title": "", "linkedin": "", "source": ""}}
- NEVER guess or invent a name. An empty result is better than a wrong name.
- Do NOT fabricate LinkedIn URLs. Only include a LinkedIn URL if you found the actual profile page.

Return JSON (no markdown):
{{
  "name": "<full name ONLY if verifiably found, else empty string>",
  "title": "<their title at {company}, else empty string>",
  "linkedin": "<actual LinkedIn profile URL if found, else empty string>",
  "source": "<URL where you verified this person's role, else empty string>"
}}""",
            max_tokens=200,
            provider="pplx",
        )
        content = data["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        contact = json.loads(content)
        # Only use if name AND source are provided (verified)
        if contact.get("name") and contact.get("source"):
            item["contact_name"] = contact["name"]
            item["contact_title"] = contact.get("title", "")
            item["contact_linkedin"] = contact.get("linkedin", "")
    except Exception as e:
        pass  # keep the signal, just without a contact

    return item


async def _enrich_all_contacts(items: list[dict]) -> list[dict]:
    """Enrich signals that are missing contacts with a dedicated search."""
    for item in items:
        if not item.get("contact_name"):
            print(f"    [contact search] {item.get('company', '?')}...")
            await _enrich_contact(item)
            await asyncio.sleep(1)  # pace to avoid rate limits

    found = sum(1 for i in items if i.get("contact_name"))
    print(f"    [contacts] {found}/{len(items)} signals have contacts")
    return items


async def _research_company_with_coverage(
    company: str,
    seller_context: dict,
    days_override: int | None = None,
) -> tuple[list[dict], int, int]:
    # Bound per-company provider concurrency to avoid tenant-level 429 bursts.
    semaphore = asyncio.Semaphore(3)

    async def run_query(query_type: dict):
        async with semaphore:
            return await _run_company_query(
                query_type, company, seller_context, days_override=days_override
            )

    tasks = [run_query(qt) for qt in QUERY_TYPES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items = []
    succeeded = failed = 0
    for query_type, result in zip(QUERY_TYPES, results):
        if isinstance(result, Exception):
            failed += 1
            print(f"    [{query_type['name']}] Error: {type(result).__name__}")
            continue
        succeeded += 1
        if result:
            print(f"    [{query_type['name']}] {len(result)} signal(s)")
            all_items.extend(result)
        else:
            print(f"    [{query_type['name']}] nothing found")

    return all_items, succeeded, failed


async def _research_company(
    company: str,
    seller_context: dict,
    days_override: int | None = None,
) -> list[dict]:
    items, _, _ = await _research_company_with_coverage(
        company, seller_context, days_override=days_override
    )
    return items


# -- Score-based ranking (deterministic, no LLM call) --

def _rank_signals(items: list[dict], *, prioritize_account_coverage: bool = False) -> list[dict]:
    """Rank signals by score, enforce diversity. No hard quality gate — take top N."""
    max_signals = cfg["digest"].get("max_signals_per_email", 24)
    max_risk = cfg["digest"].get("max_risk_signals", 8)
    max_per_company = cfg["digest"].get("max_signals_per_company", 6)

    today = date.today()

    # FINAL FRESHNESS GATE (defense-in-depth): every signal that reaches an email
    # passes through here. Independent of the per-query cutoff, enforce the
    # absolute floor so nothing undated/unparseable/stale can ever ship, even if
    # it entered via a code path that skipped the query-level filter.
    floor = today - timedelta(days=MAX_SIGNAL_AGE_DAYS)
    fresh_items = [it for it in items if _is_fresh_enough(it, floor, today)]
    dropped = len(items) - len(fresh_items)
    if dropped:
        print(f"    [rank] Final freshness gate dropped {dropped} stale/undated signal(s)")
    items = fresh_items

    def _age_penalty(item) -> float:
        """Soft freshness penalty: recent news floats up, stale news sinks.
        Not a hard cut — a strong-but-older signal can still make the cut."""
        d = item.get("date", "")
        if not d:
            return 0.0
        try:
            age_days = (today - date.fromisoformat(d)).days
        except (ValueError, TypeError):
            return 0.0
        if age_days <= 7:
            return 0.0
        if age_days <= 14:
            return 0.5
        if age_days <= 21:
            return 1.0
        return 2.0  # >3 weeks old — push down hard

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
        return strength * 2 + persona - _age_penalty(item)

    # Score and sort
    for item in items:
        item["_composite"] = _score(item)
    items.sort(key=lambda x: x["_composite"], reverse=True)

    if prioritize_account_coverage:
        first_by_company = {}
        extras = []
        for item in items:
            company = item.get("_tracked_company_key") or _norm_company(item.get("company", ""))
            if company in first_by_company:
                extras.append(item)
            else:
                first_by_company[company] = item
        items = [*first_by_company.values(), *extras]

    # Diversity caps — no hard quality filter, just take the best by score
    result = []
    company_count = {}
    risk_count = 0
    seen_headlines = set()

    for item in items:
        company = item.get("_tracked_company_key") or _norm_company(item.get("company", ""))
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

    # Keep the highest-impact stories at the top of the rendered digest.
    result.sort(key=lambda item: item["_composite"], reverse=True)
    # Clean up internal scoring fields
    for item in result:
        item.pop("_composite", None)
        item.pop("_strength", None)
        item.pop("_persona", None)
        item.pop("_tracked_company_key", None)

    return result


# -- Source quality -----------------------------

# Aggregators / social / index pages that are almost never a specific article.
_LOW_QUALITY_HOSTS = (
    "newsnow.com", "newsnow.co.uk", "facebook.com", "m.facebook.com",
    "perplexity.ai", "x.com", "twitter.com", "t.co", "google.com",
    "bing.com", "youtube.com", "youtu.be", "tiktok.com",
)

# URL path prefixes/segments that indicate a listing/section/tag page, not an article.
_INDEX_PATH_HINTS = (
    "/keywords/", "/tag/", "/tags/", "/topic/", "/topics/", "/category/",
    "/categories/", "/search", "/press-releases", "/news-releases",
    "/newsroom", "/media", "/latest-news",
)


_TRACKING_QUERY_KEYS = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid",
    "ref", "referrer", "source", "campaign",
})


def _canonical_source_url(url: str) -> str:
    """Canonicalize an article URL without discarding identity-bearing parameters."""
    if not isinstance(url, str):
        return ""
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        query.append((key, value))
    host = parsed.hostname.lower()
    try:
        if parsed.port:
            host = f"{host}:{parsed.port}"
    except ValueError:
        return ""
    return urlunparse((
        parsed.scheme.lower(), host, parsed.path.rstrip("/") or "/", "",
        urlencode(sorted(query)), "",
    ))


def _is_specific_article_url(url: str) -> bool:
    """Heuristic: is this a specific article/release, not a homepage/index/aggregator?"""
    canonical = _canonical_source_url(url)
    if not canonical:
        return False
    parsed = urlparse(canonical)
    host = parsed.hostname or ""

    if any(host == h or host.endswith(f".{h}") for h in _LOW_QUALITY_HOSTS):
        return False
    segments = [s for s in parsed.path.split("/") if s]
    identity_query = bool(parsed.query)
    if len(segments) == 0 and not identity_query:
        return False
    lower_path = parsed.path.lower().rstrip("/")
    always_index = (
        "/search", "/keywords/", "/tag/", "/tags/", "/topic/", "/topics/",
        "/category/", "/categories/",
    )
    if lower_path.startswith("/search") or any(hint in lower_path for hint in always_index[1:]):
        return False
    section_indexes = {hint.rstrip("/") for hint in _INDEX_PATH_HINTS}
    if lower_path in section_indexes:
        return False
    if len(segments) == 1 and segments[0].lower() in {
        "news", "press", "media", "investors", "about", "blog", "newsroom"
    } and not identity_query:
        return False
    return True


def _clean_sources(item: dict) -> None:
    """Drop non-specific source URLs in place. If none survive, leave sources empty
    (the email simply shows no link rather than a misleading index page)."""
    srcs = item.get("sources", []) or []
    good = [s for s in srcs if _is_specific_article_url(s)]
    item["sources"] = good


# -- Dedup against previous week ---------------

def _source_urls_from_item(item: dict) -> set[str]:
    """Extract canonical source URLs from a signal item."""
    urls = set()
    for src in item.get("sources", []):
        normalized = _canonical_source_url(src)
        if normalized:
            urls.add(normalized)
    return urls


_FP_STOPWORDS = frozenset("""
a an and the of to in on at for with from by as is are was were be been being
has have had will would can could may might new news today announces announced
announcement report reports reported said says say after amid over into out up
its it their his her our your this that these those about latest update updates
company group plc inc ltd sa ag nv corp co and/or per via than then also more
""".split())

# Corporate suffixes / generic descriptors stripped when normalizing a company
# name to a stable token. Kept broad so the SAME company normalizes identically
# even when the source string drifts week to week ("Acme Corp" vs "Acme
# Corporation" vs "Acme Technologies Inc.").
_COMPANY_SUFFIXES = frozenset("""
the group plc inc ltd limited sa ag nv corp corporation co company companies
gmbh spa srl as oyj holding holdings international intl technologies technology
tech solutions systems services global worldwide industries enterprises labs
llc lp llp kg kgaa ab bv se sas sarl pte
""".split())


def _norm_company(company: str) -> str:
    """Normalize a company name to a stable token (lowercase alnum, no suffixes)."""
    import re
    c = (company or "").lower()
    c = re.sub(r"[^a-z0-9 ]", " ", c)
    words = [w for w in c.split() if w not in _COMPANY_SUFFIXES]
    return "".join(words[:3])


def _content_tokens(headline: str) -> list[str]:
    """Casing-independent content tokens ordered by DISTINCTIVENESS.

    Distinctive tokens (longer words, proper-noun-like names, numbers) are the
    most stable across rewordings of the same event and the least likely to
    collide between different events. We sort longest-first (ties broken
    alphabetically for determinism), then append numbers. This means topic keys
    built from the leading tokens prioritize e.g. names and product terms over
    short generic words like "new"/"cio"."""
    import re
    text = (headline or "").lower()
    numbers = sorted(set(re.findall(r"\d+\.?\d*", text)))
    words = re.findall(r"[a-z][a-z0-9]+", text)
    content = {w for w in words if w not in _FP_STOPWORDS and len(w) > 2}
    ordered = sorted(content, key=lambda w: (-len(w), w))
    return ordered + numbers


def _headline_fingerprint(headline: str, company: str = "") -> str:
    """Fuzzy fingerprint (Layer 2): company + top content tokens. Casing-independent
    so the SAME headline always fingerprints identically on both the new-signal and
    previous-signal side. This fixes the prior bug where prev headlines were
    fingerprinted WITHOUT the company token and never matched."""
    toks = _content_tokens(headline)
    parts = [_norm_company(company)] + toks
    return "|".join(p for p in parts[:9] if p)


def _hard_keys(headline: str, company: str = "") -> set[str]:
    """Return only a strict normalized-headline key for irreversible ledger drops.

    Older ledgers also contain broad topic keys. They are intentionally ignored here:
    recurring earnings, hiring and regulatory events often share four long tokens but
    are distinct events. Fuzzy and LLM layers still review reworded headlines.
    """
    import re
    co = _norm_company(company)
    strict_words = re.findall(r"[a-z0-9]+", (headline or "").lower())
    return {"s:" + co + ":" + "".join(strict_words)}


async def _dedup_signals(
    items: list[dict],
    previous_signals: list[dict],
    previous_sources: set[str] | None = None,
    previous_keys: set[str] | None = None,
) -> list[dict]:
    """Remove signals already sent in previous digests. MAX-STRICT: when in doubt, DROP.

    previous_signals: list of {"headline", "company"} from prior digests.
    previous_keys: hard fingerprint ledger (from sent_signal_keys table).

    Layers, hardest first:
    0. Hard fingerprint ledger — deterministic strict+topic key match = always drop.
    1. Source URL dedup — same article URL = always drop.
    2. Fuzzy headline fingerprint (company + content tokens) = drop.
    3. LLM semantic dedup — same event described differently = drop.
    Also dedups within the current batch.
    """
    if not items:
        return items

    previous_signals = previous_signals or []
    previous_keys = previous_keys or set()

    # Build previous-side key/fingerprint sets from stored digest items (belt) AND
    # the persistent ledger (suspenders). Together they survive JSON edits + rewordings.
    prev_hard = set(previous_keys)
    prev_fingerprints = set()
    for s in previous_signals:
        h, c = s.get("headline", ""), s.get("company", "")
        prev_hard |= _hard_keys(h, c)
        prev_fingerprints.add(_headline_fingerprint(h, c))

    # Layer 0: HARD ledger gate — deterministic, the "never ever" guarantee.
    before = len(items)
    kept = []
    for item in items:
        if _hard_keys(item.get("headline", ""), item.get("company", "")) & prev_hard:
            print(f"    [dedup-hard] Dropped (ledger): {item.get('headline', '')[:60]}...")
        else:
            kept.append(item)
    items = kept
    if before - len(items):
        print(f"    [dedup-hard] Dropped {before - len(items)} exact/topic duplicate(s)")
    if not items:
        return items

    # Layer 1: Hard URL dedup — never send the same source article twice
    if previous_sources:
        before = len(items)
        items = [
            item for item in items
            if not _source_urls_from_item(item).intersection(previous_sources)
        ]
        if before - len(items):
            print(f"    [dedup-url] Dropped {before - len(items)} signal(s) with previously-sent source URLs")

    if not items:
        return items

    # Layer 2: Fuzzy fingerprint dedup — company + content tokens (casing-independent)
    if prev_fingerprints:
        before = len(items)
        kept = []
        for item in items:
            fp = _headline_fingerprint(item.get("headline", ""), item.get("company", ""))
            if fp in prev_fingerprints:
                print(f"    [dedup-fingerprint] Dropped: {item.get('headline', '')[:60]}...")
            else:
                kept.append(item)
        items = kept
        if before - len(items):
            print(f"    [dedup-fingerprint] Dropped {before - len(items)} near-duplicate(s)")

    # Dedup exact matches within the current batch first.
    seen_batch = set()
    kept = []
    for item in items:
        keys = _hard_keys(item.get("headline", ""), item.get("company", ""))
        if keys & seen_batch:
            continue
        seen_batch |= keys
        kept.append(item)
    items = kept

    if not items or (len(items) == 1 and not previous_signals):
        return items

    # The semantic pass receives both prior headlines and the other candidates,
    # so reworded versions of the same event cannot consume multiple email slots.
    previous_headlines = [s.get("headline", "") for s in previous_signals if s.get("headline")]

    # Build the list of prior headlines for the LLM layer
    previous_headlines = [s.get("headline", "") for s in previous_signals if s.get("headline")]

    # Layer 3: LLM-based headline dedup — catches semantically identical events
    signals_for_review = [
        {"i": i, "company": item.get("company", ""), "date": item.get("date", ""),
         "headline": item.get("headline", "")}
        for i, item in enumerate(items)
    ]
    prev_list = "\n".join(
        f"- {s.get('date', '')}: {s.get('headline', '')}"
        for s in previous_signals[:200] if s.get("headline")
    )

    try:
        data = await _call_model(
            system="You are a precise news-event dedup reviewer. Return only a valid JSON array of indices to keep.",
            prompt=f"""Compare these new signals against previously sent headlines.

RULES:
1. DROP when the new signal describes the SAME concrete event as a previously sent headline OR an earlier-indexed new signal, even if reworded or reported by another source.
2. When two NEW SIGNALS describe the same event, keep the lowest "i" only.
3. DROP a follow-up only when it adds no material new development.
4. KEEP a materially new event at the same company. Company overlap alone is never a duplicate.
5. KEEP different actions involving the same person when they happened on different dates or have a distinct business consequence.
6. Examples: a CTO appointment and a factory opening at the same company are DIFFERENT events; keep the factory opening. A launch announcement and another article recapping that same launch are the SAME event; keep one.

If the evidence does not show the same concrete event, KEEP the new signal.

PREVIOUSLY SENT HEADLINES:
{prev_list}

NEW SIGNALS:
{json.dumps(signals_for_review, indent=2)}

Return a JSON array of the "i" values to KEEP: [0, 2, 5, ...]
If all are duplicates, return []. No explanation.""",
            max_tokens=min(5000, max(800, len(items) * 10)),
            provider="gpt",
        )
        content = data["choices"][0]["message"]["content"]
        indices = _parse_json(content)
        if isinstance(indices, list):
            kept = [items[idx] for idx in indices if isinstance(idx, int) and 0 <= idx < len(items)]
            dropped = len(items) - len(kept)
            if dropped:
                print(f"    [dedup-llm] Dropped {dropped} duplicate(s)")
            return kept
    except Exception as e:
        # Deterministic exact/topic, URL and fingerprint gates already ran above.
        # If semantic review is unavailable, keep the remaining distinct events
        # rather than dropping all news for any previously covered company.
        print(f"    [dedup] LLM check failed ({type(e).__name__}); keeping deterministic survivors")

    return items


async def _run_catchall_query(
    company: str, seller_context: dict, days: int = 14,
    *, raise_on_error: bool = False, newsroom_fallback: bool = False,
) -> list[dict]:
    """Broad deep-research query used to recover unique news for under-covered accounts."""
    today = date.today()
    cutoff = today - timedelta(days=days)
    output_instruction = SIGNAL_OUTPUT_INSTRUCTION.format(
        company=company, all_tags=ALL_TAGS, cutoff=cutoff.isoformat(),
    )

    if newsroom_fallback:
        search_instruction = """Start with the company's own newsroom, investor releases and official blogs.
Then search named independent trade publications and local business press in the relevant languages.
Use direct story or release links from your search results. Prefer concrete developments a sales rep can
act on. Omit share buybacks, routine filings, recycled stories, social posts and aggregator copies.
If the account name is ambiguous, omit results whose company identity cannot be established.
Return [] when you find no qualifying development."""
        if company.strip().lower() in {"aston martin", "aston martin lagonda"}:
            search_instruction += "\nThe account is the Aston Martin carmaker. Exclude Formula One team news."
    else:
        search_instruction = """Search broadly across public company announcements, executive interviews,
financial results, partnerships, certifications, launches, operations, hiring and industry events.
Search in every relevant language."""
    try:
        data = await _call_model(
            system=f"""You are a B2B sales intelligence analyst. Today is {today.isoformat()}.
Only report events after {cutoff.isoformat()}. Return valid JSON. Search broadly in ALL languages.
Include the EVENT date in YYYY-MM-DD format for each signal.""",
            prompt=f"""Find up to 5 distinct recent news, announcements, or developments about {company} in the last {days} days.
Return at most 5 complete, non-duplicate items. Favor uncovered event types and primary/local-language sources.

{search_instruction}

{output_instruction}""",
            max_tokens=2200,
            provider="pplx",
        )
        content = data["choices"][0]["message"]["content"]
        items = _parse_json(content)
        if not isinstance(items, list):
            items = [items] if isinstance(items, dict) and items.get("headline") else []

        items = [item for item in items if isinstance(item, dict)]
        for item in items:
            _clean_sources(item)

        if newsroom_fallback:
            dated_urls = {
                _canonical_source_url(source["url"])
                for source in data.get("search_results") or []
                if isinstance(source, dict) and isinstance(source.get("url"), str)
                and (published := _parse_signal_date(source.get("date")))
                and cutoff <= published <= today
            }
            for item in items:
                item["sources"] = [
                    url for url in item["sources"]
                    if _canonical_source_url(url) in dated_urls
                    and _is_eligible_fallback_source(url, company)
                ]
            items = [
                item for item in items
                if not re.search(r"buyback|own shares|treasury shares|total voting rights",
                                 str(item.get("headline") or ""), re.IGNORECASE)
            ]

        # HARD DATE + SOURCE FILTER (same rule as the main query path)
        valid = [
            item for item in items
            if item.get("headline")
            and item.get("sources")
            and _is_fresh_enough(item, cutoff, today)
        ]

        if valid:
            print(f"    [catchall] Found {len(valid)} signal(s)")
        return valid
    except Exception as e:
        print(f"    [catchall] Error: {type(e).__name__}")
        if raise_on_error:
            raise
        return []


def _is_eligible_fallback_source(url: str, company: str) -> bool:
    host = urlparse(url).hostname or ""
    excluded = {"linkedin.com", "ground.news", "yahoo.com", "msn.com", "simplywall.st", "stocktitan.net"}
    if company.strip().lower() in {"aston martin", "aston martin lagonda"}:
        excluded.add("astonmartinf1.com")
    return not any(
        host == domain or host.endswith("." + domain)
        for domain in excluded
    )


# -- Public API --------------------------------

async def research_company_once(
    company: str, *, include_coverage: bool = False,
) -> list[dict] | tuple[list[dict], dict]:
    """Research and validate neutral public facts for one company.

    This boundary is reusable by the central pool. It does not read or mutate
    any subscriber's history, feedback, contacts, or delivery state.
    """
    seller_context = get_solace_context()
    items, succeeded, failed = await _research_company_with_coverage(
        company, seller_context
    )
    if succeeded == 0:
        raise RuntimeError(f"All {failed} company research queries failed")
    if not items:
        items = await _run_catchall_query(
            company, seller_context, days=_get_recency_days(), raise_on_error=True
        )
    for item in items:
        tag_key = item.get("tag", "strategic")
        category = SIGNAL_CATEGORIES.get(
            tag_key, SIGNAL_CATEGORIES.get("strategic", {"tag": "Strategic", "color": "blue"})
        )
        item["tag"] = category["tag"]
        item["tag_color"] = category["color"]
    coverage = {"succeeded": succeeded, "failed": failed, "total": len(QUERY_TYPES)}
    return (items, coverage) if include_coverage else items


async def project_signals_for_user(user: dict, items: list[dict]) -> list[dict]:
    """Apply user-scoped history dedup, ranking and selected-item enrichment."""
    if not items:
        return []
    user_id = user.get("id", "")
    if user_id:
        try:
            from db import (get_previous_signals, get_previous_source_urls,
                            get_previous_signal_keys)
            lookback = cfg["digest"].get("dedup_lookback_weeks", 12)
            previous_signals = await get_previous_signals(user_id, weeks=lookback)
            previous_sources = await get_previous_source_urls(user_id, weeks=lookback)
            previous_keys = await get_previous_signal_keys(user_id, weeks=lookback)
            items = await _dedup_signals(
                list(items), previous_signals, previous_sources, previous_keys
            )
        except Exception as exc:
            print(f"  [dedup] History check FAILED ({exc}); suppressing to avoid duplicates.")
            return []
    ranked = _rank_signals(list(items))
    return await _enrich_all_contacts(ranked)


def _apply_signal_categories(items: list[dict]) -> None:
    for item in items:
        tag_key = item.get("tag", "strategic")
        category = SIGNAL_CATEGORIES.get(
            tag_key, SIGNAL_CATEGORIES.get("strategic", {"tag": "Strategic", "color": "blue"})
        )
        item["tag"] = category["tag"]
        item["tag_color"] = category["color"]


def _company_matches_tracked(company: str, tracked: set[str]) -> bool:
    """Conservatively match exact names or known corporate-suffix variants."""
    normalized = " ".join((company or "").strip().lower().split())
    if not normalized:
        return False
    for candidate in tracked:
        candidate_name = " ".join((candidate or "").strip().lower().split())
        if normalized == candidate_name:
            return True
        words = normalized.split()
        if (
            len(candidate_name.split()) == 1
            and words
            and words[0] == candidate_name
            and words[-1] in _COMPANY_SUFFIXES
        ):
            return True
    return False


def _filter_tracked_items(items: list[dict], companies: list[str]) -> list[dict]:
    tracked = {company.strip().lower() for company in companies if company.strip()}
    low_value_patterns = ("sec form", "recall affecting")
    kept = []
    for item in items:
        matched_name = next(
            (candidate for candidate in tracked
             if _company_matches_tracked(item.get("company", ""), {candidate})),
            None,
        )
        if not matched_name:
            print(f"    [company-filter] Dropped untracked company: {item.get('company', '')}")
            continue
        item["_tracked_company_key"] = matched_name
        if any(pattern in item.get("headline", "").lower() for pattern in low_value_patterns):
            print(f"    [quality] Dropped low-value: {item.get('headline', '')[:60]}")
            continue
        kept.append(item)
    return kept


async def generate_signals(
    companies: list[str], *, finalize: bool = True,
) -> list[dict]:
    """Research validated candidates; optionally rank/enrich for bounded callers."""
    seller_context = get_solace_context()
    all_items = []
    failed_companies = []
    for i, company in enumerate(companies):
        if i > 0:
            await asyncio.sleep(cfg["digest"]["pace_between_companies_sec"])
        print(f"  [{company}] ({len(QUERY_TYPES)} queries)")
        company_items, succeeded, failed = await _research_company_with_coverage(
            company, seller_context
        )
        min_coverage = cfg["digest"].get("min_successful_queries_per_company", 6)
        if succeeded < min_coverage:
            failed_companies.append(company)
            print(f"    [coverage] only {succeeded}/{len(QUERY_TYPES)} queries succeeded")
            continue
        _apply_signal_categories(company_items)
        all_items.extend(company_items)

    if failed_companies and len(failed_companies) == len(companies):
        raise RuntimeError("All company research queries failed")
    if not all_items:
        print("  No signals found across any account.")
        return []

    print(f"  [candidates] {len(all_items)} validated raw signals")
    if not finalize:
        return all_items

    ranked = _rank_signals(all_items)
    print(f"  [ranked] {len(ranked)} signals")
    return await _enrich_all_contacts(ranked)


def _weekly_company_sample(companies: list[str], limit: int, delivery_date: date) -> list[str]:
    distinct = {}
    for company in companies:
        if key := company.strip().lower():
            distinct.setdefault(key, company.strip())
    if len(distinct) <= limit:
        return list(distinct.values())
    # A portfolio above the cap gets a stable, rotating window each week.
    ordered = [distinct[key] for key in sorted(distinct)]
    week = (delivery_date.toordinal() - delivery_date.weekday()) // 7
    start = week * limit % len(ordered)
    return (ordered[start:] + ordered[:start])[:limit]


async def generate_digest_for_user(user: dict, *, delivery_date: date | None = None) -> list[dict]:
    """Generate a full digest for a single user, with dedup against previous digests.

    Two dedup layers:
    1. Source URL dedup — same article URL = never send again
    2. LLM headline dedup — same event described differently = drop
    """
    companies = [c["name"] for c in user.get("companies", [])]
    if not companies:
        return []

    n = cfg["digest"]["companies_per_user"]
    today = date.today()
    delivery_date = delivery_date or today
    sample = _weekly_company_sample(companies, n, delivery_date)
    cutoff = delivery_date - timedelta(days=MAX_SIGNAL_AGE_DAYS)
    fallback_days = min(_get_recency_days(), (today - cutoff).days)

    # Keep the full validated pool until after account filtering and history dedup.
    items = await generate_signals(sample, finalize=False)
    items = _filter_tracked_items(items, sample)
    items = [item for item in items if _is_fresh_enough(item, cutoff, today)]
    discovered = Counter(item["_tracked_company_key"] for item in items)

    user_id = user.get("id", "")
    previous_signals: list[dict] = []
    previous_sources: set[str] = set()
    previous_keys: set[str] = set()
    if user_id:
        try:
            from db import (get_previous_signals, get_previous_source_urls,
                            get_previous_signal_keys)
            from submitted_signals import as_digest_item, list_submissions
            submitted = _filter_tracked_items(
                [as_digest_item(row) for row in await list_submissions(user_id, cutoff)], companies
            )
            items = [item for item in submitted if _is_fresh_enough(item, cutoff, today)] + items
            discovered = Counter(item["_tracked_company_key"] for item in items)
            lookback = cfg["digest"].get("dedup_lookback_weeks", 12)
            previous_signals = await get_previous_signals(user_id, weeks=lookback)
            previous_sources = await get_previous_source_urls(user_id, weeks=lookback)
            previous_keys = await get_previous_signal_keys(user_id, weeks=lookback)
            print(f"  [dedup] Comparing {len(items)} candidates against "
                  f"{len(previous_signals)} prior signals, {len(previous_sources)} URLs "
                  f"({lookback}w lookback)")
            items = await _dedup_signals(items, previous_signals, previous_sources, previous_keys)
        except Exception as e:
            print(f"  [dedup] History check FAILED ({e}); suppressing to avoid duplicates.")
            return []

    # Recover quiet accounts even if other accounts already filled the digest.
    per_company = Counter(item["_tracked_company_key"] for item in items)
    recovery_companies = [company for company in sample if not per_company[company.strip().lower()]]
    recovery_target = cfg["digest"].get("recovery_target_signals", 20)
    if len(items) < recovery_target:
        extra = max(0, cfg["digest"].get("recovery_max_companies", 16) - len(recovery_companies))
        recovery_companies.extend(
            [company for company in sample if per_company[company.strip().lower()] == 1][:extra]
        )
    if recovery_companies:
        print(f"  [recovery] Checking newsrooms and trade press for {len(recovery_companies)} accounts")
        seller_context = get_solace_context()
        recovered = []
        for company in recovery_companies:
            company_items = await _run_catchall_query(
                company, seller_context, days=fallback_days, newsroom_fallback=True
            )
            _apply_signal_categories(company_items)
            company_items = _filter_tracked_items(company_items, [company])
            print(f"    [source-fallback] {company}: {len(company_items)} dated candidate(s)")
            recovered.extend(item for item in company_items if _is_fresh_enough(item, cutoff, today))
        if recovered:
            items = await _dedup_signals(
                items + recovered, previous_signals, previous_sources, previous_keys
            )

    print(f"  [ranking] {len(items)} unique candidates")
    candidate_company = {id(item): item["_tracked_company_key"] for item in items}
    after_fallback = Counter(candidate_company.values())
    ranked = _rank_signals(items, prioritize_account_coverage=True)
    selected = Counter(candidate_company[id(item)] for item in ranked)
    print("  [weekly-coverage] " + json.dumps({
        "counts": ["discovered", "after_history", "after_fallback", "selected"],
        "accounts": {
            company: [discovered[key], per_company[key], after_fallback[key], selected[key]]
            for company in sample if (key := company.lower())
        },
        "outside_sample": sorted(set(companies) - set(sample)),
    }, ensure_ascii=False))
    ranked = await _enrich_all_contacts(ranked)
    print(f"  [done] {len(ranked)} final signals")
    return ranked


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


# -- Brand monitoring --------------------------

async def search_brand_mentions() -> list[dict]:
    """Search public web for external mentions of Solace.

    Returns list of mention dicts with headline, source_url, snippet, mention_type.
    Filters out solace.com domains.
    """
    bm_cfg = cfg.get("brand_monitor", {})
    queries = bm_cfg.get("search_queries", [])
    excluded = bm_cfg.get("excluded_domains", ["solace.com"])
    recency_days = bm_cfg.get("recency_days", 7)

    today = date.today()
    cutoff = today - timedelta(days=recency_days)

    all_mentions = []
    for query in queries:
        try:
            data = await _call_model(
                system=f"""You are a brand monitoring analyst. Today is {today.isoformat()}.

STRICT DATE RULE: Only include mentions published or posted AFTER {cutoff.isoformat()}.
- Every mention MUST have a date in YYYY-MM-DD format.
- If you cannot determine the publication date, do NOT include the mention.
- Do NOT include old content from 2024 or early 2025. Only content published after {cutoff.isoformat()}.
- The article or post must have been PUBLISHED after {cutoff.isoformat()}, not just indexed recently.

Find mentions of Solace (the enterprise messaging/event streaming company, makers of PubSub+ event broker and Agent Mesh).
Only include mentions from EXTERNAL sources (not solace.com, solace.dev, docs.solace.com).
Search broadly: news sites, tech blogs, LinkedIn, analyst reports, conference proceedings, YouTube, podcasts, GitHub discussions.
Return valid JSON only. If nothing recent found, return [].""",
                prompt=f"""Search for: {query}

Return a JSON array of mentions found (0-5 items). No markdown fences.
[
  {{
    "headline": "<what was said or written, one sentence>",
    "source_url": "<URL of the article/post>",
    "source_name": "<publication or platform name>",
    "date": "<YYYY-MM-DD — REQUIRED, must be after {cutoff.isoformat()}>",
    "snippet": "<relevant quote or excerpt, 2-3 sentences>",
    "mention_type": "<one of: news, blog, social, competitive_comparison, customer_reference, analyst, conference>",
    "sentiment": "<positive, neutral, negative, competitive_threat>"
  }}
]

RULES:
- EVERY mention MUST have a date field in YYYY-MM-DD format after {cutoff.isoformat()}
- EXCLUDE anything from solace.com, solace.dev, docs.solace.com
- EXCLUDE anything older than {cutoff.isoformat()} — no exceptions
- If nothing genuinely recent found, return []. That is the correct answer.
- Do NOT include undated content or content from previous years""",
                max_tokens=600,
                provider="pplx",
            )
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            items = json.loads(content)
            if isinstance(items, dict):
                items = [items]

            for item in items:
                if not item.get("headline"):
                    continue
                url = item.get("source_url", "")
                if any(d in url for d in excluded):
                    continue
                # HARD DATE FILTER: drop anything without date or too old
                d = item.get("date", "")
                if not d:
                    continue
                try:
                    mention_date = date.fromisoformat(d)
                    if mention_date < cutoff or mention_date > today:
                        continue
                except ValueError:
                    continue
                all_mentions.append(item)

            sources = data.get("citations", [])
            for item in all_mentions[-len(items):]:
                if not item.get("source_url") and sources:
                    item["source_url"] = sources[0]

        except Exception as e:
            print(f"    [brand-monitor] Query error: {e}")
        await asyncio.sleep(2)

    # Dedup by headline similarity
    seen = set()
    unique = []
    for m in all_mentions:
        key = m.get("headline", "").lower()[:50]
        if key not in seen:
            seen.add(key)
            unique.append(m)

    return unique[:bm_cfg.get("max_mentions_per_email", 8)]
