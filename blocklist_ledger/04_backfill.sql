-- ============================================================================
-- Central Blocklist ledger for Clay
-- 04_backfill.sql  --  load your existing ~400k records at ZERO Clay Actions
--
-- *** SUPERSEDED — DO NOT RUN AGAINST THE CURRENT SCHEMA ***
-- This script predates the normalized-key schema: its Step 4 inserts omit the
-- NOT NULL domain_key/linkedin_key columns and target constraints named
-- company_ledger_uniq/people_ledger_uniq, which are now
-- company_ledger_key_uniq/people_ledger_key_uniq (01_schema.sql). It will fail.
-- Use load_csv.js instead — it normalizes with the same DB functions the RPCs
-- use. Kept for history only.
--
-- (Original header: run FOURTH, after the verify suite passes.)
--
-- WHY THIS MATTERS: pushing the existing backlog through Clay's HTTP column costs
-- ~1 Action per row. Loading it directly into Postgres costs zero. Do the
-- backfill here and reserve the Clay column for genuinely new rows.
--
-- NOTE: staging tables are used rather than importing straight into the ledger,
-- because a CSV containing duplicate pairs would abort on the unique constraint
-- partway through. Staging + INSERT ... ON CONFLICT is restartable.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- STEP 1 -- staging tables (unconstrained, so a messy CSV always lands)
-- ---------------------------------------------------------------------------
create table if not exists public.stg_company_import (
  "Name"           text,
  "Company Domain" text
);

create table if not exists public.stg_people_import (
  "Full Name"        text,
  "Linkedin Profile" text
);

-- ⚠️ DELIBERATELY COMMENTED OUT. These must NOT run automatically.
--
-- The intended order is: create staging (above) -> import your CSV (Step 2) ->
-- inspect (Step 3) -> load (Step 4). If a TRUNCATE ran here on every execution,
-- then running this file top-to-bottom AFTER importing your CSV would wipe the
-- import and Step 4 would silently load zero rows -- looking like success.
--
-- Uncomment and run these two lines ONLY when you want to discard a previous
-- staging load and start the import over.
--
-- truncate public.stg_company_import;
-- truncate public.stg_people_import;


-- ---------------------------------------------------------------------------
-- STEP 2 -- get your data into staging. Two options.
--
-- OPTION A (easiest): Supabase Dashboard -> Table Editor -> stg_people_import
--   -> Insert -> "Import data from CSV". CSV headers must match the column names
--   EXACTLY, including spaces and capitals: "Full Name", "Linkedin Profile".
--
-- OPTION B (better for 400k rows): psql. Connection string is at
--   Project Settings -> Database -> Connection string. \copy runs client-side, so
--   the path is local to you:
--
--     psql "<connection-string>" -c "\copy public.stg_people_import (\"Full Name\", \"Linkedin Profile\") FROM 'C:/path/people.csv' WITH (FORMAT csv, HEADER true)"
--
--   Mind the escaped double quotes around the column names -- they are required.
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- STEP 3 -- inspect BEFORE committing anything to the real ledger.
--
-- Do not skip this. A bad backfill silently poisons every future 'is this new?'
-- answer, and the failure mode is invisible: records get skipped as
-- 'already seen' when they were never actually seen.
-- ---------------------------------------------------------------------------

-- Row counts and how many distinct pairs they reduce to.
select
  count(*)                                                  as staged_rows,
  count(distinct ("Full Name", "Linkedin Profile"))         as distinct_pairs,
  count(*) filter (where coalesce("Full Name",'') = ''
                     and coalesce("Linkedin Profile",'') = '') as unusable_rows
from public.stg_people_import;

select
  count(*)                                            as staged_rows,
  count(distinct ("Name", "Company Domain"))          as distinct_pairs,
  count(*) filter (where coalesce("Name",'') = ''
                     and coalesce("Company Domain",'') = '') as unusable_rows
from public.stg_company_import;

-- Because there is NO normalization, near-duplicates that differ only in case or
-- whitespace will become SEPARATE ledger rows. Review this before loading -- if
-- the list is long, your upstream data is less normalized than assumed and you
-- may want to clean it in Clay first.
select lower(btrim("Company Domain")) as domain_folded,
       count(*)                        as variants,
       array_agg(distinct "Company Domain") as spellings
from public.stg_company_import
where coalesce("Company Domain",'') <> ''
group by 1
having count(distinct "Company Domain") > 1
order by 2 desc
limit 25;

select lower(btrim("Linkedin Profile")) as profile_folded,
       count(distinct "Linkedin Profile") as variants,
       array_agg(distinct "Linkedin Profile") as spellings
from public.stg_people_import
where coalesce("Linkedin Profile",'') <> ''
group by 1
having count(distinct "Linkedin Profile") > 1
order by 2 desc
limit 25;


-- ---------------------------------------------------------------------------
-- STEP 4 -- load into the real ledger.
--
-- ON CONFLICT DO NOTHING makes this idempotent and re-runnable: existing pairs
-- keep their original first_seen, so re-importing never destroys the true
-- first-sighting date.
--
-- Plain SELECT DISTINCT now suffices: with `source` gone there is nothing to
-- choose between duplicate pairs, so DISTINCT ON + ORDER BY is unnecessary.
--
-- nullif(...,'') mirrors what the RPCs do, so a blank cell imported from CSV and
-- the same blank arriving later from Clay land on the SAME ledger row.
-- ---------------------------------------------------------------------------
insert into public.company_ledger ("Name", "Company Domain")
select distinct nullif("Name",''), nullif("Company Domain",'')
from public.stg_company_import
where nullif("Name",'') is not null
   or nullif("Company Domain",'') is not null
on conflict on constraint company_ledger_uniq do nothing;

insert into public.people_ledger ("Full Name", "Linkedin Profile")
select distinct nullif("Full Name",''), nullif("Linkedin Profile",'')
from public.stg_people_import
where nullif("Full Name",'') is not null
   or nullif("Linkedin Profile",'') is not null
on conflict on constraint people_ledger_uniq do nothing;


-- ---------------------------------------------------------------------------
-- STEP 5 -- confirm the result and check storage against the plan.
-- ---------------------------------------------------------------------------
select
  (select count(*) from public.company_ledger) as company_rows,
  (select count(*) from public.people_ledger)  as people_rows,
  pg_size_pretty(
      pg_total_relation_size('public.company_ledger')
    + pg_total_relation_size('public.people_ledger')
  ) as ledger_size,
  pg_size_pretty(pg_database_size(current_database())) as total_db_size;

-- Expect roughly ~250 bytes/row all-in. 400k rows should land near 100 MB of
-- ledger and comfortably under 200 MB of total database against the 500 MB free
-- tier ceiling. Investigate before rolling out if it is materially larger.


-- ---------------------------------------------------------------------------
-- STEP 6 -- drop staging. It is pure overhead against a 500 MB ceiling.
-- Uncomment and run once you are satisfied with Step 5.
-- ---------------------------------------------------------------------------
-- drop table if exists public.stg_company_import;
-- drop table if exists public.stg_people_import;
-- vacuum full public.company_ledger;
-- vacuum full public.people_ledger;
