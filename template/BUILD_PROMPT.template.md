# Build spec: {{ICP_NAME}} — {{ENTITY_TYPE}} pipeline in Clay (per-event)

> Generated from `template/BUILD_PROMPT.template.md` for entity type
> `{{ENTITY_TYPE}}` / ICP `{{ICP_NAME}}`. Regenerate with
> `python template/render_build_prompt.py --entity <entity> --icp <icp>`
> rather than hand-editing a rendered copy, so a config fix propagates.
> See `docs/PIPELINE_ARCHITECTURE.md` for the ICP-agnostic explanation of
> every step below.

## Source pipeline for this build

| | This pipeline |
|---|---|
| Source CSV | `{{SOURCE_CSV}}` |
| Main table | `{{MAIN_TABLE}}` |
| Seller table | `{{SELLER_TABLE}}` |
| Buyer table | `{{BUYER_TABLE}}` |
| Seller contacts table | `{{SELLER_CONTACTS_TABLE}}` |
| Buyer contacts table | `{{BUYER_CONTACTS_TABLE}}` |

Reference/lookup tables (`fit_lookup`, `seller_sublevels`,
`seller_contact_titles`, `buyer_contact_titles`) are workbook-scoped and
shared across every entity-type pipeline built into the same ICP — if a
workbook already has them (imported for another entity type), reuse them
rather than re-importing.

## Workspace and folder structure

Workspace: **{{WORKSPACE_URL}}** — "**{{WORKSPACE_NAME}}**".

- **`{{EVENTS_FOLDER}}` folder** — holds one workbook per competitor event.
  Each workbook is named **exactly** after its source folder in the
  scrapers directory — one workbook per scraper folder, same name, no
  reformatting.
- **The blocklist ledger** — a central Supabase registry (see
  `blocklist_ledger/`), **not** a Clay table. Every workbook reaches it
  through one HTTP API column (step 5 below) that does lookup-and-insert in
  a single call and returns `Is New`. That is what stops the same company
  being worked twice across two different events, and it does it *inline*,
  before the paid columns run — not downstream at CRM-sync time.

  > Earlier builds sent rows to a shared Clay "Block List" table one level up
  > from `{{EVENTS_FOLDER}}` instead. That table was a passive sink: it
  > recorded companies but gated nothing, so every event still paid to
  > enrich repeats. The ledger replaces it. If you are looking at an older
  > workbook that still has a `Send to Blocklist` column, that is the legacy
  > mechanism.

## Role and hard constraint

You have tool access to this Clay workspace. Build the tables, columns,
Claygent (AI) column configurations, formulas, and actions described below.

**The only things you must not trigger are steps that call a paid external
provider per row**: any Claygent/AI column run (`Official Domain`,
`{{CLASSIFIER_NAME}}`, `Sub Level` if used), the `Enrich Company` action
(Companies, People, Jobs), and any Find People / contact-finding run.
Configure those fully — exact prompt, exact output fields, preview on the
handful of test rows Clay requires to save the config — but do not run them
against the full row set.

**Everything else should actually be created and executed**: creating tables
and workbooks, importing CSVs, formula columns, and Send Table Data actions
between tables — none of which call a paid provider.

**The one exception you run anyway** is the blocklist ledger check (step 5).
It costs 1 Clay Action per row and 0 data credits, and it must run before
the filter it feeds — running it is what makes everything after it cheaper.

**One thing to verify rather than assume**: if a `Website`-resolution step
is built as an "AI Formula" column, check whether that column type in this
workspace calls a paid model per row or compiles to a plain deterministic
formula. If it's paid, move it into the configure-only list above. Report
which it is.

At the end, give a summary of everything you built, everything you actually
ran (should only be free operations), and every judgment call you made.

## Step 0: Populate the events folder — do this before anything else

**Do this first, before touching any of the pipeline steps below.** For
every event folder in the scrapers directory that has `{{SOURCE_CSV}}`,
import it into a workbook in `{{EVENTS_FOLDER}}` named exactly after that
folder (reusing the workbook if it already exists from another entity
type's build). Do this for every matching event folder before starting any
of the table/Claygent/formula work in the sections below. Raw columns:

`{{RAW_COLUMNS}}`

Confirm the folder had this pipeline's tables absent before you start (or
that you're additively building alongside an existing entity type's
tables in the same workbook). Run the import for real — it's a data-import
step, not an enrichment. Report exactly which workbook(s) got created or
extended, and confirm each workbook name matches its scraper folder name
exactly.

