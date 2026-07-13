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
- `cleanup_event.py` — `clean_workbook()`; also a `--only` single-workbook runner.
- `cleanup_rollout.py` — fleet driver: `--dry-run`, `--shard i --shards N`,
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
python cleanup_rollout.py --dry-run

# 3) validate the delete interaction on ONE workbook, watched (Windows)
python cleanup_rollout.py --only "Interphex" --headed

# 4) real run, 4 workers — one per terminal (Windows)
python cleanup_rollout.py --shards 4 --shard 0
python cleanup_rollout.py --shards 4 --shard 1
python cleanup_rollout.py --shards 4 --shard 2
python cleanup_rollout.py --shards 4 --shard 3

# 5) verify (WSL)
python verify_cleanup.py --wait 120
```

State per shard (`cleanup_state_w{n}.json`) makes each worker resumable — rerun a
shard to pick up where it stopped.

## Later passes (same folder, same session)

Beyond the original table cleanup, this folder accumulated a set of
manifest-driven, resumable passes over each event's `Exhibitors_normalized`.
All are `*_event.py` (one workbook) + `*_rollout.py` (fleet, `--only` / `--limit`),
keyed off `cols_manifest.json` (build it with `build_cols_manifest.py`). Every
pass writes its own `*_state_*.json` (skips already-done) and `*_logs/`.

- `trim_cols_*` — delete columns to the right of a cut column.
- `apply_v1_*` / `run_v1_*` — apply + run the "Exhibitors - All Columns - v1" template.
- `apply_lookup_*` — apply + run "Exhibitors - Lookup & Send Table Data - v1".
- `apply_filters_*` — set the two view filters (`Side = Seller` AND
  `Send table data has results`); supports `--ids-file` + `--state-suffix` for
  parallel shards.
- `people_builds.py` + `people_rollout.py` — run the 3 "Find people at these
  companies" builds into a per-event **"Sellers - People"** table (build 1 →
  new table + rename; builds 2 & 3 → append). Scope: `people_targets.json`.
  Filter-fill logic is vendored in `people_fill_lib.js` (override with
  `CLAY_PEOPLE_FILL_JS`). Run in batches; a run cut off mid-event resumes/salvages
  cleanly (no duplicate tables).

All generated `*.json` (manifests, state, targets, batch/shard lists) and
`*_logs/` are git-ignored — they carry live workspace/workbook ids. Regenerate
manifests from the live workspace with the `build_*.py` scripts.

## Environment notes

- The `clay` CLI runs only on Linux/WSL here (the Windows/Git-Bash build rejects
  the OS). `build_cleanup_manifest.py` / `verify_cleanup.py` resolve the cached
  plugin binary automatically; override with `CLAY_BIN` if needed.
- If the workspace id differs from the default, set `CLAY_WORKSPACE_ID` for the
  Playwright side (used to build `/workspaces/<id>/workbooks/<wid>` URLs).
