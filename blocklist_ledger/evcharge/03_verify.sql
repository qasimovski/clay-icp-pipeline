-- ============================================================================
-- EVCharge blocklist ledger for Clay
-- evcharge/03_verify.sql  --  self-checking test suite
--
-- Run THIRD. Every check RAISES EXCEPTION on failure, so this either prints
-- 'ALL EVCHARGE CHECKS PASSED' or aborts on the first broken assumption.
--
-- SCOPE. The normalizers themselves are already covered by ../03_verify.sql and
-- are not re-tested here -- the EVCharge RPCs call the very same functions, so
-- duplicating those assertions would only create a second place to update. What
-- IS tested here is everything that is new: the two tables, their constraints,
-- the RPC contract, the lockdown, and above all ISOLATION -- that the EVCharge
-- RPCs write to the EVCharge tables and are blind to the Labs ledger, in both
-- directions. Isolation is the entire reason these tables exist, so it is the
-- one property worth asserting rather than assuming.
--
-- Test rows are marked 'zzevchargetestzz' -- letters only, so the marker
-- survives normalization and contains no LIKE wildcards. Cleanup is asserted at
-- the end.
--
-- SAFE TO RUN AGAINST A LIVE LEDGER, including the live Labs one. Check 6
-- deliberately calls the Labs RPC once to prove it does not leak into the
-- EVCharge tables; that row is deleted in the same block, immediately, and only
-- rows carrying the marker are ever deleted. If the script aborts between the
-- insert and the delete, the residue is a single row keyed 'zzevchargetestzz.com'
-- -- clear it with the last query at the bottom of this file.
-- ============================================================================

do $$
declare
  v1 json; v2 json; v3 json; v4 json;
  v_count int; v_txt text; v_bool boolean;
  c_dom  text := 'zzevchargetestzz.com';
  c_slug text := 'zzevchargetestzz';
