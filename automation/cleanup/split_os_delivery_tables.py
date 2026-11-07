"""Split the Other Sources deliverable into one workbook's worth of CSVs.

    os_delivery_tables/Other Sources/Companies.csv
    os_delivery_tables/Other Sources/Buyside People.csv
    os_delivery_tables/Other Sources/Sellside People.csv

**THE FILENAME IS THE TABLE NAME**, as with the other two deliveries — Clay
names an imported table after the CSV stem and renaming afterwards is manual
UI work.

ONE WORKBOOK, NOT MANY (per the user, 2026-09-16). Competitive Events splits
per event and P&S splits per category because those scopes have a natural unit.
Other Sources does not: it is a single deduped table, and its `Source Name` is a
SEMICOLON-JOINED LIST of every directory a company was found in ("ADEPT Member
Directory; LCRIG Member Directory"), which is why there are 77 distinct values
over 3,047 companies and why splitting on it would produce neither clean names
nor disjoint groups. The people carry the same combined string in
`Company Event Name`.

The people split Buy/Sell on the `Side` column, matching the CE workbooks.

COMPANIES ARE GATED ON `Is New`. The people were searched only from companies
the Supabase ledger returned `Is New` for, so shipping all 3,047 would pair
people with companies that were deliberately excluded from the search — and the
82-row leak that gate once had is exactly why it is applied per row here rather
than assumed from the join. `--all-companies` overrides it if that is ever
wanted, and the report prints both numbers so the difference is visible.

    python3 split_os_delivery_tables.py --report
    python3 split_os_delivery_tables.py
"""

import argparse
import collections
import csv
import io
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

EXPORTS = os.path.join(SCRIPT_DIR, "ce_classification_exports")
PEOPLE = os.path.join(EXPORTS, "Highways Other Sources - people.csv")
COMPANIES = os.path.join(SCRIPT_DIR, "other_sources_rows.json")
DEST = os.path.join(SCRIPT_DIR, "os_delivery_tables")
PLAN = os.path.join(SCRIPT_DIR, "os_delivery_tables_plan.json")

WORKBOOK = "Other Sources"
# Tab order inside the workbook; the CE workbooks read companies-then-people.
SIDES = (("Buy Side", "Buyside People"), ("Sell Side", "Sellside People"))

# Clay's own bookkeeping columns on the source table — they describe the
# enrichment run, not the company, and mean nothing to a reviewer.
DROP_COLUMNS = {"Enrich Company", "HTTP API"}


def write_csv(path, rows, header):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--all-companies", action="store_true",
                    help="ship every company, not just the Is New ones")
    ap.add_argument("--v7", action="store_true",
                    help="the 2026-10-19 re-run (workflow v7, countries per "
                         "source, full profiles, seniority split): people from "
                         "'... - people - v7 20.csv', files to "
                         "os_delivery_tables_v7/ with their own plan")
    args = ap.parse_args()

    global PEOPLE, DEST, PLAN
    if args.v7:
        PEOPLE = os.path.join(EXPORTS, "Highways Other Sources - people - v7 20.csv")
        DEST = os.path.join(SCRIPT_DIR, "os_delivery_tables_v7")
        PLAN = os.path.join(SCRIPT_DIR, "os_v7_delivery_tables_plan.json")

    comps = json.load(open(COMPANIES, encoding="utf-8"))
    is_new = [c for c in comps if str(c.get("Is New")) == "True"]
    keep = comps if args.all_companies else is_new

    with open(PEOPLE, encoding="utf-8-sig", newline="") as fh:
        rdr = csv.DictReader(fh)
        pheader = [c for c in (rdr.fieldnames or [])]
        people = list(rdr)
    by_side = collections.Counter(p.get("Side") or "(blank)" for p in people)

    print(f"companies total   : {len(comps):,}")
    print(f"companies Is New  : {len(is_new):,}   <- shipped"
          f"{'  (overridden: shipping all)' if args.all_companies else ''}")
    print(f"people            : {len(people):,}")
    for side, table in SIDES:
        print(f"   {table:20} {by_side.get(side, 0):>6,}")
    stray = {s: n for s, n in by_side.items()
             if s not in {k for k, _ in SIDES}}
    if stray:
        # Never silently drop a person because their Side is spelled oddly.
        raise SystemExit(f"!! people carry unexpected Side values {stray} — "
                         f"refusing to split and lose them")
    if args.report:
        return 0

    d = os.path.join(DEST, WORKBOOK)
    os.makedirs(d, exist_ok=True)
    plan = []

    cheader = [k for k in dict.fromkeys(k for r in keep for k in r)
               if k not in DROP_COLUMNS]
    p = os.path.join(d, "Companies.csv")
    write_csv(p, keep, cheader)
    plan.append({"event": WORKBOOK, "table": "Companies", "kind": "companies",
                 "csv": p, "rows": len(keep)})

    wrote = 0
    for side, table in SIDES:
        rows = [x for x in people if x.get("Side") == side]
        if not rows:
            continue
        p = os.path.join(d, f"{table}.csv")
        write_csv(p, rows, pheader)
        plan.append({"event": WORKBOOK, "table": table, "kind": "people",
                     "csv": p, "rows": len(rows)})
        wrote += len(rows)

    if wrote != len(people):
        raise SystemExit(f"!! wrote {wrote:,} people but the deliverable has "
                         f"{len(people):,} — refusing a lossy split")
    json.dump(plan, open(PLAN, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\n{len(plan)} tables in 1 workbook")
    print(f"-> {DEST}\nplan -> {PLAN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
