-- ============================================================================
-- 06_dot_guard_apply.sql  --  retrofit the dot guard onto Labs and EVCharge
--
-- Run 05_dot_guard_audit.sql FIRST and read its `dotless_keys` column. This file
-- deletes those rows (after copying them to a quarantine table). Look at them
-- before you run it.
--
-- Idempotent: safe to re-run. Applies in the SAME Supabase project as the rest.
--
-- WHAT THIS FIXES
--   The Labs and EVCharge company ledgers accept a dotless domain_key. The first
--   'UNKNOWN' (or 'n/a', or a company name mistaken for a domain) claims that key
--   and every later unresolved company is told is_new=false -- silently dropped
--   from the funnel with no trace in any spend report. Highways already carries
--   this guard; see 05_dot_guard_audit.sql for the full explanation.
--
-- WHY THE ORDER BELOW IS THE ONLY SAFE ONE
--   1. Patch the RPCs first. That stops new dotless keys arriving. Adding the
--      constraint first would make an in-flight live run start erroring
--      mid-table; cleaning first without patching would let a new bad key land
--      between the delete and the ALTER, and the ALTER would then fail.
--   2. Quarantine, then delete. Copying first makes the whole thing reversible.
--   3. Add the constraint last, when it is guaranteed to succeed.
--
-- ⚠️ READ THE NOTE AT THE BOTTOM. Fixing the ledger does NOT retroactively repair
--    the wrong `Is New = false` values already written downstream.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- 0. Preconditions.
-- ---------------------------------------------------------------------------
do $$
begin
  if to_regprocedure('public.normalize_domain(text)') is null then
    raise exception
      'normalize_domain is missing. Apply blocklist_ledger/02_functions.sql first.';
  end if;
  if to_regclass('public.company_ledger') is null then
    raise exception
      'public.company_ledger is missing. This file retrofits an existing ledger.';
  end if;
end $$;


-- ---------------------------------------------------------------------------
-- 1. PATCH THE RPCs -- stop the bleeding before touching any data.
--
-- Reproduced verbatim from 02_functions.sql and evcharge/02_functions.sql with
-- exactly one change each: the unkeyable test gains `or position('.' in v_key) = 0`.
-- Signatures and argument names are untouched -- PostgREST resolves functions by
-- their full argument list, so a renamed argument is a 404 at every caller.
--
-- CREATE OR REPLACE preserves existing grants; they are re-issued at the end
-- anyway, so this is safe either way.
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
  -- Unkeyable: empty after normalization, or a sentinel with no dot.
  if v_key is null or position('.' in v_key) = 0 then
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


do $$
begin
  if to_regprocedure('public.ledger_check_evcharge_company(text, text)') is null then
    raise notice 'skipping EVCharge RPC patch -- ledger_check_evcharge_company not present';
    return;
  end if;

  execute $fn$
    create or replace function public.ledger_check_evcharge_company(
      "Company Domain" text default null,
      "Name"           text default null
    )
    returns json
    language plpgsql
    security definer
    set search_path = public, pg_temp
    as $body$
    declare
      v_key    text := public.normalize_domain("Company Domain");
      v_is_new boolean;
    begin
      if v_key is null or position('.' in v_key) = 0 then
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
    $body$;
  $fn$;
  raise notice 'patched ledger_check_evcharge_company';
end $$;


-- ---------------------------------------------------------------------------
-- 2. QUARANTINE -- copy every dotless row before deleting it, so this is
--    reversible. Locked down like the ledgers themselves: it holds the same
--    data, so it must not become the one readable table in the schema.
-- ---------------------------------------------------------------------------
create table if not exists public.dot_guard_quarantine (
  source_table     text        not null,
  domain_key       text        not null,
  "Name"           text,
  "Company Domain" text,
  first_seen       timestamptz,
  quarantined_at   timestamptz not null default now()
);

