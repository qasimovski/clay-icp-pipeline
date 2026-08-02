# Pipeline architecture

The same column pipeline runs inside every per-event Clay workbook,
regardless of entity type (Exhibitors, Sponsors, future Speakers, …) or ICP
(Labs, future Solar, …). Only the pieces called out below vary — everything
else is copy-identical.

Variance is governed by two config layers:
- `config/entity-types/<type>.yaml` — what raw columns exist and what they're named.
- `config/icps/<icp>/icp.yaml` — the classification taxonomy, fit lists, country tiers.

## Folder/workspace structure (same for every ICP)

- **`Competitive Events` folder** — one workbook per competitor event, named
  exactly after its scraper source folder. No reformatting, no added suffixes.
- **Blocklist ledger** (`blocklist_ledger/`) — a central **Supabase** registry,
  not a Clay table. Each workbook reaches it through one HTTP API column that
  does lookup-and-insert in a single call and returns `Is New`; the view is
  then filtered to `Is New` is checked, so the paid columns only ever run on
  companies not already worked. Active per-row gate, inline, before the spend.
  See `docs/PEOPLE_PASSES.md` for the same gate on the people side.

  > **Superseded:** a shared Clay "Block List" table one level up from
  > `Competitive Events`, fed by a Send Table Data action. It only recorded
  > companies — dedupe was meant to happen downstream at CRM-sync time — so
  > every event still paid to enrich repeats. Older workbooks may still carry
  > a `Send to Blocklist` column; that is the legacy mechanism, and
  > `automation/build_automation/blocklist_send.py` is the code that built it.
- **Build reusable Claygents once, not per event.** `Official Domain`, the
  ICP classifier (e.g. "Labs Series Registrar"), and `Sub Level` (if the ICP
  has one) belong in Clay's reusable-formula/Claygent layer. Every event's
  workbook deploys the same saved agent rather than the prompt being
  rewritten each time.
- **Cross-event repeats are gated, not just recorded:** a company exhibiting
  at several competitor shows still appears in each event's workbook, but the
  ledger check recognizes it from the second event onward and the `Is New`
  filter keeps it out of every paid column. (Under the old Blocklist table
  this was a standing tradeoff — repeats were logged but still enriched.)

## The run-now vs configure-only rule (always true)

Only steps that call a paid external provider per row are configure-only —
**create and fully configure them, but never trigger a full run**:
- Any Claygent/AI classification column (`Official Domain`, the ICP
  classifier, any sub-level classifier)
- The `Enrich Company` action
- Any Find People / contact-finding run

**Everything else should actually be created and executed** — it costs
nothing: creating tables/workbooks, importing CSVs, formula columns, and
Send Table Data actions between tables.

**One paid step is run anyway:** the blocklist ledger check (step 5). At
1 Action/row and 0 data credits it is the cheapest column in the pipeline,
and running it is what keeps the expensive ones off already-worked
companies.

## The column pipeline, in order

### 1. Source table (`{{MAIN_TABLE}}` — e.g. `Exhibitors_normalized` / `Sponsors_normalized`)

Base columns from import — entity-specific, see `config/entity-types/<type>.yaml: raw_columns`.

1. **`Normalize a Domain`** (formula, identical across entity types/ICPs) —
   strip protocol, `www.`, and path from the raw Website field. Reuse the
   existing workspace formula if one already exists; don't recreate under a
   different name.
2. **`Normalize Company Name`** (formula, identical) — lowercase, strip
   legal suffixes (Inc/LLC/Ltd/GmbH/…), collapse whitespace.
3. **`Official Domain`** (Claygent, identical prompt shape across ICPs;
   **configure only, do not run**) — only runs when the domain is still
   unknown after step 1. Web-research the verified corporate domain,
   exclude social/directory domains, return the bare registrable domain.
4. **`Company Domain`** (formula, identical) — coalesce: normalized domain,
   else Official Domain's result (unless it returned "unknown").
5. **Blocklist ledger check → `Is New`** (HTTP API action against the
   Supabase ledger, keyed on `Company Domain`; **run now** — 1 Action/row,
   0 data credits) — one call does lookup-and-insert atomically. **Then
   filter the view to `Is New` is checked** and build everything below
   against the filtered view; verify the filtered row count before
   continuing, because an unapplied filter means every paid column below
   runs against the whole table.

   Placed here because the ledger keys on the normalized domain, so it must
   follow `Company Domain` — but it still precedes `Enrich Company` and the
   classifier, which are the expensive steps it exists to gate.