## This runs once per competitor event

The pipeline gets built inside each event's workbook independently. Each
workbook holds five tables for this pipeline: `{{MAIN_TABLE}}`,
`{{SELLER_TABLE}}`, `{{BUYER_TABLE}}`, `{{SELLER_CONTACTS_TABLE}}`,
`{{BUYER_CONTACTS_TABLE}}`. There is no shared master table pooling
companies across events — the Supabase blocklist ledger is the only
cross-event element, and it is reached per row over HTTP, not by a Clay
table relationship.

**Build the reusable pieces once, not per event.** Three things belong in
Clay's Claygent builder / reusable-formula layer rather than being rebuilt
inside each event's workbook:
- `Official Domain` (Claygent)
- `{{CLASSIFIER_NAME}}` (Claygent — the Side/Classification agent, prompt below)
- `Sub Level` (Claygent, Sellers-only, conditional, if this ICP defines sub-levels)

Every event's workbook should deploy these same saved agents rather than
having the prompts rewritten each time.

**Cross-event repeats are handled, not tolerated:** a company exhibiting at
several competitor shows appears in each event's workbook, but the ledger
check at step 5 recognizes it the second time and the `Is New` filter keeps
it out of every paid column downstream. (This used to be a known tradeoff —
the old Clay Blocklist table only recorded repeats, so each event still paid
to enrich them.)

## Known data-integrity flags

{{KNOWN_ISSUES_BLOCK}}

## Pipeline architecture (inside one event's workbook)

### 1. {{ENTITY_TYPE}} (source table: `{{MAIN_TABLE}}`)

Base columns from import: `{{RAW_COLUMNS}}`.

Then, in order:

1. **`Normalize a Domain`** (formula) — reuse the existing formula already
   established in this workspace's pattern; don't recreate it under a
   different name if it already exists. Source: `{{WEBSITE_SOURCE_FIELD}}`.
2. **`Normalize Company Name`** (formula) — same, reuse the existing one.
3. **`Official Domain`** (Claygent) — {{OFFICIAL_DOMAIN_GATING_TEXT}}
   researches and returns the verified primary corporate domain (prompt:
   research via web search, verify via the candidate site's logo/about/footer,
   exclude social and directory domains, prefer the global root domain,
   return the bare registrable domain only). **Configure only — do not run.**
4. **`Company Domain`** (formula) — coalesce `Normalize a Domain`, else
   `Official Domain`'s result (unless it returned `unknown`).
5. **Blocklist ledger check → `Is New`** (HTTP API action, template
   "{{BLOCKLIST_LEDGER_TEMPLATE}}", connected account
   `{{BLOCKLIST_API_ACCOUNT}}`) — identifier: `Company Domain`. One call per
   row does lookup-and-insert atomically against the central Supabase ledger
   (`blocklist_ledger/`) and returns `Is New`. **Create and run it now.**
   Costs **1 Clay Action per row, 0 data credits** — it is not free, but it is
   the cheapest column here and it is what stops the expensive ones below
   from running on companies you have already worked.

   **Then filter the view to `Is New` is checked**, and build everything
   after this point against the filtered view. Click the `Is New` column
   header and set it to *is checked*.

   **Verify the filter before continuing** — the filtered row count must look
   like your new companies, not the whole table. If the filter is not really
   applied, every paid column below runs against the full company set and
   burns Actions at the full-table rate. This is the highest-leverage check
   in the build.

   *Why here and not earlier:* the ledger keys on the normalized domain, so
   it has to sit after `Company Domain`. Rows whose domain only exists
   because `Official Domain` resolved it have already paid for that one
   Claygent call — unavoidable, since a company with no domain cannot be
   deduped at all — but `Enrich Company` and the classifier below, which are
   the expensive steps, are all gated by the filter.
6. **`Normalized Country`** (formula) — map raw `{{COUNTRY_SOURCE_FIELD}}`
   values to canonical tiering-sheet names:
   {{COUNTRY_NORMALIZATION_BLOCK}}
   Anything unmapped passes through unchanged. Create and run — it's a plain
   formula.