comment on table public.dot_guard_quarantine is
  'Rows removed by 06_dot_guard_apply.sql because domain_key had no dot (sentinels like UNKNOWN/n/a, or a company name keyed as a domain). Kept so the retrofit is reversible. Safe to drop once the affected downstream cells have been re-run.';

alter table public.dot_guard_quarantine enable row level security;
revoke all on table public.dot_guard_quarantine from anon, authenticated;


-- ---------------------------------------------------------------------------
-- 3. QUARANTINE + DELETE + CONSTRAIN, for every company ledger still unguarded.
--
--    Dynamic over the discovered set rather than naming Labs and EVCharge, so a
--    vertical created before new_vertical.py existed cannot be silently skipped.
-- ---------------------------------------------------------------------------
do $$
declare
  v_tbl  text;
  v_has  boolean;
  v_bad  bigint;
  v_con  text;
begin
  for v_tbl in
    select c.relname
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public'
       and c.relkind = 'r'
       and (c.relname = 'company_ledger' or c.relname like '%\_company\_ledger')
     order by c.relname
  loop
    select exists (
      select 1 from pg_constraint
       where conrelid = format('public.%I', v_tbl)::regclass
         and contype = 'c'
         and pg_get_constraintdef(oid) ilike '%position%'
    ) into v_has;

    if v_has then
      raise notice '% -- already guarded, skipped', v_tbl;
      continue;
    end if;

    -- Copy out, then remove. The NOT EXISTS makes a re-run a no-op rather than
    -- stacking duplicate quarantine rows.
    execute format($q$
      insert into public.dot_guard_quarantine
        (source_table, domain_key, "Name", "Company Domain", first_seen)
      select %L, l.domain_key, l."Name", l."Company Domain", l.first_seen
        from public.%I l
       where position('.' in l.domain_key) = 0
         and not exists (
           select 1 from public.dot_guard_quarantine q
            where q.source_table = %L and q.domain_key = l.domain_key)
    $q$, v_tbl, v_tbl, v_tbl);

    execute format(
      'delete from public.%I where position(''.'' in domain_key) = 0', v_tbl);
    get diagnostics v_bad = row_count;

    v_con := v_tbl || '_key_shaped';
    execute format(
      'alter table public.%I add constraint %I check (position(''.'' in domain_key) > 0)',
      v_tbl, v_con);

    raise notice '% -- % dotless row(s) quarantined and deleted, constraint % added',
      v_tbl, v_bad, v_con;
  end loop;
end $$;


-- ---------------------------------------------------------------------------
-- 4. RE-ISSUE GRANTS. Harmless if unchanged; explicit beats assumed.
-- ---------------------------------------------------------------------------
revoke all on function public.ledger_check_company(text, text) from public, anon, authenticated;
grant execute on function public.ledger_check_company(text, text) to anon, authenticated;

do $$
begin
  if to_regprocedure('public.ledger_check_evcharge_company(text, text)') is not null then
    revoke all on function public.ledger_check_evcharge_company(text, text) from public, anon, authenticated;
    grant execute on function public.ledger_check_evcharge_company(text, text) to anon, authenticated;
  end if;
end $$;


-- ---------------------------------------------------------------------------
-- 5. VERIFY -- prove the guard holds at both layers, on live data.
--    Aborts on the first failure. Writes nothing that it does not remove.
-- ---------------------------------------------------------------------------
do $$
declare
  r        json;
  v_tbl    text;
  v_unguarded text[] := '{}';
