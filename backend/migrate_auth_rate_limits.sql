-- Bound unauthenticated OTP email sends and code guesses per address.
-- Safe to run repeatedly.

alter table auth_tokens add column if not exists attempts int not null default 0;

create index if not exists idx_auth_tokens_email_created
  on auth_tokens(email, created_at desc);
