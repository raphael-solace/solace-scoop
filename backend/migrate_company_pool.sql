-- Canonical company and shared signal pool. Additive and safe to run repeatedly.

create table if not exists canonical_companies (
  id uuid primary key default gen_random_uuid(), canonical_name text not null,
  lookup_name text not null, primary_domain text, country_code text,
  status text not null default 'provisional'
    check (status in ('provisional','active','merged','inactive')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists idx_canonical_company_lookup on canonical_companies(lookup_name);
create unique index if not exists idx_canonical_company_domain on canonical_companies(lower(primary_domain)) where primary_domain is not null;

create table if not exists company_aliases (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references canonical_companies(id) on delete cascade,
  alias text not null, lookup_alias text not null,
  source text not null default 'subscriber_input', verified boolean not null default false,
  created_at timestamptz not null default now(), unique(company_id, lookup_alias)
);
create index if not exists idx_company_alias_lookup on company_aliases(lookup_alias);

create table if not exists company_identifiers (
  company_id uuid not null references canonical_companies(id) on delete cascade,
  scheme text not null, value text not null, verified_at timestamptz, source text,
  primary key (scheme, value)
);

create table if not exists subscriber_companies (
  user_id uuid not null references users(id) on delete cascade,
  company_id uuid not null references canonical_companies(id) on delete cascade,
  display_name text not null, priority int not null default 0,
  active boolean not null default true, created_at timestamptz not null default now(),
  primary key (user_id, company_id)
);
create index if not exists idx_subscriber_company_active on subscriber_companies(company_id, active);

create table if not exists subscriber_company_legacy_links (
  legacy_company_id uuid primary key references companies(id) on delete cascade,
  user_id uuid not null references users(id) on delete cascade,
  company_id uuid not null references canonical_companies(id) on delete cascade,
  foreign key (user_id, company_id)
    references subscriber_companies(user_id, company_id) on delete cascade
);

create table if not exists research_jobs (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references canonical_companies(id) on delete cascade,
  window_start date not null, window_end date not null, prompt_version text not null,
  status text not null default 'pending' check (status in ('pending','running','complete','partial','failed')),
  claimed_by text, claim_token text, claimed_until timestamptz,
  attempts int not null default 0,
  error_text text, queued_at timestamptz not null default now(),
  started_at timestamptz, completed_at timestamptz,
  unique(company_id, window_start, window_end, prompt_version)
);
alter table research_jobs add column if not exists claim_token text;
create index if not exists idx_research_jobs_queue on research_jobs(status, claimed_until, queued_at);

create table if not exists shared_signals (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references canonical_companies(id) on delete cascade,
  event_key text not null, event_date date not null, category text,
  headline text not null, factual_summary text, signal_strength int,
  confidence numeric, research_job_id uuid references research_jobs(id) on delete set null,
  raw_payload jsonb not null default '{}', first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(), expires_at timestamptz not null,
  unique(company_id, event_key)
);
create index if not exists idx_shared_signals_company_date on shared_signals(company_id, event_date desc);

create table if not exists signal_sources (
  id uuid primary key default gen_random_uuid(),
  signal_id uuid not null references shared_signals(id) on delete cascade,
  source_type text not null, source_item_id text, source_url text not null,
  canonical_url text not null, publisher text, title text, excerpt text,
  published_at timestamptz, updated_at timestamptz,
  retrieved_at timestamptz not null default now(), raw_payload_hash text,
  unique(signal_id, canonical_url)
);
create unique index if not exists idx_signal_source_native_id on signal_sources(source_type, source_item_id) where source_item_id is not null;

create table if not exists subscriber_signals (
  user_id uuid not null references users(id) on delete cascade,
  signal_id uuid not null references shared_signals(id) on delete cascade,
  state text not null default 'eligible' check (state in ('eligible','selected','delivered','suppressed')),
  relevance_score numeric, so_what text, outreach_message text,
  suppression_reason text, first_eligible_at timestamptz not null default now(),
  delivered_at timestamptz, primary key (user_id, signal_id)
);
create index if not exists idx_subscriber_signals_state on subscriber_signals(user_id, state, relevance_score desc);

create table if not exists source_catalog (
  id uuid primary key default gen_random_uuid(), name text unique not null,
  adapter_type text not null, endpoint_template text not null,
  terms_url text, robots_url text, licence text,
  storage_mode text not null default 'metadata_excerpt_link',
  enabled boolean not null default false, review_status text not null default 'pending',
  poll_minutes int not null default 1440,
  max_requests_per_second numeric not null default 0.5,
  config jsonb not null default '{}', created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists company_sources (
  company_id uuid not null references canonical_companies(id) on delete cascade,
  source_id uuid not null references source_catalog(id) on delete cascade,
  external_id text, endpoint_override text, enabled boolean not null default true,
  etag text, last_modified text, cursor jsonb, last_success_at timestamptz,
  last_error text, primary key (company_id, source_id)
);

create table if not exists source_items (
  id uuid primary key default gen_random_uuid(),
  source_id uuid not null references source_catalog(id) on delete cascade,
  company_id uuid references canonical_companies(id) on delete cascade,
  source_item_id text not null, source_url text not null, title text not null,
  excerpt text, published_at timestamptz, source_updated_at timestamptz,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(), content_hash text,
  raw_payload jsonb not null default '{}',
  processing_status text not null default 'pending'
    check (processing_status in ('pending','classified','ignored','failed')),
  unique(source_id, source_item_id)
);
create index if not exists idx_source_items_pending on source_items(processing_status, published_at desc);
create index if not exists idx_source_items_company on source_items(company_id, published_at desc);
