# Runbook — end-to-end, per event workbook

How to take a competitor event from raw scraped rows to finished
`Sellers - People` / `Buyers - People` contact tables, and how to re-run the
whole thing for a **different entity type** (Exhibitors → Sponsors → …) or
**ICP** (Labs → …). Read `docs/PIPELINE_ARCHITECTURE.md` first for what each
column/table *is*; this doc is the operational sequence and the variables.

Everything that varies is config, in two layers (same split the build spec uses):

| Layer | File | What varies |
|---|---|---|
| Entity type | `config/entity-types/<entity>.yaml` | source table, people/seller/buyer table names, Clay template names, raw columns, trim cut column |
| ICP / vertical | `config/icps/<icp>/icp.yaml` + `config/icps/<icp>/people_search.yaml` | classifier taxonomy, fit/country tiers, and the Find-People job-title/segment/location lists |

The per-event automation (`automation/cleanup/`) reads both via
`automation/cleanup/pipeline_config.py`. Select the target with **`--entity` /
`--icp`** on the rollouts (default `exhibitors` / `labs`), or the
`CLAY_PIPELINE_ENTITY` / `CLAY_PIPELINE_ICP` env vars for the scripts that don't
take flags. Inspect what a selection resolves to:

```bash
python automation/cleanup/pipeline_config.py --entity sponsors --icp labs
```

---

## 0. Prerequisites (one time)

```bash
pip install -r requirements.txt        # pyyaml + playwright deps
python -m playwright install chromium
python automation/clay_sync/clay_login.py   # headed once → saves .clay_session.json (gitignored)
```

- Real data (scraped `*_normalized.csv`, ICP lookup CSVs) is **gitignored** — see
  `docs/SENSITIVE_DATA.md`. Point the build at yours with
  `CLAY_PIPELINE_SCRAPERS_ROOT` / `CLAY_PIPELINE_ICP_LOOKUPS_DIR` (defaults in
  `automation/build_automation/build_workbook.py`).
- The `clay` CLI (read-only verification, dedupe checks) runs **only under WSL
  Ubuntu** here — invoke the cached linux binary with a clean PATH; see
  `docs/KNOWN_ISSUES.md` and the pattern in `verify_cleanup.py`.

## 1. Import — scraped CSV → Clay workbook

One workbook per event, named exactly after its scraper source folder, in the
`Competitive Events` folder. The importer is
`automation/clay_sync/clay_sync.py` (Playwright CSV → workbook table). This
produces the entity's source table: `config/entity-types/<entity>.yaml: tables.main`
(`Exhibitors_normalized` / `Sponsors_normalized`).

## 2. Build the column pipeline (once per entity → a reusable template)

The column pipeline (Normalize Domain/Name → Official Domain → Enrich Company →
classifier → Side/Classification/Fit/Country Fit/Composite Tier → Split) is
identical in shape across entities/ICPs; only the config-driven pieces differ.
Generate a paste-ready build spec and execute it in Clay once, saving the result
as the two reusable templates the passes below apply:

```bash
python template/render_build_prompt.py --entity exhibitors --icp labs
```

Save the built columns as **`<entity>.templates.all_columns`** and the
lookup+send step as **`<entity>.templates.lookup`** (names live in the entity
config). `automation/build_automation/` is the reference automation that built
the live Labs Exhibitors pipeline — it is Interphex-shaped, not config-driven;
treat it as the how-it-was-done reference (see `automation/README.md`).

> **Entity-specific, not auto-generated:** the *template contents* and the lookup
> field-mapping encode the entity's raw columns, so a new entity needs its own
> templates built (per its `raw_columns`) — the code only targets the names.

## 3. Per-event passes (`automation/cleanup/`), in order

Each pass is `<verb>.py` (one workbook) + `<verb>_rollout.py` (fleet: `--only`,
`--limit`, most support `--dry-run` / sharding). Each writes its own resumable
state + `*_logs/`. Scope comes from a targets file = JSON list of workbook ids
that have this entity's source table (build with `build_workbook_manifest.py`).

```bash
cd automation/cleanup

# a. trim back to the through-import base (deletes columns right of trim_cut_column)
python trim_columns_rollout.py --limit 5

# b. apply + run the all-columns template
python apply_all_columns_rollout.py --limit 5

# c. apply + run the lookup & send-table-data template
python apply_lookup_rollout.py --limit 5

# d. set the two view filters: Side = Seller AND "Send table data has results"
python apply_view_filters_rollout.py --limit 5

# e. Sellers - People: the 3 "Find people" seller builds → one per-event table
python seller_people_rollout.py --limit 5

# f. Buyers - People: Side→Buyer, per-Classification segment searches → one table
python buyer_people_rollout.py --limit 5

# g. apply the Google Sheet lookup+send template to the seller/buyer people tables
python apply_gsheet_lookup_rollout.py --limit 5

# h. email pass: work-email waterfall + Validate Email on the people tables
python ps_people_email_rollout.py --audit      # plan only; --limit N to run a batch
```

