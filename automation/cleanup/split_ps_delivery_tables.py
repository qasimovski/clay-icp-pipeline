"""Split the sell-side P&S deliverables into one CSV per Clay table.

    ps_delivery_tables/<category>/Companies.csv
    ps_delivery_tables/<category>/People.csv

One workbook per CATEGORY, two tables inside. Unlike Competitive Events there is
no buy/sell split within a workbook: every company in sell-side P&S is Sell Side
by construction, which is the whole reason the classifier is not run on this
scope (docs/WORKFLOWS_ROUTE.md §8).

**THE FILENAME IS THE TABLE NAME**, as with the CE delivery — Clay names an
imported table after the CSV stem and renaming afterwards is manual UI work.

COMPANIES COME FROM CLAY, PEOPLE FROM THE DELIVERABLE. The people were exported
and filtered long ago (`Highways P&S - people.csv`, already country-filtered and
deduped); the companies only exist as Clay tables, so they are read over the CLI
and cached per category. A category whose read fails is left out of the plan
rather than shipped short — `--report` shows what is still missing.

`Tolling & Road User Charging` has companies but NO people: its Companies view
is 0/258 after the `Is New` filter because every one of those firms was already
recorded under ITS, Parking Technology or Transport Operations. It gets a
Companies table and no People table, which is correct, not an omission.

    python3 split_ps_delivery_tables.py --report     # what would be written
    python3 split_ps_delivery_tables.py              # read, cache, write
"""

import argparse
import collections
import csv
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import clay_bin                      # noqa: E402
import export_ps_people as E         # noqa: E402  (also wraps stdout for UTF-8)

OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")

# Sell side and buy side are the same split over different inputs, so they are
# one script with a scope switch rather than two files that drift apart. The
# key names match `import_ce_delivery_tables.DELIVERIES`, and the plan filename
# each writes is exactly what that importer reads for the same key.
SCOPES = {
    "ps": {"people": "Highways P&S - people.csv",
           "manifest": "highways_ps_companies_manifest.json",
           "dest": "ps_delivery_tables",
           "plan": "ps_delivery_tables_plan.json",
           "cache": "ps_delivery_companies"},
    "bs": {"people": "Highways BuySide P&S - people.csv",
           "manifest": "highways_bs_companies_manifest.json",
           "dest": "bs_delivery_tables",
           "plan": "bs_delivery_tables_plan.json",
           "cache": "bs_delivery_companies"},
}
PEOPLE = MANIFEST = DEST = PLAN = CACHE = None


def use_scope(name):
    global PEOPLE, MANIFEST, DEST, PLAN, CACHE
    s = SCOPES[name]
    PEOPLE = os.path.join(OUT_DIR, s["people"])
    MANIFEST = os.path.join(SCRIPT_DIR, s["manifest"])
    DEST = os.path.join(SCRIPT_DIR, s["dest"])
    PLAN = os.path.join(SCRIPT_DIR, s["plan"])
    # Each scope caches under its own key: the two sides have DIFFERENT
    # Companies tables for identically-named categories (Finance, Investment,
    # Legal exists on both), so a shared cache would serve one side's rows to
    # the other.
    CACHE = os.path.join(SCRIPT_DIR, "ps_people_cache", s["cache"])


use_scope("ps")

# Order is the tab order inside the workbook.
ORDER = ("Companies", "People")


def safe_dir(name):
    import re
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip().rstrip(".")


def companies_for(binary, category, tid):
    """That category's Companies rows, cached so a re-run costs no Clay calls."""
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, safe_dir(category) + ".jsonl")
    if os.path.exists(cp):
        return [json.loads(l) for l in open(cp, encoding="utf-8") if l.strip()]
    rows = E.read_table(binary, tid)
    tmp = cp + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, cp)      # atomic: a half-written cache must not read back
    return rows


def write_csv(path, rows, header):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, help="read at most N uncached categories")
    ap.add_argument("--scope", default="ps", choices=sorted(SCOPES),
                    help="ps = sell-side Products & Services, bs = BuySide P&S")
    args = ap.parse_args()
    use_scope(args.scope)
    print(f"scope: {args.scope}  ->  {os.path.basename(PLAN)}")

    man = json.load(open(MANIFEST, encoding="utf-8"))
    with open(PEOPLE, encoding="utf-8-sig", newline="") as fh:
        rdr = csv.DictReader(fh)
        pheader = list(rdr.fieldnames or [])
        people = collections.defaultdict(list)
        for r in rdr:
            people[r["Category"]].append(r)

    print(f"categories in the manifest : {len(man)}")
    print(f"categories with people     : {len(people)}")
    stray = sorted(set(people) - set(man))
    if stray:
        raise SystemExit(f"!! people reference categories absent from the "
                         f"manifest: {stray[:5]}")

    binary = clay_bin.pick()
    uncached = [c for c in man
                if not os.path.exists(os.path.join(CACHE, safe_dir(c) + ".jsonl"))]
    print(f"companies still to read    : {len(uncached)}")
    if args.report:
        for c in sorted(uncached):
            print(f"   {c[:44]:46} {man[c]['rows']:6,} rows")
        return 0

    todo = uncached[:args.limit] if args.limit else uncached
    for c in todo:
        rows = companies_for(binary, c, man[c]["table_id"])
        print(f"   cached {len(rows):6,}  {c[:44]}")

    still = [c for c in man
             if not os.path.exists(os.path.join(CACHE, safe_dir(c) + ".jsonl"))]
    if still:
        print(f"\n{len(still)} category(ies) unread — re-run to continue; "
              f"not writing a partial plan")
        return 1

    plan, n_c, n_p = [], 0, 0
    for c in man:
        d = os.path.join(DEST, safe_dir(c))
        os.makedirs(d, exist_ok=True)
        comps = companies_for(binary, c, man[c]["table_id"])
        if comps:
            cheader = list(dict.fromkeys(k for r in comps for k in r))
            p = os.path.join(d, "Companies.csv")
            write_csv(p, comps, cheader)
            plan.append({"event": c, "table": "Companies", "kind": "companies",
                         "csv": p, "rows": len(comps)})
            n_c += len(comps)
        if people.get(c):
            p = os.path.join(d, "People.csv")
            write_csv(p, people[c], pheader)
            plan.append({"event": c, "table": "People", "kind": "people",
                         "csv": p, "rows": len(people[c])})
            n_p += len(people[c])

    wrote_p = sum(e["rows"] for e in plan if e["kind"] == "people")
    total_p = sum(len(v) for v in people.values())
    if wrote_p != total_p:
        raise SystemExit(f"!! wrote {wrote_p:,} people but the deliverable has "
                         f"{total_p:,} — refusing a lossy split")
    json.dump(plan, open(PLAN, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\n{len(plan)} tables across {len({e['event'] for e in plan})} workbooks")
    print(f"   {n_c:,} company rows + {n_p:,} people rows")
    print(f"-> {DEST}\nplan -> {PLAN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
