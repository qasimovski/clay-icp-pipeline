# Automation — reference / manual-run, not config-driven

This folder is a copy of the Playwright automation that was actually used to
build the live Labs Exhibitors/Sponsors pipelines in Clay, moved here from
its original location inside the scraper tree. Per the current scope
decision (see the repo root README), **these scripts were not refactored to
take entity-type/ICP config as parameters this round** — they still assume
the Interphex-pilot build they were written for. Treat them as a working
reference for how the original build was automated, not a drop-in "build
for any entity/ICP" tool.

## What's here vs. what was left behind

Copied (the reusable engine parts):
- `clay_sync/` — copy of the original `scrapers/_clay_sync/`: the
  Playwright-driven CSV → Clay-workbook importer (`clay_sync.py`), session
  management (`clay_login.py`, `csv_push_state.py`), UI navigation primitives
  (`clay_ui.py`), and an audit script (`clay_audit.py`). (The original
  randomized human-pacing layer, `humanize.py`, was removed 2026-08-02 in
  favor of fixed minimal waits — see git history.)
- `build_automation/` — copy of the original
  `scrapers/Exhibitors_Clay/pilot_interphex/automation/`: shared navigation
  helpers (`browser_session.py`), the column/formula builders (`formula_columns.py`,
  `column_config.py`), the blocklist-send helper (`blocklist_send.py`), the
  per-event build driver (`build_workbook.py`), the multi-event fleet rollout
  driver (`rollout.py`), and the persistent-worker/queue mechanism
  (`worker.py`, `worker_wait.py`).

**Not copied** (left in the original scraper tree, not part of the reusable
process):
- The `stepNN_*.py` recon/verification scripts (`step00_recon.py` …
  `step09_claygent_panel_recon.py`) — these were one-off exploratory
  scripts used during initial Clay-UI discovery for the Interphex pilot,
  not part of the repeatable build flow.
- `check_acs_fall.py` — an event-specific ad-hoc check script.
- `queue/` — a directory of generated per-run command files
  (`cmd_NNN.py`/`.ok`/`.err`), a runtime artifact of the command-queue
  mechanism, not source code.
- All logs, screenshots (`shots/`, `rollout_shots/`), and state files
  (`rollout_state*.json`, `rollout_logs/`) — runtime output of a specific
  automation run.

## The one code change made when copying

Two path constants assumed the scripts lived three levels inside the
scraper tree; now that they live in their own repo, both are resolved via
environment variables with sensible defaults (see the comment block in
`build_automation/build_workbook.py`):

- `CLAY_PIPELINE_SCRAPERS_ROOT` — where your actual scraper output (the
  real, gitignored `*_normalized.csv` files per event) lives. Defaults to
  `../scrapers` relative to this repo (i.e. a sibling `scrapers/` folder) —
  override if yours lives elsewhere.
- `CLAY_PIPELINE_ICP_LOOKUPS_DIR` — where the real (gitignored) lookup CSVs
  for an ICP live. Defaults to `config/icps/labs/lookups/` in this repo —
  override (or symlink) if you're building for a different ICP or keep
  lookups elsewhere.

`build_automation/browser_session.py`'s `CLAY_SYNC_DIR` was also updated to point at
`../clay_sync` (its new sibling location in this repo) instead of the old
`../../../_clay_sync` path.

## Running it

1. `pip install -r ../requirements.txt` (needs `playwright` too — the
   original scripts assumed it was already installed; run
   `pip install playwright && playwright install chromium` if starting fresh).
2. `python clay_sync/clay_login.py` once, to save a Clay session.
3. `python build_automation/build_workbook.py --folder "<Event Folder Name>"`
   to build one event's pipeline (guarded by existence checks — safe to
   rerun if interrupted).
4. `python build_automation/rollout.py` to run `build_workbook` across every
   scraper folder that has the relevant normalized CSV (this is what built
   the original 77-workbook Exhibitors fleet).

## Known rollout cleanup items

See `build_automation/CLEANUP_NOTES.md` — a punch-list from the original
Labs rollout (stray Claygent copies, duplicate tables, cosmetic badges, a
Find People catch-up pass) worth checking after any new fleet rollout.

## If you do want to make this config-driven later

The natural next step (explicitly out of scope for this round) would be
teaching `build_workbook.py` to read `config/entity-types/<entity>.yaml` and
`config/icps/<icp>/icp.yaml` directly — the same data
`template/render_build_prompt.py` already reads — instead of its current
hardcoded Interphex/Exhibitors assumptions, so `build_workbook.py --entity
sponsors --icp labs --folder "..."` builds any configured pipeline
end-to-end without a human pasting a rendered spec into the Clay UI first.
