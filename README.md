# Solace Scoop

**Account intelligence for Solace colleagues** — powered by Solace Agent Mesh.

Six AI agents research your customer accounts in parallel: champions & stakeholders, EDA & integration signals, IT initiatives, partner activity, recent news, and competitive threats. A weekly email digest delivers only fresh, verified signals — no stale data, no repeats.

**[Live demo](https://raphael-solace.github.io/solace-scoop/)**

---

## How it works

1. Colleagues are set up with their customer accounts (via `contacts.md` or `setup_accounts.py`)
2. Every week, GitHub Actions runs the digest pipeline for each user
3. Six agents research each account via Perplexity AI (sonar-pro)
4. A three-layer quality gate filters out stale, vague, or repeated signals
5. A Solace-branded email lands in the colleague's inbox with actionable intel

## Architecture

```
contacts.md / Supabase
    |
    v
[Digest Pipeline - 6 agents per company]
    |
    +---> Champions & Stakeholders
    +---> EDA & Integration
    +---> IT Initiatives
    +---> Partner Activity (Accenture, Deloitte...)
    +---> Recent News (last 14 days only)
    +---> Risks & Competitive (Confluent, TIBCO, IBM MQ...)
    |
    v
[Ranking] → [Date Filter] → [Dedup] → [LLM Review]
    |
    v
Solace-branded email digest
```

## Quality gate

Signals pass through three layers before reaching an inbox:

1. **Date filter** — drops signals with dates older than 14 days and background info
2. **Headline dedup** — drops signals matching previous digest headlines (Jaccard similarity >50%)
3. **LLM review** — cheap model (azure-gpt-4o-mini via LiteLLM) drops vague, undated, or repeated signals

## Configuration

All tunable parameters live in `backend/config.yaml`:

```yaml
digest:
  companies_per_user: 10
  signals_per_email: 10
  news_recency_days: 14
```

## Setup

```bash
cp .env.example .env        # fill in API keys
cd backend
pip install -r requirements.txt
python setup_accounts.py     # create users from accounts CSV
python run_digest.py         # run digest for all users
```

## Tech stack

- **Frontend**: Vanilla HTML/CSS/JS — GitHub Pages
- **Backend**: Python / FastAPI — GitHub Actions cron
- **AI**: Perplexity API (sonar-pro) + LiteLLM (dedup review)
- **Database**: Supabase (PostgreSQL)
- **Email**: Gmail SMTP
