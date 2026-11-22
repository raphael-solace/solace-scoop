"""Deep-dive enrichment for thin digests."""
import os, json, asyncio, sys
from pathlib import Path
from datetime import date, timedelta

for line in Path(__file__).parent.parent.joinpath('.env').read_text().splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        key, val = line.split('=', 1)
        os.environ[key.strip()] = val.strip()

sys.path.insert(0, str(Path(__file__).parent))
import httpx
from config import cfg

DEEP_DIVE_PROMPT = """Research {company} deeply for the LAST 14 DAYS. Go beyond press releases.

FIND:
1. IT/digital projects with budgets, timelines, vendors named
2. LinkedIn posts by their CIO/CTO/VP Engineering - what are they talking about?
3. Job postings that reveal tech stack changes or new initiatives
4. Conference talks, webinar appearances by their tech leaders
5. Specific quotes from executives about technology strategy
6. RFP/tender announcements for middleware, integration, cloud
7. Partner/SI engagements (Accenture, Deloitte, etc.) with scope details
8. Internal reorganizations affecting IT/digital teams
9. Earnings call quotes about IT spending, digital transformation budgets
10. Any mentions of event-driven architecture, real-time data, messaging, API modernization

IMPORTANT: Only include VERIFIABLE facts with dates. No speculation. Include source URLs.
If you find budget numbers, headcount, or timeline details, those are gold.
If a person posted on LinkedIn or spoke at a conference, quote them.

Return a JSON array (0-3 items). No markdown fences.
[
  {{
    "company": "{company}",
    "tag": "<one of: tech_initiative, platform_change, hiring, partnership, leadership_change, competitive, financial_event>",
    "date": "<YYYY-MM-DD of event>",
    "headline": "<specific factual headline with numbers/names/dates>",
    "signal_strength": <3-5>,
    "persona_fit": <3-5>,
    "so_what": "<2-3 sentences explaining why a salesperson should care, be specific>",
    "contact_name": "<person named in source ONLY, or empty string>",
    "contact_title": "<their exact title, or empty string>",
    "contact_linkedin": "",
    "message": "<warm 2-sentence LinkedIn DM with relevant Solace proof point>",
    "risk_or_opportunity": "opportunity",
    "sources": ["<URL>"]
  }}
]

Return [] if nothing high-quality found. NEVER invent facts or people."""

SIGNAL_CATEGORIES = {
    "leadership_change": {"tag": "People Move", "color": "red"},
    "tech_initiative": {"tag": "Tech Initiative", "color": "blue"},
    "platform_change": {"tag": "Platform Change", "color": "blue"},
    "hiring": {"tag": "Hiring Signal", "color": "green"},
    "partnership": {"tag": "Partnership", "color": "green"},
    "competitive": {"tag": "Competitive", "color": "amber"},
    "financial_event": {"tag": "Financial", "color": "green"},
}


async def deep_research(company):
    today = date.today()
    cutoff = today - timedelta(days=14)
    prompt = DEEP_DIVE_PROMPT.format(company=company)
    messages = [
        {"role": "system", "content": f"You are an expert B2B researcher. Today is {today.isoformat()}. Only report events after {cutoff.isoformat()}. Return valid JSON only. NEVER fabricate names, dates, or facts."},
        {"role": "user", "content": prompt},
    ]

    deployment_id = os.getenv("AICORE_DEPLOYMENT_PPLX", "")
    if deployment_id:
        import aicore
        if aicore.is_configured():
            try:
                data = await aicore.chat_completion(
                    deployment_id=deployment_id, model="sonar-pro",
                    messages=messages, max_tokens=900, temperature=0.1,
                )
                content = data["choices"][0]["message"]["content"].strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                items = json.loads(content)
                if isinstance(items, dict):
                    items = [items]

                valid = []
                for item in items:
                    if not item.get("headline"):
                        continue
                    d = item.get("date", "")
                    if not d:
                        continue
                    try:
                        if date.fromisoformat(d) < cutoff:
                            continue
                    except ValueError:
                        continue
                    tag_key = item.get("tag", "tech_initiative")
                    cat = SIGNAL_CATEGORIES.get(tag_key, {"tag": "Tech Initiative", "color": "blue"})
                    item["tag"] = cat["tag"]
                    item["tag_color"] = cat["color"]
                    valid.append(item)

                sources = data.get("citations", [])
                for item in valid:
                    if not item.get("sources"):
                        item["sources"] = sources
                return valid
            except Exception as e:
                print(f"      [ai-core error] {e}", flush=True)
    return []


async def main():
    from db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        digest_rows = await conn.fetch(
            """select id, items, user_id from digests
               where delivery_status = 'pending'
               order by sent_at desc limit 25"""
        )
        user_rows = await conn.fetch("select id, email from users")
        company_rows = await conn.fetch("select user_id, name from companies")

    comps_by_user: dict = {}
    for c in company_rows:
        comps_by_user.setdefault(str(c['user_id']), []).append({'name': c['name']})

    digests = []
    for r in digest_rows:
        items = r['items']
        if isinstance(items, str):
            items = json.loads(items)
        digests.append({'id': str(r['id']), 'items': items, 'user_id': str(r['user_id'])})

    users = {}
    for u in user_rows:
        uid = str(u['id'])
        users[uid] = {'id': uid, 'email': u['email'], 'companies': comps_by_user.get(uid, [])}

    enriched_total = 0
    for d in digests:
        user = users.get(d['user_id'], {})
        email = user.get('email', '?')
        if not email.endswith('@solace.com'):
            continue

        items = d.get('items', [])
        companies = [c['name'] for c in user.get('companies', [])]
        signaled = set(i.get('company', '').lower() for i in items)
        missing = [c for c in companies if c.lower() not in signaled]

        max_research = 3 if len(items) <= 2 else 2 if len(items) <= 3 else 1
        to_research = missing[:max_research]
        if not to_research:
            continue

        print(f"\n  [{email}] Deep-diving: {to_research}", flush=True)

        new_signals = []
        for company in to_research:
            print(f"    {company}...", end=" ", flush=True)
            results = await deep_research(company)
            if results:
                print(f"{len(results)} signal(s)", flush=True)
                new_signals.extend(results)
            else:
                print("nothing", flush=True)
            await asyncio.sleep(2)

        if new_signals:
            items.extend(new_signals)
            async with pool.acquire() as conn:
                await conn.execute(
                    "update digests set items = $1::jsonb, item_count = $2 where id = $3",
                    json.dumps(items), len(items), d['id'],
                )
            enriched_total += len(new_signals)
            print(f"    -> Saved (now {len(items)} signals)", flush=True)

        await asyncio.sleep(3)

    print(f"\n=== ENRICHMENT DONE: +{enriched_total} signals ===", flush=True)


asyncio.run(main())
