# Competitive Events cleanup

Reduce every event workbook in `Labs [2026 - Qasim] → Competitive Events` down to
just its normalized source table(s), deleting all pipeline-byproduct tables
(`Exhibitors`, `Sellers`, `Buyers`, `Contacts – *`, `fit_lookup`,
`seller_sublevels`, `*_contact_titles`, `Sponsors*`, `Speakers`, stray
`New table`, …).

**Keep only:** `Exhibitors_normalized`, `Sponsors_normalized`.

Clay has no delete API, so this drives the web UI with Playwright. It is
manifest-driven and fail-closed: the two normalized names can never be deleted
(`clay_ui.KEEP_NEVER_DELETE`), a workbook is skipped unless its keep-table is
actually present, and navigation is by workbook **id** (immune to duplicate
names / listing virtualization).

## Files

- `build_cleanup_manifest.py` — read the live inventory via the `clay` CLI; write
  `cleanup_manifest.json` (the delete allowlist) + `inventory_snapshot.json`
  (pre-run state of everything, for the untouched-check). **Run in WSL/Linux.**
- `delete_byproduct_tables.py` — `clean_workbook()`; also a `--only` single-workbook runner.
- `delete_byproduct_tables_rollout.py` — fleet driver: `--dry-run`, `--shard i --shards N`,
  `--only`, `--headed`. Runs the Playwright browser. **Run on Windows** (same env
  as `clay_login.py`).
- `verify_cleanup.py` — read-only post-run check via the CLI. **Run in WSL/Linux.**

## Run order

```bash
# 0) one-time (Windows): save a Clay session
python ../clay_sync/clay_login.py

# 1) build the manifest (WSL)
python build_cleanup_manifest.py

# 2) dry run — review exactly what would be deleted (Windows)
python delete_byproduct_tables_rollout.py --dry-run

# 3) validate the delete interaction on ONE workbook, watched (Windows)
python delete_byproduct_tables_rollout.py --only "Interphex" --headed

# 4) real run, 4 workers — one per terminal (Windows)
python delete_byproduct_tables_rollout.py --shards 4 --shard 0
python delete_byproduct_tables_rollout.py --shards 4 --shard 1
python delete_byproduct_tables_rollout.py --shards 4 --shard 2
python delete_byproduct_tables_rollout.py --shards 4 --shard 3

# 5) verify (WSL)
python verify_cleanup.py --wait 120
```

State per shard (`cleanup_state_w{n}.json`) makes each worker resumable — rerun a
shard to pick up where it stopped.

## Later passes (same folder, same session)

Beyond the original table cleanup, this folder accumulated a set of
manifest-driven, resumable passes over each event's `Exhibitors_normalized`.
All are `<verb>.py` (one workbook) + `<verb>_rollout.py` (fleet, `--only` / `--limit`),
keyed off `cols_manifest.json` (build it with `build_workbook_manifest.py`). Every
pass writes its own `*_state_*.json` (skips already-done) and `*_logs/`.

- `trim_columns*` — delete columns to the right of a cut column.
- `apply_all_columns*` / `run_all_columns*` — apply + run the "Exhibitors - All Columns - v1" template.
- `apply_lookup*` — apply + run "Exhibitors - Lookup & Send Table Data - v1".
- `apply_view_filters_*` — set the two view filters (`Side = Seller` AND
  `Send table data has results`); supports `--ids-file` + `--state-suffix` for
  parallel shards.
- `seller_people_searches.py` + `seller_people_rollout.py` — the "Find people at these companies"
  seller builds into a per-event **seller people** table (build 1 → new table +
  rename; later builds → append). Filter-fill logic is vendored in
  `people_search_fill.js` (override with `CLAY_PEOPLE_FILL_JS`).
- `buyer_people_searches.py` + `buyer_people_rollout.py` — the buyer counterpart: flip the Side
  view filter Seller→Buyer, then per ICP `Classification` segment run an
  exact-match then a contains-match Find-People search into one per-event
  **buyer people** table. `--shards N`/`--shard i` for parallel workers +
  `merge_shards.py` to fold shard state back. Resumable per segment; salvages an
  unrenamed table from a killed run instead of duplicating.

- `apply_cpj_people*` — apply the "Qasim - CPJ - Labs - People" template to the
  `Sellers - People` / `Buyers - People` tables of every Competitive Events
  workbook, mapping its 13 inputs from under each table's `Company Table Data`
  lookup. Costs nothing per row (the created columns are `basic` references onto
  a lookup that has already run), so it applies *and* runs. Scope comes from
  `cpj_people_targets.json` (`build_cpj_targets.py`, run in WSL) rather than from
  `pipeline_config`, because the folder + the two table names are the scope.
  Two UI traps are encoded in `apply_cpj_people.py`'s docstring and worth reading
  before touching it: the panel is taller than the default viewport, and the
  template's input labels are the same strings as the values inserted into them,
  so fields are located by position, never by label text.
  `recon_cpj_people_panel.py` dumps the Configure panel; `recon_cpj_reference.py`
  reads the bindings back off a finished table.

