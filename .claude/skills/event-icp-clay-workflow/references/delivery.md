# Delivery into Clay, and the low-memory machine

Workflows cannot create Clay tables. So the review copies are imported as CSVs through the Clay web
app, driven by Playwright. The importer lives in the GitHub repo, not in this skill:

```bash
git clone https://github.com/qasimovski/clay-icp-pipeline.git     # once
cd clay-icp-pipeline/automation/cleanup
python import_ce_delivery_tables.py --plan <plan.json> --state <state.json> --folder-id f_…          # dry run
python import_ce_delivery_tables.py --plan <plan.json> --state <state.json> --folder-id f_… --apply --only "<pilot event>"
python import_ce_delivery_tables.py --plan <plan.json> --state <state.json> --folder-id f_… --apply
```

The repo's `config/local.yaml` needs `workspace_id` (see `config/local.yaml.example`).

## The review handoff (what the marketing manager opens)

- **One workbook per event**, in a folder the user creates by hand and names (Mining: "Mining
  [2026 - Qasim]" → "Competitive Events"). Ask for its URL; the id is the `f_…` part. Nothing
  creates folders, so an import can only ever reach the folder it was given.
- **Six tables at most per workbook, named exactly:** Exhibitors / Exhibitors Buyside People /
  Exhibitors Sellside People / Sponsors / Sponsors Buyside People / Sponsors Sellside People.
  **The CSV filename is the table name**, and renaming afterwards is manual UI work. No file, and
  so no empty table, for a combination with no rows.
- **Build the plan with `deliver.py split --decisions`:**
  - **Company tables** carry EVERY scraped row with its Decision and Reason, so the manager sees
    what was left out and why.
  - **People are placed** in every event table where their company (by domain) is a KEEP row. The
    same person can sit in several events' tables (Mining: 19,082 people → 37,790 rows).
  - **Outreach Channel tag:** every person carries one. `--linkedin-only` rows get
    `LinkedIn + phone only (<country>)` and must never reach the email step.
  - **Speakers:** `--exclude-source Speaker` when they are delivered apart.

  ```bash
  python scripts/deliver.py split --decisions remap/decisions.csv --people "<S> - review.csv" \
      --linkedin-only "<S> - review - LinkedIn only.csv" --linkedin-only-label "LinkedIn + phone only (Germany)" \
      --exclude-source Speaker --out event_tables --plan event_tables_plan.json
  ```

  Without a remap, `deliver.py split --classified classified.csv --people people.csv` puts one
  row per company row and per person.
- **Deliver to a NEW folder**, never over an older delivery.
- **Every table is confirmed over the CLI** (row count == CSV). A table that imports but can't be
  confirmed stays pending and is retried. Re-running is safe: present and correct tables are
  skipped.
- **Pilot one event**, let the user look at it in Clay, then the rest.
- **Before the user forwards the folder, confirm** what is in it and what isn't, e.g. "speakers
  not included, Germany included and tagged". The user asked exactly that on Mining.

## The Clay Tables sheet rows

One row per PEOPLE table:
- **Name:** `Buyers|Sellers from <event>[ - Sponsors]`, hyperlinked on the name and in the link
  column.
- **Promo:** CONF for buyers, SPEX for sellers.
- **Qty:** the people row count.
- **GTM:** the person's name.
- **List description:** `<ICP>_<name>`.

Paste it as a new block, not over rows whose notes columns line up with the old order. Either tool
works:
```bash
python scripts/deliver.py rows --plan event_tables_plan.json --state <state.json> \
    --prefix <ICP> --workspace <workspace id> --gtm <name> --out "<ICP> - Clay Tables rows"
# or, in the repo (Highways/Mining format, with section headers and totals):
#   automation/cleanup/build_icp_clay_tables.py --delivery mining_ce --prefix Mining --out icp_clay_tables_mining_ce   (WSL)
#   automation/cleanup/write_icp_clay_tables_paste.py --src icp_clay_tables_mining_ce.json --out "ICP - Mining - Clay Tables - CE v2 (paste into Sheets)"
```
Open the HTML, press Ctrl+A, copy and paste into Google Sheets; the links land on the text. The
TSV has `=HYPERLINK` formulas. Check that every link points at the workbook the state names, not
at a deleted duplicate.

## The browser

Clay blocks Playwright-launched browsers. So the importer drives a REAL Chrome over the DevTools
port, signed in to Clay, with its own profile (`C:\clay-debug` on Qasim's machine):

```powershell
chrome.exe --headless=new --remote-debugging-port=9222 --user-data-dir=C:\clay-debug --window-size=1720,1000 --user-agent="<normal desktop Chrome UA, no 'Headless'>"
```

then run the importer with `CLAY_USE_CDP=1`.
- **Port taken:** if another Chrome already holds `127.0.0.1:9222` (a 404 on `/json/version`),
  use `CLAY_CDP="http://[::1]:9222"`.
- **Sign-in:** the profile must have been signed in to Clay once in a visible window.
- **Restarting it:** kill only the Chrome processes whose command line contains the profile dir,
  never every Chrome.
- **Close it when done:** the headless Chrome holds ~1.5 GB.

## Low memory, killed jobs, and the duplicate-workbook trap

On a ~8 GB machine Claude Code kills idle background jobs when memory is critical. The user's
standing instructions:
- pause about 2 minutes when memory is low, then resume;
- repair and resume a killed FREE import without asking;
- never restart a PAID run without asking.

- **Import one workbook per invocation** (`--limit 1` in a loop). Check free memory before each
  and wait while it is under ~0.9 GB. Recycle the headless Chrome between workbooks.
- **Never pause or kill mid-table.** A half-imported table is "present but wrong size" and needs a
  repair.
- **A "killed" notice can be wrong. Check the processes before resuming.** On Mining the reported
  kill left the bash loop and the importer running. A second loop started beside it, the two raced,
  and Hillhead, IMARC and IMME each got TWO workbooks, each half filled.

  ```powershell
  Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'import_ce_delivery|import_loop' } | select ProcessId, CommandLine
  ```

  If one is alive, it is still working: wait for it, and never start another.
- **Re-read live counts before deleting anything.** A table that read 0 rows right after a kill was
  full (1,792) minutes later, because Clay finishes a submitted import by itself. Clay also
  finishes submitted bulk runs after the local driver dies.
- **Repair:** `repair_delivery_empties.py --plan … --state … --folder-id f_…` deletes only tables
  Clay reports at 0 rows. It reports a SHORT table rather than deleting it.
  - `--folder-id` finds a workbook that has no confirmed table in the state yet.
  - Then restart the loop; the importer skips confirmed tables.
- **Duplicate workbooks:**
  - **Pick the kept copy.** Keep the one the state points at, and complete it (re-import only
    0-row or missing tables).
  - **Empty the duplicate table by table.** Delete each table only after the kept copy is
    confirmed to hold that table at the plan's row count.
  - **Leave the empty workbook for the user.** The user deletes empty workbooks by hand. Give them
    the links.
  - The Mining one-off is `scrapers_mining/_v2/clay_run/fix_duplicates.py`.
- Clay has no delete API. Deleting a table is UI automation (`clay_ui.delete_table`), so
  confirm first unless the user already said so.
