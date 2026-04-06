# Scoop

**Account intelligence for sales teams.** Scoop monitors your key accounts and delivers a weekly brief so you never walk into a customer call unprepared.

> *Your customers are moving. Scoop tells you before your CRM does.*

## What it does

1. You tell Scoop which accounts you're watching and what you sell
2. Scoop scans the web for signals: exec changes, funding, M&A, layoffs, product launches, regulatory shifts
3. Every Monday you get an email digest with each signal and a **"why this matters to your deal"** insight

## Live site

**[noptus.github.io/scoop](https://noptus.github.io/scoop)**

## Project structure

```
docs/                    ← Landing page (GitHub Pages)
  index.html
  css/style.css
  js/app.js
  favicon.svg

backend/                 ← API server (FastAPI)
  main.py                  Endpoints: /api/preview, /api/digest, /health
  digest.py                Perplexity pipeline + signal synthesis
  templates/
    email.html             Jinja2 email template (email-client safe)
  requirements.txt

.env.example             ← All environment variables documented
brief.md                 ← Original product brief
```

## Quick start

### Landing page (static)

The `docs/` folder is served by GitHub Pages. No build step — just HTML, CSS, and JS.

To develop locally, open `docs/index.html` in a browser or use any static server:

```bash
cd docs && python -m http.server 8080
```

### Backend API

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env  # fill in your keys
uvicorn main:app --reload --port 8000
```

The API runs at `http://localhost:8000`. Docs at `/docs` (Swagger UI).

To connect the frontend to the backend, set `CONFIG.apiUrl` in `docs/js/app.js`:

```js
const CONFIG = {
  apiUrl: 'http://localhost:8000',
};
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `PPLX_KEY` | Yes | Perplexity API key |
| `RESEND_KEY` | No | Resend API key (email delivery) |
| `STRIPE_SECRET_KEY` | No | Stripe secret key (payments) |
| `STRIPE_PRICE_ID` | No | Stripe price ID for the $10/mo plan |
| `STRIPE_WEBHOOK_SECRET` | No | Stripe webhook signing secret |
| `SUPABASE_URL` | No | Supabase project URL (database) |
| `SUPABASE_KEY` | No | Supabase anon key |
| `CRON_SECRET` | No | Shared secret for cron-triggered digest runs |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins |

## Roadmap

### Ready now
- [x] Landing page with onboarding form
- [x] Perplexity-powered digest pipeline
- [x] Email template (email-client compatible HTML)
- [x] Personalized example digest on signup

### Next up
- [ ] **Resend integration** — Send real weekly emails
- [ ] **Supabase** — Store users, companies, preferences
- [ ] **Stripe Checkout** — $10/month billing
- [ ] **Magic link auth** — Passwordless login via email
- [ ] **Weekly cron** — GitHub Actions or Railway cron job

### Later
- [ ] Slack bot integration
- [ ] CRM sync (Salesforce, HubSpot)
- [ ] Team dashboard
- [ ] Company deduplication across users (cost optimization)

## Architecture decisions

| Decision | Rationale |
|---|---|
| **Perplexity sonar-pro** | Live web search + synthesis in one API call. ~$0.003/query. No crawling infrastructure needed. |
| **Static frontend** | Zero build step, instant deploy via GitHub Pages. Swap to Next.js/Remix when you need SSR. |
| **FastAPI backend** | Async, typed, auto-generates OpenAPI docs. Easy to deploy anywhere. |
| **No auth yet** | Validates product-market fit before adding complexity. Magic link stubs are in place. |
| **No Stripe yet** | Same reason. Checkout URL is a config variable away from working. |

## Cost model

At 100 Pro users (10 accounts each):
- Perplexity: 1,000 queries/week = ~$3/week
- Resend: ~$20/month
- **Total infra: ~$32/month** against ~$1,000 MRR

## License

MIT
