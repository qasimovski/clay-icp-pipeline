# Highways Blocklist Ledger

The Highways ICP's own "have I seen this record before?" ledger. Same design as
the Labs ledger in the parent directory and the EVCharge one beside it — read
`../README.md`, then `../evcharge/README.md`; this file records only what
differs for Highways.

| Ledger | Identity key | Payload (first sighting) | Plus |
|---|---|---|---|
| `highways_company_ledger` | `domain_key` (normalized domain) | `"Name"`, `"Company Domain"` | `first_seen` |
| `highways_people_ledger` | `linkedin_key` (normalized `/in/` slug) | `"Full Name"`, `"Linkedin Profile"` | `first_seen` |

Third vertical, same Supabase project, prefixed tables and RPCs. Highways
dedupes against Highways only — a company already worked under Labs or EVCharge
is `is_new = true` here. `03_verify.sql` check 9 asserts it.

**Starting empty.** No backfill: nothing has been worked under Highways yet, so
the first Clay run populates the ledger as it goes. The rule still stands for
later — **backfill before wiring the column, not after** — because the ledger
inserts on first sighting, so a run against an unbackfilled ledger marks
already-worked records `Is New` and re-spends the whole downstream funnel.

---

## Files

Apply in filename order, in the **same project** as the Labs ledger.

| File | Purpose |
|---|---|
| `01_schema.sql` | The two tables, prefixed constraints, RLS lockdown |
| `02_functions.sql` | The two check-and-insert RPCs + grants. **Requires the Labs `../02_functions.sql` first** — it reuses `normalize_domain`/`normalize_linkedin` and raises a clear error if they are missing |
| `03_verify.sql` | Self-asserting suite; prints `ALL HIGHWAYS CHECKS PASSED` or aborts. Cleans up after itself |
| `04_ping.sql` | **Optional.** Adds `highways_companies`/`highways_people` to `ledger_ping()`. Assumes `../evcharge/04_ping.sql` was applied — see the note in the file |

Re-running any of them is safe. Nothing here creates a second keepalive.

---

## The one real difference from Labs and EVCharge

**A dot is required in `domain_key`**, enforced twice: as a CHECK constraint in
`01_schema.sql` and as a pre-insert test in the RPC.

Why: `Official Domain`, whose formula is
`{{Website}} || {{Verified Domain}}?.verifiedDomain`, can carry the literal
string **`UNKNOWN`** — the `Verified Domain` Claygent is instructed to return it
when it cannot verify a domain — and `normalize_domain('UNKNOWN')` returns `'unknown'`, which
satisfies every Labs/EVCharge constraint (lowercase, non-empty, no slash, no
`www.`).

Left unguarded, the first unresolved company claims the key `'unknown'` and
**every later unresolved company is told `is_new = false`** — silently treated as
already-worked and dropped from the funnel. That is a wrong-answer bug, not a
wasted-credit one. Measured 2026-08-13: 13 such rows in Environmental Services
and Solutions Expo alone, so a few hundred across the 67 Highways tables.

> The live column binds `Domain` (see "Clay column config" below), which comes
> from Enrich Company and is either a real domain or empty — it never carries
> `UNKNOWN`. So the guard is not load-bearing for the CE tables as wired today.
> Keep it: it costs nothing, and it is the only thing standing between a future
> re-point at `Official Domain` and a silent whole-funnel collapse.

The test is structural rather than a word blocklist: every real domain contains
a dot, no sentinel does, so `'unknown'`, `'n/a'`, `'none'` and `'tbc'` are all
rejected without enumerating them. Such rows come back `skipped: true` and are
neither enriched-blocked nor recorded.

> The Labs and EVCharge ledgers do **not** have this guard. If either ever gets
> pointed at a column that can contain `UNKNOWN`, they are exposed to the same
> collapse.

---

## Clay column config

Identical to the Labs/EVCharge column **except the URL and one body value**.

