# Delivery into Clay, and the low-memory machine

Workflows cannot create Clay tables, so the review copies are imported as CSVs through the Clay
web app, driven by Playwright. That importer lives in the GitHub repo, not in this skill:

```bash
git clone https://github.com/qasimovski/clay-icp-pipeline.git     # once
cd clay-icp-pipeline/automation/cleanup
python import_ce_delivery_tables.py --plan <plan.json> --state <state.json> --folder-id f_…          # dry run
python import_ce_delivery_tables.py --plan <plan.json> --state <state.json> --folder-id f_… --apply --only "<pilot event>"
python import_ce_delivery_tables.py --plan <plan.json> --state <state.json> --folder-id f_… --apply
```

(`--plan/--state/--folder-id` were added 2026-10-21; older clones only have `--delivery`.) The
repo's `config/local.yaml` needs `workspace_id`; see `config/local.yaml.example`.

- **The user makes the folder by hand** in Clay and gives you its URL; the id is the `f_…` part.
  Nothing creates folders, so an import can only ever reach the folder it was given.
- **Deliver to a NEW folder**, never over an older delivery. The old tables stay until the user
  retires them by hand; nothing in this route deletes anything in Clay.
- **Plan = `deliver.py split` output.** The CSV filename is the table name.
- **Every table is confirmed over the CLI** (row count == CSV). A table that imports but cannot be
  confirmed stays pending and is retried; re-running is safe — present + correct tables are skipped.
- **Pilot one event**, let the user look at it in Clay, then the rest.

## The browser

Clay blocks Playwright-launched browsers, so the importer drives a REAL Chrome over the DevTools
port, signed in to Clay, with its own profile (`C:\clay-debug` on Qasim's machine):

```powershell
chrome.exe --headless=new --remote-debugging-port=9222 --user-data-dir=C:\clay-debug --window-size=1720,1000 --user-agent="<normal desktop Chrome UA, no 'Headless'>"
```

then run the importer with `CLAY_USE_CDP=1`. If another Chrome already holds `127.0.0.1:9222`
(a 404 on `/json/version`), use `CLAY_CDP="http://[::1]:9222"`. The profile must have been signed
in to Clay once in a visible window. Close the headless Chrome when done — it holds ~1.5 GB.

## Low memory (the user's standing instruction)

On a ~8 GB machine Claude Code kills idle background jobs when memory is critical. The user wants
long jobs to **pause about a minute and resume automatically**:

- import one workbook per invocation (`--limit 1` in a loop), check free memory before each, and
  wait in 60 s steps while it is under ~0.9 GB; close leftover Clay tabs between workbooks;
- never pause or kill mid-table — a half-imported table is "present but wrong size" and needs a
  manual repair;
- if a background job is killed anyway, check which tables are confirmed, look in Clay for a
  half-made one, wait a minute, resume (the importer skips what is already correct).

## The Clay Tables sheet

`deliver.py rows --plan … --state … --prefix <Series> --workspace <id> --gtm <name> --out …`
writes an HTML page (open, Ctrl+A, copy, paste into Google Sheets — links land on the text) and a
TSV with `=HYPERLINK`. One row per PEOPLE table: `Buyers|Sellers from <event>[ - Sponsors]`,
promo CONF (buy) / SPEX (sell). Paste it as a new block; don't paste over existing rows whose
notes columns line up with the old order.
