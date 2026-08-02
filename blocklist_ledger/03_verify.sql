-- ============================================================================
-- Central Blocklist ledger for Clay
-- 03_verify.sql  --  self-checking test suite
--
-- Run THIRD. Every check RAISES EXCEPTION on failure, so this either prints
-- 'ALL CHECKS PASSED' or aborts on the first broken assumption.
--
-- Test rows are marked 'zzledgertestzz' -- letters only, so the marker survives
-- normalization and contains no LIKE wildcards. Cleanup is asserted at the end.
-- Safe to run against a live ledger.
-- ============================================================================

do $$
declare
  v1 json; v2 json; v3 json; v4 json;
  v_count int; v_txt text;
begin
  raise notice '--- normalize_domain: every shape Clay emits --------------';

  if public.normalize_domain('https://WWW.Acme.com/careers?x=1') <> 'acme.com' then
    raise exception 'full URL: got [%]', public.normalize_domain('https://WWW.Acme.com/careers?x=1');
  end if;
  if public.normalize_domain('www.acme.com') <> 'acme.com' then
    raise exception 'www only: got [%]', public.normalize_domain('www.acme.com');
  end if;
  if public.normalize_domain('acme.com') <> 'acme.com' then
    raise exception 'bare: got [%]', public.normalize_domain('acme.com');
  end if;
  if public.normalize_domain('HTTP://Acme.COM:8080/') <> 'acme.com' then
    raise exception 'scheme+port+slash: got [%]', public.normalize_domain('HTTP://Acme.COM:8080/');
  end if;
  if public.normalize_domain('  acme.com.  ') <> 'acme.com' then
    raise exception 'whitespace+trailing dot: got [%]', public.normalize_domain('  acme.com.  ');
  end if;
  if public.normalize_domain('   ') is not null or public.normalize_domain(null) is not null then
    raise exception 'blank/null must be NULL';
  end if;
  -- real shapes taken from the actual 51k CSV
  if public.normalize_domain('http://www.ari-armaturen.com/') <> 'ari-armaturen.com' then
    raise exception 'real row 1: got [%]', public.normalize_domain('http://www.ari-armaturen.com/');
  end if;
  if public.normalize_domain('http://amelicor.group/') <> 'amelicor.group' then
    raise exception 'real row 2: got [%]', public.normalize_domain('http://amelicor.group/');
  end if;

  -- REGRESSION: four malformed rows in the real CSV produced illegal keys until
  -- whitespace was stripped before the scheme/www patterns were applied. The
  -- CHECK constraint caught them; these assertions keep them caught.
  if public.normalize_domain('https://www. psiplus.co.kr') <> 'psiplus.co.kr' then
    raise exception 'space after www.: got [%]', public.normalize_domain('https://www. psiplus.co.kr');
  end if;
  if public.normalize_domain('http: //www.yuandingmed.com') <> 'yuandingmed.com' then
    raise exception 'space inside scheme: got [%]', public.normalize_domain('http: //www.yuandingmed.com');
  end if;
  if public.normalize_domain('https://www. TECHNOMEDINDIA.org') <> 'technomedindia.org' then
    raise exception 'space + uppercase: got [%]', public.normalize_domain('https://www. TECHNOMEDINDIA.org');
  end if;
  -- repeated www. must fully reduce, or the key would start with 'www.'
  if public.normalize_domain('http://www.www.acme.com') <> 'acme.com' then
    raise exception 'repeated www.: got [%]', public.normalize_domain('http://www.www.acme.com');
  end if;
  raise notice 'PASS normalize_domain (incl. malformed real-world rows)';


  raise notice '--- normalize_linkedin -----------------------------------';

  if public.normalize_linkedin('https://www.linkedin.com/in/John-Smith-123ab/') <> 'john-smith-123ab' then
    raise exception 'canonical: got [%]', public.normalize_linkedin('https://www.linkedin.com/in/John-Smith-123ab/');
  end if;
  if public.normalize_linkedin('https://uk.linkedin.com/in/john-smith-123ab?trk=xyz') <> 'john-smith-123ab' then
    raise exception 'locale+trk: got [%]', public.normalize_linkedin('https://uk.linkedin.com/in/john-smith-123ab?trk=xyz');
  end if;
  if public.normalize_linkedin('linkedin.com/in/john-smith-123ab') <> 'john-smith-123ab' then
    raise exception 'bare: got [%]', public.normalize_linkedin('linkedin.com/in/john-smith-123ab');
  end if;
  -- a COMPANY page is not a person identity
  if public.normalize_linkedin('https://www.linkedin.com/company/acme/') is not null then
    raise exception 'company page must yield NULL, got [%]', public.normalize_linkedin('https://www.linkedin.com/company/acme/');
  end if;
  if public.normalize_linkedin('not a url') is not null then
    raise exception 'junk must be NULL';
  end if;
  raise notice 'PASS normalize_linkedin';


  raise notice '--- company: ALL domain formats collapse to ONE row ------';

  -- The headline fix. Four formats of one domain, four different company-name
  -- spellings => exactly ONE ledger row, and only the first call is new.
  v1 := public.ledger_check_company('https://www.acme-zzledgertestzz.com/', 'Acme Inc');
  v2 := public.ledger_check_company('www.acme-zzledgertestzz.com',          'Acme, Inc.');
  v3 := public.ledger_check_company('acme-zzledgertestzz.com',              'ACME');
  v4 := public.ledger_check_company('HTTP://ACME-zzledgertestzz.COM:443/x?y=1', 'acme inc');

  if (v1->>'is_new')::boolean is not true then
    raise exception 'first call must be new: %', v1;
  end if;
  if (v2->>'is_new')::boolean or (v3->>'is_new')::boolean or (v4->>'is_new')::boolean then
    raise exception 'format variants must NOT be new: % / % / %', v2, v3, v4;
  end if;
  if v1->>'key' <> 'acme-zzledgertestzz.com' then
    raise exception 'unexpected key: [%]', v1->>'key';
  end if;

  select count(*) into v_count from public.company_ledger
   where domain_key = 'acme-zzledgertestzz.com';
  if v_count <> 1 then
    raise exception 'expected exactly 1 row, found %', v_count;
  end if;
  raise notice 'PASS 4 formats + 4 name spellings -> 1 row';


  raise notice '--- first sighting is preserved, not overwritten ---------';

  select "Name" into v_txt from public.company_ledger
   where domain_key = 'acme-zzledgertestzz.com';
  if v_txt <> 'Acme Inc' then
    raise exception 'stored Name should be the FIRST sighting (Acme Inc), got [%]', v_txt;
  end if;
  select "Company Domain" into v_txt from public.company_ledger
   where domain_key = 'acme-zzledgertestzz.com';
  if v_txt <> 'https://www.acme-zzledgertestzz.com/' then
    raise exception 'stored raw domain should be the FIRST sighting, got [%]', v_txt;
  end if;
  raise notice 'PASS payload records first sighting';


  raise notice '--- no usable domain => skipped, nothing written ---------';

  v1 := public.ledger_check_company(null,  'Ghost Co zzledgertestzz');
  v2 := public.ledger_check_company('',    'Ghost Co zzledgertestzz');
  v3 := public.ledger_check_company('   ', 'Ghost Co zzledgertestzz');
  if not ((v1->>'skipped')::boolean and (v2->>'skipped')::boolean and (v3->>'skipped')::boolean) then
    raise exception 'domain-less input must be skipped: % / % / %', v1, v2, v3;
  end if;
  if (v1->>'is_new')::boolean or (v2->>'is_new')::boolean then
    raise exception 'skipped input must never report is_new=true';
  end if;
  select count(*) into v_count from public.company_ledger
   where coalesce("Name",'') = 'Ghost Co zzledgertestzz';
  if v_count <> 0 then
    raise exception 'skipped input wrote % row(s) -- must write none', v_count;
  end if;
  raise notice 'PASS domain-less input skipped and unwritten';


  raise notice '--- person: LinkedIn formats collapse, name irrelevant ---';

  v1 := public.ledger_check_person('https://www.linkedin.com/in/bob-zzledgertestzz/', 'Bob Smith');
  v2 := public.ledger_check_person('https://uk.linkedin.com/in/BOB-zzledgertestzz?trk=q', 'Robert Smith');
  v3 := public.ledger_check_person('linkedin.com/in/bob-zzledgertestzz', 'B. Smith');

  if (v1->>'is_new')::boolean is not true then
    raise exception 'first person must be new: %', v1;
  end if;
  if (v2->>'is_new')::boolean or (v3->>'is_new')::boolean then
    raise exception 'LinkedIn format variants must NOT be new: % / %', v2, v3;
  end if;
  select count(*) into v_count from public.people_ledger
   where linkedin_key = 'bob-zzledgertestzz';
  if v_count <> 1 then
    raise exception 'expected 1 person row, found %', v_count;
  end if;
  raise notice 'PASS Bob/Robert/B. on one profile URL -> 1 row';


  raise notice '--- person: no slug => skipped --------------------------';

  v1 := public.ledger_check_person(null, 'Nobody zzledgertestzz');
  v2 := public.ledger_check_person('https://www.linkedin.com/company/acme/', 'Nobody zzledgertestzz');
  if not ((v1->>'skipped')::boolean and (v2->>'skipped')::boolean) then
    raise exception 'slug-less input must be skipped: % / %', v1, v2;
  end if;
  raise notice 'PASS slug-less input skipped';


  raise notice '--- CHECK rejects an unnormalized key -------------------';

  begin
    insert into public.company_ledger (domain_key) values ('https://www.bad-zzledgertestzz.com/');
    raise exception 'CHECK should have rejected an unnormalized domain_key';
  exception
    when check_violation then
      raise notice 'PASS CHECK rejected unnormalized key';
  end;


  raise notice '--- ON CONFLICT collapses duplicates -------------------';

  insert into public.company_ledger (domain_key, "Name")
  select 'bulk-zzledgertestzz.com', 'Bulk'
  from generate_series(1, 1000)
  on conflict (domain_key) do nothing;

  select count(*) into v_count from public.company_ledger
   where domain_key = 'bulk-zzledgertestzz.com';
  if v_count <> 1 then
    raise exception 'ON CONFLICT failed, found % rows', v_count;
  end if;
  raise notice 'PASS ON CONFLICT collapses duplicates';


  raise notice '--- cleanup ---------------------------------------------';

  delete from public.company_ledger where domain_key like '%zzledgertestzz%';
  delete from public.people_ledger  where linkedin_key like '%zzledgertestzz%';

  -- Assert the cleanup worked. Leaked test rows would poison future 'is this
  -- new?' answers invisibly; an earlier revision of this suite did leak rows,
  -- which is why this assertion exists.
  select (select count(*) from public.company_ledger where domain_key like '%zzledgertestzz%')
       + (select count(*) from public.people_ledger  where linkedin_key like '%zzledgertestzz%')
    into v_count;
  if v_count <> 0 then
    raise exception 'cleanup left % test row(s) behind', v_count;
  end if;

  raise notice 'ALL CHECKS PASSED -- ledger is behaving correctly and is clean.';
end;
$$;


-- ============================================================================
-- MANUAL / MONITORING QUERIES
-- ============================================================================

-- 1. HTTP contract (note the argument order: domain first, name second):
--
--      curl -X POST "https://<ref>.supabase.co/rest/v1/rpc/ledger_check_company" \
--        -H "apikey: <publishable-key>" -H "Content-Type: application/json" \
--        -d '{"Company Domain":"https://www.acme.com/","Name":"Acme Inc"}'

-- 2. Are any raw values unresolved Clay references? Must be 0:
--
--      select count(*) from public.company_ledger where "Company Domain" like '/%';

-- 3. Format spread of the RAW stored values (informational -- matching is
--    unaffected, since it uses domain_key):
--
--      select count(*) filter (where "Company Domain" ~* '^https?://') as with_scheme,
--             count(*) filter (where "Company Domain" ~* 'www\.')      as with_www,
--             count(*) filter (where "Company Domain" like '%/')       as trailing_slash,
--             count(*)                                                 as total
--      from public.company_ledger;

-- 4. Storage against the 500 MB ceiling:
--
--      select (select count(*) from public.company_ledger) as companies,
--             (select count(*) from public.people_ledger)  as people,
--             pg_size_pretty(pg_database_size(current_database())) as total_db;
