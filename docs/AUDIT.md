# Repository Audit — Phase 1

Date: 2026-08-02 · Branch: `refactor/clay-terminology` · Scope: full repo at commit `a2e89b6`
Method: full read of `automation/build_automation` (2,545 LOC) and `automation/clay_sync` (2,082 LOC), all 59 scripts in `automation/cleanup` (12,082 LOC), `blocklist_ledger/`, `template/`, `config/`, and `docs/`. High-severity claims were re-verified by hand against source before inclusion.

**Tests: there are none.** No test files, no test framework in `requirements.txt`. (Baseline noted per engagement rules; tests will be added for anything changed in Phase 3.)

---

## 0. Repo map

### Structure and generations

The repo automates Clay.com through **Playwright UI automation** (Clay has no delete API and bot-blocks Playwright-launched logins; sessions come from a real Chrome via CDP). Two generations coexist:

| Area | Status | Role |
|---|---|---|
| `automation/clay_sync/` | active | Playwright primitives (`clay_ui.py`, 733 LOC is the core) + CSV→workbook importers (`clay_sync.py`, `import_evcharge.py`) + 4 login/session scripts |
| `automation/build_automation/` | **reference-only, Interphex-hardcoded** (`automation/README.md:1-11`) | the original 15-step per-workbook pipeline builder (`build_event.py` 971 LOC, `build_lib.py` 807 LOC) |
| `automation/cleanup/` | active, config-driven | ~14 fleet "passes", each a `*_event.py` (one workbook) + `*_rollout.py` (fleet loop) pair, parameterized by `pipeline_config.py` (`--entity/--icp`) |
| `blocklist_ledger/` | active | Supabase Postgres "seen-before" ledger called from Clay as an HTTP API enrichment (credit gate) |
| `template/` + `config/` | active | YAML-driven rendering of a paste-ready Clay build spec (`render_build_prompt.py`) |

### Dependency graph (no cycles)

```
humanize, clay_state (leaves)
  └─ clay_ui ── clay_sync ── common ── formula_lib ── build_lib ──┬─ blocklist_route
                                                                   └─ build_event ── rollout
cleanup/*: pipeline_config (config) + add_workemail_waterfall_event (de-facto UI lib,
           imported by 11 modules) ← each *_event.py ← its *_rollout.py
```

- `common.py:17` mutates `sys.path` at import time to bridge into `../clay_sync`, and thereby **shadows the PyPI `humanize` package** process-wide.
- `clay_sync.py` is imported by 4 modules purely for the `SESSION_PATH`/`UA` constants; `SESSION_PATH` is *also* independently redefined in `clay_login.py:22`, `clay_audit.py:23`, `import_evcharge.py:42`.

### External services

Everything in `clay_sync`/`build_automation` is Playwright UI automation against `app.clay.com` — **no HTTP client anywhere there**. Elsewhere: Chrome DevTools Protocol (`127.0.0.1:9222`, `common.py:45`), the WSL-only `clay` CLI (subprocess from 8 cleanup scripts), Supabase Postgres via `pg`/`COPY` (`blocklist_ledger/load_csv.js`), Clay HTTP-API enrichment columns (configured in-UI), and Google OAuth implicitly during login.

### Config & secrets surface

- `config/local.yaml` (gitignored, verified) ← `render_build_prompt.py:35-43` and a *second, hand-rolled* parser at `import_evcharge.py:48-67` that truncates any value containing `#`.
- `config/entity-types/*.yaml` + `config/icps/<icp>/*` ← `pipeline_config.py` and `render_build_prompt.py`. Several keys are **dead** (§3.4).
- ~25 env vars, all `CLAY_*` except `CLAUDE_JOB_DIR`, `PGHOST`/`PGPASSWORD` (JS), `MSYS_NO_PATHCONV`. No dotenv.
- `requirements.txt` = `pyyaml` only; **playwright is an undeclared dependency**, and `load_csv.js` needs `pg` + `pg-copy-streams` with **no `package.json`**.

### Secrets verdict

