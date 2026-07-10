"""
Sync scraper output CSVs into Clay.

Walks every scraper folder under the scrapers root and, for each one that has
any of the four standard CSVs (Exhibitors.csv, Speakers.csv, Sponsors.csv,
Attendees.csv), creates one Clay workbook and imports all of them together:

  workbook name = folder name       (e.g. "ACHEMA")
  table names   = the CSV filenames (Clay auto-names each table, e.g.
                  Exhibitors.csv -> "Exhibitors")

Everything is created inside  Labs [2026 - Qasim] -> Competitive Events  and
nothing outside it is ever touched. Any CSV whose name isn't one of the four
standard names is ignored.

Per event folder:
  workbook missing        -> create it and import all its standard CSVs
  workbook exists, changed -> flagged as "update needed" (replace not yet
                              implemented) and left untouched
  workbook exists, same    -> skip

Usage:
  python clay_sync.py                       # dry run — report only, no changes
  python clay_sync.py --apply               # create missing workbooks
  python clay_sync.py --apply --folder NAME # restrict to a single event folder
  python clay_sync.py --apply --force       # re-evaluate ignoring saved state
  python clay_sync.py --apply --show        # visible, slowed browser (debugging)

Requires a saved session (run `python clay_login.py` first).
"""

import argparse
import datetime
import os
import sys

from playwright.sync_api import sync_playwright

import clay_state
import clay_ui
import humanize

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# The scrapers root (with the event folders) is the parent of this folder.
SCRAPERS_ROOT = os.path.dirname(SCRIPT_DIR)
SESSION_PATH = os.path.join(SCRIPT_DIR, ".clay_session.json")

SKIP_DIRS = {".claude", "__pycache__", ".git", "node_modules", ".venv", "venv",
             os.path.basename(SCRIPT_DIR)}

# Folders that repeatedly fail to import through the UI (import commits but
# Clay never renders data). Retrying these created duplicate workbooks, so the
# tool now leaves them alone entirely — add them to Clay manually.
MANUAL_FOLDERS = {
    "MEDICA",
    "Medtech Japan",
    "Pittcon",
    "SLAS Europe",
    "SLAS2026",
    "WHX Dubai",
    "World Health Expo Lagos",
}