- `export_event_tables*` / `audit_view_filters.py` / `read_view_filters.py` /
  `clear_filters_and_export.py` / `unhide_timestamps_and_export.py` — download
  finished tables to `<export_root>/[<Folder>/]<Workbook>/<Table>.csv`. Clay's
  CSV export is the **Default view**, so filtered rows and hidden columns are
  dropped with nothing in the file to say so: audit first, clear filters, unhide
  `Created At`/`Updated At`, then export once. Every download is asserted against
  Clay's own suggested filename before it is written, because a tab click that
  has not landed exports the *previous* table under the new name. Full step,
  flags and the UI traps: `docs/RUNBOOK.md` step (i) plus each module docstring.
  Other Clay folders reuse the same passes via `--workbooks <manifest>` and a
  `{event}` placeholder in `--tables`.

### EVCharge — Products & Services company searches

A different shape from everything above: these passes do not run over an
existing per-event table, they **create** the workbooks. One workbook + one
table per **Top level** row of the EVCharge ICP's `Products_Services` sheet,
inside `10. EVCharge [2026 - Qasim] → Products & Services`.

- `ps_query_config.py` — reads `config/icps/<icp>/products_services.yaml` and
  renders one `select from companies` query per category (countries → industry →
  sub-level keywords / semantic products → the EV anchor on horizontal
  categories). It validates `industry` and `country_name` against the enum lists
  in `docs/CLAY_QUERY_SKILL.md`, and enforces Clay's cap of **3
  `products_and_services` values per query** — both are failures Clay swallows
  silently, returning a narrower result set rather than an error.
  `--list` / `--print "<category>"` / `--print-all`, no browser.
- `evcharge_ps_scope.py` — read-only folder listing (`{workbook_id: name}` +
  subfolders). The re-entry guard's source of truth; fails closed on a listing
  that will not hydrate rather than reporting an empty folder.
- `build_ps_companies.py` (+ `_rollout.py`) — create the workbook, rename it,
  Find companies → *Build with filters instead* → the `</>` Query mode toggle,
  paste the query, read back the preview count and the Result limits. With
  `--run`, save the results and rename the table to **`Companies`** — the
  workbook carries the category name, the table is `Companies` in every
  workbook (as in the Labs Product & Services folder), so the later people pass
  addresses one table name across the fleet.

- `ps_people_config.py` + `build_ps_people.py` (+ `_rollout.py`) — the second
  half: *Find people at these companies* on each `Companies` table into a
  `People` table. Persona comes from the ICP's `seller_people.yaml` (the
  `Seller JTs` sheet: exact C-titles → `job_title in (...)`, keywords →
  `job_title contains (...)`, seniority Mid-level and above, the same 22
  countries read from `products_services.yaml` so the two can never drift).
- `verify_ps_companies.py` — read-only confirmation **in WSL**, against the Clay
  CLI rather than the browser or the rollout's own log: a `Companies` table (and
  optionally `People`) per workbook, no duplicate or unexpected table (a second
  one means an interrupted run saved twice, and those rows are already paid
  for), and the real row counts.

Three things about the people pass that are not guessable from the companies one:

- The scoped action is **Tools → Import → "Find people at these companies"**,
  not the bottom-bar `Add` (whose "Find people" is an unscoped search). Ctrl+E
  toggles Tools; its tab strip sits at y≈194, which is why `people_search.py`'s
  older 200–235 band no longer matches.
- The panel's **Filters section starts collapsed**, and its header is a bare
  `<div>` with no role and no chevron hit area — expand it by clicking the
  *parent element of the "Filters" text node*, then the `</>` toggle appears
  beside the funnel.
- Clay **prefills the company scope** as
  `clay.filter_to_companies(@table("t_…:gv_…:f_…"))`. That clause is read off
  the prefill at run time and the persona query is composed around it; it is
  never stored (live ids, `docs/SENSITIVE_DATA.md`). If it cannot be read the
  pass stops — pasting over it turns a search of one workbook's companies into a
  search of all of Clay.

The per-company cap lives in the **query** (`limit 10 by clay_company_id`), not
in the panel's "People per company" box, which is left empty like the Total.

#### The blocklist ledger step

