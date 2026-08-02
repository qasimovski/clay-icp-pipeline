"""
Import the EVCharge scraped exhibitors into Clay.

One workbook per scraper event folder (workbook name = folder name), each with a
single table imported from that folder's `exhibitors_normalized.csv`. Clay names
the table after the CSV stem, so every workbook gets a table called
`exhibitors_normalized`.

HARD SCOPE: everything is created inside
    10. EVCharge [2026 - Qasim]  ->  Competitive Events
addressed by that subfolder's own id (EVCHARGE_SUBFOLDER_ID below), reached by
direct URL. This script never navigates the Labs folder listing and has no
delete/edit operation — it only creates workbooks and imports CSVs.

It reuses the Playwright primitives in clay_ui.py but not clay_ui's
open_target_location/list_workbooks, which are hard-wired to Labs (and whose
"an empty listing is never real" guard is wrong for a folder that starts empty).

Usage:
  python import_evcharge.py                     # dry run — report the plan
  python import_evcharge.py --apply             # create everything missing
  python import_evcharge.py --apply --limit 5   # first 5 pending folders only
  python import_evcharge.py --apply --only "ACT Expo" --only "AMTS"
  python import_evcharge.py --apply --show      # visible, slowed browser
  python import_evcharge.py --list              # live listing of the subfolder

Requires a saved session (automation/clay_sync/.clay_session.json).
"""

import argparse
import datetime
import json
import os
import sys

from playwright.sync_api import sync_playwright

