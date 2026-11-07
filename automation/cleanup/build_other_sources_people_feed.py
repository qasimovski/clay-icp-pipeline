"""Build the people feed for Other Sources from the classification results.

Joins three things on `Domain`:

  * the company row from `ALL_SOURCES_DEDUPED` (cached by other_sources_classify),
  * the Registrar's `Side` + `Classification`,
  * the countries of the directory the company came from (other_sources_config).

Only rows that resolve to a persona AND a country list are written, so a company
that cannot be searched is absent from the file rather than filtered out later —
skip-by-absence, the same guard `build_ce_people_feed.py` applies.

WHAT MAKES THIS DIFFERENT FROM THE CE FEED: the `Countries Override` column.
Other Sources takes its geography from the SOURCE directory rather than the ICP
category (user, 2026-09-14) — a CECA member is a UK contractor, so its people
should be UK, not the 13 countries `Construction Materials` carries. The people
workflow's `ICP Mapping` node uses the override when it is non-empty and falls
back to the ICP list when it is blank, so the CE and P&S feeds are unaffected.

`Company Event Name` carries the Source Name and `Exhibitor/Sponsor` carries the
literal "Other Sources": both are provenance columns on the shared trigger, and
renaming them would mean reshaping a live workflow that CE also runs through.

    python3 build_other_sources_people_feed.py
    python3 build_other_sources_people_feed.py --report   # coverage, write nothing
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

import ce_people_mapping as M        # noqa: E402
import other_sources_config as OSC   # noqa: E402

ROWS = os.path.join(SCRIPT_DIR, "other_sources_rows.json")
RESULT_DIR = os.path.join(SCRIPT_DIR, "other_sources_results")
OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
OUT = os.path.join(OUT_DIR, "Highways Other Sources - people feed.csv")

COLUMNS = ("Company Name", "Domain", "Side", "Classification",
           "Company Event Name", "Exhibitor/Sponsor", "Countries Override",
           "Category", "Country", "Website")


def classifications():
    """{domain: (side, classification)} from every result file on disk."""
    out = {}
    if not os.path.isdir(RESULT_DIR):
        raise SystemExit(f"no results at {RESULT_DIR} — run other_sources_classify.py")
    for name in sorted(os.listdir(RESULT_DIR)):
        if not name.endswith(".jsonl"):
            continue
        for line in open(os.path.join(RESULT_DIR, name), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("status") != "complete":
                continue
            so = ((rec.get("result") or {}).get("structuredOutputs") or {})
            out[rec["id"]] = (so.get("Side") or "", so.get("Classification") or "")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    rows = json.load(open(ROWS, encoding="utf-8"))
    cls = classifications()
    idx = OSC.index(OSC.load())
    loaded = M._load()

    keep, skipped = [], collections.Counter()
    combos = collections.Counter()
    seen_domain = {}
    for r in rows:
        dom = str(r.get("Domain", "")).strip()
        # THE LEDGER GATE, re-applied on the ROW and not on the domain. 194
        # domains appear on more than one row of this table (subsidiaries and
        # renames sharing a website), and 82 rows that are NOT `Is New` share a
        # domain with one that is. Joining on domain alone let those 82 into the
        # feed — companies the ledger has already claimed, which is exactly the
        # re-spend the gate exists to stop.
        if str(r.get("Is New", "")).strip().lower() != "true":
            skipped["not Is New (ledger already claimed it)"] += 1
            continue
        if not dom or dom not in cls:
            skipped["no Domain, or not classified"] += 1
            continue
        # One search per domain. The people search keys on the domain, so a
        # second row with the same one is a second charge for the same company.
        if dom in seen_domain:
            skipped[f"duplicate domain (first seen as {seen_domain[dom][:28]})"] += 1
            continue
        side, cat = cls[dom]
        if not side:
            skipped["classifier returned nothing"] += 1
            continue
        try:
            M.resolve(side, cat, loaded)
        except M.Unmapped as e:
            skipped[f"unmapped: {str(e)[:56]}"] += 1
            continue
        try:
            ctys = OSC.countries_for(r.get("Source Name", ""), idx)
        except OSC.UnknownSource as e:
            skipped[f"source has no countries: {e}"] += 1
            continue
        keep.append({
            "Company Name": r.get("Company Name", ""),
            "Domain": dom,
            "Side": side,
            "Classification": cat,
            "Company Event Name": r.get("Source Name", ""),
            "Exhibitor/Sponsor": "Other Sources",
            "Countries Override": ", ".join(ctys),
            "Category": r.get("Category", ""),
            "Country": r.get("Country Code", ""),
            # Carried so the deliverable's Website column is not 100% blank —
            # export_ce_people expects it and Other Sources has it on the table.
            "Website": r.get("Primary Website", "") or r.get("Website", ""),
        })
        combos[(side, cat, ", ".join(ctys))] += 1
        seen_domain[dom] = r.get("Company Name", "")

    print(f"table rows      : {len(rows):,}")
    print(f"  SEARCHABLE    : {len(keep):,}")
    print(f"  left out      : {sum(skipped.values()):,}")
    for why, n in skipped.most_common(8):
        print(f"      {n:5}  {why}")

    print(f"\nsearch configurations: {len(combos)}")
    for (side, cat, ctys), n in combos.most_common(14):
        print(f"   {n:4}  {side:10} {cat[:40]:42} {ctys}")

    # Every row must carry a Domain and a country list, or the search would run
    # unscoped. Assert rather than assume.
    bad = [k for k in keep if not k["Domain"].strip()
           or not k["Countries Override"].strip()]
    if bad:
        raise SystemExit(f"!! {len(bad)} rows missing Domain or countries — "
                         f"refusing to ship a feed that searches unscoped")

    if args.report:
        print("\n--report: nothing written")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        w.writeheader()
        w.writerows(keep)
    print(f"\n{len(keep):,} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
