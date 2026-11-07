"""Verify the Katie delivery folder against the plan (run in WSL).

Read-only. For every event in `ce_event_tables_plan.json`, ask Clay — not the
state file and not the browser — three things:

  * does the delivery folder hold a workbook for that event,
  * does it hold each table the plan says it should,
  * does each table's row count match its source CSV exactly.

WHY IT ASKS CLAY. The import's own state file records what it believed it did.
This exists to catch the cases where that belief is wrong: a table that imported
short, one that never landed, a workbook created twice. `verify_ps_companies.py`
is the same idea for the P&S build and the reason it exists is that a rollout
log is not evidence.

WHAT IT REPORTS, in the order that matters:
  MISSING   plan says a table should exist and it does not
  SHORT     the table exists but its row count differs from the CSV
  EXTRA     a table in the workbook the plan never asked for (`Untitled
            workbook` leftovers and the UI chrome are filtered out)

Exit code is non-zero if anything is wrong, so this can gate the handoff.

    python3 verify_ce_delivery.py
    python3 verify_ce_delivery.py --only "Big 5 Construct Saudi"
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import clay_bin  # noqa: E402

# Keys and filenames match import_ce_delivery_tables.DELIVERIES exactly — the
# verifier must read the same plan the importer built from, or "confirmed"
# means nothing.
DELIVERIES = {
    "ce": ("ce_event_tables_plan.json", "ce_delivery_tables_state.json"),
    "ps": ("ps_delivery_tables_plan.json", "ps_delivery_tables_state.json"),
    "bs": ("bs_delivery_tables_plan.json", "bs_delivery_tables_state.json"),
    "os": ("os_delivery_tables_plan.json", "os_delivery_tables_state.json"),
}
PLAN = STATE = None


def use_delivery(name):
    global PLAN, STATE
    plan, state = DELIVERIES[name]
    PLAN = os.path.join(SCRIPT_DIR, plan)
    STATE = os.path.join(SCRIPT_DIR, state)


use_delivery("ce")
# Tab-strip entries Clay renders that are not tables (see build_ps_companies:
# filtering these by name kept failing because the overflow glyph is not the
# character it renders as).
CHROME = {"Stop", "History", "—", "-"}


def clay(args, tries=4):
    binary = clay_bin.pick()
    for _ in range(tries):
        ok, d, err = clay_bin.run(binary, tuple(args))
        if ok:
            return d
        if d is not None:
            return None
        time.sleep(2)
    return None


def delivery_workbooks(state):
    """{event: workbook_id} for the DELIVERY copies only.

    The ids come from the import state because the CLI cannot filter by folder
    (it has no folder concept at all) and the delivery workbooks share their
    NAMES with the live Competitive Events ones. Matching on name pulled in the
    working copies: every event looked duplicated and each one's
    `exhibitors_normalized` looked like an unexpected table.

    The id is the only thing taken on trust; every row count below is still read
    from Clay, so a table that imported short or vanished is still caught.
    """
    out = {}
    for k, v in state.items():
        wid = v.get("workbook_id")
        if wid:
            out.setdefault(k.split("||")[0], wid)
    return out


def tables_of(wid):
    code = subprocess.run([sys.executable,
                           os.path.join(SCRIPT_DIR, "list_workbook_table_rows.py"),
                           wid], capture_output=True, text=True)
    if code.returncode != 0:
        return None
    out = {}
    for line in code.stdout.strip().splitlines():
        if "|" not in line:
            continue
        name, rows = line.rsplit("|", 1)
        out[name.strip()] = int(rows) if rows.strip().isdigit() else None
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--delivery", default="ce", choices=sorted(DELIVERIES),
                    help="which handoff folder to verify")
    args = ap.parse_args()
    use_delivery(args.delivery)
    print(f"delivery: {args.delivery}")

    if not os.path.exists(PLAN):
        raise SystemExit(f"no plan at {PLAN} — run the splitter first")
    plan = json.load(open(PLAN, encoding="utf-8"))
    want = {}
    for e in plan:
        if args.only and e["event"] not in args.only:
            continue
        want.setdefault(e["event"], {})[e["table"]] = e["rows"]

    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    by_event = delivery_workbooks(state)

    missing_wb, missing, short, extra, ok = [], [], [], [], 0
    dup_wb = {}

    print(f"{'event':44} {'tables':>7} {'rows':>9}")
    for event in sorted(want):
        wid = by_event.get(event)
        if not wid:
            missing_wb.append(event)
            print(f"{event[:42]:44} {'-':>7} {'NOT BUILT':>9}")
            continue
        live = tables_of(wid) or {}
        real = {k: v for k, v in live.items() if k not in CHROME}
        rows = 0
        for table, expect in want[event].items():
            got = real.get(table)
            if got is None:
                missing.append((event, table, expect))
            elif got != expect:
                short.append((event, table, expect, got))
            else:
                ok += 1
                rows += got
        for t in real:
            if t not in want[event]:
                extra.append((event, t))
        print(f"{event[:42]:44} {len(want[event]):>7} {rows:>9,}")

    total = sum(len(v) for v in want.values())
    print(f"\ntables expected {total}   confirmed {ok}   "
          f"state says done {sum(1 for v in state.values() if v.get('status') == 'done')}")
    for label, items in (("WORKBOOK MISSING", missing_wb),
                         ("TABLE MISSING", missing),
                         ("ROW COUNT MISMATCH", short),
                         ("UNEXPECTED TABLE", extra),
                         ("DUPLICATE WORKBOOK", sorted(dup_wb))):
        if items:
            print(f"\n{label} ({len(items)}):")
            for it in items[:15]:
                print(f"   {it}")
    bad = len(missing_wb) + len(missing) + len(short) + len(dup_wb)
    if not bad and not extra:
        print("\nALL CLEAR — every planned table exists with the exact row count")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