begin
  ---------------------------------------------------------------------------
  raise notice '--- 0. clean slate ----------------------------------------';
  ---------------------------------------------------------------------------
  delete from public.evcharge_company_ledger where domain_key   = c_dom;
  delete from public.evcharge_people_ledger  where linkedin_key = c_slug;
  delete from public.company_ledger          where domain_key   = c_dom;
  delete from public.people_ledger           where linkedin_key = c_slug;

  ---------------------------------------------------------------------------
  raise notice '--- 1. companies: first sighting ---------------------------';
  ---------------------------------------------------------------------------
  v1 := public.ledger_check_evcharge_company(c_dom, 'ZZ EVCharge Test Ltd');

  if (v1->>'is_new')::boolean is not true then
    raise exception 'first company sighting must be is_new=true, got %', v1;
  end if;
  if (v1->>'skipped')::boolean is not false then
    raise exception 'first company sighting must be skipped=false, got %', v1;
  end if;
  if v1->>'key' <> c_dom then
    raise exception 'company key must be [%], got %', c_dom, v1->>'key';
  end if;

  ---------------------------------------------------------------------------
  raise notice '--- 2. companies: repeat in a DIFFERENT format is not new ---';
  ---------------------------------------------------------------------------
  -- The whole point of normalizing in the DB: these three are one company.
  v2 := public.ledger_check_evcharge_company('https://WWW.ZZEVCHARGETESTZZ.com/careers?x=1', 'ACME');
  v3 := public.ledger_check_evcharge_company('  www.ZZEvChargeTestZZ.com.  ', null);

  if (v2->>'is_new')::boolean is not false or (v3->>'is_new')::boolean is not false then
    raise exception 'repeat sightings must be is_new=false, got % and %', v2, v3;
  end if;

  select count(*) into v_count
    from public.evcharge_company_ledger where domain_key = c_dom;
  if v_count <> 1 then
    raise exception 'three shapes of one domain must leave 1 row, found %', v_count;
  end if;

  ---------------------------------------------------------------------------
  raise notice '--- 3. companies: payload records the FIRST sighting -------';
  ---------------------------------------------------------------------------
  -- A later call carrying a different "Name" must NOT overwrite the stored one.
  select "Name" into v_txt
    from public.evcharge_company_ledger where domain_key = c_dom;
  if v_txt <> 'ZZ EVCharge Test Ltd' then
    raise exception 'payload must survive later calls, got [%]', v_txt;
  end if;

  ---------------------------------------------------------------------------
  raise notice '--- 4. companies: unusable input is skipped, writes nothing -';
  ---------------------------------------------------------------------------
  select count(*) into v_count from public.evcharge_company_ledger;
  v4 := public.ledger_check_evcharge_company('   ', 'No Domain Ltd');
  if (v4->>'skipped')::boolean is not true or (v4->>'is_new')::boolean is not false then
    raise exception 'blank domain must be skipped=true is_new=false, got %', v4;
  end if;
  if v4->>'key' is not null then
    raise exception 'skipped call must return a null key, got %', v4->>'key';
  end if;
  if (select count(*) from public.evcharge_company_ledger) <> v_count then
    raise exception 'a skipped call must not write a row';
  end if;

  ---------------------------------------------------------------------------
  raise notice '--- 5. ISOLATION: EVCharge RPC does not touch the Labs ledger';
  ---------------------------------------------------------------------------
  if not exists (select 1 from public.evcharge_company_ledger where domain_key = c_dom) then
    raise exception 'row must be in evcharge_company_ledger';
  end if;
  if exists (select 1 from public.company_ledger where domain_key = c_dom) then
    raise exception 'LEAK: EVCharge RPC wrote into the Labs company_ledger';
  end if;

  ---------------------------------------------------------------------------
  raise notice '--- 6. ISOLATION: Labs RPC does not touch the EVCharge ledger';
  ---------------------------------------------------------------------------
  -- One deliberate write to the live Labs ledger, deleted immediately below.
  v1 := public.ledger_check_company(c_dom, 'ZZ Labs Side Test');
  if not exists (select 1 from public.company_ledger where domain_key = c_dom) then
    raise exception 'Labs RPC should have recorded the marker row, got %', v1;
  end if;
  select count(*) into v_count
    from public.evcharge_company_ledger where domain_key = c_dom;
  if v_count <> 1 then
    raise exception 'LEAK: Labs RPC changed the EVCharge ledger (rows now %)', v_count;
  end if;
  delete from public.company_ledger where domain_key = c_dom;   -- immediate cleanup

  -- And the corollary that makes the design intentional rather than accidental:
  -- a domain already in the Labs ledger is STILL new to EVCharge. If this ever
  -- fails, the two verticals have been merged and EVCharge will silently skip
  -- companies it has never worked.
  delete from public.evcharge_company_ledger where domain_key = c_dom;
  v1 := public.ledger_check_company(c_dom, 'ZZ Labs Side Test');
  v2 := public.ledger_check_evcharge_company(c_dom, 'ZZ EVCharge Test Ltd');
  if (v2->>'is_new')::boolean is not true then
    raise exception 'a Labs-known domain must still be new to EVCharge, got %', v2;
  end if;
  delete from public.company_ledger where domain_key = c_dom;   -- immediate cleanup

  ---------------------------------------------------------------------------
  raise notice '--- 7. people: same contract, keyed on the /in/ slug -------';
  ---------------------------------------------------------------------------
  v1 := public.ledger_check_evcharge_person(
          'https://www.linkedin.com/in/' || c_slug || '/', 'ZZ EVCharge Person');
  v2 := public.ledger_check_evcharge_person(
          'https://uk.linkedin.com/in/' || upper(c_slug) || '?trk=x', 'Other Name');

  if (v1->>'is_new')::boolean is not true then
    raise exception 'first person sighting must be is_new=true, got %', v1;
  end if;
  if (v2->>'is_new')::boolean is not false then
    raise exception 'locale subdomain + tracking params must match, got %', v2;
  end if;
  if v1->>'key' <> c_slug then
    raise exception 'person key must be [%], got %', c_slug, v1->>'key';
  end if;

  select count(*) into v_count
    from public.evcharge_people_ledger where linkedin_key = c_slug;
  if v_count <> 1 then
    raise exception 'two shapes of one profile must leave 1 row, found %', v_count;
  end if;

  -- A /company/ page is not a person identity.
  v3 := public.ledger_check_evcharge_person(
          'https://www.linkedin.com/company/' || c_slug, 'Not A Person');
  if (v3->>'skipped')::boolean is not true then
    raise exception 'a /company/ URL must be skipped, got %', v3;
  end if;

  ---------------------------------------------------------------------------
  raise notice '--- 8. ISOLATION: people ledgers are independent -----------';
  ---------------------------------------------------------------------------
  if exists (select 1 from public.people_ledger where linkedin_key = c_slug) then
    raise exception 'LEAK: EVCharge person RPC wrote into the Labs people_ledger';
  end if;
  v1 := public.ledger_check_person(
          'https://www.linkedin.com/in/' || c_slug, 'ZZ Labs Person');
  select count(*) into v_count
    from public.evcharge_people_ledger where linkedin_key = c_slug;
  if v_count <> 1 then
    raise exception 'LEAK: Labs person RPC changed the EVCharge ledger (rows now %)', v_count;
  end if;
  delete from public.people_ledger where linkedin_key = c_slug;  -- immediate cleanup

  ---------------------------------------------------------------------------
  raise notice '--- 9. an unnormalized key is physically unstorable --------';
  ---------------------------------------------------------------------------
  -- This is the integrity half of the lockdown: even the table owner cannot
  -- write a key the normalizer would never produce.
  begin
    insert into public.evcharge_company_ledger (domain_key) values ('WWW.Zzevchargetestzz.com');
    raise exception 'CHECK constraint did not reject an unnormalized company key';
  exception
    when check_violation then null;
  end;

  begin
    insert into public.evcharge_people_ledger (linkedin_key)
      values ('linkedin.com/in/' || c_slug);
    raise exception 'CHECK constraint did not reject an unnormalized person key';
  exception
    when check_violation then null;
  end;

  ---------------------------------------------------------------------------
  raise notice '--- 10. lockdown: RLS on, no policies ----------------------';
  ---------------------------------------------------------------------------
  select bool_and(relrowsecurity) into v_bool
    from pg_class
   where oid in ('public.evcharge_company_ledger'::regclass,
                 'public.evcharge_people_ledger'::regclass);
  if v_bool is not true then
    raise exception 'RLS is not enabled on both EVCharge ledger tables';
  end if;

  select count(*) into v_count from pg_policies
   where schemaname = 'public'
     and tablename in ('evcharge_company_ledger', 'evcharge_people_ledger');
  if v_count <> 0 then
    raise exception 'expected 0 RLS policies, found % -- a policy would open direct table access', v_count;
  end if;

  ---------------------------------------------------------------------------
  raise notice '--- 11. lockdown: anon reaches the RPCs and nothing else ---';
  ---------------------------------------------------------------------------
  if has_table_privilege('anon', 'public.evcharge_company_ledger', 'SELECT')
     or has_table_privilege('anon', 'public.evcharge_company_ledger', 'INSERT')
     or has_table_privilege('anon', 'public.evcharge_people_ledger',  'SELECT')
     or has_table_privilege('anon', 'public.evcharge_people_ledger',  'INSERT') then
    raise exception 'anon must have NO direct table privileges on the EVCharge ledgers';
  end if;

  if not has_function_privilege('anon', 'public.ledger_check_evcharge_company(text,text)', 'EXECUTE')
     or not has_function_privilege('anon', 'public.ledger_check_evcharge_person(text,text)', 'EXECUTE') then
    raise exception 'anon must be able to EXECUTE both EVCharge RPCs -- the Clay column runs as anon';
  end if;

  -- The normalizers stay ungranted: anon reaches them only inside the RPCs.
  if has_function_privilege('anon', 'public.normalize_domain(text)', 'EXECUTE')
     or has_function_privilege('anon', 'public.normalize_linkedin(text)', 'EXECUTE') then
    raise exception 'the normalizers must not be directly executable by anon';
  end if;

  ---------------------------------------------------------------------------
  raise notice '--- 12. no unresolved Clay refs stored ---------------------';
  ---------------------------------------------------------------------------
  -- A typed "/Name" in the Clay body is stored as that literal string. This is
  -- not hypothetical: it produced ten junk rows during the Labs setup.
  select count(*) into v_count from public.evcharge_company_ledger
   where "Name" like '/%' or "Company Domain" like '/%';
  if v_count > 0 then
    raise exception '% company rows store a literal Clay ref (/Name) -- the body values were typed, not picked', v_count;
  end if;
  select count(*) into v_count from public.evcharge_people_ledger
   where "Full Name" like '/%' or "Linkedin Profile" like '/%';
  if v_count > 0 then
    raise exception '% people rows store a literal Clay ref -- the body values were typed, not picked', v_count;
  end if;

  ---------------------------------------------------------------------------
  raise notice '--- 13. cleanup, asserted ---------------------------------';
  ---------------------------------------------------------------------------
  delete from public.evcharge_company_ledger where domain_key   = c_dom;
  delete from public.evcharge_people_ledger  where linkedin_key = c_slug;
  delete from public.company_ledger          where domain_key   = c_dom;
  delete from public.people_ledger           where linkedin_key = c_slug;

  if exists (select 1 from public.evcharge_company_ledger where domain_key   = c_dom)
     or exists (select 1 from public.evcharge_people_ledger where linkedin_key = c_slug)
     or exists (select 1 from public.company_ledger        where domain_key   = c_dom)
     or exists (select 1 from public.people_ledger         where linkedin_key = c_slug) then
    raise exception 'test rows were not fully cleaned up';
  end if;

  raise notice '';
  raise notice 'ALL EVCHARGE CHECKS PASSED';
end $$;


-- ---------------------------------------------------------------------------
-- Post-run state. Both EVCharge counts should be 0 on a fresh install; the two
-- Labs counts are shown only to confirm this suite left them where it found
-- them.
-- ---------------------------------------------------------------------------
select
  (select count(*) from public.evcharge_company_ledger) as evcharge_companies,
  (select count(*) from public.evcharge_people_ledger)  as evcharge_people,
  (select count(*) from public.company_ledger)          as labs_companies,
  (select count(*) from public.people_ledger)           as labs_people;


-- ---------------------------------------------------------------------------
-- Residue clearer. Only needed if the suite aborted mid-way; safe any time,
-- since 'zzevchargetestzz' is never a real key.
-- ---------------------------------------------------------------------------
-- delete from public.evcharge_company_ledger where domain_key   = 'zzevchargetestzz.com';
-- delete from public.evcharge_people_ledger  where linkedin_key = 'zzevchargetestzz';
-- delete from public.company_ledger          where domain_key   = 'zzevchargetestzz.com';
-- delete from public.people_ledger           where linkedin_key = 'zzevchargetestzz';