begin
  -- 5a. Every company ledger now carries the constraint.
  for v_tbl in
    select c.relname
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind = 'r'
       and (c.relname = 'company_ledger' or c.relname like '%\_company\_ledger')
  loop
    if not exists (
      select 1 from pg_constraint
       where conrelid = format('public.%I', v_tbl)::regclass
         and contype = 'c'
         and pg_get_constraintdef(oid) ilike '%position%'
    ) then
      v_unguarded := v_unguarded || v_tbl;
    end if;
  end loop;

  if array_length(v_unguarded, 1) is not null then
    raise exception 'VERIFY FAILED: still unguarded: %', array_to_string(v_unguarded, ', ');
  end if;
  raise notice 'verify 1 ok -- every company ledger carries the dot guard';

  -- 5b. No dotless rows survive anywhere.
  if exists (select 1 from public.company_ledger where position('.' in domain_key) = 0) then
    raise exception 'VERIFY FAILED: dotless rows remain in company_ledger';
  end if;
  raise notice 'verify 2 ok -- no dotless rows remain';

  -- 5c. The Labs RPC now refuses the sentinel instead of recording it.
  r := public.ledger_check_company('UNKNOWN', 'Verify Unresolved Co');
  if (r->>'skipped')::boolean is not true or (r->>'is_new')::boolean is not false then
    raise exception 'VERIFY FAILED: ledger_check_company still accepts UNKNOWN, got %', r;
  end if;
  if exists (select 1 from public.company_ledger where domain_key = 'unknown') then
    raise exception 'VERIFY FAILED: an ''unknown'' row was written -- the guard leaked';
  end if;
  raise notice 'verify 3 ok -- ledger_check_company skips UNKNOWN, nothing written';

  -- 5d. A real domain still works. Cleaned up immediately.
  r := public.ledger_check_company('https://www.verify-dotguard.example/x', 'Verify Dot Guard');
  if (r->>'is_new')::boolean is not true then
    raise exception 'VERIFY FAILED: a real domain was not accepted, got %', r;
  end if;
  delete from public.company_ledger where domain_key = 'verify-dotguard.example';
  raise notice 'verify 4 ok -- real domains still accepted';

  -- 5e. Same two checks for EVCharge, when present.
  if to_regprocedure('public.ledger_check_evcharge_company(text, text)') is not null then
    r := public.ledger_check_evcharge_company('UNKNOWN', 'Verify Unresolved Co');
    if (r->>'skipped')::boolean is not true then
      raise exception 'VERIFY FAILED: ledger_check_evcharge_company still accepts UNKNOWN, got %', r;
    end if;
    r := public.ledger_check_evcharge_company('https://www.verify-dotguard.example/x', 'Verify Dot Guard');
    if (r->>'is_new')::boolean is not true then
      raise exception 'VERIFY FAILED: EVCharge rejected a real domain, got %', r;
    end if;
    delete from public.evcharge_company_ledger where domain_key = 'verify-dotguard.example';
    raise notice 'verify 5 ok -- EVCharge RPC skips UNKNOWN, accepts real domains';
  end if;

  raise notice '----------------------------------------';
  raise notice 'DOT GUARD RETROFIT COMPLETE';
  raise notice 'quarantined rows: %',
    (select count(*) from public.dot_guard_quarantine);
  raise notice '----------------------------------------';
end $$;


-- ============================================================================
-- ⚠️ WHAT THIS DOES **NOT** FIX
--
-- Deleting the bad key repairs the ledger, so those companies are correctly
-- `is_new = true` the next time they are checked. It does NOT repair the values
-- already written downstream: every row that was told `Is New = false` because
-- of the collapse still holds that false, and is still excluded from enrichment
-- by the view filter that reads it.
--
-- If 05_dot_guard_audit.sql reported dotless_rows > 0, those rows need their
-- ledger call re-run to pick up the corrected answer. Two cautions:
--
--   * `Is New` is write-once by convention. A blanket re-run of the whole column
--     turns every legitimate stored `true` into `false`, because the second call
--     genuinely is a repeat sighting. Re-run ONLY the affected rows -- the ones
--     whose key was unresolvable -- never the full column.
--   * Identify them by the key, not by the flag: rows whose resolved-domain
--     value is a sentinel or is empty. Those are exactly the rows that were
--     answered by the poisoned key.
--
-- public.dot_guard_quarantine holds what was removed, in case that set is needed
-- to scope the re-run. Drop it once the cleanup is done.
-- ============================================================================
