"""Split the CE deliverables into one CSV per Clay table for the Katie handoff.

    ce_event_tables/<event>/<table name>.csv

Six possible tables per event, written in the order they should appear as tabs:

    Exhibitors.csv                  companies, from the combined deliverable
    Exhibitors Buyside People.csv
    Exhibitors Sellside People.csv
    Sponsors.csv                    companies
    Sponsors Buyside People.csv
    Sponsors Sellside People.csv

**THE FILENAME IS THE TABLE NAME.** Clay names an imported table after the CSV's
stem and that is the only control we have over it, so these carry their final
names. Renaming afterwards is manual UI work on every table. Names per the user
(2026-09-15): `Exhibitors` / `Sponsors` for the companies rather than the
internal `exhibitors_normalized`, which would mean nothing to an external
reviewer.

COMPANIES CARRY EVERY ROW, not just the searchable ones. The people tables only
cover companies that had a Domain, were `Is New` and resolved to a persona;
the companies table is the event's full exhibitor/sponsor list with `Side` and
`Classification` filled in where the Registrar could classify. That is the file
the user already reviewed, column set included.

PEOPLE KEEP ALL COLUMNS, including the four constant within a table
(`Company Event Name`, `Exhibitor/Sponsor`, `Side`, `Outcome`) — the user's
choice, so a table stays self-describing if it is exported and the filename lost.

A COMBINATION WITH NO ROWS GETS NO FILE, so it gets no table: an empty table is
indistinguishable from an import that failed.

    python split_ce_event_tables.py --report
    python split_ce_event_tables.py
"""

import argparse
import collections
import csv
import io
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
COMPANIES = os.path.join(OUT_DIR, "Highways CE - all tables combined.csv")
PEOPLE = os.path.join(OUT_DIR, "Highways CE - people.csv")
DEST = os.path.join(SCRIPT_DIR, "ce_event_tables")
PLAN = os.path.join(SCRIPT_DIR, "ce_event_tables_plan.json")
MANIFESTS = ("highways_ce_exhibitors_manifest.json",
             "highways_ce_sponsors_manifest.json")

COMPANY_TABLE = {"Exhibitor": "Exhibitors", "Sponsor": "Sponsors"}
PEOPLE_TABLE = {
    ("Exhibitor", "Buy Side"): "Exhibitors Buyside People",
    ("Exhibitor", "Sell Side"): "Exhibitors Sellside People",
    ("Sponsor", "Buy Side"): "Sponsors Buyside People",
    ("Sponsor", "Sell Side"): "Sponsors Sellside People",
}
# Tab order inside a workbook — companies first, then that entity's people.
ORDER = ["Exhibitors", "Exhibitors Buyside People", "Exhibitors Sellside People",
         "Sponsors", "Sponsors Buyside People", "Sponsors Sellside People"]