- **No credentials are committed**, current or historical. `automation/clay_sync/.clay_session.json` on disk holds live Clay + Google session cookies but is gitignored (`.gitignore:15`) and was never committed (`git rev-list --all --objects` clean).
- **However, live workspace identifiers are committed in tracked source**, contradicting `docs/SENSITIVE_DATA.md:15`:
  - `automation/build_automation/blocklist_route.py:16-19` — fully-resolved live URL (workspace `448891`, workbook `wb_0thngorwWnTjpruUykJ`, table `t_0thngozNZTVZ4cYPz3i`, view `gv_0thngozjQPZ8XmH6KuZ`) + folder names.
  - `automation/clay_sync/clay_ui.py:344` — `WORKSPACE_ID = os.environ.get("CLAY_WORKSPACE_ID", "448891")`; `clay_ui.py:27-28` hardcodes folder names.
  - `automation/clay_sync/import_evcharge.py:70` — personal absolute path default.
- `blocklist_ledger/README.md:181-190` documents two credentials leaked *in a chat transcript* (DB password, `sb_secret_...` key) with revocation instructions — not in the repo, but rotation is unverifiable from here.

---

## 1. Correctness risks (ranked)

### CRITICAL — can spend credits redundantly or corrupt fleet state

**C1. `run_v1_event.py:31-33` — the only credit-spending trigger with no already-run guard.** Precondition is merely "signature *column* exists"; it then selects all rows → Actions → `Run N rows`. Nothing checks cell fill. The sole protection is the tag-scoped state file (`run_v1_rollout.py:69`); if `v1run_state_<slug>_all.json` is lost/renamed, the entire "All Columns - v1" enrichment set re-runs across every applied workbook. Contrast `people_email_event.py:191-211`, which does check.

**C2. `apply_evcharge_tpl_rollout.py:117-119` — run triggered unconditionally after a skipped apply.** `RUN.run_v1(...)` fires even when `apply_template` short-circuited on "already applied"; events that failed *after* their run was triggered stay `pending` (`:85`, `:122`) and are re-run (Domain + Enrich Company + Lookup re-spend) on the next batch. Also: its `load_state` (`:44-48`) is the only copy with no `try/except`, and the `PRE_DONE` seed protecting two hand-run events applies only when the state file is absent.

**C3. `people_email_rollout.py:147-157` — the double-charge guard is disabled in exactly the sharded mode it was written for.** `people_email_event.py:44-55` documents that parallel workers need per-worker run logs via `CLAY_RUNS_FILE`; the rollout shards the *state* file (`:154-155`) but never sets `CLAY_RUNS_FILE`, so concurrent workers read-modify-write one `people_email_runs.json` (non-atomic, `people_email_event.py:73-108`). A lost `"triggering"` record re-fires a credit-spending waterfall. `log_path` (`:157`) is unsharded too.

**C4. `check_table_rows.py:34-35` + `people_email_rollout.py:130-136` — CLI failure permanently drops workbooks.** After 4 failed tries the script prints `0` (exit 1); the rollout reads stdout `0` → files the event under "empty table(s)" → removed from every future batch. The `except` at `:78-79` catches only subprocess exceptions, not the 0-on-stdout path.

**C5. `worker.py:59` — arbitrary code execution against a logged-in Clay session.** `exec(compile(...))` of any file dropped in `./queue`, no allowlist; plus `time.sleep(0.3)  # let the write finish` (`:53`) instead of atomic rename, so a partially-flushed command file compiles truncated. No queue lock either (`:44-46`) — two workers can pick the same `cmd_N`.

**C6. Table-wide "% completed" used as the done-signal.** `add_workemail_waterfall_event.py:790-792` documents that the table-wide `_pct` "previously made a 4%-run column look finished" — yet `_pct == 100` remains the "already applied" oracle in `apply_gsheet_event.py:184`, `apply_findlinkedin_event.py:155`, `apply_findworkemail_event.py:648`. A stale 100% from an unrelated column permanently marks the workbook done (`apply_gsheet_event.py:239-241` records the exact false positive without changing the signal).

**C7. `rollout.py:98-106` — shard state merged once at startup; `pending` is a snapshot.** Two workers can build the same event; `CLEANUP_NOTES.md` records it happening ("CMEF: worker collision built columns concurrently → duplicate 'Sector Keyword Match'/'Composite Tier' columns").

