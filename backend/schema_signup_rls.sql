-- Signup RPC function (SECURITY DEFINER)
-- Callable by anon key but runs with service-role permissions.
-- This is the ONLY way anon can create users/companies.
--
-- Run this in the Supabase SQL Editor after schema.sql.

create or replace function public.signup(
  p_email text,
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
  -- Validate email format (basic check)
  if p_email !~ '^[^@]+@[^@]+\.[^@]+$' then
    return json_build_object('error', 'Invalid email');
  end if;

  -- Validate product length
  if length(p_product) > 500 then
    return json_build_object('error', 'Product description too long');
  end if;

  -- Create user (ignore if exists)
  insert into users (email, product)
  values (p_email, p_product)
  on conflict (email) do nothing
  returning id into v_user_id;

  -- If user already existed, get their id
  if v_user_id is null then
    select id into v_user_id from users where email = p_email;
  end if;

  -- Insert companies (max 10)
  foreach v_company in array p_companies[1:10]
  loop
    if length(v_company) > 0 and length(v_company) <= 200 then
      insert into companies (user_id, name)
      values (v_user_id, trim(v_company));
    end if;
  end loop;

  return json_build_object('status', 'ok', 'user_id', v_user_id);
end;
$$;

-- Grant anon access to call the function
grant execute on function public.signup(text, text, text[]) to anon;
