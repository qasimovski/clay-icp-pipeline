"""Build the CSV that becomes the Clay companies table for the people pass.

WHY A NEW TABLE AT ALL. The people search scopes itself with Clay's own
`clay.filter_to_companies(@table("<table>:<view>:<filter>"))`, so the companies
have to BE a Clay table with a filterable view. Our classification came out of a
Workflow and lives in a CSV — and Workflows cannot write columns back into a
Clay table — so the 67 existing Competitive Events tables have no `Side` or
`Classification` to filter on. Importing the classified rows back is what closes
that loop.

ONE TABLE, NOT ONE PER EVENT. Labs built a people table per event because its
deliverable was per event. Ours is a single combined CSV with
`Company Event Name` carrying provenance, so one table means **one search per
(Side, Classification)** — about 30 in total — instead of that many again for
every one of the 39 events. Row count is ~4.5k, far inside Clay's ~50k ceiling.

ONLY SEARCHABLE ROWS ARE INCLUDED, and that is the point of the file. A row is
searchable when `ce_people_mapping.resolve()` finds it a persona AND a country
list. Everything else is left out by construction rather than filtered out
later:

  * 909 rows classified `All other industries` — inspected 2026-09-12 and 89% of
    them carry no roads/highways/civil language at all (HVAC, cold chain, paint
    brushes, heat pumps, a trade-promotion agency). The catch-all is the
    classifier correctly saying "outside this ICP", not a taxonomy gap. Searching
    them would pay to find marketing directors at refrigeration firms.
  * 33 rows whose label is off the closed list.
  * Every row the ledger already claimed, or that has no `Domain` — both already
    excluded upstream by decision.

Skip-by-absence beats skip-by-rule: a row that is not in this file cannot be
searched by any later mistake.

    python build_ce_people_feed.py                 # write the CSV + report
    python build_ce_people_feed.py --event "Big 5 Construct Saudi"
"""

import argparse
import collections
import csv
import io
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import ce_people_mapping as M  # noqa: E402

OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
COMBINED = os.path.join(OUT_DIR, "Highways CE - all tables combined.csv")

# Kept deliberately narrow. This table exists to be SEARCHED, not to be the
# deliverable — the deliverable is the combined CSV. Company Name and Domain are
# what Clay matches on; Side and Classification are what the view filters slice
# by; the rest is provenance so a person can be traced back to its event and
# table without re-joining.
COLUMNS = ("Company Name", "Domain", "Side", "Classification",
           "Company Event Name", "Exhibitor/Sponsor", "Country", "Website")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=COMBINED)
    ap.add_argument("--event", help="limit to one event (pilot)")
    ap.add_argument("--out")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        raise SystemExit(f"no combined CSV at {args.csv}")
    loaded = M._load()

    rows = [r for r in csv.DictReader(open(args.csv, encoding="utf-8-sig"))]
    if args.event:
        rows = [r for r in rows if r["Company Event Name"] == args.event]

    keep, skipped = [], collections.Counter()
    combos = collections.Counter()
    for r in rows:
        if not r.get("Side", "").strip():
            skipped["not classified (no domain / not new)"] += 1
            continue
        try:
            kind, cat, ctys = M.resolve(r["Side"], r["Classification"], loaded)
        except M.Unmapped as e:
            skipped[f"unmapped: {str(e)[:60]}"] += 1
            continue
        keep.append({c: r.get(c, "") for c in COLUMNS})
        combos[(r["Side"], cat, len(ctys))] += 1

    name = args.out or (f"Highways CE - people feed"
                        + (f" - {args.event}" if args.event else "") + ".csv")
    path = os.path.join(OUT_DIR, name)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        w.writeheader()
        w.writerows(keep)

    scope = f" — {args.event}" if args.event else ""
    print(f"source rows{scope}: {len(rows)}")
    print(f"  SEARCHABLE, written : {len(keep)}")
    print(f"  left out            : {sum(skipped.values())}")
    for why, n in skipped.most_common(8):
        print(f"      {n:5}  {why}")

    print(f"\nsearch configurations in this file: {len(combos)}")
    for (side, cat, nc), n in combos.most_common():
        print(f"   {n:5}  {side:10} {cat[:44]:46} {nc} countries")

    # Every company must carry a Domain — it is what Clay matches on, and the
    # upstream gate already guaranteed it. Assert rather than assume.
    nodom = [r for r in keep if not r["Domain"].strip()]
    if nodom:
        raise SystemExit(f"!! {len(nodom)} rows have no Domain — refusing to "
                         f"ship a feed Clay cannot match on")
    dupes = collections.Counter(r["Domain"].strip().lower() for r in keep)
    rep = sum(1 for v in dupes.values() if v > 1)
    print(f"\nevery row has a Domain: yes")
    print(f"distinct domains       : {len(dupes)} "
          f"({rep} appear more than once — same company across events)")
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