**C8. Corrupt/truncated state files silently reset all progress.** No `os.replace`/temp-file rename anywhere; a kill mid-`json.dump` truncates state (`rollout.py:58`, `buyer_rollout.py:127-134`, `clay_state.py:33-35`, `import_evcharge.py:135-137`), and the ~19 copies of `load()` swallow `JSONDecodeError` → `{}` → every event reverts to pending → full rebuild.

### HIGH — silent wrong results

**C9. `clay_ui.py:206-209` — `existing_tables()` swallows all exceptions.** Timeout reads as "table absent" → duplicate CSV import (call sites `clay_sync.py:177,347`, `import_evcharge.py:299`). The same selector also matches identically-named sidebar/Overview elements (documented at `clay_ui.py:418-421`: "deleted 7/8 fine but missed 'Buyers'") → false positives skip table creation.

**C10. Fail-closed guards latch "done" on transient errors.** `speakers_template_rollout.py:58-64`: a WSL timeout makes `already_built()` return `True` → state records `"ok"/"already_built"` for an *unbuilt* table, permanently. Same shape at `:89-91` and `check_column_fill.py:38`.

**C11. Find-People failures downgraded to done.** `build_event.py:925-936` catches and WARNs; `rollout.py:123` records `status: "done"`. `CLEANUP_NOTES.md` confirms "~15 events with <2 saved Find People searches". Similarly `build_event.py:404-406` keeps a possibly-errored formula column with no flag.

**C12. `blocklist_ledger/04_backfill.sql` fails against the current schema.** Inserts omit NOT-NULL `domain_key` (`:122-134` vs `01_schema.sql:40`) and target constraints `company_ledger_uniq`/`people_ledger_uniq` (`:127,:134`) that are actually named `company_ledger_key_uniq`/`people_ledger_key_uniq` (`01_schema.sql:45,71`) — verified. Yet `README.md:63` still lists it as the working loader. `load_csv.js` is the real one.

**C13. `blocklist_ledger/README.md` documents the opposite of the implemented design.** `:6-9` and `:20-22` state "Matching is exact. No normalization" — `02_functions.sql:35-53` normalizes aggressively and `03_verify.sql:86-110` asserts it. First-time readers will misunderstand the system's central property.

**C14. `render_build_prompt.py` defects (verified by execution).** (a) `--entity exhibitors_evcharge` crashes (`KeyError: 'gating'`, bare `[]` at `:130-131,138-141,150,157`) — 1 of 4 entity configs unrenderable. (b) The unresolved-placeholder check (`:180`) runs *after* substitution over the already-replaced list — structurally cannot catch a new `{{PLACEHOLDER}}` missing from `replacements`. (c) The RUNBOOK's own no-`--out` command (`docs/RUNBOOK.md:61`) dies with `UnicodeEncodeError` on Windows cp1252 (template emits `→ ∈ ×`); `--out` opens UTF-8 (`:199`), stdout doesn't. (d) Silent `REPLACE_ME` output: falls back to `local.yaml.example` with only a stderr warning, and the real `local.yaml` still has `REPLACE_ME` in the 4 keys this script consumes.

**C15. Unstable fleet ordering / resume markers.** `apply_findworkemail_rollout.py:75-76`, `apply_findlinkedin_rollout.py`, `speakers_email_rollout.py:84-85` derive shard partitions and `--after` cursors from **dict insertion order** of a regenerated JSON file (only `speakers_template_rollout.py:135` sorts). And `speakers_email_rollout.py:75` bakes `--after "BioTrinity"` in as a *default* — a bare run silently skips everything up to BioTrinity, and raises if that name leaves the scope file.

**C16. `is_logged_in()` false positive on the documented bot-block page.** `clay_ui.py:40-50` judges by URL substring after swallowing `PWTimeout`; Clay's logged-out marketing page on `app.clay.com` (documented at `common.py:39-41`) passes. Non-timeout errors propagate and leak the browser (`clay_audit.py:52`).

### MEDIUM

