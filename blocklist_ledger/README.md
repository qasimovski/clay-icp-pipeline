# Central Blocklist Ledger for Clay (Supabase)

A single central "have I seen this record before?" ledger for hundreds of Clay
tables.

| Ledger | Identity key | Payload (first sighting) | Plus |
|---|---|---|---|
| `people_ledger` | `linkedin_key` (normalized `/in/` slug) | `"Full Name"`, `"Linkedin Profile"` | `first_seen` |
| `company_ledger` | `domain_key` (normalized domain) | `"Name"`, `"Company Domain"` | `first_seen` |

There is no `source` column — removed by request. Provenance (which table first
recorded a key) is therefore not tracked; `first_seen` is the only metadata.

**The core property:** one HTTP call per row does both the lookup and the insert,
atomically. That halves Clay Action spend versus lookup-then-insert, and
eliminates the race where two concurrent Clay rows both believe they're the first
sighting of a record.

**Matching is normalized.** Identity is a normalized key computed in the database
(`02_functions.sql`): `normalize_domain()` lowercases, strips whitespace/scheme/
`www.`/paths; `normalize_linkedin()` extracts the `/in/` slug (and returns NULL
for `/company/` pages). So `Acme.com`, `https://www.acme.com/` and `acme.com`
are the SAME company. A CHECK constraint makes an unnormalized key physically
unstorable, and `03_verify.sql` asserts the round-trip. Empty strings are
treated as `NULL`, so a blank Clay cell doesn't create a second row.

---

## Status: live and verified

Deployed to project `<project-ref>` and tested against it directly.

| Check | Result |
|---|---|
| Schema, constraints, RLS applied | ✅ |
| Verify suite (8 assertion groups) | ✅ `ALL CHECKS PASSED` |
| `anon` blocked from reading/writing tables directly | ✅ permission denied |
| `anon` can call only the 3 intended functions | ✅ |
| **Atomicity: 25 parallel connections, one key** | ✅ **exactly one `is_new=true`, one row** |
| Same, with a `NULL` second column | ✅ exactly one |
| Backfill idempotent across repeat runs | ✅ |
| **RPC over HTTP with NO `Prefer` header** | ✅ **HTTP 200, `{"is_new": true, "skipped": false}`** |
| Spaced parameter names (`"Full Name"`) through PostgREST | ✅ |
| Repeat HTTP call reports `is_new: false` | ✅ |
| Blank input over HTTP reports `skipped: true` | ✅ |
| Lockdown: direct table read via publishable key | ✅ **HTTP 401 permission denied** |
| Lockdown: direct table write via publishable key | ✅ **HTTP 401 permission denied** |
| **Atomicity over HTTP: 30 parallel requests** | ✅ **exactly 1 `true`, 29 `false`, 0 errors** |
| Ledger left clean after testing | ✅ `{companies: 0, people: 0}` |

Nothing remains unverified. The `Prefer`-header question that shaped this design
is now moot in both directions: the RPC needs no such header, and it is confirmed
working over real HTTP.

Only the Clay column itself is left to build (config below).

---

## Files

| File | Purpose |
|---|---|
| `01_schema.sql` | Two tables, composite unique constraints, RLS lockdown |
| `02_functions.sql` | The check-and-insert RPCs, keepalive, grants |
| `03_verify.sql` | Self-asserting test suite; aborts on the first bad assumption |
| `04_backfill.sql` | **Superseded — predates the normalized schema** (missing `domain_key`, stale constraint names). Use `load_csv.js` for bulk loads. |
| `load_csv.js` | The working bulk loader: CSV → ledger via Postgres `COPY`, normalizing with the same DB functions the RPCs use, at **0 Clay Actions** |

Re-running any of them is safe.

---

## Clay column config

One HTTP API enrichment column per table. People shown; companies is identical
with `ledger_check_company`, `"Name"` and `"Company Domain"`.