- **Method:** `POST`
- **URL:**
  ```
  https://<project-ref>.supabase.co/rest/v1/rpc/ledger_check_highways_company
  https://<project-ref>.supabase.co/rest/v1/rpc/ledger_check_highways_person
  ```
  Same `<project-ref>` as Labs and EVCharge.
- **Headers:** reuse the existing saved **"HTTP API (Headers)" account**
  (`Qasim - Labs`). Same project ⇒ same publishable key.
  ```
  apikey:        <publishable-key>
  Content-Type:  application/json
  ```
  No `Prefer` header. Never a `sb_secret_...` key — it maps to `service_role`
  and bypasses RLS entirely.
- **Body** — exactly two keys, matching the RPC arguments:
  ```json
  { "Company Domain": "/Domain", "Name": "/Name" }
  ```
  Note the value: on the Highways tables there is **no column called
  `Company Domain`**. The "Domain and Company (CPJ)" template produces
  `Official Domain` (coalesce of `Website` and the Claygent result, populated on
  ~100% of rows) and `Domain` (Enrich Company's canonical domain, populated only
  where the enrichment matched — ~77-93%, measured per table).

  **`Domain` is the binding, confirmed against the live columns and reaffirmed
  by the user 2026-09-12.** Read straight off the deployed column with
  `clay tables columns get <tableId>`, the CE tables' `HTTP API` binds
  `Domain`, not `Official Domain`:

  ```
  "body": "... Clay.formatForJSON({{<Domain's field id>}}) ..."
  "conditionalRunFormulaText": "!!{{<Domain's field id>}}"
  ```

  Earlier revisions of this file specified `Official Domain` on coverage
  grounds. That was never what shipped, and the doc — not the column — was the
  thing that was wrong. The cost of `Domain` is accepted and deliberate: rows
  where Enrich Company found no canonical domain are not ledger-checked and not
  classified. The same binding is used by the Highways Registrar classifier, so
  the rows that get a ledger verdict and the rows that get classified are the
  same set.

  The JSON **key** must stay `"Company Domain"` — PostgREST matches body keys to
  argument names, and a third key or a renamed one answers `404 PGRST202`.

  Insert values with Clay's `/` column picker; do **not** type them. A typed
  `"/Name"` is stored as that literal string — this produced ten junk rows during
  the Labs setup. `03_verify.sql` check 12 fails if any such row exists.
- **Rate limit:** Request limit `10`, Duration `1000` ms.
- **Response mapping:** extract `is_new` into a boolean column named `Is New`.
  Optionally map `skipped` to spot unkeyable rows — worth doing here, since it
  is how you see the `UNKNOWN` population.
- **Conditional run:** `!!{{Domain}}` — "Run only if" `Domain` is not empty.
  Matches the live column and the classifier's gate.

### The branch

- `Is New = true` → first Highways sighting; this call recorded it. **Enrich.**
- `Is New = false` → already worked. **Skip** the paid columns downstream.
- `skipped = true` → unkeyable (blank or `UNKNOWN`). Nothing recorded; the row
  is not blocklisted and will be offered again next time.

`Is New` is **write-once**: the RPC is idempotent, so rerunning an *errored* cell
is safe, but rerunning a cell that already succeeded returns `is_new: false`. Do
not let that clobber a stored `true`.

Filter the view on `Is New` **before** any paid column runs — that ordering is
the entire saving.

---

## Cost

1 Action per row that fires; 0 Data Credits. This is the one paid column the
repo deliberately runs (CLAUDE.md), because it is what keeps `Enrich Company`,
Claygent and Find People off already-worked records.

Note the ordering wart specific to Highways: the `Domain and Company (CPJ)`
template was applied and **run** across all 67 tables before this ledger existed,
so that enrichment was paid for without the ledger gating it. The ledger starts
earning from the next pass onward — People, and any future Highways event.