- **State-tag fragmentation:** `--only` runs record to `*_state_only.json`, bare runs read `*_state_all.json` → reprocessing (`apply_v1_rollout.py:30`, `apply_tpl_rollout.py:35`, `cleanup_rollout.py:49-60`). Three passes fixed this three incompatible ways (`apply_lookup_rollout.py:66-69`, `people_rollout.py:82-88`, `buyer_rollout.py:88-95`).
- **Cross-module global mutation:** `apply_evcharge_tpl_rollout.py:111-112` (`RUN.TABLE = APP.TABLE`), `people_email_rollout.py:104`, `audit_people_email.py:50-54`.
- **`wait_out.py:6-8`** — unchecked argv, unguarded `int()`, and path traversal (`os.path.join` of user input); `import_evcharge.py:326-328` screenshot names keep `..`/`/`.
- **`assert` as verification gates** — `formula_lib.py:70,78,210,284`; disabled under `python -O`.
- **Missing timeouts/guards:** `blocklist_route.py:71` `goto` with no timeout/retry (every other nav retries 6×); `:30-31` unguarded `int()` on scraped text; `build_lib.py:373-374,420,437` `bounding_box()` may be `None`.
- **State bleed:** `build_event.py:808-820` `self._contacts_ct` not reset if `formula()` raises → wrong formula template for subsequent columns.
- **Rename-by-elimination:** `build_event.py:242-252` renames the rightmost unknown header on a WARN; a mis-rename silently breaks name-referencing formulas (e.g. `:170`).
- **Resource leaks:** `clay_sync.py:169` `sys.exit(1)` inside the loop skips `browser.close()` (`:192`); unclosed file handles `rollout.py:44,54,58`, `worker.py:36,57`, `wait_out.py:15`; `clay_login_auto.py:78`/`clay_login_watch.py:99` close not in `finally`.
- **`clay_state.py:65`** bare `entry["sha256"]` where neighbors use `.get()`.
- **102 `except Exception: pass` sites** in `cleanup/` alone; most dangerous are those that discard evidence: `apply_lookup_event.py:88-90` (mislabels failure as "template not found"), `run_v1_event.py:51-52` / `apply_lookup_event.py:210-211` (detached-node error on the *correct* menu item → aborts with the wrong reason).

### LOW

- `worker.py:41` infinite loop, no deadline; `wait_out.py` has no `__main__` guard.
- Unbounded scroll loops (exit only on 3 no-growth rounds): `clay_ui.py:146-152,176-182`, `import_evcharge.py:177-184`.
- Mixed CRLF/LF (3 files CRLF, no `.gitattributes`); tool-mangled continuation whitespace `build_lib.py:585`, `build_event.py:373`; 2-space-inside-16-space indent `speakers_template_rollout.py:186-196`.
- Inconsistent data-wait budgets for one operation: 300 s (`clay_ui.py:319`) vs 180 s (`build_lib.py:634`) vs 120 s (`build_lib.py:61`).

---

## 2. Efficiency (Clay ops are credit-metered)

### Credit-relevant (flagged per engagement rules)

**E1. 308 duplicated reference tables in Clay.** `build_event.py:610-618` imports the same 4 lookup CSVs into *every* event workbook (77 × 4) instead of one shared workbook + lookup.

**E2. Credit re-spend paths** — C1, C2, C3, C6 above are also the top efficiency items: each converts a state/oracle flake into a paid re-run. Additionally a false-negative `header_exists` (`build_lib.py:126-139`) rebuilds enrichment columns whose duplicates later get run manually (`CLEANUP_NOTES.md` lists the strays), and `build_event.py:639-640`'s rename-recovery can re-create the one auto-run send over all rows.

**E3. Superseded 14.5-credits/row code still runnable.** `apply_findworkemail_event.py` + rollout (939 LOC), explicitly obsoleted by `add_workemail_waterfall_event.py:4-8`, retain live `__main__` entry points with no deprecation guard (`apply_findworkemail_rollout.py:9` itself quantifies 30,000+ credits for a fleet run).

**E4. No credit accounting exists anywhere.** The only spend record is `r["ran"]` holding a UI menu label ("Save and run 254 rows…"); nothing reads, estimates, or budgets credits despite auto-run-off being the design's whole motivation.

### Redundant navigation / N+1