- **Method:** `POST`
- **URL:** `https://<project-ref>.supabase.co/rest/v1/rpc/ledger_check_person`
- **Headers** — save as a reusable **"HTTP API (Headers)" account** in Clay rather
  than pasting into hundreds of tables; that also makes key rotation a one-place
  change:
  ```
  apikey:        <publishable-key>
  Content-Type:  application/json
  ```
  Only these **two** are required — verified by testing each combination:

  | Headers sent | Result |
  |---|---|
  | `apikey` only (no body) | `200` |
  | `Authorization` only | **`401`** — not sufficient on its own |
  | `apikey` + `Content-Type`, with body | `200` |
  | `apikey` only, with body, no `Content-Type` | **`404 PGRST202`** — body unparsed |
  | none | `401` |

  So `Authorization: Bearer <key>` is **redundant** with the new publishable key
  format, while `Content-Type: application/json` is **mandatory** for any call
  carrying a JSON body — without it PostgREST cannot parse the body and reports
  the function as not found, which is a confusing way to fail. Adding
  `Authorization` anyway is harmless if you prefer it for portability.
  There is deliberately **no `Prefer` header** — that's the point of the RPC
  design (see the header comment in `02_functions.sql`), and it is confirmed
  working over real HTTP without one.

  This is the **publishable** key, which maps to the `anon` role. Verified above
  that it cannot read or write the tables directly — only call the three
  functions. Never use a `sb_secret_...` key here: it maps to `service_role` and
  bypasses RLS entirely, so pasting it into Clay columns would expose the whole
  ledger to anyone who saw it.
- **Body** — the JSON keys are your exact column names:
  ```json
  {
    "Full Name": "/Full Name",
    "Linkedin Profile": "/Linkedin Profile"
  }
  ```
  Exactly two keys. Sending a third (e.g. `"source"`) makes PostgREST fail with
  `404 PGRST202` — it matches functions by their full argument list, so an extra
  key means "no such function" rather than being ignored.

  The values must be inserted via Clay's `/` column picker, not typed. A typed
  `"/Name"` is stored as that literal string; this happened once during setup and
  produced ten ledger rows whose values were `/Name` and `/Domain`. Verify with:
  `select count(*) from public.company_ledger where "Name" like '/%';` → must be 0.
- **Rate limit:** Request limit `10`, Duration `1000` ms. Not strictly required —
  Supabase's free tier documents unlimited API requests — but it costs nothing and
  protects against a runaway 50k-row run.
- **Response mapping:** extract path `is_new` into a boolean column named
  `Is New`. Optionally map `skipped` too, to spot blank-input rows.
- **Conditional run:** "Run only if" your key column is not empty. Conditions and
  formulas cost **0 Actions**, so this is free savings on every row it skips.

### The "is it new?" branch

- `Is New = true` → first sighting; this call already recorded it. **Enrich.**
- `Is New = false` → already in the ledger. **Skip** — this is where you save both
  Actions and Data Credits on the expensive downstream columns.

No array-emptiness inspection, no second call, no window where two concurrent
rows both think they're first.

---

## Prevent the inactivity pause

Free projects pause after **7 days** without activity. Any API request resets the
timer, and `ledger_ping()` exists for this:

```
curl -X POST "https://<project-ref>.supabase.co/rest/v1/rpc/ledger_ping" \
  -H "apikey: <publishable-key>" \
  -H "Authorization: Bearer <publishable-key>"
```

Schedule it daily — GitHub Actions cron, Windows Task Scheduler, or any free
uptime pinger. I'd use an **external** ping rather than `pg_cron`, because I could
not confirm whether Supabase's pause detection counts purely-internal database
activity. An external HTTP request definitely counts.

If it pauses anyway: restore is a click, takes a few minutes, and data survives
intact for up to a year.

---

## Security model

Why it's safe to put the anon key in hundreds of Clay columns:

1. RLS is **enabled** on both tables with **no policies**, and table privileges are
   revoked from `anon`. Verified: `permission denied for table people_ledger`.
