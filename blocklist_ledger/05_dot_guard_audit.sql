-- ============================================================================
-- 05_dot_guard_audit.sql  --  READ-ONLY. Writes nothing. Safe at any time.
--
-- Answers one question across every company ledger in the project:
--   "Has a dotless key already collapsed part of this vertical's funnel?"
--
-- WHAT A DOTLESS KEY IS AND WHY IT MATTERS
--   A resolved-domain column is normally a coalesce of a scraped website and an
--   AI fallback, and that fallback returns a literal sentinel -- 'UNKNOWN' -- when
--   it cannot resolve one. normalize_domain('UNKNOWN') returns 'unknown':
--   lowercase, non-empty, no slash, no 'www.', so it satisfies every constraint
--   the Labs and EVCharge ledgers carry.
--
--   The first such row therefore claims the key 'unknown', and EVERY later
--   unresolved company is told is_new=false -- silently treated as already-worked
--   and dropped from the funnel. That is a WRONG-ANSWER bug, not a wasted-credit
--   one: it never shows up as spend, only as companies that quietly stopped being
--   enriched. The same collapse happens for any sentinel of that shape ('n/a',
--   'none', 'tbc') and for a company NAME accidentally keyed as a domain.
--
--   Highways hit exactly this and added a dot guard (a real domain always has a
--   dot; no sentinel does). Labs and EVCharge predate that guard.
--
-- WHY LABS HAS PROBABLY SURVIVED SO FAR -- AND WHY THAT IS ABOUT TO STOP
--   Labs is not protected by the database. It is protected by one JavaScript
--   string inside a Clay formula column (automation/build_automation/
--   build_workbook.py, the "Company Domain" formula), which maps UNKNOWN -> "":
--
--     ({{Normalize a Domain}}||"") ? ({{Normalize a Domain}}||"")
--       : ((({{Official Domain}}||"").toUpperCase()=="UNKNOWN") ? "" : ({{Official Domain}}||""))
--
--   That protection is invisible to Postgres, replicated by hand across every
--   workbook, and disappears the moment a caller other than that formula reaches
--   the RPC. Any replacement for the Clay layer that forwards a resolved domain
--   without reproducing the UNKNOWN case re-opens the collapse against the
--   largest and most valuable ledger in the project.
--
-- HOW TO READ THE RESULT
--   has_dot_guard = false and dotless_rows = 0
--       Not yet hit. Apply 06_dot_guard_apply.sql to make it structural.
--   has_dot_guard = false and dotless_rows > 0
--       Already hit. Companies have been silently dropped. Read the note at the
--       bottom of 06_dot_guard_apply.sql -- deleting the bad key fixes the ledger
--       but does NOT repair the stale `Is New = false` values already recorded
--       downstream; those rows need their ledger call re-run.
--   has_dot_guard = true
--       Guarded (Highways, and anything from new_vertical.py). Nothing to do.
-- ============================================================================

drop table if exists pg_temp.dot_guard_audit;

create temp table dot_guard_audit (
  ledger         text,
  has_dot_guard  boolean,
  total_rows     bigint,
  dotless_rows   bigint,
  dotless_keys   text
);

do $$
declare
  v_tbl    text;
  v_has    boolean;
  v_total  bigint;
  v_bad    bigint;
  v_sample text;
begin
  -- Discovered rather than listed, so a vertical added later cannot be missed
  -- simply because nobody remembered to add it to this file.
  for v_tbl in
    select c.relname
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public'
       and c.relkind = 'r'
       and (c.relname = 'company_ledger' or c.relname like '%\_company\_ledger')
     order by c.relname
  loop
    -- The dot guard is the only CHECK on these tables that uses position();
    -- the *_key_clean constraint uses lower/btrim/length/like only.
    select exists (
      select 1
        from pg_constraint
       where conrelid = format('public.%I', v_tbl)::regclass
         and contype = 'c'
         and pg_get_constraintdef(oid) ilike '%position%'
    ) into v_has;

    execute format('select count(*) from public.%I', v_tbl) into v_total;
    execute format(
      'select count(*) from public.%I where position(''.'' in domain_key) = 0',
      v_tbl) into v_bad;
    execute format($q$
      select string_agg(distinct domain_key, ', ' order by domain_key)
        from (select domain_key from public.%I
               where position('.' in domain_key) = 0
               limit 25) s
    $q$, v_tbl) into v_sample;

    insert into dot_guard_audit
      values (v_tbl, v_has, v_total, v_bad, v_sample);
  end loop;
end $$;

select
  ledger,
  has_dot_guard,
  total_rows,
  dotless_rows,
  case
    when has_dot_guard              then 'guarded - nothing to do'
    when dotless_rows = 0           then 'UNGUARDED, not yet hit - apply 06'
    else                                 'UNGUARDED AND ALREADY HIT - apply 06, then re-run affected ledger cells'
  end as verdict,
  dotless_keys
from dot_guard_audit
order by has_dot_guard, dotless_rows desc, ledger;