**E5. `clay_sync.py:170-176, 339-346`** — per folder: `open_target_location` → full virtualized-scroll `list_workbooks` → `open_target_location` *again* → `open_workbook` (scrolls again) = 3 folder hydrations + 2 full scroll passes per folder, when the first listing already returned ids and `open_workbook_by_id` (`clay_ui.py:347`) exists (adopted by `import_evcharge.py:291-298`, never backported).

**E6. `import_evcharge.py:289-290`** — folder listing re-scraped *inside* the per-folder loop: O(N²), noted by the code itself at `:294-296`. `build_event.py:263-271` + `:948` similarly opens the same workbook 3 times per event.

**E7. `blocklist_route.ensure_target` (`blocklist_route.py:59-93`)** — walks Tables 1…10, each probe opening two popovers, re-executed for **every event** in a 77-workbook fleet although the answer changes ~every 20 sends. No per-run cache.

**E8. Workbook re-opened once per table.** `apply_gsheet_event.py:173` / `people_email_event.py:148` / `apply_findworkemail_event.py:592,644` re-navigate per table; hoisting into the rollout loop halves navigations for two-table passes.

### Chatty UI / fixed sleeps

**E9. 346 `wait_for_timeout` calls totalling ~852 s** of unconditional wall-clock per full pass (top: `people_search_event.py` 103 s, `apply_findworkemail_event.py` 51 s). Three identical bare `wait_for_timeout(16000)`s (`apply_lookup_event.py:222`, `apply_gsheet_event.py:231`, `apply_findworkemail_event.py:739`) sit right next to a correct condition-wait (`apply_findworkemail_event.py:744-749`). ~20 min avoidable idle per fleet pass.

**E10. Full-DOM scans in poll loops.** `document.querySelectorAll('*')` ×56 across 18 files; `_LABEL_Y` (`apply_lookup_event.py:46-55`) forces layout on every element inside a 12-iteration poll; `_pct` serializes `document.body.innerText` every 12 s for up to 30–60 min (`_wait_for_full_completion` ×4 copies). `build_event.py:383-396` polls up to 3 min per formula column ×12 columns. `set_mapping` (`build_lib.py:674-712`) ≈ 80 evaluate round-trips ≥30 s per send column.

**E11. Redundant CLI passes.** `speakers_template_rollout.py:53,83` shells to WSL twice per workbook; 4 audit/check scripts independently re-list the same columns; `check_column_fill.py:50-60` runs up to 20 sequential paginated CLI calls to answer a boolean — and silently caps at 2,000 rows, so row-count ordering data is wrong for larger tables.

**E12. Sequential fleets.** `rollout.py:112` and all cleanup rollouts are strictly sequential; the only parallelism is uncoordinated `--shard/--shards` (which produced the CMEF collision). Independent workbooks could be safely concurrent with a real lease/queue. Three separate full-folder-scan scripts (`folder_scope.py`, `folder_scope_named.py`, `list_other_sources_workbooks.py`) do the same Playwright scan.

---

## 3. Dead code, duplication, inconsistency

### 3.1 Copy-paste volume: ~1,900–2,300 of 12,082 LOC in `cleanup/` (16–19%)

| Duplicated unit | Copies | Notes |
|---|---|---|
| `load_state`/`save_state` (or `load`/`save`) — 6-line JSON I/O with swallowed exceptions | **19** | e.g. `apply_lookup_rollout.py:35,44`, `cleanup_rollout.py:63,72`, `buyer_rollout.py:35`, `run_v1_rollout.py:33`… |
| `_open_template` + `_open_template_retry` (~50 lines) | **8** | already diverged: `apply_tpl_event.py:61` presses Ctrl+E first, other 7 don't — fixes need 8 edits |
| `_save_disabled` | 7 | byte-identical |
| Clay-CLI binary resolver + retry loop | 7 | search paths already diverged (`build_cleanup_manifest.py:59` checks 2 dirs) |
| Save-split-button "Save and run…" menu scan (~18 lines) | 6 | |
| `_pct` + `_wait_for_full_completion` | 4 | carries the C6 bug in every copy |
| `cf >= 3 → sys.exit(2)` circuit breaker | 14 | every rollout |
| Rollout drivers overall | 16 files | difflib similarity 67–84%; `apply_gsheet_rollout_other_sources.py` is 119/148 lines byte-identical to `apply_gsheet_rollout.py` |
| `.replace("C:\\", "/mnt/c/")` WSL path shim | 3 | no-op if repo moves off C: |