2. All access flows through `SECURITY DEFINER` functions owned by `postgres`, each
   with `search_path` pinned — closing the search-path hijack vector that
   otherwise makes `SECURITY DEFINER` risky.
3. `anon` has `EXECUTE` on those three functions and nothing else.

Net effect if the key leaks: the holder can check and insert ledger entries. They
**cannot dump your ledger** or reach any other table.

One correction worth recording: an earlier revision revoked only `FROM public`,
which left a helper function callable by `anon` — Supabase grants EXECUTE
*directly* to `anon` by default, and revoking from `PUBLIC` does not remove a
direct role grant. Both revokes are now issued. Caught in testing, not in review.

**Two credentials to revoke.** Both were shared in chat and are in that
transcript:

1. **The database password** (Project Settings → Database) — grants full owner
   access, far beyond what Clay needs.
2. **The `sb_secret_...` key** (Project Settings → API Keys) — maps to
   `service_role` and bypasses RLS entirely. Revoke and reissue it.

Neither is used by this setup, which runs on the publishable key. Revoking them
breaks nothing here.

---

## Operating notes

**Reruns are safe, but `Is New` is write-once.** The RPC is idempotent, so
rerunning errored Clay cells creates no duplicates. But a rerun of a row that
*already succeeded* returns `is_new: false`, because the record is now in the
ledger. If that overwrites a stored `true`, the row gets wrongly skipped for
enrichment. Don't let a rerun clobber an existing `true`.

**Clay does not auto-retry.** On a 429 or 5xx the cell errors and stays errored.
Recover with right-click column → "Run All Rows that haven't run or have errors".
Each rerun is another Action.

**Normalization has limits.** Domains and LinkedIn slugs are normalized in the
DB, but people with no LinkedIn URL cannot be keyed at all (`skipped: true`),
and nothing fuzzy-matches names — the ledger keys on `linkedin_key` only. If a
lot of rows come back `skipped`, fix the LinkedIn coverage upstream in Clay
rather than trusting name-based dedupe.

**No backups on the free tier.** Real gap. Take a periodic dump:

```
pg_dump "<connection-string>" -t public.company_ledger -t public.people_ledger -Fc -f ledger.dump
```

The ledger is also reconstructible from your Clay tables, so it's low-stakes —
but a weekly dump is cheap insurance.

**Storage headroom.** ~250 bytes/row all-in (~4,000 rows/MB). 400k records ≈
100 MB of ledger, under 200 MB total, against a 500 MB ceiling — roughly 4×
headroom. Check quarterly with the query in `04_backfill.sql` Step 5. Postgres
won't silently truncate; writes start failing and Supabase warns first.

**Cost per run.** 1 Action per row that fires; **0 Data Credits**. A 20,000-row
table ≈ 20,000 Actions; 50,000 rows ≈ 50,000.

⚠️ Confirm with your CSM before a large run: Clay's docs phrase this as 1 Action
*"per execution"*. The per-row reading — one execution = one cell for an
enrichment column — is a well-supported inference, not a verbatim quote, and
Enterprise metering is negotiated. **Your Actions budget, not the 500 MB storage
ceiling, is the constraint that will actually bite.**

---

## Exit path

The Clay-side contract is just a URL, two header values, and a JSON body — and
PostgREST (the API layer Supabase exposes) is open source.

- **Need more room, least effort:** Supabase Pro, $25/mo → 8 GB, daily backups, no
  pausing. Zero Clay changes.
- **Stay free, self-host:** `pg_dump` → Postgres + PostgREST on a free VM behind
  Caddy/Let's Encrypt. Same contract, so the only Clay change is the hostname.

Migration is one `pg_dump`/`pg_restore` plus a base-URL swap; schema and functions
port unchanged. The tedious part is editing the URL across hundreds of tables —
an argument for putting the endpoint behind a stable custom domain
(`ledger.yourdomain.com`) now, while there's nothing to migrate.
