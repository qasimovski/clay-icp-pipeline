-- ============================================================================
-- Central Blocklist ledger for Clay
-- 02_functions.sql  --  normalizers + the single-call check-and-insert RPCs
--
-- Run SECOND, after 01_schema.sql. Idempotent: safe to re-run.
--
-- WHY RPC INSTEAD OF PostgREST's TABLE UPSERT:
--   PostgREST's upsert needs a `Prefer` header, which Clay's docs do not confirm
--   is accepted. POST /rest/v1/rpc/<fn> needs no Prefer header at all and returns
--   an explicit boolean instead of forcing Clay to distinguish [] from [{...}].
--   Confirmed working over real HTTP.
--
-- THE LOOKUP AND THE INSERT ARE THE SAME STATEMENT.
--   There is no separate lookup step that could normalize differently from the
--   insert. `INSERT ... ON CONFLICT DO NOTHING` against the unique index on the
--   normalized key IS the lookup:
--       key already present -> conflict -> DO NOTHING -> is_new=false
--       key absent          -> row inserted           -> is_new=true
--   That is what makes it atomic under Clay's concurrency, and why it stays one
--   HTTP call and one Clay Action.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- Drop earlier signatures. A signature change cannot be handled by CREATE OR
-- REPLACE, and leaving both versions would make PostgREST ambiguous.
-- ---------------------------------------------------------------------------
drop function if exists public.ledger_check_company(text, text);
drop function if exists public.ledger_check_person(text, text);
drop function if exists public.ledger_check_company(text, text, text);
drop function if exists public.ledger_check_person(text, text, text);


-- ---------------------------------------------------------------------------
-- normalize_domain(text) -> text
--
-- Reduces every shape Clay actually emits to one canonical bare domain:
--   'https://WWW.Acme.com/careers?x=1'  ->  'acme.com'
--   'www.acme.com'                      ->  'acme.com'
--   'acme.com'                          ->  'acme.com'
--   'HTTP://Acme.COM:8080/'             ->  'acme.com'
--   '  acme.com.  '                     ->  'acme.com'
-- Returns NULL when nothing usable remains.
--
-- IMMUTABLE: depends only on its input, so it is safe in indexes and CHECKs.
-- ---------------------------------------------------------------------------
-- Real malformed rows this must survive (all four are in the 51k CSV):
--   'https://www. psiplus.co.kr'   -> 'psiplus.co.kr'      (space after www.)
--   'http: //www.yuandingmed.com'  -> 'yuandingmed.com'    (space inside scheme)
--   'https://www. TECHNOMEDINDIA.org' -> 'technomedindia.org'
-- Stripping ALL whitespace first is what repairs these: a domain can never
-- contain a space, so removing them is lossless, and it lets the scheme/www
-- patterns match text that was previously broken up by stray spaces.
create or replace function public.normalize_domain(p_input text)
returns text
language sql
immutable
as $$
  select nullif(
    btrim(                                            -- 6. final safety trim
      rtrim(                                          -- 5. drop trailing dot
        regexp_replace(
          split_part(                                 -- 3. host only; drop /path?query#frag
            regexp_replace(
              regexp_replace(                         -- 1b. remove ALL whitespace
                lower(coalesce(p_input, '')),         -- 1a. lowercase
                '\s+', '', 'g'
              ),
              '^(https?://)?(www\.)*', ''             -- 2. strip scheme, then any run of www.
            ),
            '/', 1
          ),
          ':\d+$', ''                                 -- 4. strip port
        ),
        '.'
      )
    ),
    ''
  );
$$;