A `rollout_lib.py` (state I/O, argparse, loop, breaker) + `clay_panel.py` (template picker, save flows, `_pct`) would absorb nearly all of it — and give C6 a single fix point.

### 3.2 Superseded but still tracked/runnable

- `apply_findworkemail_event.py` + `apply_findworkemail_rollout.py` (939 LOC) — obsoleted by `add_workemail_waterfall_event.py:4-8`, still runnable at ~14.5 credits/row.
- `apply_tpl_event.py`/`apply_tpl_rollout.py` (333 LOC) — superseded by `apply_v1_*` + `run_v1_*`; last reader of legacy `cols_manifest.json` (`apply_tpl_rollout.py:23`).
- `apply_companies_lookup_event.py` (295 LOC) — imported by nobody, no rollout driver.
- `clay_login_auto.py` — superseded by `clay_login_watch.py` (polls all tabs).
- `folder_scope.py` + `list_other_sources_workbooks.py` — both subsumed by `folder_scope_named.py` (modulo the manifest cross-check).
- `add_workemail_waterfall_event.py` is a *misplaced shared library*: named for one Speakers operation, hardcodes `TABLE = "Speakers_normalized"` (`:45`), imported by **11** modules for its column-config/save/gate helpers.

### 3.3 Dead artifacts on disk (untracked — git hygiene itself is clean)

Verified: `git ls-files` contains **no** state/log/shard files; `.gitignore` covers them. But ~700 KB of orphaned JSON + 1.4 MB logs sit in the working tree: pre-slug state files (`buyer_state_all.json` 121 KB, `gsheet_state.json` 47 KB, …), unmerged `_w0/_w1/_w2` shards for 7 state families (`merge_shards.py` handles only `buyer_state_` and never deletes shards), 4 `.premerge` snapshots, 9 hand-written batch id-lists consumable only by `apply_filters_rollout.py --ids-file`, `cols_manifest_sponsors_labs.FULL/.batch16.json` experiments, byproducts `cols_snapshot.json`/`inventory_snapshot.json` (275 KB, never read), loose `evcharge_tpl_logs_batch1..8.log` beside an *empty* `evcharge_tpl_logs/` dir, and stale `cpython-314.pyc` next to `cpython-311.pyc`.

Root strays: `Query Skill.md` is a **byte-identical duplicate** of tracked `docs/CLAY_QUERY_SKILL.md` (md5-verified); the `.gitignore:35-42` comment justifying its exclusion ("holds live ids") is wrong for that file. Three screenshots document undocumented facts (the step-g mapping shows **5** fields, not the 2 the RUNBOOK describes at `docs/RUNBOOK.md:121-135`; a Speakers person-level enrichment no config covers).

### 3.4 Dead config and stale docs

- Config keys **never read by code**: `classifier.model`, `classifier.max_cost_usd`, `composite_tier_buyer_matrix` (`config/icps/labs/icp.yaml`), `notes` (entity YAMLs), and `people_search.yaml: seniority` — superseded by `SENIORITY_VALUES` in `people_fill_lib.js` (`pipeline_config.py:22-23`); duplicated source of truth that will drift.
- `template/FOLDER_CLAUDE.md.template` — placeholders present, **never rendered by anything**; absent from README's file tour.
- `docs/RUNBOOK.md` — passes a–g only; the committed Supabase, work-email waterfall, email-validation, LinkedIn, speakers, and Product & Services passes (commits `ac808fb`, `a2e89b6`) are undocumented. `docs/RUNBOOK.md:31` claims `requirements.txt` installs "pyyaml + playwright deps" — it installs pyyaml.
- `docs/SENSITIVE_DATA.md` (2026-07-10) is stale relative to `.gitignore` (misses `People - Filters*.txt`, ledger CSVs, root PNGs) and is violated by the committed IDs (§0).
- Speakers: docs say "future Speakers" everywhere, yet 3 speakers scripts exist with **no `config/entity-types/speakers.yaml`** — the extensibility showcase was built ad-hoc.
- `blocklist_ledger/README.md` + `04_backfill.sql` — see C12/C13.