7. **`Enrich Company`** (Clay Action — Companies, People, Jobs) —
   identifier: company domain. Columns: {{ENRICH_COMPANY_FIELDS}}. **This
   calls a paid data provider — configure only, do not run.**
8. **`Resolved Description`** (formula) — prefer the `description` field
   returned by `Enrich Company`; if blank, fall back to the raw CSV
   `{{DESCRIPTION_SOURCE_FIELD}}` column. This is what feeds the classifier's
   Description input below.
9. **`Side` / `Classification`** (Claygent — "{{CLASSIFIER_NAME}}") —
   outputs `Side` (Buyer/Seller) and `Classification` (one label from the
   closed taxonomy below). **Configure only — do not run.**

**`{{CLASSIFIER_NAME}}` prompt (build once in Claygent builder):**

```
#CONTEXT#
You are the "{{CLASSIFIER_NAME}}" — an AI classification agent for {{ICP_NAME}}. Your sole job is to read incoming company data and classify each company into exactly one Buyer OR Seller category from a closed list. Use the provided Description and Primary Offerings to determine the company's core commercial activity. If the Description is blank or insufficient, visit the Company Domain to understand what the company does before classifying. Do not include any names, companies, confidence scores, or reasoning in the output.

#OBJECTIVE#
Classify each company into exactly one category from either Buyer or Seller using the closed lists below, based on the company's core commercial activity inferred from Description, Primary Offerings, and, if needed, information found by visiting the Company Domain.

{{CLASSIFIER_TAXONOMY_BLOCK}}

#INSTRUCTIONS#
1. Primary determination (Step A):
   - Read Description first to identify core activity. If insufficient or blank, visit the Company Domain to determine what the company does.
   - Decide: does the company manufacture, distribute, or provide services related to this vertical's products/software/testing/facilities (Seller), or does it operate/consume/deliver primarily for its own purposes (Buyer)?
2. Tie-breakers (Step B):
   - If the company type seems ambiguous/diversified, prefer signals from Company Domain and Description. If titles are present in Description, use these hints: procurement/operations/QA-QC/R&D/facility roles imply Buyer; sales/business development/channel/account roles imply Seller.
3. Special-case rules (Step C):
{{CLASSIFIER_TIE_BREAK_RULES_BLOCK}}
4. Catchall rule (Step D):
   - Only use the catchall Buyer category when, after Steps A-C, the company and any title clues still do not resolve to a clear vertical.
5. Output format:
   - Output only two lines exactly:
     - Side: Buyer or Seller
     - Classification: exact short label from the closed list above (no parenthetical description)
   - Do not include any additional commentary or fields.
6. Self-verification before output:
   - Confirm the classification is one of the exact labels in the closed list (matched by its short form, ignoring any parenthetical).
   - Confirm only one side (Buyer or Seller) is selected.
   - Confirm Step A was checked before inferring from titles.
   - If using Catchall, confirm Steps B-D genuinely failed to resolve a vertical.
7. If {{Description}} and {{Primary Offerings}} are empty but {{Company Domain}} has a value then use {{Company Domain}} for classification tasks.

#EXAMPLES#
Example 1:
Input summary: A company that manufactures core equipment for this vertical.
Output:
Side: Seller
Classification: <a Seller label from the taxonomy above>

Example 2:
Input summary: An organization that consumes/operates using this vertical's products for its own purposes.
Output:
Side: Buyer
Classification: <a Buyer label from the taxonomy above>

#INPUTS#
Company Name:
Company Domain:
Description:
Primary Offerings:
```

