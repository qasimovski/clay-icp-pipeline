-- =============================================================================
-- 05_enrichment_columns.sql -- EVCharge company ledger: store the Enrich Company payload
--
-- WHAT THIS CHANGES (EVCharge company ledger ONLY)
--   * public.evcharge_company_ledger      +12 nullable text columns
--   * public.ledger_check_evcharge_company  replaced: 2 args -> 14 args (all optional)
--
-- WHAT IT DOES NOT TOUCH
--   * evcharge_people_ledger / ledger_check_evcharge_person
--   * the Labs ledger (company_ledger / people_ledger) and the Highways ledger
--   * existing rows (new columns start NULL)
--
-- WHY THE FUNCTION IS REPLACED, NOT EXTENDED
--   PostgREST matches an RPC by its FULL argument-name list, so a Clay body with 12 extra keys
--   against the old 2-argument function answers 404 PGRST202. The old (text, text) function is
--   dropped so there is exactly one overload (two overloads would make PostgREST answer PGRST203).
--
-- CONTRACT (unchanged): returns { is_new, skipped, key }.
--   Identity is still the normalised domain only; the 12 new values are PAYLOAD and play no part
--   in matching. First sighting wins: on a repeat call the stored values are left untouched.
--
-- Clay HTTP API body keys (insert each value with the "/" column picker, never type it):
--   "Company Domain"  <- Domain          "Name"                           <- Name
--   "Size"            <- Size            "Type"                           <- Type
--   "LinkedIn"        <- LinkedIn        "Founded"                        <- Founded
--   "Industry"        <- Industry        "Description (2)"                <- Description (2)
--   "Business Stage"  <- Business Stage  "Annual Revenue"                 <- Annual Revenue
--   "Total Funding Amount Range Usd" <- Total Funding Amount Range Usd
--   "Company Specialties" <- Company Specialties   "Subindustry"          <- Subindustry
--   "Primary Offerings"   <- Primary Offerings
--
-- Safe to re-run. Apply in the Supabase SQL editor (project "Block List").
-- Requires 01_schema.sql and 02_functions.sql (this folder) to be applied already.
-- =============================================================================

begin;

-- 1. payload columns --------------------------------------------------------------------------
alter table public.evcharge_company_ledger
  add column if not exists "Size"                           text,
  add column if not exists "Type"                           text,
  add column if not exists "LinkedIn"                       text,
  add column if not exists "Founded"                        text,
  add column if not exists "Industry"                       text,
  add column if not exists "Description (2)"                text,
  add column if not exists "Business Stage"                 text,
  add column if not exists "Annual Revenue"                 text,
  add column if not exists "Total Funding Amount Range Usd" text,
  add column if not exists "Company Specialties"            text,
  add column if not exists "Subindustry"                    text,
  add column if not exists "Primary Offerings"              text;

-- 2. replace the function ---------------------------------------------------------------------
drop function if exists public.ledger_check_evcharge_company(text, text);
drop function if exists public.ledger_check_evcharge_company(
  text, text, text, text, text, text, text, text, text, text, text, text, text, text);

create or replace function public.ledger_check_evcharge_company(
  "Company Domain"                 text default null,
  "Name"                           text default null,
  "Size"                           text default null,
  "Type"                           text default null,
  "LinkedIn"                       text default null,
  "Founded"                        text default null,
  "Industry"                       text default null,
  "Description (2)"                text default null,
  "Business Stage"                 text default null,
  "Annual Revenue"                 text default null,
  "Total Funding Amount Range Usd" text default null,
  "Company Specialties"            text default null,
  "Subindustry"                    text default null,
  "Primary Offerings"              text default null
)
returns json
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_key    text := public.normalize_domain("Company Domain");
  v_is_new boolean;
begin
  if v_key is null then
    return json_build_object('is_new', false, 'skipped', true, 'key', null);
  end if;

  with ins as (
    insert into public.evcharge_company_ledger (
      domain_key, "Name", "Company Domain",
      "Size", "Type", "LinkedIn", "Founded", "Industry", "Description (2)", "Business Stage",
      "Annual Revenue", "Total Funding Amount Range Usd", "Company Specialties", "Subindustry",
      "Primary Offerings")
    values (
      v_key,
      nullif(btrim(coalesce("Name", '')), ''),
      nullif(btrim(coalesce("Company Domain", '')), ''),
      nullif(btrim(coalesce("Size", '')), ''),
      nullif(btrim(coalesce("Type", '')), ''),
      nullif(btrim(coalesce("LinkedIn", '')), ''),
      nullif(btrim(coalesce("Founded", '')), ''),
      nullif(btrim(coalesce("Industry", '')), ''),
      nullif(btrim(coalesce("Description (2)", '')), ''),
      nullif(btrim(coalesce("Business Stage", '')), ''),
      nullif(btrim(coalesce("Annual Revenue", '')), ''),
      nullif(btrim(coalesce("Total Funding Amount Range Usd", '')), ''),
      nullif(btrim(coalesce("Company Specialties", '')), ''),
      nullif(btrim(coalesce("Subindustry", '')), ''),
      nullif(btrim(coalesce("Primary Offerings", '')), ''))
    on conflict (domain_key) do nothing
    returning 1
  )
  select exists (select 1 from ins) into v_is_new;

  return json_build_object('is_new', v_is_new, 'skipped', false, 'key', v_key);
end;
$$;

-- 3. grants: anon may EXECUTE this one function and nothing else -------------------------------
-- (revoking from PUBLIC alone is not enough on Supabase -- see ../README.md)
revoke all on function public.ledger_check_evcharge_company(
  text, text, text, text, text, text, text, text, text, text, text, text, text, text)
  from public, anon, authenticated;
grant execute on function public.ledger_check_evcharge_company(
  text, text, text, text, text, text, text, text, text, text, text, text, text, text)
  to anon, authenticated;

commit;

-- 4. self-check: run this after the block above; every line should say OK -----------------------
select
  case when (select count(*) from information_schema.columns
             where table_schema = 'public' and table_name = 'evcharge_company_ledger') = 16
       then 'OK  table has 16 columns' else 'CHECK table column count' end                as table_columns,
  case when (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
             where n.nspname = 'public' and p.proname = 'ledger_check_evcharge_company') = 1
       then 'OK  exactly one overload' else 'CHECK number of overloads' end              as overloads,
  case when has_function_privilege('anon',
         'public.ledger_check_evcharge_company(text,text,text,text,text,text,text,text,text,text,text,text,text,text)',
         'execute')
       then 'OK  anon can execute' else 'CHECK anon grant' end                           as anon_grant,
  (select count(*) from public.evcharge_company_ledger)                                  as company_rows_now;
