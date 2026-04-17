# Solace Scoop — Account Intelligence Skill

## What this skill does

Researches a list of customer accounts and generates actionable sales intelligence: recent news, recommended contacts, and ready-to-send messages that bridge naturally to Solace products.

**Input:** A list of company names (e.g. "BNP Paribas, Danone, Airbus")
**Output:** For each signal found:
- What happened (headline + source)
- Why it matters (business context)
- Who to contact (name, title, LinkedIn)
- What to say (warm, copy-pasteable message connecting news to Solace)

---

## How to use this skill

### As a prompt to any LLM with web search (Perplexity, GPT with browsing, etc.)

```
Research recent news (last 14 days) about [COMPANY]. For each signal found, provide:

1. HEADLINE: What happened, one sentence with names, dates, numbers.
2. SOURCE: URL of the article or announcement.
3. SO WHAT: 2-3 sentences explaining the business impact — write like you're briefing a colleague over coffee.
4. CONTACT: Find a senior IT/tech leader at [COMPANY] to reach out to about this news.
   Search LinkedIn for: CTO, CIO, CDO, CISO, VP Engineering, VP Architecture,
   Head of Integration, Head of Platform, Enterprise Architect, Chief Architect,
   DSI (Directeur des Systèmes d'Information), Architecte d'Entreprise.
   Include their name, title, and LinkedIn URL.
5. MESSAGE: A warm, human, 2-sentence message for LinkedIn DM or email.
   First sentence: acknowledge or congratulate on the news.
   Second sentence: gently connect to event-driven architecture / Solace as a helpful thought.
   Sound like a trusted advisor, not a salesperson.

TONE GUIDELINES (from Solace culture):
- Be warm, genuine, human. Solace is a people-first company.
- Values: trust, humility, craftsmanship, human experience.
- Never be pushy. Never say "I sell" or "our product."
- Be the kind of person they'd want to grab coffee with.
- Think trusted advisor sharing an insight, not cold outreach.

QUALITY RULES:
- Only report events with specific dates. No vague "recently" or background info.
- Every signal must have a source URL.
- Always find a contact. Search harder. Check company leadership pages, LinkedIn, press releases.
- The message must reference the SPECIFIC news, not be generic.
- Quality over quantity: one great signal beats three mediocre ones.
```

### As an API call

The skill runs as a Python pipeline. Core function:

```python
from digest import generate_signals

# Returns list of signal dicts
signals = await generate_signals(["BNP Paribas", "Danone", "Airbus"])

# Each signal contains:
# - company, tag, date, headline, so_what
# - contact_name, contact_title, contact_linkedin
# - message, sources, risk_or_opportunity
```

### As a weekly email digest

The full pipeline:
1. **Sunday evening**: Research all accounts for all users, save to database
2. **Monday morning**: Render and send branded email digests

```bash
python run_digest.py --research-only   # Sunday
python run_digest.py --send-only       # Monday
```

---

## Signal schema

```json
{
  "company": "BNP Paribas",
  "tag": "Partnership",
  "date": "2026-04-10",
  "headline": "BNP Paribas extends multi-custodian Automated FX solution to continental Europe with La Financière de l'Echiquier as first client.",
  "so_what": "This expansion into continental Europe means BNP is connecting more custodian systems for real-time FX execution. That's exactly the kind of multi-system integration where event mesh shines — connecting disparate trading platforms with guaranteed delivery and low latency.",
  "contact_name": "Laurent Cordeil",
  "contact_title": "CTO Global Markets at BNP Paribas",
  "contact_linkedin": "https://linkedin.com/in/laurentcordeil",
  "message": "Great to see the Automated FX solution expanding to continental Europe — connecting multiple custodian systems in real-time is no small feat. If you're looking at how event mesh could help scale that cross-custodian connectivity further, happy to share how others in capital markets have approached it.",
  "sources": ["https://group.bnpparibas/en/all-news"],
  "risk_or_opportunity": "opportunity"
}
```

---

## Research query types

The pipeline runs 8 parallel queries per company:

| Query | What it finds | Time window |
|-------|--------------|-------------|
| **people_moves** | Executive appointments, promotions, departures | 14 days |
| **business_news** | M&A, contracts, product launches, strategy | 14 days |
| **technology_initiatives** | Cloud migration, AI, platform modernization | 14 days |
| **partnerships_deals** | Vendor selections, SI engagements, alliances | 14 days |
| **financial_signals** | Earnings, capex, investments, budget shifts | 14 days |
| **risk_signals** | Layoffs, reorgs, departures, budget cuts | 14 days |
| **competitive_intel** | Competitor adoption, migration, platform choices | 14 days |
| **industry_regulatory** | Regulations, compliance, industry changes | 14 days |

