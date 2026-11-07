-- ============================================================================
-- Highways blocklist ledger for Clay
-- highways/03_verify.sql  --  self-asserting verification suite
--
-- Run THIRD, after highways/01_schema.sql and highways/02_functions.sql.
-- Prints `ALL HIGHWAYS CHECKS PASSED` or aborts on the first bad assumption.
--
-- Everything it writes it removes: the suite works on sentinel domains under
-- verify-*.example, and the final block deletes exactly those rows and asserts
-- the tables are back to their pre-run counts. Safe to run against a live
-- ledger, and safe to re-run.
-- ============================================================================

do $$
declare
  r          json;
  n_before   bigint;
  n_after    bigint;
  p_before   bigint;
begin
  select count(*) into n_before from public.highways_company_ledger;
  select count(*) into p_before from public.highways_people_ledger;

  -- -------------------------------------------------------------------------
  -- 1. The objects exist with the exact signatures PostgREST will look for.
  --    A wrong signature is the PGRST202 "no such function" class of failure,
  --    which from Clay looks like a 404 with no explanation.
  -- -------------------------------------------------------------------------
  if to_regprocedure('public.ledger_check_highways_company(text, text)') is null then
    raise exception 'CHECK 1 FAILED: ledger_check_highways_company(text,text) missing';
  end if;
  if to_regprocedure('public.ledger_check_highways_person(text, text)') is null then
    raise exception 'CHECK 1 FAILED: ledger_check_highways_person(text,text) missing';
  end if;
  raise notice 'check 1 ok  -- both RPCs exist with the expected signatures';

  -- -------------------------------------------------------------------------
  -- 2. First sighting inserts and reports is_new=true.
  -- -------------------------------------------------------------------------
  r := public.ledger_check_highways_company('https://www.verify-one.example/path?q=1', 'Verify One Ltd');
  if (r->>'is_new')::boolean is not true or (r->>'skipped')::boolean is not false then
    raise exception 'CHECK 2 FAILED: expected is_new=true skipped=false, got %', r;
  end if;
  if r->>'key' <> 'verify-one.example' then
    raise exception 'CHECK 2 FAILED: expected key=verify-one.example, got %', r->>'key';
  end if;
  raise notice 'check 2 ok  -- first sighting inserts, scheme/www/path stripped';

  -- -------------------------------------------------------------------------
  -- 3. Second sighting in a DIFFERENT format is the same record.
  --    This is the whole point of normalizing in the database.
  -- -------------------------------------------------------------------------
  r := public.ledger_check_highways_company('WWW.Verify-One.Example', 'Verify One Limited');
  if (r->>'is_new')::boolean is not false then
    raise exception 'CHECK 3 FAILED: reformatted domain should be is_new=false, got %', r;
  end if;
  raise notice 'check 3 ok  -- casing/www variants collapse to one record';

  -- -------------------------------------------------------------------------
  -- 4. First-sighting payload is preserved, not overwritten by the repeat.
  -- -------------------------------------------------------------------------
  if (select "Name" from public.highways_company_ledger
      where domain_key = 'verify-one.example') <> 'Verify One Ltd' then
    raise exception 'CHECK 4 FAILED: repeat call overwrote the stored "Name"';
  end if;
  raise notice 'check 4 ok  -- payload records the FIRST sighting';

  -- -------------------------------------------------------------------------
  -- 5. Unkeyable input writes nothing.
  -- -------------------------------------------------------------------------
  r := public.ledger_check_highways_company(null, 'No Domain At All');
  if (r->>'skipped')::boolean is not true or (r->>'is_new')::boolean is not false then
    raise exception 'CHECK 5 FAILED: null domain should skip, got %', r;
  end if;
  r := public.ledger_check_highways_company('   ', 'Blank Domain');
  if (r->>'skipped')::boolean is not true then
    raise exception 'CHECK 5 FAILED: blank domain should skip, got %', r;
  end if;
  raise notice 'check 5 ok  -- null/blank domains are skipped, nothing written';

  -- -------------------------------------------------------------------------
  -- 6. THE SENTINEL GUARD -- the reason this vertical's RPC differs.
  --
  --    'UNKNOWN' is what the Verified Domain Claygent returns when it cannot
  --    verify a domain, and normalize_domain() turns it into the well-formed
  --    key 'unknown'. Unguarded, the first such row would claim that key and
  --    every later unresolved company would be reported already-seen.
  --
  --    Both calls below must skip, and NEITHER may leave a row behind.
  -- -------------------------------------------------------------------------
  r := public.ledger_check_highways_company('UNKNOWN', 'Unresolved Co');
  if (r->>'skipped')::boolean is not true or (r->>'is_new')::boolean is not false then
    raise exception 'CHECK 6 FAILED: UNKNOWN must be skipped, got %', r;
  end if;
  r := public.ledger_check_highways_company('unknown', 'Unresolved Co 2');
  if (r->>'skipped')::boolean is not true then
    raise exception 'CHECK 6 FAILED: lowercase unknown must be skipped, got %', r;
  end if;
  if exists (select 1 from public.highways_company_ledger where domain_key = 'unknown') then
    raise exception 'CHECK 6 FAILED: a row keyed ''unknown'' exists -- the guard leaked';
  end if;
  raise notice 'check 6 ok  -- UNKNOWN and friends are unkeyable, no ''unknown'' row';

  -- -------------------------------------------------------------------------
  -- 7. The CHECK constraint independently refuses a dotless key, so even a
  --    direct insert (not via the RPC) cannot create the collapse of check 6.
  -- -------------------------------------------------------------------------
  begin
    insert into public.highways_company_ledger (domain_key) values ('unknown');
    raise exception 'CHECK 7 FAILED: a dotless domain_key was accepted by the table';
  exception
    when check_violation then
      raise notice 'check 7 ok  -- the table itself refuses a dotless key';
  end;

  -- -------------------------------------------------------------------------
  -- 8. The CHECK constraint refuses an unnormalized key (scheme/case/www).
  -- -------------------------------------------------------------------------
  begin
    insert into public.highways_company_ledger (domain_key) values ('WWW.Bad.Example');
    raise exception 'CHECK 8 FAILED: an unnormalized domain_key was accepted';
  exception
    when check_violation then
      raise notice 'check 8 ok  -- the table refuses an unnormalized key';
  end;

  -- -------------------------------------------------------------------------
  -- 9. VERTICAL ISOLATION -- the headline behavioural claim.
  --    A domain already in the Labs and/or EVCharge ledger must still be new
  --    here. Asserted with a domain planted directly in the other ledgers.
  -- -------------------------------------------------------------------------
  insert into public.company_ledger (domain_key, "Name")
    values ('verify-shared.example', 'Planted By Highways Verify')
    on conflict (domain_key) do nothing;
  if to_regclass('public.evcharge_company_ledger') is not null then
    insert into public.evcharge_company_ledger (domain_key, "Name")
      values ('verify-shared.example', 'Planted By Highways Verify')
      on conflict (domain_key) do nothing;
  end if;

  r := public.ledger_check_highways_company('verify-shared.example', 'Shared Domain Co');
  if (r->>'is_new')::boolean is not true then
    raise exception
      'CHECK 9 FAILED: a domain present in the Labs/EVCharge ledgers must still be new to Highways, got %', r;
  end if;
  raise notice 'check 9 ok  -- Highways dedupes against Highways only';

  -- -------------------------------------------------------------------------
  -- 10. People RPC: insert, repeat, and slug normalization.
  -- -------------------------------------------------------------------------
  r := public.ledger_check_highways_person('https://www.linkedin.com/in/Verify-Person/', 'Verify Person');
  if (r->>'is_new')::boolean is not true then
    raise exception 'CHECK 10 FAILED: first person sighting should be is_new=true, got %', r;
  end if;
  r := public.ledger_check_highways_person('linkedin.com/in/verify-person', 'Verify Person Again');
  if (r->>'is_new')::boolean is not false then
    raise exception 'CHECK 10 FAILED: same slug in another format should be is_new=false, got %', r;
  end if;
  r := public.ledger_check_highways_person('https://www.linkedin.com/company/acme', 'Not A Person');
  if (r->>'skipped')::boolean is not true then
    raise exception 'CHECK 10 FAILED: a company URL should skip, got %', r;
  end if;
  raise notice 'check 10 ok -- people RPC inserts, dedupes and rejects non-/in/ URLs';

  -- -------------------------------------------------------------------------
  -- 11. anon can EXECUTE the RPCs but cannot read or write the tables.
  --     This is the security model AND the integrity model in one.
  -- -------------------------------------------------------------------------
  if not has_function_privilege('anon',
        'public.ledger_check_highways_company(text, text)', 'execute') then
    raise exception 'CHECK 11 FAILED: anon cannot execute the company RPC';
  end if;
  if has_table_privilege('anon', 'public.highways_company_ledger', 'select')
     or has_table_privilege('anon', 'public.highways_company_ledger', 'insert') then
    raise exception 'CHECK 11 FAILED: anon has direct table access -- lockdown is broken';
  end if;
  if not (select relrowsecurity from pg_class
          where oid = 'public.highways_company_ledger'::regclass) then
    raise exception 'CHECK 11 FAILED: RLS is not enabled on highways_company_ledger';
  end if;
  raise notice 'check 11 ok -- anon may execute the RPCs and nothing else; RLS on';

  -- -------------------------------------------------------------------------
  -- 12. No unresolved Clay column references were ever stored. A typed "/Name"
  --     instead of a picked one produced ten junk rows during the Labs setup.
  -- -------------------------------------------------------------------------
  if exists (select 1 from public.highways_company_ledger
             where domain_key like '/%' or "Name" like '/%' or "Company Domain" like '/%') then
    raise exception
      'CHECK 12 FAILED: a value starting with "/" is stored -- a Clay column reference was typed, not picked';
  end if;
  raise notice 'check 12 ok -- no unresolved Clay column references stored';

  -- -------------------------------------------------------------------------
  -- CLEANUP -- remove exactly what this suite created, then prove it.
  -- -------------------------------------------------------------------------
  delete from public.highways_company_ledger
   where domain_key in ('verify-one.example', 'verify-shared.example');
  delete from public.highways_people_ledger where linkedin_key = 'verify-person';
  delete from public.company_ledger where domain_key = 'verify-shared.example'
     and "Name" = 'Planted By Highways Verify';
  if to_regclass('public.evcharge_company_ledger') is not null then
    delete from public.evcharge_company_ledger where domain_key = 'verify-shared.example'
       and "Name" = 'Planted By Highways Verify';
  end if;

  select count(*) into n_after from public.highways_company_ledger;
  if n_after <> n_before then
    raise exception
      'CLEANUP FAILED: company ledger has % rows, started with % -- verify rows left behind',
      n_after, n_before;
  end if;
  select count(*) into n_after from public.highways_people_ledger;
  if n_after <> p_before then
    raise exception
      'CLEANUP FAILED: people ledger has % rows, started with %', n_after, p_before;
  end if;

  raise notice '----------------------------------------';
  raise notice 'ALL HIGHWAYS CHECKS PASSED';
  raise notice 'company ledger rows: %   people ledger rows: %', n_before, p_before;
  raise notice '----------------------------------------';
end $$;