Feed `Company Domain` from `Official Domain`/`Company Domain` (whichever
resolved), `Description` from `Resolved Description` (item 7), and
`Primary Offerings` from the `Enrich Company` output field of the same name
(if this entity type's `enrich_company_fields` includes it).

10. **`Fit`** (formula/lookup — create and run) — reference table keyed on
    `Classification`, shared across entity types within this ICP:

{{FIT_LOOKUP_TABLE}}

11. **`Country Fit`** (formula — create and run), branch on `Side`:

{{COUNTRY_FIT_BLOCK}}

12. **`Composite Tier`** (formula — create and run), branch on `Side`:
    - **Seller** ({{COMPOSITE_TIER_SELLER_MODE}}):
      {{COMPOSITE_TIER_SELLER_BLOCK}}
    - **Buyer** (both pipelines): leave null here — the real composite needs
      JT Fit from an actual contact, which isn't known until Find People
      runs in the Buyer contacts table.

13. **Split** — create and activate two Send Table Data actions:
    {{FANOUT_GATING_TEXT}}
    `Side = Seller` → `{{SELLER_TABLE}}`; `Side = Buyer` → `{{BUYER_TABLE}}`.
    Free, run now. They won't move real rows until a human has actually run
    the `{{CLASSIFIER_NAME}}` classifier — expected, not a problem.

---

### 2. Sellers (`{{SELLER_TABLE}}`)

Receives seller rows with `Classification`, `Fit`, `Country Fit`, and
`Composite Tier` already attached from the source table — no need to
recompute any of those here. `Classification` already holds the specific
category, so there's no separate "Top Level Category" step needed. Add only:

- **`Sub Level`** (Claygent, only run if `Classification` is filled, if this
  ICP defines sub-levels) — restrict the option list to the sub-levels
  under whichever category `Classification` already holds. **Configure
  only — do not run.**

**Route to contacts:** filter `Composite Tier` ∈ {1, 2} → Send table data →
`{{SELLER_CONTACTS_TABLE}}`. Create and run — Tier 3 is inbound-only and
should not be sent.

---

### 3. Buyers (`{{BUYER_TABLE}}`)

Receives buyer rows with `Classification`, `Fit`, `Country Fit` already
attached. `Classification` already holds the specific segment — no separate
"BuySide Segment" step needed. `Composite Tier` is still null, pending
contact-level JT Fit.

**Route to contacts:** Send table data → `{{BUYER_CONTACTS_TABLE}}` (all
rows — volume here is naturally low). Create and run.

---

### 4. Seller contacts (`{{SELLER_CONTACTS_TABLE}}`)

One row per person. Configure (don't run) a Find People search per company,
filtered to this job title list:

{{SELLER_CONTACT_TITLES_BLOCK}}

Add a formula column `Sector Keyword Match` (create and run) requiring the
contact's title or company context to also contain one of this ICP's sector
keywords. Only treat a Tier-2 contact as valid if this is true — cross-check
the exact wording against the seller contact titles reference table.

---

### 5. Buyer contacts (`{{BUYER_CONTACTS_TABLE}}`)

One row per person. Configure a Find People search per buyer company (don't
run), filtered to the BuySide job title list matching that company's
`Classification` — pull the specific title list per segment from the
buyer contact titles reference table.

Add `JT Fit` (A = sector-specific keyword, B = senior title only,
C = generic commercial), then a `Composite Tier` formula (create and run)
using the company's `Fit` × the contact's `JT Fit` × the company's
`Country Fit`, per this ICP's full Buyer composite-tier matrix.

---

## What to report back

1. What Step 0 produced: which workbook(s) got created from `{{SOURCE_CSV}}`,
   confirming each name matches its scraper folder exactly.
2. Every table and column you created, with types, and which were actually
   run (should only be formulas, Send Table Data, and the step-5 ledger
   check) versus which you configured but left unrun (Claygents, Enrich
   Company, Find People).
3. Whether `Normalize a Domain` / `Normalize Company Name` already existed
   elsewhere in the workspace and were reused, or had to be created fresh.
4. Whether the `Website`-resolution column is paid or free in this workspace.
5. Confirmation of the `Resolved Description` precedence (Enrich Company →
   raw CSV fallback) — flag if you think a different precedence makes more
   sense.
6. The `Is New` reading from step 5 — how many rows came back new versus
   already-in-ledger — and confirmation that the view filter is applied, with
   the filtered row count, before any paid column was configured.
7. The saved Claygents (`Official Domain`, `{{CLASSIFIER_NAME}}`, `Sub
   Level` if used) and confirmation they're reusable across event workbooks.
8. Whether each Send Table Data action fires automatically once a human
   later runs the upstream enrichment, or needs a manual re-trigger.
9. The exact `Fit` and `Composite Tier` reference tables you built, for
   spot-checking.
10. Every judgment call, including at minimum any items in the "Known
    data-integrity flags" section above, how you handled countries not
    named in any tier list, and any ambiguity in the ICP's reference sheets.
