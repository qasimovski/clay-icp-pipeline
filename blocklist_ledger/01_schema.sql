-- ============================================================================
-- Central Blocklist / "have I seen this before?" ledger for Clay
-- 01_schema.sql  --  tables, constraints, lockdown
--
-- Run FIRST. Idempotent: safe to re-run.
--
-- IDENTITY
--   companies : normalized domain          (domain_key)
--   people    : normalized LinkedIn slug   (linkedin_key)
--
-- The name columns ("Name", "Full Name") are STORED but do NOT participate in
-- identity. Lookup happens on the domain / profile URL; the name is payload.
--
-- WHY normalization is back after being removed:
--   Clay emits the same domain in multiple shapes -- 'https://www.acme.com/',
--   'www.acme.com', 'acme.com' -- so exact matching cannot work in either
--   direction. Measured on the first 50,548-row load: 65.7% carried a scheme,
--   52.6% contained 'www.', 14.7% a trailing slash, and only 31.1% were bare.
--   1,851 domains were present in more than one format within a single export.
--
-- Normalization lives in the DATABASE, not in Clay:
--   * one place instead of a formula column on hundreds of tables
--   * costs 0 Clay Actions
--   * robust to whatever format arrives next, not just the ones seen so far
--   * cannot be bypassed -- see LOCKDOWN below
--
-- The raw value is still stored so the original is always inspectable. Only
-- MATCHING uses the normalized form.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- Companies ledger -- identity is domain_key
--
-- domain_key is NOT NULL and UNIQUE, so NULLS NOT DISTINCT is no longer needed:
-- a row without a usable domain cannot exist, and the RPC reports such input as
-- skipped rather than inventing a key for it.
-- ---------------------------------------------------------------------------
create table if not exists public.company_ledger (
  domain_key        text        not null,
  "Name"            text,
  "Company Domain"  text,
  first_seen        timestamptz not null default now(),

  constraint company_ledger_key_uniq  unique (domain_key),
  constraint company_ledger_key_clean check (
    domain_key = lower(btrim(domain_key))
    and length(domain_key) > 0
    and domain_key not like '%/%'      -- no scheme, no path
    and domain_key not like 'www.%'
  )
);

comment on table public.company_ledger is
  'Seen-before ledger for companies. Identity is domain_key (normalized domain). "Name"/"Company Domain" store the values as first seen. Write via ledger_check_company().';
comment on column public.company_ledger.domain_key is
  'Normalized domain -- the unique identity. Produced by normalize_domain().';
comment on column public.company_ledger."Company Domain" is
  'Domain exactly as first received. Later calls in other formats match on domain_key and do NOT overwrite this.';


-- ---------------------------------------------------------------------------
-- People ledger -- identity is linkedin_key (the /in/ vanity slug)
-- ---------------------------------------------------------------------------
create table if not exists public.people_ledger (
  linkedin_key        text        not null,
  "Full Name"         text,
  "Linkedin Profile"  text,
  first_seen          timestamptz not null default now(),

  constraint people_ledger_key_uniq  unique (linkedin_key),
  constraint people_ledger_key_clean check (
    linkedin_key = lower(btrim(linkedin_key))
    and length(linkedin_key) > 0
    and linkedin_key not like '%/%'
  )
);

comment on table public.people_ledger is
  'Seen-before ledger for people. Identity is linkedin_key (the /in/ slug). Write via ledger_check_person().';
comment on column public.people_ledger.linkedin_key is
  'Normalized LinkedIn vanity slug -- the unique identity. Produced by normalize_linkedin().';


-- ---------------------------------------------------------------------------
-- LOCKDOWN
--
-- Doubles as the integrity guarantee. RLS is enabled with NO policies and table
-- privileges are revoked, so `anon` cannot write these tables directly -- only
-- EXECUTE the SECURITY DEFINER functions. There is therefore NO path by which an
-- unnormalized row can be inserted using the publishable key, even deliberately.
-- The security model and the data-integrity model are the same mechanism.
-- ---------------------------------------------------------------------------
alter table public.company_ledger enable row level security;
alter table public.people_ledger  enable row level security;

revoke all on table public.company_ledger from anon, authenticated;
revoke all on table public.people_ledger  from anon, authenticated;
