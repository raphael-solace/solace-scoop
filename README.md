# Scoop 🐶🗞️

**Actionable account news for sales teams.**

Scoop monitors your key accounts and emails you a weekly brief with the signals that matter: leadership changes, new initiatives, hiring surges, funding rounds, partnerships, and more. Each signal includes **why it matters for your deal** and **what to do next**.

Works for any industry. SaaS, insurance, manufacturing, consulting, financial services, healthcare, logistics. If you sell B2B, Scoop works for you.

**[Try it free](https://noptus.github.io/Scoop/)** &middot; No credit card, no install, just email.

---

## How it works

1. Enter your email, what you sell, and your top 10 accounts
2. Scoop researches your company to understand your buyers and what triggers a sale
3. Every Monday, you get an email with the most actionable signals across your accounts
4. Reply to any digest to ask follow-up questions

## What makes it different

| Google Alerts | Scoop |
|---|---|
| Raw links, no context | "Why this matters for your deal" |
| Every mention, mostly noise | Only signals relevant to what you sell |
| Same alert for everyone | Tailored to your product, your buyers, your industry |
| No action items | "Reach out to [Name], [Title] about..." |

## Examples across industries

**SaaS sales rep selling a data platform:**
> **[People Move] Acme Corp** — New VP of Engineering appointed. Previously led cloud migration at a Fortune 500. He's likely re-evaluating the data stack.
>
> &rarr; *Reach out to introduce your platform before vendor reviews start.*

**Insurance broker tracking mid-market accounts:**
> **[Expansion] Meridian Logistics** — Opened 3 new distribution centers in the Southeast and hired 200 warehouse staff.
>
> &rarr; *Contact their Director of Risk. New facilities mean new coverage needs.*

**Consulting firm watching enterprise clients:**
> **[Strategic] Northwind Financial** — Announced a $40M digital transformation program to modernize core banking systems by 2027.
>
> &rarr; *Reach out to the program director. They'll need implementation partners.*

**Manufacturing supplier tracking OEM accounts:**
> **[M&A] Titan Automotive** — Acquired a battery component supplier in South Korea, signaling a push into EV production.
>
> &rarr; *Contact their Head of Procurement. Supply chain is being rebuilt.*

---

## Self-host Scoop (open source)

Scoop is fully open source. Run your own instance with a Perplexity API key and a Gmail account.

### Quick start

```bash
git clone https://github.com/Noptus/Scoop.git
cd Scoop/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in your keys
```

### Required setup

**1. Perplexity API key** (for intelligence)
- Sign up at [perplexity.ai](https://perplexity.ai)
- Get an API key, add to `.env` as `PPLX_KEY`

**2. Gmail App Password** (for sending emails)
- Enable 2FA at [myaccount.google.com](https://myaccount.google.com)
- Create an App Password under Security > App passwords
- Add `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` to `.env`

**3. Supabase** (for storing users, free tier)
- Create a project at [supabase.com](https://supabase.com)
- Run `backend/schema.sql` in the SQL Editor
- Add `SUPABASE_URL` and `SUPABASE_KEY` to `.env`

### Run locally

```bash
# Start the API server
cd backend && uvicorn main:app --reload --port 8000

# Send a test digest to yourself
python -c "
import asyncio
from dotenv import load_dotenv; load_dotenv('../.env')
from digest import generate_digest_preview
from send_email import send_digest_email

async def test():
    user = {
        'email': 'you@example.com',
        'product': 'Your Product',
        'companies': [{'name': 'Acme Corp'}, {'name': 'Globex'}],
    }
    items = await generate_digest_preview(['Acme Corp', 'Globex'], 'Your Product')
    await send_digest_email(user, items)

asyncio.run(test())
"

# Generate a demo signal for outreach
python demo_signal.py "Datadog" "observability platform"
```

### Automate with GitHub Actions

Add these secrets to your repo (Settings > Secrets > Actions):

| Secret | Description |
|---|---|
| `PPLX_KEY` | Perplexity API key |
| `GMAIL_ADDRESS` | Gmail address for sending |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon/service key |

Two workflows are included:
- **Weekly Digest** (`digest.yml`) — runs every Sunday 11pm UTC, sends Monday morning digests
- **Reply Handler** (`replies.yml`) — checks for email replies every 15 min, answers with Perplexity

---

## How the pipeline works

Scoop adapts to any industry automatically. When you tell it what you sell, it:

1. **Researches your company** — understands your product, your typical buyers (by job title), and what events at a prospect would create an opportunity
2. **Runs 4 parallel searches per account** — each looking for a different signal type:

| Query | What it finds |
|---|---|
| People moves | New decision-makers, leadership changes relevant to your sale |
| Business initiatives | Expansions, transformations, new programs, budget changes |
| Hiring signals | Job postings that reveal where a company is investing |
| Partnerships & vendors | New relationships, vendor changes, competitive moves |

3. **Ranks and deduplicates** — picks the most actionable signals, drops the noise

The same account produces completely different signals for different sellers. An insurance broker and a SaaS rep tracking the same company get different digests because their buyers and triggers are different.

## Cost

At 100 users, 10 accounts each:
- **Perplexity:** ~1,300 queries/week (13 per user) = ~$4/week
- **Gmail:** Free (500 emails/day)
- **Supabase:** Free tier
- **GitHub Actions:** Free tier (2,000 min/month)
- **Total: ~$16/month**

## Project structure

```
docs/                         Landing page (GitHub Pages)
  index.html                    Single-page signup + info
  css/style.css                 Styles
  js/app.js                     Form handling

backend/                      Python backend
  main.py                       FastAPI: /api/subscribe, /api/digest
  digest.py                     Multi-query Perplexity pipeline
  db.py                         Supabase REST wrapper
  send_email.py                 Gmail SMTP delivery
  reply_handler.py              IMAP polling + AI-powered replies
  run_digest.py                 CLI for cron jobs
  demo_signal.py                Generate signals for outreach
  schema.sql                    Database migration
  requirements.txt              Python dependencies

.github/workflows/
  digest.yml                    Weekly digest cron
  replies.yml                   Reply handler cron

outreach/
  STRATEGY.md                   LinkedIn go-to-market playbook
```

## Contributing

Scoop is MIT licensed. Contributions welcome.

**Areas where help is most needed:**
- [ ] Stripe Checkout integration for paid plans
- [ ] Magic link auth (passwordless login)
- [ ] Slack bot (push signals to a channel)
- [ ] CRM integration (Salesforce, HubSpot)
- [ ] Company dedup (two users tracking the same account share query results)
- [ ] Dashboard for managing accounts and viewing past digests
- [ ] More signal sources (LinkedIn, Crunchbase, job boards, SEC filings)
- [ ] Industry-specific signal templates (healthcare, finance, manufacturing)

To contribute: fork, branch, PR. Keep it simple.

## License

MIT