### 3.5 Inconsistent patterns

- **4 incompatible state-file key schemes**: by workbook id / workbook name / event name / table id (any cross-pass tool must know which).
- **30 distinct `"status"` literals**, no shared enum; each rollout's done-set disagrees (`apply_gsheet_rollout.py:53` counts `skip_requested`, `apply_findworkemail_rollout.py:44` doesn't; `speakers_email_rollout.py:46` counts `running` as done).
- `--only` matches id *or* name; repeatable in exactly 1 of 14 rollouts (`apply_evcharge_tpl_rollout.py:68`).
- Two YAML loaders with divergent error handling (`pipeline_config.py:42-43` friendly vs `render_build_prompt.py:30-32` raw traceback); two `local.yaml` parsers (one hand-rolled and `#`-unsafe).
- `discover()` ×3 with 3 return shapes; `shot()` vs `snap()` for one screenshot helper; snapshot numbering stale vs the 15-entry `STEPS` list (`build_event.py:939-943`).

---

## 4. Naming inventory

Reference vocabulary (Clay product terms, verified in-product/docs during Phase 2): *workbook, table, view, column, row, source, enrichment, integration/provider, waterfall, Claygent, formula, lookup, filter, run condition, auto-run, credit, run, template, Find People, Send Table Data / write-to-table, HTTP API.*

### 4.1 Overloaded domain words (highest-impact)

| Term | Current meanings | Problem |
|---|---|---|
| **event** | ① trade show ② scraper folder ③ Clay **workbook** ④ the `"Event"` column ⑤ `*_event.py` = "single-workbook scope" | Five referents; Clay has no "event". `*_event.py` vs `*_rollout.py` is explained once (`docs/RUNBOOK.md:75`) and nowhere else |
| **build** | ① construct columns in a workbook (`build_event.py`, `build_lib`) ② a Find-People **search** config (`people_builds.py`, `seller.builds`) ③ generate a file (`build_cols_manifest.py`) ④ the directory `build_automation/` | Head-on collisions |
| **lookup** | ① Clay lookup template `"…Lookup & Send Table Data - v1"` (`apply_lookup_event.py`) ② different template with `"Lookup row"` sig (`apply_companies_lookup_event.py`) ③ static ICP reference CSVs (`config/icps/labs/lookups/`) ④ the `Fit` column (docs call it "lookup" and "formula/lookup" interchangeably) | Four senses of a real Clay word |
| **filter** | ① view filters (`apply_filters_event.py` — correct) ② Clay **search queries** (`People - Filters*.txt`) ③ run conditions are instead called **`gate`** everywhere (`GATE_COLUMN`, `repair_gate`, `fix_workemail_gate.py`) | Clay's term "run condition" appears only in comments |
| **template** | ① `template/` placeholder files ② saved Clay table templates (`templates.all_columns`) ③ `_template.yaml` config scaffolds; abbreviated **`tpl`** in half the files, spelled out in the rest | RUNBOOK needs a callout box to disambiguate (`docs/RUNBOOK.md:63-73`) |
| **pass / step** | RUNBOOK "passes a–g" vs PIPELINE_ARCHITECTURE's 14 "steps" (columns) vs RUNBOOK's own numbered "steps"; `PEOPLE_STEP.md` documents passes e/f; `_pass` also = retry iteration (`build_lib.py:675`) | Three granularities, two words |
| **manual** | ① hand-written formula template (`MANUAL_FORMULAS`, `build_formula_manual`) ② "human must do this" (`MANUAL_FOLDERS`, `rollout.py:29 MANUAL`) | Same package, opposite meanings |
| **Blocklist** | ① a passive Clay sink table (Send Table Data target) ② the active Supabase ledger gate | Architecturally opposite systems; spelled "Blocklist"/"Block List"/"block-list"; the canonical architecture doc never mentions the ledger |
| **route / split / target** | all mean Clay **Send Table Data** and its **destination** (`step_routes`, `_split`, `blocklist_route.py`, `ensure_target`) | Three coinages for one Clay object |

