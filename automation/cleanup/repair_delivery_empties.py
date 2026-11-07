"""Delete delivery tables that landed EMPTY, so the importer can rebuild them.

Generic where `repair_ce_delivery.py` is a hard-coded one-off: the targets come
from comparing the plan against what Clay actually holds, so this works for any
delivery and needs no editing when the next table lands short.

WHAT IT DELETES, and nothing else:

  * a table the plan expects rows for that Clay reports as **0 rows**, and
  * a `Name (2)` / `(3)` duplicate whose base name the plan knows about.

WHY THOSE TABLES EXIST. Clay's Add-table CSV import used to be committed via
the "Continue" button, which advances past the upload: the table was created
with its header and no rows, and the failure was swallowed. Retrying on top of
it made `Sponsors (2)` rather than replacing it. `clay_ui._import_csv` now
commits through the footer, so this script cleans up the damage rather than
racing it — but it stays useful, because a browser that dies mid-import can
still leave a short table behind.

THE SAFETY RULE, unchanged from the CE repair: never delete a table that holds
rows unless it is a numbered duplicate. A table that is merely SHORT (some rows,
but not all) is reported and left alone — deleting it would throw away rows this
script cannot prove are recoverable. Clay has no delete API, so this is
Playwright UI automation via `clay_ui.delete_table`.

    python repair_delivery_empties.py --delivery ps           # report only
    python repair_delivery_empties.py --delivery ps --apply
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import clay_ui           # noqa: E402
import browser_session   # noqa: E402
import state_io          # noqa: E402

ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}

# Same keys/filenames as import_ce_delivery_tables.DELIVERIES.
DELIVERIES = {
    "ce": ("ce_event_tables_plan.json", "ce_delivery_tables_state.json"),
    "ps": ("ps_delivery_tables_plan.json", "ps_delivery_tables_state.json"),
    "bs": ("bs_delivery_tables_plan.json", "bs_delivery_tables_state.json"),
    "os": ("os_delivery_tables_plan.json", "os_delivery_tables_state.json"),
}
DUP = re.compile(r"^(.*) \((\d+)\)$")


def wsl(args):
    r = subprocess.run(["wsl", "bash", "-lc",
                        f"cd /mnt/c/Users/qasim/terrapinn/clay-icp-pipeline/"
                        f"automation/cleanup && {args}"],
                       capture_output=True, env=ENV)
    return r.returncode, r.stdout.decode("utf-8", "replace").strip()


def live_tables(wid):
    """{table name: row count} for one workbook, or None if Clay can't be read.

    None is NOT an empty workbook — telling the two apart is the whole point.
    A failed read that looked like "no tables" would delete nothing here, but
    upstream it is what made the importer create duplicate workbooks.
    """
    code, out = wsl(f"python3 list_workbook_table_rows.py {wid}")
    if code != 0:
        return None
    out_map = {}
    for line in out.splitlines():
        if "|" in line:
            name, rows = line.rsplit("|", 1)
            out_map[name.strip()] = int(rows) if rows.strip().isdigit() else None
    return out_map


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delivery", default="ce", choices=sorted(DELIVERIES))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", action="append", default=[],
                    help="limit to these events/categories")
    # As in import_ce_delivery_tables.py (2026-10-21): a delivery outside this repo
    # (Mining, 2026-10-26) passes its own plan + state instead of a --delivery key.
    ap.add_argument("--plan", help="plan JSON; overrides --delivery's")
    ap.add_argument("--state", help="state JSON; overrides --delivery's")
    ap.add_argument("--folder-id", help="delivery folder: also find workbooks by name inside it, for one "
                                        "killed before any table was confirmed (so not in the state)")
    args = ap.parse_args()

    plan_file, state_file = (os.path.join(SCRIPT_DIR, f)
                             for f in DELIVERIES[args.delivery])
    plan_file, state_file = args.plan or plan_file, args.state or state_file
    if not os.path.exists(plan_file):
        raise SystemExit(f"no plan at {plan_file}")
    plan = json.load(open(plan_file, encoding="utf-8"))
    state = state_io.load_json(state_file)

    want = {}
    for e in plan:
        if args.only and e["event"] not in args.only:
            continue
        want.setdefault(e["event"], {})[e["table"]] = e["rows"]

    # Workbook ids come from the state file: the CLI has no folder concept, and
    # delivery workbooks share names with the live working copies.
    wb = {}
    for k, v in state.items():
        if v.get("workbook_id"):
            wb.setdefault(k.split("||")[0], v["workbook_id"])
    if args.folder_id:
        # Inside the hand-made delivery folder a name can only be a delivery workbook, so the
        # name-clash with the live working copies (above) does not arise here.
        code, out = wsl(f"clay folders list {args.folder_id}")
        if code == 0:
            for item in json.loads(out).get("data", []):
                if item.get("resourceType") == "WORKBOOK":
                    wb.setdefault(item["name"], item["id"])

    todo, short, unreadable = [], [], []
    for event in sorted(want):
        wid = wb.get(event)
        if not wid:
            continue
        live = live_tables(wid)
        if live is None:
            unreadable.append(event)
            print(f"!! could not read {event!r} — skipping rather than "
                  f"deleting blind")
            continue
        for name, rows in live.items():
            m = DUP.match(name)
            base = m.group(1) if m else name
            if base not in want[event]:
                continue                     # not a table the plan asked for
            expect = want[event][base]
            if m:                            # a numbered duplicate
                todo.append((event, wid, name, rows, base,
                             "duplicate left by a retry"))
            elif not rows and expect:
                todo.append((event, wid, name, rows, base,
                             f"empty, expected {expect}"))
            elif rows and rows != expect:
                short.append((event, name, expect, rows))

    for event, _wid, name, rows, _base, why in todo:
        print(f"   {event[:30]:32} {name:22} {str(rows):>6} rows   {why}")
    for event, name, expect, rows in short:
        print(f"!! {event[:30]:32} {name:22} has {rows} of {expect} rows — "
              f"SHORT, not empty; left alone (delete it by hand if you want it "
              f"rebuilt)")

    if not todo:
        print("\nnothing to repair")
        return 1 if (short or unreadable) else 0
    if not args.apply:
        print(f"\n{len(todo)} table(s) would be deleted. Add --apply.")
        return 0

    with browser_session.clay_page(headless=True) as page:
        for attempt in range(3):
            if clay_ui.is_logged_in(page):
                break
            page.wait_for_timeout(5000)
        else:
            raise SystemExit("not logged in to Clay after 3 probes")
        for event, wid, name, _rows, base, _why in todo:
            print(f"\n{event} — deleting {name!r}", flush=True)
            try:
                clay_ui.open_workbook_by_id(page, wid)
                clay_ui.delete_table(page, name)
            except Exception as e:
                print(f"   !! {type(e).__name__}: {str(e)[:90]} — leaving it")
                continue
            after = live_tables(wid) or {}
            if name in after:
                print(f"   !! {name} still present — leaving state alone")
                continue
            print("   deleted")
            # Clear the base entry so the importer rebuilds it next pass.
            state.pop(f"{event}||{base}", None)
            state_io.save_json(state_file, state)

    print(f"\nrepair done — re-run the importer for {args.delivery}, then verify")
    return 0


if __name__ == "__main__":
    sys.exit(main())