Steps **e** and **f** are the config-driven Find-People passes:
- source table + output table name ← `config/entity-types/<entity>.yaml: tables`
  (`seller_people` / `buyer_people`).
- job titles, segments, seniority, Location list ←
  `config/icps/<icp>/people_search.yaml` (`seller.builds`, `buyer.segments`,
  `*.location_countries`; seniority is the 11 levels in `people_search_fill.js`).
- run files namespaced per `<entity>_<icp>` slug:
  `{people,buyer}_targets_<slug>.json`, `{people,buyer}_state_<slug>.json`,
  `{people,buyer}_logs/run_<slug>*.log`.

Both passes are **resumable and salvage-safe**: a run cut off mid-event resumes
from `segments_done`; an unrenamed people table from a killed run is salvaged
rather than duplicated; `Sellers - People` and `Buyers - People` are never
clobbered by each other.

Step **g** (`apply_gsheet_lookup_rollout.py` / `apply_gsheet_lookup.py`) applies a
single, entity-agnostic Clay template — **"Google Sheet - Lookup & Send
Data"** — to whichever of the two people tables (`seller_people`/
`buyer_people`) exist for the workbook. It's entity-agnostic because it
targets the People tables' own columns directly (its two Configure fields,
"Full Name" and "LinkedIn Profile", auto-map by exact column-name match —
every entity's People table has the same shape, since both come out of the
same Find-People builds), unlike steps b/c which need per-entity template
content and field mapping. Idempotent via the "Lookup in Audiences" column
the template adds (NOT "Send table data", despite the template's name
following the same convention as step c). Scope = the union of steps e and
f's target files (a workbook only needs step g once it has one of those two
tables); state `gsheet_state_<slug>.json`. This template is built once, in
Clay, and reused for every entity/ICP — no per-entity variant to create,
unlike the templates steps b/c apply.

### Step h — the email pass (work-email waterfall + Validate Email)

Turns a People table into an emailable one. Two template variants exist; they
differ only in their Configure panel, so pick by which template the workspace
has saved:

| People table | Template | Pass |
|---|---|---|
| `Sellers - People` / `Buyers - People` | "Waterfall and Validate Email" | `people_email.py` + `people_email_rollout.py` |
| Product & Services `People` | "Enrich and Validate Email" | `apply_people_enrich_email.py` + `ps_people_email_rollout.py` |

**Fields and values** — what to bind in the Configure panel. Clay prefills only
where an input's name exactly matches a column on the table; everything else is
mapped by walking the picker tree. For the "Enrich and Validate Email" panel:

| Panel label | Bind to | Arrives |
|---|---|---|
| `Full Name` | `Full Name` | prefilled |
| `Domain` | `Company Table Data` › `Domain` | map |
| `records` | `People - Supabase` › `Lookup in Audiences` › `records` | map |
| `Is New` | `People - Supabase` › `Is New` | map |
| `Name` | `Company Table Data` › `Name` | map |
| `LinkedIn Profile` | `LinkedIn Profile` | prefilled |

Two traps here. `Domain`/`Name` have no top-level column on these tables — they
only exist under `Company Table Data`, and Clay's picker search does **not**
descend into an unexpanded column's schema, so filtering by "Name" reports "No
properties"; expand the group instead. And `Is New` exists under *both*
`Company Table Data` (company ledger) and `People - Supabase` (people ledger) —
bind the person-level one on a People table, or the waterfall ends up gated on
the wrong population.

**Run conditions** — on the `Validate Email` column the template creates:
`Email Address` = `WORK EMAIL`, *Only run if* = `!!{{WORK EMAIL}}`,
Auto-update OFF. **Always retype the condition**; never accept the inherited
one. The template carries it over as a raw field id from the table it was built
on (seen as `!!{{f_…}}` on all 11 P&S tables), which resolves to nothing in the
target table, leaving the gate dead.

**Save, then run after** — three commits in this order:

1. apply the template with **Save and don't run** (template panel = split button)
2. bind Validate Email and retype the condition, **plain Save** (Edit-column
   panel commits on plain Save — the split button is a template-panel thing)
