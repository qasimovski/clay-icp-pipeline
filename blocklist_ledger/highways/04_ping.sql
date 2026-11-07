-- ============================================================================
-- Highways blocklist ledger for Clay
-- highways/04_ping.sql  --  OPTIONAL: surface Highways counts in the keepalive
--
-- Run LAST, and only if you want it. Idempotent.
--
-- Nothing depends on this. The existing daily ledger_ping() already keeps the
-- project awake for ALL the ledgers -- one project, one 7-day inactivity timer,
-- so there is no third keepalive to schedule. This file exists purely so that
-- ping also reports how the Highways ledgers are growing, which is otherwise
-- invisible: `anon` cannot read the tables, by design.
--
-- WHAT IS AND IS NOT CHANGED
--   The function NAME is unchanged and every existing response key keeps its
--   exact spelling and meaning -- 'companies'/'people' still count the LABS
--   ledgers, 'evcharge_*' still count EVCharge. Only two keys are added. Any
--   existing pinger, cron job or dashboard reading this response keeps working
--   untouched; that is a deliberate constraint, not a coincidence.
--
--   This file assumes evcharge/04_ping.sql was applied (it preserves those two
--   keys). If it was NOT, the evcharge_* sub-selects below will fail on missing
--   tables -- in that case delete those two lines before running.
--
--   If you would rather not touch a live function at all, skip this file. The
--   cost is only that you check Highways counts by hand in the SQL editor.
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
    'evcharge_people',    (select count(*) from public.evcharge_people_ledger),
    'highways_companies', (select count(*) from public.highways_company_ledger),
    'highways_people',    (select count(*) from public.highways_people_ledger)
  );
$$;

-- CREATE OR REPLACE preserves existing grants, but re-issuing them keeps this
-- file safe to run against a project where the files were applied in a
-- different order. Both revokes are required on Supabase -- see ../02_functions.sql.
revoke all on function public.ledger_ping() from public, anon, authenticated;
grant execute on function public.ledger_ping() to anon, authenticated;
