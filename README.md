# Solace Scoop

**Account intelligence for Solace colleagues** — powered by Solace Agent Mesh.

Twelve research queries cover each customer account, including champions & stakeholders, EDA & integration signals, IT initiatives, partner activity, recent news, and competitive threats. A weekly email digest delivers fresh, sourced signals and suggested next steps.

**[Live demo](https://raphael-solace.github.io/solace-scoop/)**

---

## How it works

1. Colleagues manage their customer accounts through the account page and FastAPI API, backed by RDS
2. EventBridge launches the weekly pipeline in ECS Fargate
3. Perplexity sonar-pro searches run through SAP AI Core, with bounded query concurrency
4. A three-layer quality gate filters out stale, vague, or repeated signals
5. A Solace-branded email lands in the colleague's inbox with actionable intel

## Architecture

```
Account page / FastAPI / RDS PostgreSQL
    |
    v
[ECS Digest Pipeline - 12 research queries per company]
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
2. **History dedup** — checks source URLs, headlines, and event fingerprints against 12 weeks of delivered signals
3. **LLM review** — GPT-4o-mini reviews repeated events through SAP AI Core, with a LiteLLM fallback

Weekly readiness also checks account ownership, specific source links, delivery-day freshness, candidate uniqueness, and email rendering.

## Configuration

All tunable parameters live in `backend/config.yaml`:

```yaml
digest:
  companies_per_user: 20
  max_signals_per_email: 24
  news_recency_days: 14
```

## Setup

```bash
cp .env.example .env        # configure a reachable Postgres database and providers
cd backend
pip install -r requirements.txt
python run_migrations.py
python run_digest.py --research-only
python run_digest.py --preflight
```

## Production recovery

Production commands run in the ECS digest task with its Secrets Manager configuration and private RDS networking. `--research-only` resumes missing or invalid candidates and keeps ready candidates. A database advisory lock serializes scheduled research and recovery. `--preflight` validates the batch. `--send-only` claims validated candidates atomically before delivery.

| Weekly job | UTC schedule |
| --- | --- |
| Research | Sunday 18:00 |
| Recovery | Sunday 22:00 |
| Recovery | Monday 03:00 |
| Delivery | Monday 06:00 |

Failed research produces a nonzero task exit and an owner readiness report. AI Core tokens use the issuer's expiry, refresh ahead of expiry, and retry one unauthorized completion with a refreshed token. Throttled requests receive longer backoff.

`deploy.sh` updates the API, digest task, scheduler targets, and scheduler task permissions. Terraform manages the schedules under `infra/`. The public HTTPS API origin is configured through `public_api_url` and supplies email feedback links.

The deployment script carries forward running task revisions during infrastructure preparation. Scheduler permissions cover the old and new revisions during the switch. Existing ALB attachments and ingress rules remain owned by the public routing setup. Deployment checks verify the requested API revision after ECS stabilizes.

Run the focused regression suite from the repository root with `PYTHONPATH=backend python3 -m pytest -q backend/test_aicore.py backend/test_accounts.py backend/test_admin.py backend/test_readiness.py backend/test_submitted_signals.py`.

## Tech stack

- **Frontend**: Vanilla HTML/CSS/JS — GitHub Pages
- **Backend**: Python / FastAPI on ECS Fargate, scheduled through EventBridge
- **AI**: SAP AI Core with Perplexity sonar-pro and GPT-4o-mini, plus configured direct API fallbacks
- **Database**: Amazon RDS PostgreSQL via asyncpg
- **Email**: Office 365 SMTP with Gmail fallback, HTML and plain-text versions
