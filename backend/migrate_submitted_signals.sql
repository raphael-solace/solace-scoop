create table if not exists submitted_signals (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  company text not null,
  linkedin_post_id text not null,
  linkedin_url text not null,
  note text not null,
  headline text not null,
  source_url text not null,
  source_kind text not null check (source_kind in ('company', 'trade_press')),
  source_published_on date not null,
  created_at timestamptz not null default now(),
  unique (user_id, linkedin_post_id)
);

create index if not exists idx_submitted_signals_user_date
  on submitted_signals (user_id, source_published_on desc, created_at desc);