import clay_ui
import humanize

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_PATH = os.path.join(SCRIPT_DIR, ".clay_session.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "evcharge_logs")
LOCAL_CONFIG = os.path.join(os.path.dirname(os.path.dirname(SCRIPT_DIR)),
                            "config", "local.yaml")


def _local(key, env_var):
    """Read an account-specific id from the environment or config/local.yaml.

    Both are outside git (see docs/SENSITIVE_DATA.md). Parsed with a plain
    scan rather than PyYAML so this module keeps its only-playwright dependency.
    """
    val = os.environ.get(env_var)
    if val:
        return val.strip()
    try:
        with open(LOCAL_CONFIG, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line.startswith(f"{key}:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    raise SystemExit(
        f"missing {key!r}: set ${env_var} or add `{key}: \"...\"` to "
        f"{LOCAL_CONFIG} (see config/local.yaml.example)")

SCRAPERS_ROOT = os.environ.get("CLAY_EVCHARGE_SCRAPERS_ROOT",
                               r"C:\Users\qasim\terrapinn\scrapers_evcharge")
# Set from --csv-name at startup; the exhibitors run's state file keeps its
# original name so that run stays resumable.
CSV_NAME = "exhibitors_normalized.csv"
TABLE_NAME = "exhibitors_normalized"
STATE_PATH = os.path.join(SCRIPT_DIR, "evcharge_import_state.json")


def set_csv(csv_name):
    """Point the module at a normalized CSV (exhibitors / sponsors / ...)."""
    global CSV_NAME, TABLE_NAME, STATE_PATH
    CSV_NAME = csv_name
    TABLE_NAME = os.path.splitext(csv_name)[0]
    STATE_PATH = os.path.join(
        SCRIPT_DIR,
        "evcharge_import_state.json" if TABLE_NAME == "exhibitors_normalized"
        else f"evcharge_import_state_{TABLE_NAME}.json")

# "Competitive Events" inside the EVCharge folder. These identify a live Clay
# account, so per docs/SENSITIVE_DATA.md they live in config/local.yaml (which is
# gitignored) or the environment — never hard-coded here. They are still pinned
# to explicit ids rather than a folder-name lookup, so no run can ever land in a
# different (e.g. Labs) folder.
WORKSPACE_ID = _local("workspace_id", "CLAY_WORKSPACE_ID")
EVCHARGE_FOLDER_ID = _local("evcharge_folder_id", "CLAY_EVCHARGE_FOLDER_ID")
EVCHARGE_SUBFOLDER_ID = _local("evcharge_subfolder_id",
                               "CLAY_EVCHARGE_SUBFOLDER_ID")
SUBFOLDER_URL = (f"{clay_ui.CLAY_URL}/workspaces/{WORKSPACE_ID}"
                 f"/home/{EVCHARGE_SUBFOLDER_ID}")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Every cell in a workbook row links to that workbook; first non-empty cell
# text per workbook id is its name (same shape as clay_ui._COLLECT_ROWS_JS).
_ROWS_JS = """() => {
    const out = {};
    for (const a of document.querySelectorAll('a[href*="/workbooks/"]')) {
        const m = a.href.match(/\\/workbooks\\/([^/?]+)/);
        if (!m) continue;
        const cell = a.closest('td, [role="cell"], [role="gridcell"]');
        const text = (cell ? cell.textContent : a.textContent).trim();
        if (!(m[1] in out) && text) out[m[1]] = text;
    }
    return out;
}"""


def discover():
    """[(folder, csv_path), ...] for every scraper folder holding CSV_NAME."""
    out = []
    for name in sorted(os.listdir(SCRAPERS_ROOT)):
        path = os.path.join(SCRAPERS_ROOT, name, CSV_NAME)
        if os.path.isfile(path):
            out.append((name, path))
    return out


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)


def open_subfolder(page):
    """Navigate straight to the EVCharge / Competitive Events subfolder and
    confirm it's a folder view we can create workbooks in. Raises otherwise —
    never falls back to another folder."""
    last_err = None
    for _ in range(4):
        try:
            try:
                page.goto(SUBFOLDER_URL, wait_until="domcontentloaded",
                          timeout=45000)
            except Exception:
                pass
            page.get_by_test_id("create-new").wait_for(timeout=20000)
            if EVCHARGE_SUBFOLDER_ID not in page.url:
                raise clay_ui.ClayUIError(
                    f"landed outside the target subfolder: {page.url}")
            humanize.dwell(0.8, 1.5)
            return
        except Exception as e:
            last_err = e
            humanize.dwell(1.5, 3.0)
    raise clay_ui.ClayUIError(
        f"Could not open the EVCharge/Competitive Events subfolder: {last_err}")


def list_workbooks(page):
    """{workbook_id: name} for the open subfolder. Unlike clay_ui's version this
    tolerates a genuinely empty folder (this one starts empty), but still scrolls
    the virtualized listing so names below the fold aren't read as absent."""
    page.wait_for_timeout(3500)  # let the listing hydrate (may be empty)
    try:
        box = page.get_by_role("cell").first.bounding_box(timeout=5000)
        if box:
            page.mouse.move(box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2)
    except Exception:
        pass  # empty folder: no cells to hover
    found, stable = {}, 0
    while stable < 3:
        before = len(found)
        found.update(page.evaluate(_ROWS_JS))
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1000)
        found.update(page.evaluate(_ROWS_JS))
        stable = stable + 1 if len(found) == before else 0
    return found


