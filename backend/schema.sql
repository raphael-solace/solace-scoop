-- Scoop — Supabase Schema
-- Run this in the Supabase SQL editor to set up the database.

-- Users table
create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text unique not null,
  product text not null,              -- what they sell
  plan text not null default 'free',  -- free | pro | team
  stripe_customer_id text,            -- set after Stripe checkout
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Companies being tracked
create table if not exists companies (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  name text not null,
  created_at timestamptz not null default now()
);

-- Digest history (for dedup and analytics)
create table if not exists digests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  sent_at timestamptz not null default now(),
  item_count int not null default 0,
  items jsonb not null default '[]'
);

-- Magic link tokens (for passwordless auth)
create table if not exists auth_tokens (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  token text unique not null,
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);

-- Indexes
create index if not exists idx_companies_user on companies(user_id);
create index if not exists idx_digests_user on digests(user_id);
create index if not exists idx_auth_tokens_token on auth_tokens(token);
create index if not exists idx_auth_tokens_email on auth_tokens(email);
create index if not exists idx_users_email on users(email);

-- Row-level security (enable after setting up auth)
-- alter table users enable row level security;
-- alter table companies enable row level security;
-- alter table digests enable row level security;
