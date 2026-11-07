# EVCharge Blocklist Ledger

The EVCharge ICP's own "have I seen this record before?" ledger. Same design as
the Labs ledger in the parent directory — read `../README.md` first; this file
records only what differs.

| Ledger | Identity key | Payload (first sighting) | Plus |
|---|---|---|---|
| `evcharge_people_ledger` | `linkedin_key` (normalized `/in/` slug) | `"Full Name"`, `"Linkedin Profile"` | `first_seen` |
| `evcharge_company_ledger` | `domain_key` (normalized domain) | `"Name"`, `"Company Domain"` | `first_seen` |

---

## The two decisions this directory encodes

**1. Same Supabase project, prefixed tables.** Not a second project. One
publishable key, one daily keepalive, one Clay "HTTP API (Headers)" account —
and the Labs tables are left byte-identical. The tradeoff accepted: both
verticals share the 500 MB free-tier ceiling and the blast radius of that one
key. At ~250 bytes/row that ceiling is not close.

**2. EVCharge dedupes against EVCharge only.** A company already worked under
Labs comes back `is_new = true` here and gets enriched again. That is intended —
they are different audiences — and `evcharge/03_verify.sql` check 6 asserts it
rather than leaving it to be discovered later. If you ever *do* want the
verticals to share a blocklist, that is a schema change, not a config toggle.

**Starting empty** (per the user, 2026-08-07). No backfill: the first Clay run
populates the ledger as it goes, so anything already enriched under EVCharge
gets enriched once more. See "Backfill later" below before that stops being
true.

---

## Files

Apply in filename order, in the **same project** as the Labs ledger.

| File | Purpose |
|---|---|
| `01_schema.sql` | The two tables, prefixed constraints, RLS lockdown |
| `02_functions.sql` | The two check-and-insert RPCs + grants. **Requires the Labs `../02_functions.sql` to be applied first** — it reuses `normalize_domain`/`normalize_linkedin` instead of redefining them, and raises a clear error if they are missing |
| `03_verify.sql` | Self-asserting suite; prints `ALL EVCHARGE CHECKS PASSED` or aborts on the first bad assumption |
| `04_ping.sql` | **Optional.** Adds `evcharge_companies`/`evcharge_people` to the existing `ledger_ping()` response. Existing keys unchanged; skip the file entirely if you'd rather not touch a live function |

Re-running any of them is safe.

Nothing here creates a second keepalive. One project, one 7-day inactivity
timer — the `ledger_ping()` you already schedule covers both ledgers.

---

## Clay column config

Identical to the Labs column **except the URL**. That is by construction: the
RPC argument names are deliberately the same, and PostgREST matches body keys to
argument names, so the headers, body, account, rate limit and response mapping
all carry over unchanged. In practice: duplicate the existing
"Companies - Supabase" / "People - Supabase" template, change one path segment,
save under an EVCharge name.

- **Method:** `POST`
- **URL:**
  ```
  https://<project-ref>.supabase.co/rest/v1/rpc/ledger_check_evcharge_company
  https://<project-ref>.supabase.co/rest/v1/rpc/ledger_check_evcharge_person
  ```
  Same `<project-ref>` as Labs.
- **Headers:** reuse the existing saved **"HTTP API (Headers)" account** (the
  Labs workspace calls it `Qasim - Labs`). Same project ⇒ same publishable key.
  Two headers, both required:
  ```
  apikey:        <publishable-key>
  Content-Type:  application/json
  ```
  No `Prefer` header — that is the point of the RPC design. `Authorization` is
  redundant with the publishable key format. Never a `sb_secret_...` key: it
  maps to `service_role` and bypasses RLS entirely.

  The account name reading "Labs" while serving EVCharge is a cosmetic wart, not
  a functional one. Creating a second account with the same key would work
  equally well; renaming the existing one would not — it is a live Clay object
  name that the Labs passes select by string.
- **Body** — exactly two keys, matching the RPC arguments:
  ```json
  { "Company Domain": "/Company Domain", "Name": "/Name" }
  { "Linkedin Profile": "/Linkedin Profile", "Full Name": "/Full Name" }
  ```
  A third key makes PostgREST answer `404 PGRST202` — it matches functions by
  their full argument list, so an extra key means "no such function".

  Insert the values with Clay's `/` column picker; do **not** type them. A typed
  `"/Name"` is stored as that literal string — this produced ten junk rows during
  the Labs setup. `03_verify.sql` check 12 fails if any such row exists.
- **Rate limit:** Request limit `10`, Duration `1000` ms.
- **Response mapping:** extract `is_new` into a boolean column named `Is New`.
  Optionally map `skipped` to spot unkeyable rows.
- **Conditional run:** "Run only if" the key column is not empty. Conditions and
  formulas cost 0 Actions.

### The branch

- `Is New = true` → first EVCharge sighting; this call recorded it. **Enrich.**
- `Is New = false` → already worked. **Skip** the paid columns downstream.

`Is New` is **write-once**: the RPC is idempotent, so rerunning an *errored* cell
is safe, but rerunning a cell that already succeeded returns `is_new: false`. Do
not let that clobber a stored `true`, or the row gets wrongly skipped.

Filter the view on `Is New` **before** any paid column runs — that ordering is
the entire saving.

---

## Cost

1 Action per row that fires; 0 Data Credits. This is the one paid column the
repo deliberately runs (CLAUDE.md), because it is what keeps `Enrich Company`,
Claygent and Find People off already-worked records.

---

## Backfill later

Not needed now (starting empty), but when it is:

`../load_csv.js` currently hard-codes only `--target company|people`, pointing at
the Labs tables. Pointing it at the EVCharge ledgers is a two-entry addition to
the target map near the top of that file — it has **not** been made, so do not
assume `--target evcharge-company` works.

The rule that matters more than the tooling: **backfill before wiring the
column, not after.** The ledger inserts on first sighting, so a Clay run against
an unbackfilled ledger marks already-worked records `Is New` and re-spends the
whole downstream funnel on them.
