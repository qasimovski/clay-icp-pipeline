-- ============================================================================
-- Highways blocklist / "have I seen this before?" ledger for Clay
-- highways/01_schema.sql  --  tables, constraints, lockdown
--
-- Run FIRST, in the SAME Supabase project as the Labs and EVCharge ledgers.
-- Idempotent.
--
-- Third vertical, same pattern as blocklist_ledger/evcharge/: prefixed tables
-- and prefixed RPCs beside the Labs ones, never a discriminator column on the
-- live tables. Read ../README.md then ../evcharge/README.md first; this file and
-- its siblings record only what differs for Highways.
--
-- Highways dedupes against Highways only. A company already worked under Labs or
-- EVCharge is `is_new = true` here and gets enriched again -- they are different
-- audiences. 03_verify.sql check 6 asserts that rather than leaving it to be
-- discovered later.
--
-- IDENTITY -- unchanged from Labs, same normalizers, same guarantees:
--   companies : normalized domain          (domain_key)
--   people    : normalized LinkedIn slug   (linkedin_key)
--
-- ONE DELIBERATE DIFFERENCE FROM THE LABS/EVCHARGE SCHEMA -- see the
-- `domain_key_shaped` check below. It exists because the Highways source tables
-- feed this ledger from a column that can legitimately contain the string
-- 'UNKNOWN', which the shared normalize_domain() happily returns as 'unknown'.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- Highways companies ledger -- identity is domain_key
--
-- Constraint names are prefixed too: a UNIQUE constraint creates an index, and
-- index names share the schema-wide relation namespace, so an unprefixed name
-- here would collide with the Labs / EVCharge ones.
-- ---------------------------------------------------------------------------
create table if not exists public.highways_company_ledger (
  domain_key        text        not null,
  "Name"            text,
  "Company Domain"  text,
  first_seen        timestamptz not null default now(),

  constraint highways_company_ledger_key_uniq  unique (domain_key),
  constraint highways_company_ledger_key_clean check (
    domain_key = lower(btrim(domain_key))
    and length(domain_key) > 0
    and domain_key not like '%/%'      -- no scheme, no path
    and domain_key not like 'www.%'
  ),

  -- WHY THIS EXTRA CHECK EXISTS (Highways only).
  --   The Clay column here keys on `Official Domain`, whose formula is
  --   `{{Website}} || {{Verified Domain}}?.verifiedDomain`. The Verified Domain
  --   Claygent is instructed to return the literal string 'UNKNOWN' when it
  --   cannot verify a domain, so hundreds of rows across the 67 Highways tables
  --   carry Official Domain = 'UNKNOWN' (13 in Environmental Services and
  --   Solutions Expo alone, measured 2026-08-13).
  --
  --   normalize_domain('UNKNOWN') returns 'unknown' -- lowercase, no slash,
  --   non-empty -- so it satisfies every constraint above. The first such row
  --   would insert one ledger row keyed 'unknown' and EVERY later unresolved
  --   company would come back is_new=false, i.e. silently blocklisted as
  --   "already worked" when nothing had been worked at all.
  --
  --   Requiring a dot is the general form of that guard: every real domain has
  --   one, and no sentinel word does. Paired with the RPC's own pre-check in
  --   02_functions.sql, an unkeyable value is both un-inserted AND unstorable --
  --   same belt-and-braces the repo uses for normalization.
  constraint highways_company_ledger_key_shaped check (
    position('.' in domain_key) > 0
  )
);

comment on table public.highways_company_ledger is
  'Seen-before ledger for Highways companies. Identity is domain_key (normalized domain). "Name"/"Company Domain" store the values as first seen. Write via ledger_check_highways_company(). Separate from public.company_ledger (Labs) and public.evcharge_company_ledger on purpose -- the verticals dedupe independently.';
comment on column public.highways_company_ledger.domain_key is
  'Normalized domain -- the unique identity. Produced by normalize_domain(). Must contain a dot: see highways_company_ledger_key_shaped.';
comment on column public.highways_company_ledger."Company Domain" is
  'Domain exactly as first received. Later calls in other formats match on domain_key and do NOT overwrite this.';


-- ---------------------------------------------------------------------------
-- Highways people ledger -- identity is linkedin_key (the /in/ vanity slug)
--
-- No people tables exist under Highways yet; this is created now so the pair
-- stays symmetrical and the People pass has somewhere to write when it lands.
-- A slug is not a domain, so it gets no dot check.
-- ---------------------------------------------------------------------------
create table if not exists public.highways_people_ledger (
  linkedin_key        text        not null,
  "Full Name"         text,
  "Linkedin Profile"  text,
  first_seen          timestamptz not null default now(),

  constraint highways_people_ledger_key_uniq  unique (linkedin_key),
  constraint highways_people_ledger_key_clean check (
    linkedin_key = lower(btrim(linkedin_key))
    and length(linkedin_key) > 0
    and linkedin_key not like '%/%'
  )
);

comment on table public.highways_people_ledger is
  'Seen-before ledger for Highways people. Identity is linkedin_key (the /in/ slug). Write via ledger_check_highways_person(). Separate from public.people_ledger (Labs) and public.evcharge_people_ledger on purpose.';
comment on column public.highways_people_ledger.linkedin_key is
  'Normalized LinkedIn vanity slug -- the unique identity. Produced by normalize_linkedin().';


-- ---------------------------------------------------------------------------
-- LOCKDOWN
--
-- Doubles as the integrity guarantee. RLS is enabled with NO policies and table
-- privileges are revoked, so `anon` cannot write these tables directly -- only
-- EXECUTE the SECURITY DEFINER functions. There is therefore NO path by which an
-- unnormalized or unshaped row can be inserted using the publishable key, even
-- deliberately.
--
-- The publishable key is now shared across three verticals' Clay columns, so the
-- blast radius of that one key is all three ledgers. It stays "can check and
-- insert, cannot read" for every one of them.
-- ---------------------------------------------------------------------------
alter table public.highways_company_ledger enable row level security;
alter table public.highways_people_ledger  enable row level security;

revoke all on table public.highways_company_ledger from anon, authenticated;
revoke all on table public.highways_people_ledger  from anon, authenticated;
