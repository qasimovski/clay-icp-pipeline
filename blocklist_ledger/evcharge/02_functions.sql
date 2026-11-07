-- ============================================================================
-- EVCharge blocklist ledger for Clay
-- evcharge/02_functions.sql  --  the single-call check-and-insert RPCs
--
-- Run SECOND, after evcharge/01_schema.sql. Idempotent: safe to re-run.
--
-- DEPENDS ON THE LABS FILES. normalize_domain() and normalize_linkedin() are
-- NOT redefined here -- they are reused from ../02_functions.sql, which must
-- already be applied in this project. One definition means the two ICPs can
-- never drift into normalizing the same domain differently, and it keeps the
-- regression suite that hardened those functions (the four malformed rows in
-- the 51k CSV) authoritative for both. The guard below fails loudly rather
-- than letting a missing normalizer surface as a mysterious NULL key.
--
-- ARGUMENT NAMES ARE DELIBERATELY IDENTICAL to the Labs RPCs. PostgREST maps
-- JSON body keys to argument names, so keeping them identical means the
-- EVCharge Clay column differs from the Labs one by the URL alone -- same
-- headers, same account, same body, same response mapping. Do not "improve"
-- these names.
--
-- WHY RPC INSTEAD OF PostgREST's TABLE UPSERT: see ../02_functions.sql. Short
-- version: no `Prefer` header needed, and an explicit boolean back instead of
-- making Clay distinguish [] from [{...}].
--
-- THE LOOKUP AND THE INSERT ARE THE SAME STATEMENT.
--   `INSERT ... ON CONFLICT DO NOTHING` against the unique index on the
--   normalized key IS the lookup:
--       key already present -> conflict -> DO NOTHING -> is_new=false
--       key absent          -> row inserted           -> is_new=true
--   Atomic under Clay's concurrency, one HTTP call, one Clay Action.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- Precondition guard -- the shared normalizers must exist.
-- ---------------------------------------------------------------------------
do $$
begin
  if to_regprocedure('public.normalize_domain(text)') is null
     or to_regprocedure('public.normalize_linkedin(text)') is null then
    raise exception
      'normalize_domain/normalize_linkedin are missing. Apply blocklist_ledger/02_functions.sql (the Labs file) in this project first -- the EVCharge RPCs reuse them.';
  end if;
end $$;


-- ---------------------------------------------------------------------------
-- Drop earlier signatures. A signature change cannot be handled by CREATE OR
-- REPLACE, and leaving both versions would make PostgREST ambiguous.
-- ---------------------------------------------------------------------------
drop function if exists public.ledger_check_evcharge_company(text, text);
drop function if exists public.ledger_check_evcharge_person(text, text);


-- ---------------------------------------------------------------------------
-- ledger_check_evcharge_company("Company Domain", "Name") -> json
--
-- Returns: { is_new, skipped, key }
--   is_new=true   first EVCharge sighting of this domain -- this call recorded
--                 it. Enrich.
--   is_new=false  domain already in the EVCharge ledger. Skip enrichment.
--   skipped=true  no usable domain in the input; NOTHING was written.
--
-- "Name" is stored as payload and plays no part in matching, so 'Acme Inc',
-- 'Acme, Inc.' and 'ACME' on the same domain are ONE record.
--
-- On a repeat call the stored "Name"/"Company Domain" are left untouched: they
-- record the FIRST sighting, not the latest.
--
-- Idempotent, so rerunning errored Clay cells is safe -- but a rerun of a row
-- that already succeeded returns is_new=false. Treat is_new=true as write-once
-- on the Clay side.
--
-- Reads and writes public.evcharge_company_ledger ONLY. It cannot see the Labs
-- ledger, so a domain worked under Labs is still new here.
-- ---------------------------------------------------------------------------
create or replace function public.ledger_check_evcharge_company(
  "Company Domain" text default null,
  "Name"           text default null
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
    insert into public.evcharge_company_ledger (domain_key, "Name", "Company Domain")
    values (v_key, nullif(btrim(coalesce("Name",'')), ''), nullif(btrim(coalesce("Company Domain",'')), ''))
    on conflict (domain_key) do nothing
    returning 1
  )
  select exists (select 1 from ins) into v_is_new;

  return json_build_object('is_new', v_is_new, 'skipped', false, 'key', v_key);
end;
$$;


-- ---------------------------------------------------------------------------
-- ledger_check_evcharge_person("Linkedin Profile", "Full Name") -> json
-- Same contract; identity is the LinkedIn slug, "Full Name" is payload.
--
-- skipped=true here means no /in/ slug could be extracted -- a blank cell, a
-- company page, or a malformed URL. Watch this count: those people are NOT
-- being blocklisted.
-- ---------------------------------------------------------------------------
create or replace function public.ledger_check_evcharge_person(
  "Linkedin Profile" text default null,
  "Full Name"        text default null
)
returns json
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_key    text := public.normalize_linkedin("Linkedin Profile");
  v_is_new boolean;
begin
  if v_key is null then
    return json_build_object('is_new', false, 'skipped', true, 'key', null);
  end if;

  with ins as (
    insert into public.evcharge_people_ledger (linkedin_key, "Full Name", "Linkedin Profile")
    values (v_key, nullif(btrim(coalesce("Full Name",'')), ''), nullif(btrim(coalesce("Linkedin Profile",'')), ''))
    on conflict (linkedin_key) do nothing
    returning 1
  )
  select exists (select 1 from ins) into v_is_new;

  return json_build_object('is_new', v_is_new, 'skipped', false, 'key', v_key);
end;
$$;


-- ---------------------------------------------------------------------------
-- GRANTS
--
-- anon may EXECUTE these two functions and nothing else.
--
-- NOTE: `revoke ... from public` alone is NOT sufficient on Supabase -- its
-- default privileges grant EXECUTE directly to anon/authenticated, and revoking
-- from PUBLIC does not remove a direct role grant. Both revokes are required.
-- (Caught in Labs testing, not in review -- see ../README.md.)
-- ---------------------------------------------------------------------------
revoke all on function public.ledger_check_evcharge_company(text, text) from public, anon, authenticated;
revoke all on function public.ledger_check_evcharge_person(text, text)  from public, anon, authenticated;

grant execute on function public.ledger_check_evcharge_company(text, text) to anon, authenticated;
grant execute on function public.ledger_check_evcharge_person(text, text)  to anon, authenticated;
