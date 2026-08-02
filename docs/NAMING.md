# Terminology Alignment Proposal — Phase 2

Goal: someone who knows Clay can read this repo and immediately understand it. Clay's real
domain nouns are used where they genuinely apply; generic infrastructure keeps generic names.

## Clay terms verified against official docs (2026-08-02)

| Clay's current term | Replaces / not | Source |
|---|---|---|
| **Only run if** (a "conditional run" set in a column's Run settings) | the repo's "gate" | [Conditional runs](https://university.clay.com/docs/conditional-runs) |
| **Auto-update** (Run settings toggle; re-runs enrichment on new rows) | the repo's "auto-run" | [Table management settings](https://university.clay.com/docs/table-management-settings) |
| **Send Table Data** (table Actions menu; officially replaced "Write to Other Table") | the repo's "route"/"split"/"send" | [Write to Other Table](https://university.clay.com/docs/write-to-table-integration-overview), community: "'Write to Other Table' has been replaced by 'Send Table Data'" |
| **Lookup Rows** (pull matching data from another table) | — | [Lookup Rows](https://university.clay.com/docs/lookup-rows) |
| **Enrichment / credits** | — | [Enrichments](https://university.clay.com/docs/enrichments), [Ways to save Clay credits](https://university.clay.com/docs/clay-credit-conservation) |
| workbook, table, view, column, source, waterfall, Claygent, template, filter | already used correctly in this repo | in-product (selectors in `clay_ui.py` were codegen-captured from the live UI) |

## Hard rules for the apply step

1. **Never rename anything persisted or external.** State/log/manifest *file names*, JSON keys
   inside state files (`"ran"`, `"triggering"`, every `"status"` literal), env var names, and
   any string matching a **live Clay object name** ("Labs - Block List - Companies",
   "Exhibitors - All Columns - v1", menu labels like "Save and run … in this view") stay
   byte-identical. Two rollouts have in-flight resume state (buyers, speakers email) — renaming
   any of this risks silent restarts that re-spend credits.
2. **Superseded files are not renamed** (`apply_findworkemail_event.py` + rollout,
   `apply_tpl_event.py` + rollout, `apply_companies_lookup_event.py`, `clay_login_auto.py`,
   `folder_scope.py`, `list_other_sources_workbooks.py`) — they are Phase 3 retirement
   candidates; renaming them first would be wasted churn.
3. **Renames only** — no function moves between files, no logic changes. Where a *name* can't be
   fixed without a refactor (e.g. `formula_lib.py` also contains grid geometry), the file gets
   the best honest name now and the split is noted for Phase 3.
4. `git mv` for every file rename; every reference (imports, aliases, docs, comments) updated;
   post-apply grep must show zero stale references.
5. Directory names (`clay_sync/`, `build_automation/`, `cleanup/`) stay — they are referenced by
   external memory/docs/logs and the churn outweighs the gain.

---

## A. File renames (git mv)

### automation/build_automation/

| Old | New | Rationale |
|---|---|---|
| `common.py` | `browser_session.py` | It is the CDP/saved-session browser bootstrap (`clay_page()`), not "common" anything. Generic infra → generic-but-descriptive name. |
| `build_lib.py` | `column_config.py` | Everything in it configures columns via Clay's column config panel: enrichment picker, prompts, Auto-update off, Only-run-if, save flows, mappings, Send-panel. |
| `formula_lib.py` | `formula_columns.py` | Authors Clay formula columns. (Also holds grid-header geometry — split into `grid.py` is a Phase 3 item, not done here.) |
| `build_event.py` | `build_workbook.py` | It builds one event's Clay **workbook** (the 15-step column pipeline). "Event" stays reserved for the trade show itself. |
| `blocklist_route.py` | `blocklist_send.py` | It configures a **Send Table Data** action and picks its destination table. "Route" is not a Clay concept. |
| `wait_out.py` | `worker_wait.py` | Waits for `worker.py`'s output sentinel; name says whose output. |
| `rollout.py`, `worker.py` | *(keep)* | "Rollout" is an established repo coinage (docs, runbook, state files); `worker.py` is generic queue infra. Both get a defining comment instead. |

### automation/clay_sync/

| Old | New | Rationale |
|---|---|---|
| `clay_state.py` | `csv_push_state.py` | It is the sha256+mtime ledger of which scraper CSVs were already pushed; "clay_state" says nothing. |
| `clay_sync.py`, `clay_ui.py`, `clay_login*.py`, `clay_audit.py`, `import_evcharge.py`, `clay_session_from_cdp.py` | *(keep)* | Accurate enough; `clay_sync.py`'s accidental role as config home (`SESSION_PATH`/`UA` imported by 4 modules) is a Phase 3 extraction, not a rename. |

### automation/cleanup/ — the `*_event.py` convention

`_event` currently means "operates on ONE workbook" (vs `_rollout` = fleet). Since event ==
workbook here and Clay has no "event", the suffix is dropped: the bare verb is the
single-workbook operation, `*_rollout.py` remains the fleet driver.

| Old | New | Rationale |
|---|---|---|
| `apply_lookup_event.py` | `apply_lookup.py` | Pattern: bare verb = one workbook. Applies the "…Lookup & Send Table Data - v1" template. |
| `apply_gsheet_event.py` / `apply_gsheet_rollout.py` / `apply_gsheet_rollout_other_sources.py` | `apply_gsheet_lookup.py` / `apply_gsheet_lookup_rollout.py` / `apply_gsheet_lookup_rollout_other_sources.py` | The template is "Google Sheet - **Lookup** & Send Data" — a lookup enrichment, not Google API code. |
| `apply_filters_event.py` | `apply_view_filters.py` | They are Clay **view filters** — the one place "filters" was already right, made explicit. |
| `apply_v1_event.py` / `apply_v1_rollout.py` | `apply_all_columns.py` / `apply_all_columns_rollout.py` | "v1" is the literal suffix of the Clay template "… - All Columns - v1", not a script version. |
| `run_v1_event.py` / `run_v1_rollout.py` | `run_all_columns.py` / `run_all_columns_rollout.py` | Same. |
| `trim_cols_event.py` / `trim_cols_rollout.py` | `trim_columns.py` / `trim_columns_rollout.py` | Clay says column. |
| `cleanup_event.py` / `cleanup_rollout.py` | `delete_byproduct_tables.py` / `delete_byproduct_tables_rollout.py` | Says exactly what it deletes (manifest-driven pipeline-byproduct tables), which "cleanup" hides. |
| `add_workemail_waterfall_event.py` | `add_workemail_waterfall.py` | 11 modules import it as the shared column-panel library; extracting that library is Phase 3. |
| `apply_people_waterfall_event.py` | `apply_people_waterfall.py` | |
| `add_validate_email_event.py` / `configure_validate_email_event.py` | `add_validate_email.py` / `configure_validate_email.py` | |
| `apply_email_template_event.py` | `apply_email_template.py` | |
| `apply_companies_supabase_event.py` | `apply_companies_supabase.py` | |
| `fix_supabase_api_event.py` | `fix_supabase_api.py` | |
| `fix_workemail_gate.py` | `fix_workemail_run_condition.py` | Clay's term is "Only run if" / conditional run — not "gate". |
| `people_email_event.py` | `people_email.py` | |
| `people_search_event.py` | `people_search.py` | |
| `apply_evcharge_tpl.py` / `apply_evcharge_tpl_rollout.py` | `apply_evcharge_template.py` / `apply_evcharge_template_rollout.py` | "tpl" and "template" both in use for the same Clay concept; one spelling. |
| `people_builds.py` / `people_rollout.py` | `seller_people_searches.py` / `seller_people_rollout.py` | A "build" is a saved **Find People search** config; these are the Seller side (they fill "Sellers - People"). |
| `buyer_builds.py` / `buyer_rollout.py` | `buyer_people_searches.py` / `buyer_people_rollout.py` | Same, Buyer side. State files (`buyer_state_*.json` etc.) are NOT renamed (rule 1). |
| `build_cols_manifest.py` | `build_workbook_manifest.py` | Its output lists **workbooks**, not columns (`{"workbooks": [...]}`) — the current name points at the wrong noun. Output *filename* `cols_manifest_<slug>.json` is kept (rule 1); a header comment documents the mismatch. |
| `people_fill_lib.js` | `people_search_fill.js` | It fills the Find People search panel. |

## B. Symbol renames (all references updated; persisted strings untouched)

| Old | New | Rationale |
|---|---|---|
| `auto_run_off` / "auto-run" in comments | `auto_update_off` / "Auto-update" | Clay's Run-settings toggle is named Auto-update (cited above). |
| `_set_gate_condition`, `repair_gate`, `gate_ok`, `GATE_COLUMN`, `expected_condition` callers' "gate" wording | `set_run_condition`, `repair_run_condition`, `run_condition_ok`, `RUN_IF_COLUMN`, comments say "Only run if" | Clay's term. Persisted status strings (`gate_missing`, …) stay. |
| `GateError` (build_automation) | `VerificationError` | It marks *verification* failures during a build, not run conditions — two unrelated "gates" currently share the word. |
| `V1_SIGNATURE`, `SIGNATURE`, `SIG`, `SIG_CANDIDATES`, `_sig_present` | `ALL_COLUMNS_MARKER`, `MARKER_COLUMN`, `MARKER`, `MARKER_CANDIDATES`, `_marker_present` | A "signature" reads cryptographic; it is a **marker column** whose presence proves a template was applied. |
| `ensure_target` (blocklist) | `ensure_destination` | Clay's Send panel picks a destination table. |
| `step_routes`, `_split` (build_workbook) | `step_sends`, `_send_split` | They configure **Send Table Data** actions. |
| `run_event` / `EventBuilder` (build_automation) | `build_workbook` / `WorkbookBuilder` | Event → workbook, consistent with the file rename. |
| `MANUAL_FOLDERS`, `MANUAL` (skip lists) | `NEEDS_HUMAN_FOLDERS`, `NEEDS_HUMAN` | Disambiguates from `MANUAL_FORMULAS`. |
| `MANUAL_FORMULAS`, `build_formula_manual` | `HANDWRITTEN_FORMULAS`, `build_formula_handwritten` | The other "manual": a formula authored from text rather than AI-generated. |
| `shot()` (common) / `snap()` (build_event) | both `screenshot()` | One name for one helper. |
| `discover()` ×3 | `discover_csv_folders` (clay_sync), `discover_import_folders` (import_evcharge), `discover_workbook_folders` (rollout) | Three different return shapes deserve three names. |
| `seller_builds` / `b["build"]` locals (config loader + people scripts) | `seller_searches` / `search` | Find-People **search**, matching the file rename. In-memory only; persisted state keys untouched. |
| Single-letter module aliases (`A B E P R S T V W BU APP RUN`) | ≥3-char mnemonics or full module names (e.g. `import column_config as colcfg`, `import add_workemail_waterfall as panel`) | `B.foo(...)` is unreadable; `A` = "whichever module this rollout drives" is worse. |

## C. Optional (ask before applying — higher blast radius)

| Old | New | Rationale / risk |
|---|---|---|
| Config key `gating:` (`official_domain_skip_if_domain_known`, `fanout_gated_on_side`) | `run_conditions:` (`official_domain_skip_if_domain_known`, `people_fanout_only_if_side`) | Clay vocabulary. Readers updated in same commit (`render_build_prompt.py`). Tracked YAML only — but any unmerged local copies of entity YAMLs would break. |
| Config key `seller.builds:` (people_search.yaml) | `seller.searches:` | Matches B-row. Reader is `pipeline_config.py`. Same caveat. |
| Config keys `field_sources.{website,country,description}_source_field` | `field_sources.{website,country,description}` | Triple redundancy. Readers: `render_build_prompt.py`, `apply_all_columns.py`. |
| `docs/PEOPLE_STEP.md` | `docs/PEOPLE_PASSES.md` | It documents RUNBOOK passes e/f, not a pipeline "step". |

## D. Docs & comments (terminology edits, no renames)

- **Glossary additions** (`docs/GTM_METHODOLOGY.md` + RUNBOOK intro): *event* = trade show, one
  Clay **workbook** per event; *pass* = one fleet-wide automation run (`<verb>.py` = one
  workbook, `<verb>_rollout.py` = fleet); *step* = one column/action inside a table (reserved
  for PIPELINE_ARCHITECTURE's 14 steps); *marker column*; *rollout*.
- **Blocklist disambiguation** everywhere in prose: "**Blocklist table**" (the Clay sink fed by
  Send Table Data) vs "**blocklist ledger**" (the Supabase gate queried per row). Live Clay
  object names ("Labs - Block List - Companies") are never edited.
- **`blocklist_ledger/README.md` corrected**: identity is `domain_key`/`linkedin_key` with
  normalization (per `01_schema.sql`/`02_functions.sql`) — the "matching is exact, no
  normalization" sections describe a superseded design. `04_backfill.sql` gets a header warning
  that it predates the normalized schema and `load_csv.js` is the working loader (actual fix is
  Phase 3).
- Comments saying "gate" → "run condition (Only run if)", "auto-run" → "Auto-update",
  "route/split" → "Send Table Data", "signature column" → "marker column".

## E. Explicitly left alone

- State/log/manifest filenames and every persisted JSON key or status string (rule 1).
- Env var names (`CLAY_*`, `CLAUDE_JOB_DIR`, `PGHOST`/`PGPASSWORD`) — external contracts.
- Live Clay object names in strings and selectors.
- `rollout` as a coinage; `worker.py`/`queue/` mechanism names; generic infra (retries, CDP).
- Directory names (`clay_sync/`, `build_automation/`, `cleanup/`, `blocklist_ledger/`).
- Superseded files (rule 2) — Phase 3 decides retire-vs-rename.
- `slug`, `tag`, `--only`, `w0/w1/w2`, `.premerge` — state-file adjacent; documented in the
  glossary instead of renamed.