`apply_ps_supabase.py` (+ `_rollout.py`) applies "Companies - Supabase" to each
`Companies` table in the exact order the user specified — **keep it separate
from the Labs `apply_companies_supabase.py`**, which sets the API account inside
the template panel rather than afterwards:

1. Tools (Ctrl+E) → **Templates** → the template's Configure panel,
2. map **Name** → the table's `Name` column (Domain auto-maps),
3. **Save and don't run**,
4. **Edit column** → API account → `Qasim - Labs` (the template ships pointing
   at "Data Cloud and Clay", which this workspace cannot use — the panel says
   "You don't have access to this account"), verified by REOPENING the panel,
5. `--run` → **"Run N empty or out-of-date rows"**.

Step 5 is the one `/run/i` control this repo clicks on purpose: the ledger check
is 1 Action/row and is what keeps the expensive columns off already-worked
companies. Nothing else in the pass may click a run control.

Four traps, all of which cost a failed run before being fixed:

- **No fixed panel coordinates.** Dismissing the orange credit banner shifts the
  page up ~28px, so every hardcoded y broke mid-fleet. Fields are found relative
  to their own labels (`field_below`).
- **The Tools panel remembers its last tab.** The first table opens on "For you"
  (Templates *card* present); the next opens straight into Templates, where a
  card click lands on the panel's own heading and silently no-ops. The card is
  told apart from the title by requiring y > 250.
- **The Templates search box is a `contenteditable` `role="textbox"` div**, not
  an `<input>` — every input-based locator returns nothing.
- **The run label changes form with table size**: "Run 387 empty or out-of-date
  rows" but "Run 1.2K empty or out-of-date rows" past ~1,000. A
  digits-and-commas pattern matches the small tables and misses every large one;
  `RUN_EMPTY_RE` allows a decimal point and a K/M suffix. It failed CLOSED onto
  the never-click-"Force run all" rule, which sits next to it in the same menu.

`verify_ps_ledger.py` (WSL) is the completion check: for every workbook it walks
EVERY page of the `Companies` table and reports `Is New` resolved vs total plus
the new/already-seen split. Do not use `check_column_fill.py` for this — it is a
run-guard that stops at a 2,000-row sample, which on an 8,631-row table reads a
quarter-finished run as nearly done.

**The default is configure-only and that is deliberate**: creating the workbook
and building the search is free, materialising the results into a table is what
spends credits, so it sits behind an explicit `--run`. Re-entry asks Clay, not
the state file — the folder listing supplies the workbook, `list_table_tabs`
supplies its tables, and a workbook that already has a table is refused.

Clay's **Result limits → Total** input is left **empty** by this pass, on
purpose. Empty means Clay's real default of 50K rows per table; the greyed
`100` next to it is an illustrative placeholder, not an applied cap (per the
user, 2026-08-07). The pass reports the input's value and placeholder each run
so the distinction stays visible — typing `100` in would truncate every
category to 100 companies.

Two flags exist for **iterating on a query after its table is already built**:

- `--measure` re-prices an edited query against a built workbook — configure,
  read the preview count, stop. It deliberately skips the re-entry guard (the
  existing table is the case being measured), so argparse refuses to combine it
  with `--run`, and it never overwrites the recorded `status`.
- `--replace` deletes the workbook's `Companies` table and builds a new one, on
  top of `clay_ui.delete_table`. Delete happens **before** the build, never
  after: two tables named `Companies` are indistinguishable to the tab menu, so
  cleaning up afterwards can remove the new one. Requires `--run`; rows are
  recoverable from Clay's Trash.

Both depend on one non-obvious fact: a workbook that already has a table opens
into the grid, **not** the empty-workbook source picker, so there is no `Find
companies` button — the picker is reached through the bottom-bar `Add`. That
button must be located by its position in the tab strip; several other controls
are named `Add` and a role lookup silently matches a hidden one.

```bash
python ps_query_config.py --print "Finance, Investment & Funding"   # no browser
python build_ps_companies_rollout.py --list                          # no browser
python build_ps_companies.py "Energy, Power & Grid Solutions"        # configure, spend nothing
python build_ps_companies_rollout.py --measure --only "<category>"   # re-price an edit
python build_ps_companies_rollout.py --run                           # SPENDS CREDITS
python build_ps_companies_rollout.py --run --replace --only "<cat>"  # DELETES then rebuilds
wsl -d Ubuntu -- python3 .../verify_ps_companies.py                  # confirm via the CLI
```

`config/icps/<icp>/products_services.yaml` carries no live ids and **is**
committed; `ps_companies_state_*.json`, `evcharge_ps_workbooks.json`,
`ps_companies_logs/` and `ps_companies_shots/` are git-ignored.

