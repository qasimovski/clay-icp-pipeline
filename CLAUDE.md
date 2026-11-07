# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A **process repo**, not a data repo: config + docs + Playwright automation that turns
scraped trade-show CSVs into tiered, scored Clay contact lists. The pipeline shape is
fixed; everything that varies is config in two layers:

| Layer | File | Varies |
|---|---|---|
| Entity type (data source) | `config/entity-types/<entity>.yaml` | source/output table names, Clay template names, `raw_columns`, trim cut column |
| ICP (vertical) | `config/icps/<icp>/icp.yaml` + `people_search.yaml` | classifier taxonomy, fit/country tiers, Find-People job titles/segments/locations |

Defaults everywhere: `exhibitors` + `labs`. `automation/cleanup/pipeline_config.py` is the
single resolver both the passes and the spec renderer read. **Three ways in, not one** — check
which one a pass uses before assuming a flag exists:

1. `pcfg.add_cli_args(ap)` → real `--entity` / `--icp` flags: `seller_people_rollout`,
   `buyer_people_rollout`, `apply_gsheet_lookup_rollout`, `merge_shards`.
2. Env-only (`CLAY_PIPELINE_ENTITY` / `CLAY_PIPELINE_ICP`): the earlier passes —
   `trim_columns`, `apply_all_columns`, `run_all_columns`, `apply_lookup`,
   `apply_view_filters`, `build_workbook_manifest`. Passing `--entity` to one of these is an
   argparse error, not a silent no-op — but exporting the env var and forgetting it *is*
   silent, and it picks the source table for every later pass in that shell.
3. Neither: scope is a hand-curated JSON of live table ids for one named Clay folder —
   `ps_*_rollout.py` (Product & Services), `apply_gsheet_lookup_rollout_other_sources.py`
   (Other Sources), `speakers_email_rollout.py`, `people_email_rollout.py`. Those folders
   aren't an entity/ICP concept (no per-event build pipeline created their tables), so don't
   try to retrofit `--entity` onto them; regenerate the scope file instead
   (`list_workbook_tables.py`, `list_other_sources_workbooks.py`, the `build_*_manifest.py`
   pair). Their in-source `SKIP` sets and row-count orderings are recorded human decisions —
   see "Conventions" below.

Read `docs/GTM_METHODOLOGY.md` (why) → `docs/PIPELINE_ARCHITECTURE.md` (the 14-step column
pipeline) → `docs/RUNBOOK.md` (the operational sequence, a–h) before changing pipeline
behavior. `docs/PEOPLE_PASSES.md` holds the Find-People query-correctness rules (whole persona
in one experience predicate, `contains` not `in` for titles, always cap per company) — read it
before touching `people_search.yaml` or the seller/buyer search builders;
`docs/CLAY_QUERY_SKILL.md` is the Clay advanced-search query-language reference those filters
compile down to. `docs/WORKFLOWS_ROUTE.md` is the second way through the pipeline — Clay
Workflows driven from the CLI instead of Playwright — and says when to prefer it, which is
only when a company's ICP category is not already a property of the table it sits in. `docs/AUDIT.md` is the full-repo audit with a Phase 3 remediation table — it is
the "why does this guard exist" index for `state_io`, `column_completion`, the sharded run log
and the tri-state pre-checks; consult it before you simplify one of them away. A rendered build
spec lives in `docs/examples/interphex-build-spec.md`.

## Commands

```bash
pip install -r requirements.txt && python -m playwright install chromium
python -m pytest tests/ -q                      # from repo root; browser-free, <1s
python -m pytest tests/test_state_io.py -q      # single file
python -m pytest tests/test_state_io.py -q -k corrupt   # single test

python template/render_build_prompt.py --entity exhibitors --icp labs --out /tmp/spec.md
python automation/cleanup/pipeline_config.py --entity sponsors --icp labs   # inspect resolution
python automation/clay_sync/clay_login.py       # one-time, headed → gitignored session file
```

Per-event passes live in `automation/cleanup/` and run in the RUNBOOK order (a–h, where h is the
email waterfall + Validate Email pass on the people tables). Each is a
pair: `<verb>.py` (one workbook) + `<verb>_rollout.py` (fleet). Scope comes from a generated
targets/manifest JSON (`build_workbook_manifest.py`, `build_cleanup_manifest.py`); state and
logs are namespaced per `<entity>_<icp>` slug so two entities never share them.

The flag vocabulary is near-uniform across the fleet drivers, and the escalation order matters
because selector drift against the live Clay DOM is the failure mode the tests cannot catch:

```bash
python <pass>_rollout.py --list                     # scope only, no browser
python <pass>_rollout.py --dry-run                  # what it would touch
python <pass>_rollout.py --only "<event>" --headed --recon   # watch one workbook, probe the
                                                    # panel/selectors, change nothing
python <pass>_rollout.py --only "<event>" --headed  # one for real, watched
python <pass>_rollout.py --shards 4 --shard 0       # fleet, one worker per terminal
```