### 4.2 Generic/misleading files & symbols

| Name | Actual content | Suggested direction (Phase 2) |
|---|---|---|
| `common.py` | Clay session mgmt + CDP attach + a hardcoded `WORKBOOK = "Interphex"` | session/browser module name |
| `build_lib.py` / `formula_lib.py` | `_lib` says nothing; `formula_lib.py:112-249` is actually grid-header geometry | split/rename by function |
| `humanize.py` | randomized pacing (anti-bot), shadows PyPI `humanize` | e.g. `pacing.py` |
| `clay_sync.py` | importer + accidental config home (`SESSION_PATH`, `UA` imported by 4 modules) | extract session constants |
| `wait_out.py`, `worker.py`, `queue/cmd_NNN.py` | opaque job-queue mechanism | name for what it is |
| `v1` (8 files/dirs) | the literal Clay template suffix "…- v1", not a script version | tie to template name |
| `cols_manifest.json` | contains **workbooks**, not columns (`{"workbooks": [...]}`); `cols_snapshot.json` holds the columns | workbook-manifest name |
| `gsheet_*` | the Clay template "Google Sheet - Lookup & Send Data" (a lookup enrichment), not Google API code | |
| `sig`/`SIGNATURE`/`V1_SIGNATURE` | "column whose presence proves template applied" — reads cryptographic | e.g. marker column |
| `chips` (`field_chips`, `CHIP_VOCAB`) | Configure-panel column-mapping pills — DOM jargon in domain layer | mapping terms |
| `ran` (8 scripts) | the only credit-spend audit record, holding the UI menu label | e.g. `run_label` |
| `"triggering"` sentinel | the load-bearing value in the double-charge guard (`people_email_event.py:82-85`) — looks like a progress message | explicit sentinel name |
| Module aliases `A B E P R S T V W BU PC APP RUN` | 30+ files; `A` = "whichever event module this rollout drives" | spell out |
| `_local()` (`import_evcharge.py:48`) | reads ids from local.yaml/env | |
| `shot()`/`snap()`, `discover()` ×3 | inconsistent helpers | unify |
| `w0/w1/w2`, `tag`, `slug`, `only`, `other_sources`/`othersources`, `ps`, `.premerge` | worker index / state suffix / entity_icp namespace / scope selector / folder names / snapshot | document or rename consistently |
| `recon` | read-only Configure-panel dump | |
| `GateError`, `gate` | project coinage for run-condition assertions | align with "run condition" |

### 4.3 Config keys & env vars

- `tables.{main,seller,buyer,seller_people,buyer_people,seller_contacts,buyer_contacts}` — 7 table names, no input/output/byproduct distinction; `main` = normalized source table.
- `templates.all_columns` — the literal template "Exhibitors - All Columns - v1", nothing to do with "all columns of a table".
- `field_sources.country_source_field` — triple redundancy.
- `gating.*` — Clay calls these run conditions; `fanout` undefined anywhere.
- `raw_columns` — source-CSV headers, not Clay columns.
- `job_title_mode`/`exact`/`contains` — Clay search operators named as bare adjectives.
- Env vars are consistently `CLAY_*` (good); `CLAY_EVCHARGE_*` are campaign-specific and could generalize; `CLAUDE_JOB_DIR` is the odd one out.

### 4.4 Well-named already (leave alone)

`workbook`, `table`, `column`, `view`, `waterfall`, `enrichment`, `run condition` (in comments), `auto_run_off`, `Claygent`, `open_workbook_by_id`, `focus_table`, `pipeline_config`, entity/ICP axis and its glossary (`docs/GTM_METHODOLOGY.md:143-151`). Generic infrastructure (pacing, retries, CDP, state ledger) correctly keeps generic names and should not be forced into Clay vocabulary.

---

## Appendix: verification notes

- `.clay_session.json` cookie inventory inspected without printing values; git history checked via `git rev-list --all --objects`.
- `render_build_prompt.py` crashes (C14a, C14c) confirmed by execution; backfill constraint mismatch (C12) confirmed by grep; C1/C3/C4 confirmed by direct file reads.
- Line counts measured; duplication percentages via difflib on stripped lines.
