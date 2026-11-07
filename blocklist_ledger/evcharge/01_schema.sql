-- ============================================================================
-- EVCharge blocklist / "have I seen this before?" ledger for Clay
-- evcharge/01_schema.sql  --  tables, constraints, lockdown
--
-- Run FIRST, in the SAME Supabase project as the Labs ledger. Idempotent.
--
-- WHY SEPARATE TABLES RATHER THAN A DISCRIMINATOR COLUMN
--   The Labs ledger deliberately has no `source` column (removed by request),
--   and identity there is a plain UNIQUE on the normalized key. Adding an ICP
--   discriminator would mean widening that unique constraint on a live table
--   holding 82,875 people rows -- a migration of production identity semantics
--   to serve a new vertical. Two prefixed tables cost nothing and leave the
--   Labs ledger byte-identical.
--
--   The consequence is the intended one: EVCharge dedupes against EVCharge
--   only. A company already worked under Labs is `is_new = true` here and gets
--   enriched again. That is the point -- they are different audiences.
--
-- IDENTITY -- unchanged from Labs, same normalizers, same guarantees:
--   companies : normalized domain          (domain_key)
--   people    : normalized LinkedIn slug   (linkedin_key)
--
-- The name columns ("Name", "Full Name") are STORED but do NOT participate in
-- identity. Lookup happens on the domain / profile URL; the name is payload.
--
-- Normalization lives in the DATABASE (see evcharge/02_functions.sql, which
-- reuses the Labs project's normalize_domain / normalize_linkedin rather than
-- redefining them -- one definition, one behaviour, both ICPs).
-- ============================================================================


-- ---------------------------------------------------------------------------
-- EVCharge companies ledger -- identity is domain_key
--
-- Constraint names are prefixed too: a UNIQUE constraint creates an index, and
-- index names share the schema-wide relation namespace, so an unprefixed
-- `company_ledger_key_uniq` here would collide with the Labs one.
-- ---------------------------------------------------------------------------
create table if not exists public.evcharge_company_ledger (
  domain_key        text        not null,
  "Name"            text,
  "Company Domain"  text,
  first_seen        timestamptz not null default now(),

  constraint evcharge_company_ledger_key_uniq  unique (domain_key),
  constraint evcharge_company_ledger_key_clean check (
    domain_key = lower(btrim(domain_key))
    and length(domain_key) > 0
    and domain_key not like '%/%'      -- no scheme, no path
    and domain_key not like 'www.%'
  )
);

comment on table public.evcharge_company_ledger is
  'Seen-before ledger for EVCharge companies. Identity is domain_key (normalized domain). "Name"/"Company Domain" store the values as first seen. Write via ledger_check_evcharge_company(). Separate from public.company_ledger (Labs) on purpose -- the two verticals dedupe independently.';
comment on column public.evcharge_company_ledger.domain_key is
  'Normalized domain -- the unique identity. Produced by normalize_domain().';
comment on column public.evcharge_company_ledger."Company Domain" is
  'Domain exactly as first received. Later calls in other formats match on domain_key and do NOT overwrite this.';


-- ---------------------------------------------------------------------------
-- EVCharge people ledger -- identity is linkedin_key (the /in/ vanity slug)
-- ---------------------------------------------------------------------------
create table if not exists public.evcharge_people_ledger (
  linkedin_key        text        not null,
  "Full Name"         text,
  "Linkedin Profile"  text,
  first_seen          timestamptz not null default now(),

  constraint evcharge_people_ledger_key_uniq  unique (linkedin_key),
  constraint evcharge_people_ledger_key_clean check (
    linkedin_key = lower(btrim(linkedin_key))
    and length(linkedin_key) > 0
    and linkedin_key not like '%/%'
  )
);

comment on table public.evcharge_people_ledger is
  'Seen-before ledger for EVCharge people. Identity is linkedin_key (the /in/ slug). Write via ledger_check_evcharge_person(). Separate from public.people_ledger (Labs) on purpose.';
comment on column public.evcharge_people_ledger.linkedin_key is
  'Normalized LinkedIn vanity slug -- the unique identity. Produced by normalize_linkedin().';


-- ---------------------------------------------------------------------------
-- LOCKDOWN
--
-- Doubles as the integrity guarantee. RLS is enabled with NO policies and table
-- privileges are revoked, so `anon` cannot write these tables directly -- only
-- EXECUTE the SECURITY DEFINER functions. There is therefore NO path by which an
-- unnormalized row can be inserted using the publishable key, even deliberately.
-- The security model and the data-integrity model are the same mechanism.
--
-- This matters more here than it did for Labs: the publishable key is now shared
-- across two verticals' Clay columns, so the blast radius of that one key is
-- both ledgers. It stays "can check and insert, cannot read" for both.
-- ---------------------------------------------------------------------------
alter table public.evcharge_company_ledger enable row level security;
alter table public.evcharge_people_ledger  enable row level security;

revoke all on table public.evcharge_company_ledger from anon, authenticated;
revoke all on table public.evcharge_people_ledger  from anon, authenticated;