### Entity/ICP parameterization

`seller_people_rollout.py` and `buyer_people_rollout.py` take **`--entity`** (default
`exhibitors`) and **`--icp`** (default `labs`); the earlier passes
(`trim_columns` / `apply_all_columns` / `apply_lookup` / `apply_view_filters`) read the same via
`CLAY_PIPELINE_ENTITY` / `CLAY_PIPELINE_ICP`. `pipeline_config.py` resolves:

- source table + output table names ← `config/entity-types/<entity>.yaml: tables`
- Clay template names ← `config/entity-types/<entity>.yaml: templates`
- job titles / segments / Location lists ← `config/icps/<icp>/people_search.yaml`

Run files are namespaced per `<entity>_<icp>` slug, so an Exhibitors run and a
Sponsors run never share state/targets/logs. See `docs/RUNBOOK.md` for the full
end-to-end sequence and how to run for Sponsors.

All generated `*.json` (manifests, state, targets, batch/shard lists) and
`*_logs/` are git-ignored — they carry live workspace/workbook ids. Regenerate
manifests from the live workspace with the `build_*.py` scripts. The Find-People
config (`config/icps/<icp>/people_search.yaml`) has **no** live ids and **is**
committed.

## Highways Competitive Events — the Workflows route

**Full playbook: `docs/WORKFLOWS_ROUTE.md`** — when to use this route, the traps
index, the output contract, and what changes for Products & Services, Buyside P&S
and Other Sources.

These do not use Playwright at all. The CE classification and people passes run
as Clay **Workflows** driven from the WSL CLI, because a Workflow can express
the per-row DAG (gate, classify, map, search) without touching the DOM.

| Script | What it does |
|---|---|
| `ce_classification_prompt.py` | The Registrar prompt + its interpolation markers |
| `ce_classification_rollout.py` | Fleet driver, one bulk run per CE table (67) |
| `audit_ce_classification.py` | Fail-loud read-back with a per-table `paged == rowCount` check |
| `export_ce_classification.py` | Per-event CSV; re-applies the Domain and `Is New` gates |
| `combine_ce_tables.py` | The single combined deliverable CSV |
| `ce_people_mapping.py` | `(Side, Classification)` -> persona + countries, or `Unmapped` |
| `build_ce_people_feed.py` | The searchable-companies feed the people pass runs on |
| `extend_v2_people.py` | Adds the people nodes to the classification workflow |
| `build_ce_people_workflow.py` | Builds the people-only workflow (bulk entry point) |
| `ce_people_rollout.py` | Fleet driver for the people search, 250-row chunks |
| `export_ce_people.py` | One row per person, with its company context |
| `export_ps_people.py` | The same for P&S / Buyside P&S, read over the CLI |
| `people_export_rules.py` | The four rules both exporters apply, in one place |
| `other_sources_classify.py` | Classifies `ALL_SOURCES_DEDUPED` with the CE workflow |
| `split_ce_event_tables.py` | Splits the deliverables into one CSV per Clay table |
| `import_ce_delivery_tables.py` | Builds the handoff folder: a workbook per event |

Two workflows, on purpose. The people nodes live in BOTH: `extend_v2_people.py`
put them on the classification workflow (where they can be tested against a
freshly classified row), and `build_ce_people_workflow.py` builds a second
workflow whose trigger takes an already-classified row. A bulk run always enters
at the trigger, so without the second one the 4,547-company search would have
replayed — and re-paid for — the classification agent. Both mapping nodes are
generated from the same YAML by `extend_v2_people.build_icp()`, and each script
has an `--update` flag that regenerates it, so they cannot drift.

Gotchas the scripts encode, all of which fail silently if you get them wrong:

- `workflows create` produces an EMPTY workflow — create the trigger yourself.
- Edges are **not** implied by an `inputSchema` reference. Wire each one with
  `nodes update {"incomingEdges": [...]}`; branch edges carry `ruleId`.
- `outputSchema` is written FLAT (`{name: {type, description}}`) but read back
  expanded into full JSON Schema — writing back what you read is rejected.
- A workflow is not runnable as a routine until `clay routines create workflow
  <id>` registers it; before that `runs start` answers "Routine not found".

## Environment notes

- The `clay` CLI runs only on Linux/WSL here (the Windows/Git-Bash build rejects
  the OS). `build_cleanup_manifest.py` / `verify_cleanup.py` resolve the cached
  plugin binary automatically; override with `CLAY_BIN` if needed.
- If the workspace id differs from the default, set `CLAY_WORKSPACE_ID` for the
  Playwright side (used to build `/workspaces/<id>/workbooks/<wid>` URLs).