---

## Quality gate

After research, signals pass through:

1. **Date filter**: Drop signals with dates older than 14 days
2. **Ranking**: Select diverse mix (max 3 risk signals, spread across companies)
3. **Contact enrichment**: Dedicated search per signal for a real person at the company
4. **Dedup**: Compare against previous week's headlines (if available)

---

## Contact search strategy

For each signal missing a contact, a dedicated search runs:

```
Find ONE senior IT/tech leader at [COMPANY] on LinkedIn.
Roles to search for:
- CTO, CIO, CDO, CISO, Chief Digital Officer
- VP Engineering, VP IT, VP Architecture, VP Technology
- Head of Integration, Platform, Middleware, Data, Cloud
- Enterprise Architect, Chief Architect, Solution Architect
- French: DSI, Directeur Technique, Architecte d'Entreprise, Architecte SI
```

If a direct LinkedIn URL is not found, a LinkedIn search URL is generated:
`https://linkedin.com/search/results/people/?keywords=Name+Company`

---

## Message tone guide

**Do:**
- "Congratulations on the new role! I'd love to hear how you're thinking about the integration landscape."
- "Great news about the partnership. If you're connecting those systems in real-time, happy to share how others have done it with event mesh."
- "Saw the $700M investment announcement — exciting times for smart factories. Event-driven architecture has been key for others scaling IoT in manufacturing."

**Don't:**
- "I sell Solace PubSub+ and think you should buy it."
- "This creates an opportunity for our event broker product."
- "Would you like to schedule a demo?"

The message should make the recipient think: "This person understands my world and has something useful to share."

---

## Solace product context

When connecting news to Solace, reference these naturally:

| Product | What it does | When to mention |
|---------|-------------|-----------------|
| **PubSub+ Event Broker** | Real-time messaging across cloud, on-prem, hybrid | Multi-system integration, real-time data flows |
| **Event Portal** | Design and govern event-driven architecture | Architecture modernization, new platform builds |
| **Event Mesh** | Connect brokers across environments | Multi-cloud, hybrid, cross-geography connectivity |
| **Agent Mesh** | Orchestrate AI agents via events | AI/ML initiatives, agentic AI projects |

**Competitors to be aware of:** Confluent/Kafka, IBM MQ, TIBCO, RabbitMQ, AWS EventBridge, Azure Service Bus

**Key partners:** Accenture, Deloitte, Capgemini, Boomi, SAP

---

## Configuration

All tunable parameters are in `backend/config.yaml`:

```yaml
digest:
  companies_per_user: 20        # max accounts per digest
  signals_per_email: 10         # top N signals after ranking
  news_recency_days: 14         # only include news from last N days
  pace_between_companies_sec: 3 # API rate limit pacing
```

---

## Architecture

```
User's accounts (Supabase)
  │
  ▼
8 research queries per company (Perplexity via SAP AI Core)
  │
  ▼
Rank by diversity + freshness (Perplexity)
  │
  ▼
Cap risk signals (max 3)
  │
  ▼
Enrich contacts (1 search per signal missing contact)
  │
  ▼
Dedup vs previous week (GPT-4o-mini)
  │
  ▼
Render email / store in Supabase / show on account page
```

---

## Dependencies

- **Perplexity API** (via SAP AI Core or direct): web search + LLM synthesis
- **GPT-4o-mini** (via SAP AI Core or LiteLLM): dedup review
- **Supabase**: user data, company lists, digest history
- **Gmail SMTP**: email delivery
- **GitHub Actions**: weekly cron (Sunday research, Monday send)
- **AWS EC2 + API Gateway**: backend for OTP auth

---

## How to extend this skill

1. **Add Salesforce contacts**: Query the SalesforceAgent for real CRM contacts per account. This pushes contact rate to 100%.
2. **Add people tracking**: Subscribe to specific individuals, research their LinkedIn posts and conference talks.
3. **Add Gong integration**: Include last call summary for each account.
4. **Add Slack delivery**: Post signals to a Slack channel instead of email.
5. **Add meeting prep**: "I have a call with BNP tomorrow" generates a one-pager.