def new_context(p, show):
    browser = p.chromium.launch(
        headless=not show, slow_mo=500 if show else 0,
        args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(storage_state=SESSION_PATH, user_agent=UA,
                              viewport={"width": 1600, "height": 900})
    ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return browser, ctx


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="do the work (default: dry run)")
    ap.add_argument("--list", action="store_true",
                    help="just print the live subfolder listing and exit")
    ap.add_argument("--only", action="append", metavar="FOLDER",
                    help="restrict to this scraper folder (repeatable)")
    ap.add_argument("--limit", type=int,
                    help="process at most N pending folders this run")
    ap.add_argument("--show", action="store_true",
                    help="visible, slowed browser (debugging)")
    ap.add_argument("--csv-name", default=CSV_NAME,
                    help="normalized CSV to import; the table takes its stem "
                         "(default exhibitors_normalized.csv)")
    args = ap.parse_args()
    set_csv(args.csv_name)

    if not os.path.exists(SESSION_PATH):
        print(f"No Clay session at {SESSION_PATH} — run clay_login.py")
        sys.exit(1)

    targets = discover()
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {f for f, _ in targets}
        if unknown:
            print(f"No {CSV_NAME} in: {', '.join(sorted(unknown))}")
            sys.exit(1)
        targets = [t for t in targets if t[0] in wanted]

    state = load_state()
    pending = [(f, p) for f, p in targets if f not in state]

    if args.list:
        with sync_playwright() as p:
            browser, ctx = new_context(p, args.show)
            page = ctx.new_page()
            open_subfolder(page)
            live = list_workbooks(page)
            browser.close()
        print(f"{len(live)} workbook(s) in EVCharge / Competitive Events:")
        for wid, name in sorted(live.items(), key=lambda kv: kv[1]):
            print(f"  {name}   [{wid}]")
        return

    print(f"\nTarget : 10. EVCharge [2026 - Qasim] / Competitive Events")
    print(f"Source : {SCRAPERS_ROOT}\\<folder>\\{CSV_NAME}")
    print(f"Table  : {TABLE_NAME}")
    print(f"{len(targets)} folder(s) with {CSV_NAME}; "
          f"{len(pending)} not yet imported per local state.\n")
    todo = pending[:args.limit] if args.limit else pending
    # Planned action per folder. Live existence is re-checked at apply time (the
    # listing is authoritative); this is just an honest preview.
    known = set()
    ids_path = os.path.join(LOG_DIR, "wb_ids.json")
    if os.path.exists(ids_path):
        with open(ids_path, encoding="utf-8") as fh:
            known = set(json.load(fh).values())
    for folder, _ in todo:
        print(f"  [{'add table' if folder in known else 'create wb'}] {folder}")
    if not args.apply:
        print("\nDRY RUN — nothing changed. Re-run with --apply.")
        return
    if not todo:
        print("Nothing to do.")
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    created, skipped, failed = [], [], []

    with sync_playwright() as p:
        browser, ctx = new_context(p, args.show)
        probe = ctx.new_page()
        if not clay_ui.is_logged_in(probe):
            browser.close()
            print("Clay session expired — run clay_login.py")
            sys.exit(1)
        probe.close()

        first = True
        for folder, csv_path in todo:
            if not first:
                humanize.pace()
            first = False
            # Fresh page per folder so one stuck import can't cascade.
            page = ctx.new_page()
            try:
                open_subfolder(page)
                live = list_workbooks(page)
                if folder in set(live.values()):
                    # Workbook exists (e.g. the exhibitors run made it): add this
                    # CSV as another table rather than creating a duplicate.
                    # Open it BY ID, not by clicking its row: the listing is
                    # virtualized and, once the folder holds ~50+ workbooks,
                    # scroll-and-click times out (it did for 14 events here).
                    wid = next(i for i, n in live.items() if n == folder)
                    clay_ui.open_workbook_by_id(page, wid)
                    if TABLE_NAME in clay_ui.existing_tables(page, [TABLE_NAME]):
                        print(f"  SKIP {folder}: table {TABLE_NAME!r} already present")
                        outcome = "skipped-existing"
                        skipped.append(folder)
                    else:
                        clay_ui.add_csv_table(page, csv_path)
                        print(f"  ADDED {folder}: table {TABLE_NAME!r} "
                              f"to existing workbook")
                        outcome = "added-table"
                        created.append(folder)
                else:
                    open_subfolder(page)
                    clay_ui.create_workbook_with_csvs(page, folder, [csv_path])
                    print(f"  CREATED {folder}  ({TABLE_NAME})")
                    outcome = "created"
                    created.append(folder)
                state[folder] = {
                    "table": TABLE_NAME,
                    "csv": os.path.relpath(csv_path, SCRAPERS_ROOT).replace(os.sep, "/"),
                    "imported_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "status": outcome,
                }
                save_state(state)
            except Exception as e:
                print(f"  FAILED {folder}: {e}")
                failed.append((folder, str(e)))
                try:
                    page.screenshot(path=os.path.join(
                        LOG_DIR, f"fail_{folder.replace(os.sep, '_')[:60]}.png"))
                except Exception:
                    pass
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        browser.close()

    print("\n" + "=" * 60)
    print(f"  Created : {len(created)}")
    for f in created:
        print(f"      + {f}")
    print(f"  Existing: {len(skipped)}")
    for f in skipped:
        print(f"      = {f}")
    print(f"  Failed  : {len(failed)}")
    for f, err in failed:
        print(f"      ! {f}: {err}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
