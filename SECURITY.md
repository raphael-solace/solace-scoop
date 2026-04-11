# Security Policy

## Reporting Vulnerabilities

If you find a security vulnerability, email raphael.caillon@solace.com directly. Do not open a public issue.

## Secrets

- `.env` is gitignored and never committed
- Pre-commit hook (`.githooks/pre-commit`) blocks patterns matching API keys and passwords
- TruffleHog CI scans every push and PR
- Frontend uses Supabase anon key only, routed through RPC function (SECURITY DEFINER)
- Backend uses service role key (never exposed to frontend)

## Supabase

- Row-Level Security enabled on all tables
- Anon key can only call `public.signup()` RPC function
- Service role key used only in backend/GitHub Actions
