---
name: supabase-known-lookup
description: Read-only check of the team's shared Supabase (schema gtm) for companies and contacts we already have. Fills a people CSV's email and ZeroBounce validation columns from Supabase so nobody pays the email waterfall twice, flags people a colleague already searched with no email found, and flags company domains the team already knows or targeted for an event before paying for enrichment or classification. Use this whenever a people or company CSV is about to be enriched, sent through an email waterfall, or classified — and whenever someone asks "have we already enriched these", "do we already have emails for this list", "is this company new", "check Supabase", or the event-icp-clay-workflow skill reaches its company gate or final export. Never spends anything and never writes.
---

# Supabase known-lookup (read-only)

The team keeps one shared Postgres in Supabase, schema `gtm`: `companies` (by domain), `people`
(by `person_key` = `li:` + LinkedIn slug), `person_identities` (aliases), `emails` (with the
ZeroBounce answer), `paid_ledger` (every paid waterfall/verify, hits and misses) and
`company_targets` (which companies were targeted for which event/side). This skill only reads it.

Why it matters: an email found and verified once costs ~1.2 waterfall credits + a ZeroBounce
check. Reusing it is free. Checked on the Highways delivery (2026-10-21): 191 of 10,951 people
already had an email, 158 of them valid.

## Setup (once per machine)

Two ways in; the answers are identical (verified on 300 people and their companies, 2026-10-22).

**A. The team API — the default for everyone.** One shared team key, the same one the Clay email
workflow uses; no database password on the machine.
1. A `.env` with `SUPABASE_URL=https://<project-ref>.supabase.co` and `SUPABASE_PUBLISHABLE_KEY=...`
   (Supabase → Project Settings → API Keys), plus the team key file `clay-team.key` — ask the user
   where theirs are. Never ask for a key in the chat, and never echo one.
2. Prove it: `python scripts/supabase_lookup.py --api --env-file <.env> --api-key-file <clay-team.key> ping`.
3. Add `--api --api-key-file <clay-team.key>` to every command below.

**B. The database connection** (Qasim's machine, the gtm pipeline): `SUPABASE_DB_URL` in a `.env`
(it holds the master password — never paste or echo it), Python with `psycopg` (v3), and
`python scripts/supabase_lookup.py --env-file <path> ping` prints row counts only.

## Contacts — reuse emails before the waterfall

```bash
python scripts/supabase_lookup.py --env-file <.env> contacts \
    --in "<people>.csv" --linkedin-col "LinkedIn URL" --out "<people> - supabase.csv"
```

People are matched on every identity form the gtm pipeline writes: the LinkedIn slug (and its
URL-decoded form) plus two name+company-domain hashes (`nd:`, `nf:`). Pass `--first-col`,
`--last-col` / `--name-col` and `--domain-col` when the CSV's names differ from the defaults
(`First Name`, `Last Name`, `Person Name`, `Domain`) — without a domain only LinkedIn matches,
and on the Highways test that missed 9 of 86 reusable emails.

Adds, named like the Salesforce Competitive Events file: `Contact Email`, `Email Validation
Status`, `Email Sub Status`, `MX Provider`, `Validated At`, plus `Supabase Status`:

| Supabase Status | Meaning | Where it goes |
|---|---|---|
| `email reused - valid` | ZeroBounce valid | done — don't pay for this person |
| `email reused - risky` | catch-all / unknown / pending | filled in; tell the user how many, they may want a re-verify |
| `known, bad email` | invalid / spamtrap / abuse / do_not_mail | `… - user to decide.csv` |
| `known, no email` | in gtm.people, never got an email | `… - user to decide.csv` |
| `searched before, no email` | the team ledger says the waterfall missed | skipped |
| `not in supabase` / `no identity` | unknown | `… - still to enrich.csv`, the paid worklist |

**Ask the user about the "user to decide" file** — re-find them (pay again) or skip them. The gtm
pipeline skips them by default; don't silently do either. "Searched before" grows as the Clay email
workflow writes every paid miss back (api.record_contacts); misses from before Oct 2026 are not in it.

In the event-icp-clay-workflow pipeline this check also runs INSIDE the Clay email workflow, per
person, right before the waterfall (and there every stored email is reused as stored, including bad
ones). Running it here is for information: the marketing manager's review, or a list outside Clay.

Add `--with-person-fields` to also copy Supabase's first/last name, title, city/state/country
(prefixed `Supabase …`) when the user wants the stored row, not just the email. An email already
present in the input is never overwritten.

## Companies — the "is it new?" gate

```bash
python scripts/supabase_lookup.py --env-file <.env> companies \
    --in "<companies>.csv" --domain-col Domain --out "<companies> - supabase.csv"
```

Adds `Known Company` (yes / no / no domain), `Known Since`, `Known By` and `Targeted Events`
(`event_slug/side` list). "Known" only means the team has seen the domain somewhere — 107k
companies, many merely sourced. `Targeted Events` says it was actually worked for an event.
**Show the user both counts and ask which rule gates the spend** (skip all known? only those
targeted for this same event? none?). Don't decide for them: skipping known companies saves
credits but also drops companies that are legitimately new for this event. Once they choose,
re-run with `--skip known`, `--skip targeted`, or `--skip targeted-for-event --event-slug <slug>`;
it also writes `<out> - gated.csv`, the feed that may be paid for.

## Rules

- **Ask, don't guess, which column holds the LinkedIn URL or the domain** when the CSV has more
  than one candidate (e.g. `LinkedIn URL` vs `Company LinkedIn URL`; `Domain` vs `Website`). A
  company LinkedIn URL never matches a person, so the wrong column silently returns zero hits.
- Report counts, never rows. Contact data stays in files, not in the conversation.
- This skill never writes. Clay's email workflow records its own paid answers through the team API
  (api.record_contacts). Recording a whole delivered list is the `gtm` pipeline's job (`clay_to_claude`: `gtm supabase import-lists <csv> --event <slug>`, dry run
  by default) — offer it, explain what it writes, and run it only after the user says yes.
- If the connection fails, say so plainly and stop the paid step that depended on it; don't fall
  back to "assume everything is new".