# Only these exact filenames are synced. Table name = key.
STANDARD_CSVS = {
    "Exhibitors": "Exhibitors.csv",
    "Speakers": "Speakers.csv",
    "Sponsors": "Sponsors.csv",
    "Attendees": "Attendees.csv",
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def discover(only_folder=None):
    """Return {folder_name: [(table_name, csv_path), ...]} for every event
    folder that has at least one standard CSV. Non-standard CSV names are
    ignored. Tables are ordered Exhibitors/Speakers/Sponsors/Attendees."""
    events = {}
    for dirpath, dirnames, filenames in os.walk(SCRAPERS_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if os.path.abspath(dirpath) == SCRAPERS_ROOT:
            continue  # root itself holds no event CSVs
        folder = os.path.basename(dirpath)
        if only_folder and folder != only_folder:
            continue
        present = set(filenames)
        tables = [(t, os.path.join(dirpath, f))
                  for t, f in STANDARD_CSVS.items() if f in present]
        if tables:
            events[folder] = tables
    return events


def sync_normalized(args):
    """Add `<folder>/Exhibitors_normalized.csv` as a new table to the already-
    existing workbook named after that folder. Clay names the table after the
    CSV stem ("Exhibitors_normalized"), so it sits alongside the old-schema
    "Exhibitors" table without touching it — this module has no delete op, so a
    true replace is deliberately out of scope. Idempotent: skips if the table
    is already present. Requires --folder and --apply."""
    if not args.folder:
        print("--normalized requires --folder NAME"); sys.exit(1)
    csv_path = os.path.join(SCRAPERS_ROOT, args.folder, "Exhibitors_normalized.csv")
    if not os.path.exists(csv_path):
        print(f"No Exhibitors_normalized.csv in folder {args.folder!r} "
              f"({csv_path})"); sys.exit(1)
    table = os.path.splitext(os.path.basename(csv_path))[0]  # "Exhibitors_normalized"

    print(f"\nTarget: {clay_ui.TARGET_FOLDER} / {clay_ui.TARGET_SUBFOLDER} / "
          f"{args.folder}")
    print(f"Add table {table!r} from {csv_path}")
    if not args.apply:
        print("\nDRY RUN — nothing was changed. Re-run with --apply.")
        return
    if not os.path.exists(SESSION_PATH):
        print(f"\nNo Clay session found at {SESSION_PATH}.\nRun: python clay_login.py")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.show, slow_mo=500 if args.show else 0,
            args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(storage_state=SESSION_PATH, user_agent=UA,
                                  viewport={"width": 1600, "height": 900})
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        try:
            if not clay_ui.is_logged_in(page):
                print("\nClay session expired or invalid — run: python clay_login.py")
                sys.exit(1)
            clay_ui.open_target_location(page)
            if not clay_ui.workbook_exists(page, args.folder):
                print(f"\nWorkbook {args.folder!r} does not exist — nothing to add to.")
                sys.exit(1)
            clay_ui.open_target_location(page)
            clay_ui.open_workbook(page, args.folder)
            if table in clay_ui.existing_tables(page, [table]):
                print(f"\nTable {table!r} already present — nothing to do.")
                return
            clay_ui.add_csv_table(page, csv_path)
            print(f"\nAdded table {table!r} to workbook {args.folder!r}.")
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="perform the sync (default: dry run, report only)")
    parser.add_argument("--folder", metavar="NAME",
                        help="restrict to a single scraper folder")
    parser.add_argument("--force", action="store_true",
                        help="re-evaluate every folder, ignoring saved state")
    parser.add_argument("--show", action="store_true",
                        help="run with a visible, slowed-down browser (for debugging selectors)")
    parser.add_argument("--normalized", action="store_true",
                        help="add <folder>/Exhibitors_normalized.csv as a new table to an "
                             "existing workbook (requires --folder --apply). Non-destructive: "
                             "leaves the old Exhibitors table in place.")
    args = parser.parse_args()

    if args.normalized:
        sync_normalized(args)
        return

    state = clay_state.load_state()
    events = discover(only_folder=args.folder)

    if not events:
        where = f" in folder {args.folder!r}" if args.folder else ""
        print(f"No standard CSVs found{where}.")
        return

    # Decide a per-folder action from state alone (no Clay calls yet).
    # Live workbook existence is re-checked during --apply.
    planned = []  # (folder, tables, action, sigs) ; sigs: {path: (sha, mtime)}
    for folder in sorted(events):
        tables = events[folder]
        sigs, any_new, any_changed = {}, False, False
        for _, path in tables:
            changed, sha, mtime = clay_state.needs_sync(path, state)
            sigs[path] = (sha, mtime)
            if clay_state.rel_key(path) not in state:
                any_new = True
            elif changed:
                any_changed = True

        seen_before = any(clay_state.rel_key(p) in state for _, p in tables)
        if folder in MANUAL_FOLDERS:
            action = "manual"          # known-broken import — add to Clay by hand
        elif args.force or not seen_before:
            action = "create"          # workbook expected to be missing
        elif any_new or any_changed:
            action = "update"          # exists but CSVs changed — not yet supported
        else:
            action = "skip"
        planned.append((folder, tables, action, sigs))

    creates = [p for p in planned if p[2] == "create"]
    updates = [p for p in planned if p[2] == "update"]
    manuals = [p for p in planned if p[2] == "manual"]

    print(f"\nTarget: {clay_ui.TARGET_FOLDER} / {clay_ui.TARGET_SUBFOLDER}")
    print(f"Scanned {len(events)} event folder(s): "
          f"{len(creates)} to create, {len(updates)} changed (update not yet "
          f"supported), {len(manuals)} manual-only, "
          f"{len(planned) - len(creates) - len(updates) - len(manuals)} unchanged.\n")
    for folder, tables, action, _ in planned:
        names = ", ".join(t for t, _ in tables)
        print(f"  [{action:>6}] {folder}  ({names})")

    if updates:
        print("\nNote: 'update' folders already have a workbook but their CSVs "
              "changed. Replacing existing tables isn't implemented yet, so "
              "they are left untouched.")
    if manuals:
        print("\nNote: 'manual' folders are known to fail Clay's CSV import "
              "and are never synced — import them by hand.")

    if not args.apply:
        print("\nDRY RUN — nothing was changed. Re-run with --apply to sync.")
        return

    if not creates:
        print("\nNothing to create. (Use codegen to add the replace flow for "
              "changed folders.)")
        return

    if not os.path.exists(SESSION_PATH):
        print(f"\nNo Clay session found at {SESSION_PATH}.")
        print("Run: python clay_login.py")
        sys.exit(1)

    created, completed, skipped_exists, failed = [], [], [], []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.show,
            slow_mo=500 if args.show else 0,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(storage_state=SESSION_PATH, user_agent=UA,
                                  viewport={"width": 1600, "height": 900})
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = ctx.new_page()

        if not clay_ui.is_logged_in(page):
            browser.close()
            print("\nClay session expired or invalid — run: python clay_login.py")
            sys.exit(1)
        page.close()

        first = True
        for folder, tables, action, sigs in creates:
            if not first:
                humanize.pace()  # human gap before the next workbook
            first = False
            # Give every folder a fresh page. A stuck/slow import (e.g. a very
            # large CSV) can wedge the page; without this, the failure cascades
            # to every subsequent folder in the run.
            page = ctx.new_page()
            try:
                # Re-open the subfolder each time (creating a workbook navigates
                # into it) and reconcile live against Clay, so a partial prior
                # run converges instead of skipping incomplete workbooks or
                # creating duplicates.
                clay_ui.open_target_location(page)
                if clay_ui.workbook_exists(page, folder):
                    # Workbook already there — add only whichever tables are
                    # missing (self-heals partial/interrupted earlier runs).
                    # The existence check scrolled the (virtualized) listing,
                    # so re-open the folder before clicking the workbook cell.
                    clay_ui.open_target_location(page)
                    clay_ui.open_workbook(page, folder)
                    have = clay_ui.existing_tables(page, [t for t, _ in tables])
                    missing = [(t, p) for t, p in tables if t not in have]
                    for _, p in missing:
                        clay_ui.add_csv_table(page, p)
                    if missing:
                        completed.append((folder, [t for t, _ in missing]))
                    else:
                        skipped_exists.append(folder)
                else:
                    clay_ui.create_workbook_with_csvs(
                        page, folder, [path for _, path in tables])
                    created.append((folder, [t for t, _ in tables]))

                # Persist state per CSV only after the workbook is complete.
                now = datetime.datetime.now().isoformat(timespec="seconds")
                for table, path in tables:
                    sha, mtime = sigs[path]
                    state[clay_state.rel_key(path)] = {
                        "sha256": sha, "mtime": mtime,
                        "workbook": folder, "table": table, "last_synced": now,
                    }
                clay_state.save_state(state)
            except Exception as e:
                failed.append((folder, str(e)))
                print(f"  FAILED {folder}: {e}")
            finally:
                try:
                    page.close()
                except Exception:
                    pass

        browser.close()

    print("\n" + "=" * 60)
    print("SYNC SUMMARY")
    print("=" * 60)
    print(f"  Created workbooks        : {len(created)}")
    for f, ts in created:
        print(f"      + {f}  ({', '.join(ts)})")
    print(f"  Added tables to existing : {len(completed)}")
    for f, ts in completed:
        print(f"      ~ {f}  (+{', '.join(ts)})")
    print(f"  Already complete, skipped: {len(skipped_exists)}")
    for f in skipped_exists:
        print(f"      = {f}")
    print(f"  Failed                  : {len(failed)}")
    for f, err in failed:
        print(f"      ! {f}: {err}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
