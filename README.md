# Scoop 🐶🗞️

**Actionable account news for sales teams.**

Scoop monitors your key accounts and emails you a weekly brief with the signals that matter: leadership changes, tech initiatives, hiring surges, vendor moves. Each signal includes **why it matters for your deal** and **what to do next**.

**[Try it free](https://noptus.github.io/Scoop/)** &middot; No credit card, no install, just email.

---

## How it works

1. Enter your email, what you sell, and your top 10 accounts
2. Scoop researches your company to understand your buyers and triggers
3. Every Monday, you get an email with the most actionable signals across your accounts
4. Reply to any digest to ask follow-up questions (powered by Perplexity)

## What makes it different

| Google Alerts | Scoop |
|---|---|
| Raw links, no context | "Why this matters for your deal" |
| Every mention, mostly noise | Only IT, architecture, and leadership signals |
| Same alert for everyone | Tailored to what you sell and who you sell to |
| No action items | "Reach out to [Name], [Title] about..." |

## Example signal

> **[People Move] Renault**
>
> Philippe Brunet was appointed CTO of Renault Group, managing engineering across Renault and Ampere to accelerate innovation and coordination with manufacturing and supply chain.
>
> **Why this matters:** New CTO focused on engineering transformation. He's evaluating the tech stack.
>
> **&rarr; Email Philippe Brunet to introduce your platform for real-time integration across manufacturing and supply chain.**

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

# Generate a demo signal for LinkedIn outreach
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

## How the pipeline works

Scoop runs **multiple Perplexity queries per company** to find different signal types in parallel:

| Query | What it finds |
|---|---|
| People moves | CTO/CIO/VP Eng appointments, IT leadership changes with names and titles |
| Tech initiatives | Cloud migrations, platform modernization, AI deployments, RFPs |
| Hiring signals | Job postings for architects, platform engineers, AI/ML roles |
| Partnerships | Vendor selections, technology partnerships, competitor deployments |

Before querying accounts, Scoop first **researches the seller's company** to understand what they sell, who their buyers are, and what events create opportunities. This means the same account produces completely different signals for different sellers.

## Cost

At 100 users, 10 accounts each:
- **Perplexity:** ~1,300 queries/week (13 per user) = ~$4/week
- **Gmail:** Free (500 emails/day)
- **Supabase:** Free tier
- **GitHub Actions:** Free tier (2,000 min/month)
- **Total: ~$16/month**

## Contributing

Scoop is open source under the MIT license. Contributions welcome.

**Areas where help is most needed:**
- [ ] Stripe Checkout integration for paid plans
- [ ] Magic link auth (passwordless login)
- [ ] Slack bot (push signals to a channel)
- [ ] CRM integration (Salesforce, HubSpot)
- [ ] Company dedup (two users tracking the same account share query results)
- [ ] Dashboard for managing accounts and viewing past digests
- [ ] More signal sources beyond Perplexity (LinkedIn, Crunchbase, job boards)

To contribute: fork, branch, PR. Keep it simple.

## License

MIT
