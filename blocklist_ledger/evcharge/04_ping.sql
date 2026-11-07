-- ============================================================================
-- EVCharge blocklist ledger for Clay
-- evcharge/04_ping.sql  --  OPTIONAL: surface EVCharge counts in the keepalive
--
-- Run LAST, and only if you want it. Idempotent.
--
-- Nothing depends on this. The existing daily ledger_ping() already keeps the
-- project awake for BOTH ledgers -- one project, one 7-day inactivity timer, so
-- there is no second keepalive to schedule. This file exists purely so that
-- ping also tells you how the EVCharge ledgers are growing, which is otherwise
-- invisible: `anon` cannot read the tables, by design.
--
-- WHAT IS AND IS NOT CHANGED
--   The function NAME is unchanged and the three existing response keys
--   ('ok', 'companies', 'people') keep their exact spelling and meaning --
--   'companies'/'people' still count the LABS ledgers. Only two keys are added.
--   Any existing pinger, cron job or dashboard reading this response keeps
--   working untouched; that is a deliberate constraint, not a coincidence.
--
--   If you would rather not touch a live function at all, skip this file. The
--   cost is only that you check EVCharge counts by hand in the SQL editor.
-- ============================================================================

create or replace function public.ledger_ping()
returns json
language sql
security definer
set search_path = public, pg_temp
as $$
  select json_build_object(
    'ok', true,
    'companies',          (select count(*) from public.company_ledger),
    'people',             (select count(*) from public.people_ledger),
    'evcharge_companies', (select count(*) from public.evcharge_company_ledger),
    'evcharge_people',    (select count(*) from public.evcharge_people_ledger)
  );
$$;

-- CREATE OR REPLACE preserves existing grants, but re-issuing them keeps this
-- file safe to run against a project where 02_functions.sql was applied in a
-- different order. Both revokes are required on Supabase -- see ../02_functions.sql.
revoke all on function public.ledger_ping() from public, anon, authenticated;
grant execute on function public.ledger_ping() to anon, authenticated;