`--recon` (and `--screenshot` / `--inspect` where present) is the read-only probe; `--skip-run`
or `--configure-only` configures without triggering; `--limit N` caps a batch. Prefer `--recon`
over reading the DOM by hand — several passes vendor their recon output shape into the repair
scripts. Near-uniform is not uniform: the no-browser plan is `--list` on the event passes but
`--audit` on the `ps_*` family, `--after "<workbook>"` resumes past a named workbook, and
`--order alpha` overrides a pass's default ordering. **Each pass's module docstring is the
authoritative invocation reference** — it carries that pass's real flag set, its scope
decisions, and which steps spend credits.

### Conventions the passes share

- **Final Salesforce CSV names** (user, 2026-11-07): `Competitive_<Brand>.csv`, `Other_sources_<Brand>.csv`,
  `Buy_side_<Brand>.csv`, `Sell_side_<Brand>.csv`; short brand (`salesforce_brand` in the ICP's icp.yaml),
  `_<Part>` for a second upload of one scope, companions add ` - <label>`. `sf_naming.py` enforces it in
  `deliver.py salesforce`; `docs/NAMING.md` §F has the full rule. New exports only.
- **Smallest table first.** Fleet order is deliberately ascending by live row count so a
  selector regression surfaces on a 225-row table, not on Testing & Diagnostics (8,671). Row
  counts baked into these modules are for ordering and blast-radius reporting only, never a
  correctness input — and they go stale (`ps_people_email_rollout.py` re-read them because
  `ps_people_rollout.py`'s list predated a table).
- **Docstrings are the decision log.** "Per the user (date)", accepted-cost notes (e.g. the
  deliberate 401s in `ps_people_rollout.py`), and "absent on purpose / already done by hand"
  entries record choices a human already made and paid for. Don't rewrite them as generic
  descriptions, and don't re-add a skipped table because the rationale looks obsolete.
- **Skip-by-absence beats skip-by-rule.** The strongest guards make a table unreachable by
  leaving it out of the manifest, so no edit to a `SKIP` constant can re-charge it.
- **Guards ask Clay, not the state file.** Re-entry safety comes from querying the live table
  for the pass's marker column (via the CLI, failing closed) *in addition to* the state file —
  `people_email_rollout.py` and `ps_supabase_rollout.py` are the models to copy.

## Three generations of automation — know which you're touching

- `automation/clay_sync/` — **active** Playwright primitives (`clay_ui.py` is the core) plus
  the CSV → workbook importers.
- `automation/build_automation/` — **reference-only, Interphex/Exhibitors-hardcoded**. It built
  the original live pipeline; it is *not* config-driven. Don't extend it as if it were the
  current tool (see `automation/README.md`).
- `automation/cleanup/` — **active and config-driven**; this is where new fleet passes go.
  `add_workemail_waterfall.py` is the de-facto shared column-panel library (imported by ~11
  modules); `browser_session.py`, `column_config.py`, `state_io.py`, `column_completion.py`
  are the other shared pieces.

Even inside `cleanup/` some files are **superseded** and kept only as Phase 3 retirement
candidates — `apply_tpl_event.py` (+ rollout), `apply_findworkemail_event.py` (+ rollout),
`apply_companies_lookup_event.py`, `folder_scope.py`, `clay_login_auto.py`. Extend
`apply_all_columns*` / `apply_lookup*` / the `people_email*` pair instead; `docs/NAMING.md`
rule 2 lists the full set and says why they weren't renamed.

Cross-event dedupe is **not** a Clay table: it's the Supabase `blocklist_ledger/`, queried per
row by one HTTP column that does lookup-and-insert atomically and returns `Is New`. Views filter
on `Is New` *before* any paid column runs. A legacy Clay "Block List" table (fed by Send Table
Data) only recorded repeats without gating them — older workbooks may still carry it.

The ledger's SQL applies in filename order (`01_schema` → `02_functions` → `03_verify` →
`04_backfill`); identity keys are normalized *in the database* (`normalize_domain`,
`normalize_linkedin`) with a CHECK constraint making an unnormalized key unstorable, so never
normalize on the Clay side too. **Backfill before wiring the column, not after** — the ledger
inserts on first sighting, so a Clay run against an unbackfilled ledger marks already-worked
records `Is New` and re-spends the full downstream funnel on them.

**The ledger is per-ICP.** `blocklist_ledger/` is Labs (`company_ledger`, `people_ledger`);
`blocklist_ledger/evcharge/` is EVCharge (`evcharge_*_ledger`, `ledger_check_evcharge_*`) —
prefixed tables and RPCs in the *same* Supabase project, reusing the Labs normalizers rather
than redefining them, so the Labs `02_functions.sql` is a prerequisite. The verticals dedupe
independently on purpose: a company worked under Labs is still `Is New` to EVCharge. RPC
argument names are identical across ICPs, so an EVCharge Clay column differs from its Labs
twin by the URL alone — same account, headers, body and response mapping. A new ICP means a
new prefixed pair, never a discriminator column on the live tables.

## Hard rules

**Run-now vs configure-only.** Anything calling a paid provider per row — Claygent/AI columns,
`Enrich Company`, Find People — is created and fully configured but **never triggered**. Formula
columns, table creation, CSV import, and Send Table Data actions are created *and* run. The one
paid exception deliberately run is the blocklist ledger check (1 Action/row), because it's what
keeps the expensive columns off already-worked companies. `browser_session.py` encodes the
matching safety rule: never click a control whose accessible name matches `/run/i` unless it is
an explicit "save without running".

**Never rename anything persisted or external.** State/log/manifest *filenames*, JSON keys and
status literals inside state files, env var names (`CLAY_*`, `PG*`), and any string matching a
live Clay object name ("Exhibitors - All Columns - v1", "Save and run … in this view") stay
byte-identical — rollouts have in-flight resume state and a silent restart re-spends credits.
`docs/NAMING.md` is the authoritative old→new symbol/file map and the rationale.

**State files fail loud.** `state_io.load_json` refuses to treat a corrupt state file as empty;
`save_json` is atomic (temp + `os.replace`). Don't add `except Exception: return {}` around them.
The suite in `tests/` exists to guard exactly these money-and-state decisions — `tests/README.md`
maps each file to the failure it prevents. Adding a pass that can double-charge or strand a
workbook means adding a row to that table, not just a test file.

**Don't trust the Clay page's own status text.** "Save and run N rows" / "N% of table completed"
can be a stale leftover from an unrelated column. Poll for the pass's marker column, and confirm
with `clay tables columns list <tableId>` (WSL CLI) when in doubt. `column_completion.py` exists
because a table-wide 100% banner marked an unfinished column done.

## Environment gotchas (win32 + WSL split)

- **Clay bot-blocks Playwright-launched logins.** The working path is driving a real
  user-launched Chrome over CDP: `chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\clay-debug`,
  logged into Clay, then `CLAY_USE_CDP=1` (`CLAY_CDP` overrides the endpoint). `clay_page()`
  falls back to a bundled browser + saved session. On a CDP connection, never call
  `browser.close()` — it kills the user's Chrome.
- **The `clay` CLI is Linux/WSL-only here** and read-only. Playwright passes run on Windows; the
  manifest-build and verify scripts run in WSL. `CLAY_BIN` overrides binary resolution.
- **Clay has no delete API, and no build API for tables/columns either** — the CLI (and the Clay
  MCP server, when connected) are read paths for verification only. Every mutation this repo
  makes — creating columns, applying templates, setting filters, deleting tables — is Playwright
  UI automation (`clay_ui.delete_table`, with `KEEP_NEVER_DELETE` as a fail-closed allowlist).
- **Long background tasks get killed at a variable cadence.** Always run via the resumable
  rollouts and relaunch; check `Get-Process python` (PowerShell) before relaunching to avoid
  overlapping workers. Watch for *silent hangs* (log mtime stops) as well as crashes.
- Navigate by workbook **id**, not name — duplicate names and listing virtualization break
  name-based navigation.

## Sensitive data

Live Clay ids (workspace/folder/workbook/table/view) belong in `config/local.yaml` (gitignored;
copy `config/local.yaml.example`) or env vars, read via `browser_session.local_setting()` — never
hard-coded in tracked source (`tests/test_local_settings.py` guards this). Also excluded: real
`*_normalized.csv`, the ICP playbook `.xlsx`, real `config/icps/*/lookups/*.csv` (only
`.example.csv` committed), every generated `automation/cleanup/*.json`, and all `*_logs/`
`shots/` `*session*.json`. `git status` before every commit — see `docs/SENSITIVE_DATA.md`.

## Glossary

*event* = a trade show, one Clay **workbook** per event · *pass* = one fleet-wide automation run
· *step* = one column/action inside a table (reserved for PIPELINE_ARCHITECTURE's 14) ·
*marker column* = a column whose presence proves a template was applied · *rollout* = the fleet
driver. Clay's own terms: **Only run if** (not "gate"), **Auto-update** (not "auto-run"),
**Send Table Data** (not "route"/"split"), **Lookup Rows**.

## Known open items

`docs/KNOWN_ISSUES.md` — the unresolved CAB composite-tier discrepancy (needs an ICP-owner
decision, must not be silently resolved) and Sponsors-replica drift in live tables (missing
run conditions, narrower `Enrich Company` field set). This repo's config is authored to the
corrected/mature behavior; already-live tables need a separate audit pass.