6. **`Normalized Country`** (formula, identical lookup — sourced from the
   entity-specific country field, see `config/entity-types/<type>.yaml:
   country_source_field`) — map raw country strings to canonical names
   (e.g. `Great Britain`→`United Kingdom`). Unmapped values pass through
   unchanged.
7. **`Enrich Company`** (Clay's native action, keyed on `Company Domain`;
   **configure only, do not run**) — passthrough field set is
   entity-specific, see `config/entity-types/<type>.yaml:
   enrich_company_fields` (use the fuller set — see `KNOWN_ISSUES.md` on
   why the narrower set some tables use is drift, not intentional).
8. **`Resolved Description`** (formula, identical logic) — prefer
   `Enrich Company`'s returned description; fall back to the raw CSV
   description field (entity-specific field name — see
   `config/entity-types/<type>.yaml: description_source_field`).
9. **ICP classifier** (Claygent, e.g. "Labs Series Registrar";
   **configure only, do not run**) — outputs `Side` (Buyer/Seller) and
   `Classification` (one label from the ICP's closed taxonomy). Prompt body
   is 100% ICP-specific — see `config/icps/<icp>/icp.yaml: classifier`.
   Inputs: `Company Domain`, `Resolved Description`, `Primary Offerings`
   (from Enrich Company, if the entity type has it), `Company Name`.
10. **`Side`, `Classification`** (formulas, identical) — extract the two
    fields from the classifier's output.
11. **`Fit`** (lookup, identical mechanism — content is ICP-specific) — maps
    `Classification` to A/B/C via the ICP's fit-lookup list. Free, run now.
12. **`Country Fit`** (formula, identical mechanism — content is
    ICP-specific) — buckets `Normalized Country` into A/B/C, branched by
    `Side`, using the ICP's country tier lists. Free, run now.
13. **`Composite Tier`** (formula, identical mechanism for Sellers — content
    is entity/ICP-specific, see `config/entity-types/<type>.yaml:
    composite_tier` for whether this entity type uses the 2D Fit×CountryFit
    matrix or Country Fit alone) — Seller-only; Buyer composite is deferred
    to the contacts table (needs contact-level JT Fit). Free, run now.
14. **Split** (Send Table Data ×2, identical pattern) — `Side = Seller` →
    the Seller table; `Side = Buyer` → the Buyer table. Free, run now — they
    move no rows until the classifier has actually been run by a human.

### 2. Sellers table

Receives seller rows with `Classification`, `Fit`, `Country Fit`,
`Composite Tier` already attached.

- **`Sub Level`** (Claygent, if the ICP defines one; **configure only, do
  not run**) — restrict the option list to the sub-levels under whichever
  category `Classification` holds.
- **Route to contacts** (free, run now): filter `Composite Tier ∈ {1, 2}` →
  Send Table Data → the Seller contacts table. Tier 3 is inbound-only —
  do not send.

### 3. Buyers table

Receives buyer rows with `Classification`, `Fit`, `Country Fit` attached.
`Composite Tier` is still null, pending contact-level JT Fit.

- **Route to contacts** (free, run now): Send Table Data → the Buyer
  contacts table (all rows — volume here is naturally low).

### 4. Seller contacts table

One row per person. **Configure (don't run)** a Find People search per
company, filtered to the ICP's seller job-title list (senior titles = JT
Fit B/Tier 1, no sector filter; commercial keywords = JT Fit C/Tier 2, must
co-occur with a sector keyword).

- **`Sector Keyword Match`** (formula, free, run now) — true if the
  contact's title/company context contains one of the ICP's sector
  keywords. A Tier-2 (commercial) contact is only valid if this is true.

### 5. Buyer contacts table

One row per person. **Configure (don't run)** a Find People search per
buyer company, filtered to the BuySide job-title list matching that
company's `Classification` (from the ICP's BuySide job-title tiering
sheet).

- **`JT Fit`** — A = sector-specific keyword, B = senior title only,
  C = generic commercial.
- **`Composite Tier`** (formula, free, run now) — company `Fit` × contact
  `JT Fit` × company `Country Fit`, per the ICP's full Buyer composite-tier
  matrix.

## Reference lookup tables (workbook-scoped, shared across entity types within one ICP)

- **Fit lookup** — Classification → Fit (A/B/C).
- **Seller sub-levels** — Seller category → sub-level options.
- **Seller contact titles** — job title → match type, JT Fit, Tier, sector-filter-required.
- **Buyer contact titles** — BuySide segment → job title.

If a workbook already has these imported for one entity type, reuse them for
another rather than re-importing — they're the same reference data.
