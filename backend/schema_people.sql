-- People tracking table
-- Users can subscribe to specific people at their accounts
-- Run this in Supabase SQL Editor

create table if not exists people (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  company text not null,           -- which account they belong to
  name text not null,              -- full name
  title text,                      -- job title
  email text,                      -- work email
  linkedin text,                   -- LinkedIn profile URL
  salesforce_url text,             -- Salesforce contact link
  created_at timestamptz not null default now()
);

create index if not exists idx_people_user on people(user_id);

-- RLS: allow anon to read/write (same as other tables)
alter table people enable row level security;
create policy "Anon manage people" on people
  for all to anon using (true) with check (true);