-- ---------------------------------------------------------------------------
-- normalize_linkedin(text) -> text
--
-- Reduces a LinkedIn profile URL to its vanity slug, the stable identity:
--   'https://www.linkedin.com/in/John-Smith-123ab/'       -> 'john-smith-123ab'
--   'https://uk.linkedin.com/in/john-smith-123ab?trk=x'   -> 'john-smith-123ab'
--   'linkedin.com/in/john-smith-123ab'                    -> 'john-smith-123ab'
--
-- Locale subdomains (uk., de., ...) and tracking query strings are precisely the
-- noise that would otherwise make one person look like several.
--
-- Returns NULL if no /in/ slug is present -- including for company pages
-- (/company/...) and legacy /pub/ URLs, which are NOT person identities.
-- ---------------------------------------------------------------------------
create or replace function public.normalize_linkedin(p_input text)
returns text
language sql
immutable
as $$
  select nullif(
    (regexp_match(
      lower(btrim(coalesce(p_input, ''))),
      'linkedin\.com/in/([^/?#\s]+)'
    ))[1],
    ''
  );
$$;


-- ---------------------------------------------------------------------------
-- ledger_check_company("Company Domain", "Name") -> json
--
-- Returns: { is_new, skipped, key }
--   is_new=true   first sighting of this domain -- this call recorded it. Enrich.
--   is_new=false  domain already in the ledger. Skip enrichment.
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
-- ---------------------------------------------------------------------------
create or replace function public.ledger_check_company(
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
    insert into public.company_ledger (domain_key, "Name", "Company Domain")
    values (v_key, nullif(btrim(coalesce("Name",'')), ''), nullif(btrim(coalesce("Company Domain",'')), ''))
    on conflict (domain_key) do nothing
    returning 1
  )
  select exists (select 1 from ins) into v_is_new;

  return json_build_object('is_new', v_is_new, 'skipped', false, 'key', v_key);
end;
$$;


-- ---------------------------------------------------------------------------
-- ledger_check_person("Linkedin Profile", "Full Name") -> json
-- Same contract; identity is the LinkedIn slug, "Full Name" is payload.
--
-- skipped=true here means no /in/ slug could be extracted -- a blank cell, a
-- company page, or a malformed URL. Watch this count: those people are NOT
-- being blocklisted.
-- ---------------------------------------------------------------------------
create or replace function public.ledger_check_person(
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
    insert into public.people_ledger (linkedin_key, "Full Name", "Linkedin Profile")
    values (v_key, nullif(btrim(coalesce("Full Name",'')), ''), nullif(btrim(coalesce("Linkedin Profile",'')), ''))
    on conflict (linkedin_key) do nothing
    returning 1
  )
  select exists (select 1 from ins) into v_is_new;

  return json_build_object('is_new', v_is_new, 'skipped', false, 'key', v_key);
end;
$$;


-- ---------------------------------------------------------------------------
-- ledger_ping() -> json   (daily keepalive against the 7-day inactivity pause)
-- ---------------------------------------------------------------------------
create or replace function public.ledger_ping()
returns json
language sql
security definer
set search_path = public, pg_temp
as $$
  select json_build_object(
    'ok', true,
    'companies', (select count(*) from public.company_ledger),
    'people',    (select count(*) from public.people_ledger)
  );
$$;


-- ---------------------------------------------------------------------------
-- GRANTS
--
-- anon may EXECUTE these three functions and nothing else.
--
-- NOTE: `revoke ... from public` alone is NOT sufficient on Supabase -- its
-- default privileges grant EXECUTE directly to anon/authenticated, and revoking
-- from PUBLIC does not remove a direct role grant. Caught in testing: a helper
-- stayed callable by anon after a PUBLIC-only revoke. Both revokes are required.
--
-- The normalizers are intentionally NOT granted to anon. They are helpers for
-- backfill and inspection; anon reaches them only indirectly, inside the RPCs.
-- ---------------------------------------------------------------------------
revoke all on function public.ledger_check_company(text, text) from public, anon, authenticated;
revoke all on function public.ledger_check_person(text, text)  from public, anon, authenticated;
revoke all on function public.ledger_ping()                    from public, anon, authenticated;
revoke all on function public.normalize_domain(text)           from public, anon, authenticated;
revoke all on function public.normalize_linkedin(text)         from public, anon, authenticated;

grant execute on function public.ledger_check_company(text, text) to anon, authenticated;
grant execute on function public.ledger_check_person(text, text)  to anon, authenticated;
grant execute on function public.ledger_ping()                    to anon, authenticated;
