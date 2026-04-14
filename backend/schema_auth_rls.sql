-- Supabase Auth RLS policies for account management
-- Run this in Supabase SQL Editor AFTER enabling Supabase Auth
--
-- This adds an auth_user_id column to users table that links
-- to Supabase Auth's auth.users table.

-- Add auth link column
alter table users add column if not exists auth_user_id uuid references auth.users(id);
create unique index if not exists idx_users_auth on users(auth_user_id);

-- Function to link auth user to app user on first login
create or replace function public.handle_auth_login()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Link auth user to existing app user by email match
  update users
  set auth_user_id = new.id
  where email = new.email
  and auth_user_id is null;
  return new;
end;
$$;

-- Trigger: when a new auth user is created, link to app user
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_auth_login();

-- RLS policies using auth.uid()
-- Users can read/update their own row
drop policy if exists "Users read own" on users;
create policy "Users read own" on users
  for select using (auth_user_id = auth.uid());

drop policy if exists "Users update own" on users;
create policy "Users update own" on users
  for update using (auth_user_id = auth.uid());

-- Companies: user can read, insert, delete their own
drop policy if exists "Companies read own" on companies;
create policy "Companies read own" on companies
  for select using (user_id in (select id from users where auth_user_id = auth.uid()));

drop policy if exists "Companies insert own" on companies;
create policy "Companies insert own" on companies
  for insert with check (user_id in (select id from users where auth_user_id = auth.uid()));

drop policy if exists "Companies delete own" on companies;
create policy "Companies delete own" on companies
  for delete using (user_id in (select id from users where auth_user_id = auth.uid()));

-- Digests: user can read their own
drop policy if exists "Digests read own" on digests;
create policy "Digests read own" on digests
  for select using (user_id in (select id from users where auth_user_id = auth.uid()));

-- Function for authenticated account update (replaces companies)
create or replace function public.update_account(
  p_product text,
  p_companies text[]
) returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid;
  v_company text;
begin
  -- Get the app user linked to this auth user
  select id into v_user_id
  from users
  where auth_user_id = auth.uid();

  if v_user_id is null then
    return json_build_object('error', 'User not found');
  end if;

  -- Update product
  update users set product = p_product, updated_at = now()
  where id = v_user_id;

  -- Replace companies
  delete from companies where user_id = v_user_id;

  foreach v_company in array p_companies[1:50]
  loop
    if length(trim(v_company)) > 0 and length(trim(v_company)) <= 200 then
      insert into companies (user_id, name)
      values (v_user_id, trim(v_company));
    end if;
  end loop;

  return json_build_object('status', 'ok');
end;
$$;

grant execute on function public.update_account(text, text[]) to authenticated;