3. only then trigger: select all rows → **Run N rows**

Step 3 must come last: running at step 1 charges every row while Validate Email
is still gated on a nonexistent field. Two guards sit in front of it — a
write-ahead run log (survives a killed worker) and a `WORK EMAIL` fill count
(does not, since results land *after* the trigger) — checked in that order.
Default is configure-only; running is opt-in.

Expect roughly a 1-in-10 miss on the final "Run N rows" click. It fails safe
(configured, unrun, logged retryable, left pending), so re-run the rollout at
the end to sweep stragglers; finished tables are a no-op.

**Verify Clay UI automation results with the CLI, not the page's own status
text**: a "Save and run N rows in this view" / "N% of table completed"
message on the page is not proof the action landed — it can be a stale
leftover from an unrelated column's auto-run. `apply_gsheet_lookup.py` learned
this the hard way (a run reported success while the template was never
actually added) and now polls for the real signature column before ever
returning "ok"; the same principle applies to any future UI-automation pass —
confirm with `clay tables columns list <tableId>` (WSL CLI) when in doubt.

### Parallelism (buyer pass)

`buyer_people_rollout.py --shards N --shard i` partitions events disjointly and writes
per-shard state (`buyer_state_<slug>_w{i}.json`), merged by `merge_shards.py
--entity <e>`. Use only when RAM allows a second headless browser — on a
low-RAM machine run a single worker (the builds already use memory-lean
Chromium flags in `browser_session.py`).

## 4. Verify

```bash
# post-run inventory / untouched checks (WSL)
python automation/cleanup/verify_cleanup.py --wait 120
```

Row-level dedupe check for the people tables (WSL, read-only via the `clay`
CLI): list each `*- People` table's rows and flag repeated (name + company).
A small number of repeats is expected by design — a company whose
`Classification` spans two segments is returned by both segment searches.

## 5. Run it for a different entity (e.g. Sponsors)

```bash
# 0. config already exists: config/entity-types/sponsors.yaml (tables, templates,
#    raw_columns) — edit if the source schema changed.
# 1. import Sponsors_normalized.csv per event (step 1)
# 2. build the Sponsors templates once (step 2) — different raw columns than Exhibitors
# 3. targets: build sponsors targets = workbook ids that have Sponsors_normalized
#    -> people_targets_sponsors_labs.json / buyer_targets_sponsors_labs.json
# 4. run every pass with --entity sponsors:
cd automation/cleanup
CLAY_PIPELINE_ENTITY=sponsors python trim_columns_rollout.py --limit 5     # env for passes a–d
CLAY_PIPELINE_ENTITY=sponsors python apply_all_columns_rollout.py --limit 5
CLAY_PIPELINE_ENTITY=sponsors python apply_lookup_rollout.py --limit 5
CLAY_PIPELINE_ENTITY=sponsors python apply_view_filters_rollout.py --limit 5
python seller_people_rollout.py --entity sponsors --limit 5                    # flag for e–f
python buyer_people_rollout.py  --entity sponsors --limit 5
python apply_gsheet_lookup_rollout.py --entity sponsors --limit 5              # flag for g
```

Nothing collides with the live Exhibitors run: Sponsors uses distinct table
names (`Sponsors - Sellers - People`, …), distinct templates, and distinct
`*_sponsors_labs.*` run files. The ICP config (`labs`) — taxonomy, tiers,
job-title/segment lists — is shared across entities and unchanged. Step g's
template ("Google Sheet - Lookup & Send Data") is the one exception to
"distinct templates per entity" — it's the same saved template reused as-is
for every entity, since it targets the people tables' own (entity-independent)
columns rather than the entity's raw columns.

For a new **ICP**, copy `config/icps/_template/` → `config/icps/<icp>/`, fill in
`icp.yaml` + `people_search.yaml`, and pass `--icp <icp>`.

## 6. Operational notes

- **Environment kills long background tasks** at a variable cadence — always run
  via the resumable rollouts; relaunch on kill and it picks up from state. Verify
  a worker is really dead with PowerShell `Get-Process python` (Windows `tasklist`
  is unreliable here) before relaunching, to avoid overlapping workers.
- **Watch progress** by tailing the pass's `*_logs/run_<slug>*.log`; watch for a
  *silent hang* (log mtime stops with no kill notification), not just crashes.
- **Clay has no delete API** — table/row deletion is Playwright UI automation
  (`clay_ui.delete_table`); the `clay` CLI is read-only. See
  `docs/KNOWN_ISSUES.md`.