def safe_dir(name):
    """A directory name Windows accepts. `<` and `:` occur in these event names
    and are hard errors, not warnings."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip().rstrip(".")


def events_with_workbooks():
    """Every CE event seen in the manifests. Used only to sanity-check that the
    two deliverables agree on the event list — the delivery folder gets NEW
    workbooks, so the old ids are not reused."""
    out = set()
    for name in MANIFESTS:
        p = os.path.join(SCRIPT_DIR, name)
        if os.path.exists(p):
            out |= set(json.load(open(p, encoding="utf-8")))
    return out


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--fit", action="store_true",
                    help="the post-fit-filter rebuild: companies the scraper "
                         "fit filter DROPPED are left out, people come from "
                         "'Highways CE - people - fit.csv' (or --people), and "
                         "files go to ce_event_tables_fit/ with their own plan")
    ap.add_argument("--people", help="people CSV to split (default per mode)")
    ap.add_argument("--event", action="append", default=[],
                    help="only this Company Event Name (repeatable); the plan "
                         "keeps every other event's entries")
    args = ap.parse_args()

    global PEOPLE, DEST, PLAN
    if args.fit:
        PEOPLE = os.path.join(OUT_DIR, "Highways CE - people - fit.csv")
        DEST = os.path.join(SCRIPT_DIR, "ce_event_tables_fit")
        PLAN = os.path.join(SCRIPT_DIR, "ce_event_tables_fit_plan.json")
    if args.people:
        PEOPLE = os.path.join(OUT_DIR, args.people) if not os.path.isabs(args.people) else args.people

    for p in (COMPANIES, PEOPLE):
        if not os.path.exists(p):
            raise SystemExit(f"missing {p}")
    chdr, crows = read(COMPANIES)
    phdr, prows = read(PEOPLE)
    if args.event:
        crows = [r for r in crows if r["Company Event Name"] in args.event]
        prows = [r for r in prows if r["Company Event Name"] in args.event]
        if not crows:
            raise SystemExit(f"no company rows for {args.event}")
    if args.fit:
        import build_ce_fit_people_feed as fit   # noqa: E402
        before = len(crows)
        crows = [r for r in crows if fit.decide(r) == "KEEP"]
        for r in crows:
            fit.fix_domain(r)   # same shared-host corrections as the people feed
        print(f"fit filter: {before - len(crows):,} of {before:,} companies dropped")

    groups = collections.OrderedDict()      # (event, table) -> (header, rows)
    for r in crows:
        t = COMPANY_TABLE.get(r["Exhibitor/Sponsor"])
        if not t:
            raise SystemExit(f"unrecognised Exhibitor/Sponsor "
                             f"{r['Exhibitor/Sponsor']!r} in the companies file")
        groups.setdefault((r["Company Event Name"], t), (chdr, []))[1].append(r)
    for r in prows:
        t = PEOPLE_TABLE.get((r["Exhibitor/Sponsor"], r["Side"]))
        if not t:
            raise SystemExit(f"unrecognised (entity, side) "
                             f"{(r['Exhibitor/Sponsor'], r['Side'])!r} in people")
        groups.setdefault((r["Company Event Name"], t), (phdr, []))[1].append(r)

    known = events_with_workbooks()
    events = sorted({e for e, _ in groups})
    stray = [e for e in events if e not in known]
    if stray:
        raise SystemExit(f"!! events not in the CE manifests: {stray[:5]}")

    per_table = collections.Counter(t for _, t in groups)
    print(f"companies {len(crows):,} rows   people {len(prows):,} rows")
    print(f"events {len(events)}   tables {len(groups)}")
    for t in ORDER:
        if per_table.get(t):
            print(f"   {per_table[t]:3}  {t}")

    if args.report:
        print("\n--report: nothing written")
        return 0

    plan, wrote_c, wrote_p = [], 0, 0
    for event in events:
        d = os.path.join(DEST, safe_dir(event))
        os.makedirs(d, exist_ok=True)
        for table in ORDER:                      # ORDER fixes the tab order
            g = groups.get((event, table))
            if not g:
                continue
            header, part = g
            path = os.path.join(d, table + ".csv")
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=header)
                w.writeheader()
                w.writerows(part)
            kind = "companies" if table in COMPANY_TABLE.values() else "people"
            if kind == "companies":
                wrote_c += len(part)
            else:
                wrote_p += len(part)
            plan.append({"event": event, "table": table, "kind": kind,
                         "csv": path, "rows": len(part)})

    if (wrote_c, wrote_p) != (len(crows), len(prows)):
        raise SystemExit(f"!! wrote {wrote_c:,} company and {wrote_p:,} people "
                         f"rows but the sources hold {len(crows):,} and "
                         f"{len(prows):,} — refusing to ship a lossy split")
    if args.event and os.path.exists(PLAN):
        # Partial run: replace only these events' entries.
        kept = [p for p in json.load(open(PLAN, encoding="utf-8"))
                if p["event"] not in args.event]
        plan = kept + plan
    json.dump(plan, open(PLAN, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\n{wrote_c:,} company + {wrote_p:,} people rows in {len(plan)} files")
    print(f"-> {DEST}\nplan -> {PLAN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
