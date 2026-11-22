-- Persistent delivered-signal fingerprint ledger. Safe to run repeatedly.

create table if not exists sent_signal_keys (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  digest_id uuid,
  fp text not null,
  headline text,
  company text,
  sent_at timestamptz not null default now()
);

create index if not exists idx_sent_keys_user_fp
  on sent_signal_keys(user_id, fp);
create index if not exists idx_sent_keys_user_sent
  on sent_signal_keys(user_id, sent_at);
create index if not exists idx_sent_keys_digest
  on sent_signal_keys(digest_id);
