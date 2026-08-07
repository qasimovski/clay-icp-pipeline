"""Audit the exported CSVs under "Labs - Tables - Cleaned" for two things the
user asked about (2026-08-07):

  A. EMAIL-VALIDATION GENERATION DRIFT. The email waterfall exists in two
     generations. The older LeadMagic build writes `Mx Provider` / `Status`
     (+ `Mx Record`, `Validate email`); the current template writes
     `Smtp Provider` / `Email Validation Result`. Where a table is old-gen, its
     `Mx Provider` values belong under `Smtp Provider` and its `Status` values
     under the validation-status column. This reports every file carrying
     old-gen columns so none is missed in a downstream merge.

  B. PEOPLE-TABLE COLUMN COVERAGE against the 26 columns the user listed.
     Reported as exact-name hits, alias hits, and misses — separately, because
     "present under a different name" and "absent" need different fixes.

Read-only: opens each CSV's header (and, with --fill, counts non-empty cells).
Touches nothing in Clay and writes no CSV.

Two findings drive how the aliases below are written, both confirmed against
sample rows rather than assumed:

  * `Company Table Data` is the lookup column that displays the linked company's
    name ("BASF", "Viking Therapeutics, Inc."), so it — not the P&S-only
    `Company` column — is what "Company Name" resolves to in most tables.
  * `Location` is NOT a substitute for `Country`: it is a composite string
    ("Ankara, Ankara, Türkiye", "中国 浙江省 杭州") whose country component is
    neither last nor in a consistent language. It is reported as a separate
    near-miss, never merged into Country.

    python audit_cleaned_exports.py                 # both sections, headers only
    python audit_cleaned_exports.py --fill          # + non-empty counts (slower)
    python audit_cleaned_exports.py --json out.json
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "build_automation"))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "clay_sync"))

import browser_session  # noqa: E402

csv.field_size_limit(10 ** 9)

# Account-specific local path; see the note in combine_people_csvs.py.
DEFAULT_ROOT = browser_session.local_setting(
    "cleaned_export_root", "CLAY_CLEANED_EXPORT_ROOT",
    default=r"C:\Users\qasim\terrapinn\Labs - Tables - Cleaned")

PEOPLE_FILES = {"Sellers - People.csv", "Buyers - People.csv",
                "Sponsors - Sellers - People.csv",
                "Sponsors - Buyers - People.csv", "People.csv"}

# --- Section A -------------------------------------------------------------
# Old-gen (LeadMagic) markers and the current-template columns they map onto.
OLD_GEN = {
    "Mx Provider": "Smtp Provider",
    "Mx Provider (2)": "Smtp Provider",
    "Status": "Email Validation Status",
    "Status (2)": "Email Validation Status",
    "Mx Record": "(no equivalent - dropped by the new template)",
    "Validate email": "Validate Email",
    "LeadMagic Validate Email": "Validate Email",
}
NEW_GEN = ["Smtp Provider", "Email Validation Result", "Email Validation Status",
           "Email Status Combined", "Validate Email", "WORK EMAIL"]

# --- Section B -------------------------------------------------------------
# requested name -> (exact candidates, alias candidates)
# Exact = the user's own spelling if it appears verbatim; aliases = the real
# Clay column that carries the same data under a different name/case.
REQUESTED = [
    ("Full Name",                    ["Full Name"], []),
    ("Job Title",                    ["Job Title"], []),
    ("Company Name",                 ["Company Name"], ["Company Table Data", "Company"]),
    ("Country",                      ["Country"], []),
    ("Side",                         ["Side"], []),
    ("Classification",               ["Classification"], []),
    ("Linkedin Profile",             ["Linkedin Profile"], ["LinkedIn Profile"]),
    ("Work Email",                   ["Work Email"], ["WORK EMAIL"]),
    ("Email Validation Status",      ["Email Validation Status"],
                                     ["Email Validation Result", "Email Status Combined",
                                      "Status", "Status (2)"]),
    ("Smtp Provider",                ["Smtp Provider"], ["Mx Provider", "Mx Provider (2)"]),
    ("Created at",                   ["Created at"], ["Created At"]),
    ("Updated at",                   ["Updated at"], ["Updated At"]),
    ("Company Enrichments (CPJ)",    ["Company Enrichments (CPJ)"], []),
    ("Size",                         ["Size"], []),
    ("Type",                         ["Type"], []),
    ("Domain",                       ["Domain"], []),
    ("Linkedin URL",                 ["Linkedin URL"], ["LinkedIn URL"]),
    ("Founded",                      ["Founded"], []),
    ("Industry",                     ["Industry"], []),
    ("Description",                  ["Description"], []),
    ("Specialities",                 ["Specialities"], []),
    ("Subindustry",                  ["Subindustry"], ["Sub Industry", "Sub-Industry"]),
    ("Primary Offerings",            ["Primary Offerings"], []),
    ("Business Stage",               ["Business Stage"], []),
    ("Annual Revenue",               ["Annual Revenue"], []),
    ("Total Funding Amount Range USD", ["Total Funding Amount Range USD"],
                                     ["Total Funding Amount Range Usd"]),
]

# Columns worth reporting as context even though the user did not ask for them:
# each is the nearest thing to a requested column that is NOT the same data.
NEAR_MISSES = ["Location", "Company Domain", "City", "State or Province",
               "First Name", "Last Name"]


def walk_csvs(root):
    out = []
    for dp, _dns, fns in os.walk(root):
        for f in sorted(fns):
            if f.lower().endswith(".csv"):
                out.append((os.path.relpath(os.path.join(dp, f), root), f,
                            os.path.join(dp, f)))
    return sorted(out)


def read_header(path):
    # utf-8-sig so a BOM (Clay's exports have none, but a re-save may add one)
    # never turns the first column into "\ufeffCreated At".
    with open(path, encoding="utf-8-sig", newline="") as fh:
        try:
            return next(csv.reader(fh))
        except StopIteration:
            return []


def fill_counts(path, wanted):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        got = Counter()
        n = 0
        for r in rd:
            n += 1
            for c in wanted:
                if (r.get(c) or "").strip():
                    got[c] += 1
    return n, got


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--fill", action="store_true",
                    help="also count non-empty cells (reads every row)")
    ap.add_argument("--json", help="write the full per-file report here")
    args = ap.parse_args()

    files = walk_csvs(args.root)
    people = [(rel, f, p) for rel, f, p in files if f in PEOPLE_FILES]
    others = [(rel, f, p) for rel, f, p in files if f not in PEOPLE_FILES]
    print(f"[root] {args.root}")
    print(f"[files] {len(files)} csv  ({len(people)} people, {len(others)} other)\n")

    report = {"root": args.root, "n_files": len(files), "old_gen": {},
              "people": {}, "people_files": len(people)}

    # ---- A: generation drift, across EVERY file, not just people ----------
    print("=" * 74)
    print("A. EMAIL-VALIDATION GENERATION DRIFT")
    print("=" * 74)
    hits = defaultdict(list)
    newgen_count = Counter()
    for rel, f, p in files:
        h = read_header(p)
        for c in h:
            if c in OLD_GEN:
                hits[c].append(rel)
            if c in NEW_GEN:
                newgen_count[c] += 1
    if not hits:
        print("  No old-generation (LeadMagic) email columns anywhere.")
        print("  Nothing needs remapping into Smtp Provider / Email Validation Status.")
    else:
        for c, rels in sorted(hits.items(), key=lambda kv: -len(kv[1])):
            print(f"  {c!r} in {len(rels)} file(s) -> should map to {OLD_GEN[c]!r}")
            for r in rels[:12]:
                print(f"      {r}")
            if len(rels) > 12:
                print(f"      ... +{len(rels) - 12} more")
        report["old_gen"] = {c: rels for c, rels in hits.items()}
    print("\n  current-template columns, for comparison:")
    for c in NEW_GEN:
        if newgen_count[c]:
            print(f"    {newgen_count[c]:4}/{len(files)}  {c}")

    # ---- B: people column coverage ---------------------------------------
    print("\n" + "=" * 74)
    print(f"B. PEOPLE-TABLE COLUMN COVERAGE  ({len(people)} files)")
    print("=" * 74)
    headers = {rel: read_header(p) for rel, f, p in people}
    print(f"  {'requested column':32} {'exact':>6} {'alias':>6} {'MISSING':>8}   resolves to")
    print("  " + "-" * 86)
    for name, exact, alias in REQUESTED:
        e = a = 0
        used = Counter()
        missing = []
        for rel, h in headers.items():
            hit = next((c for c in exact if c in h), None)
            if hit:
                e += 1
                used[hit] += 1
                continue
            hit = next((c for c in alias if c in h), None)
            if hit:
                a += 1
                used[hit] += 1
            else:
                missing.append(rel)
        via = ", ".join(f"{c} x{n}" for c, n in used.most_common()) or "-"
        flag = "" if not missing else "  <<<"
        print(f"  {name:32} {e:6} {a:6} {len(missing):8}   {via}{flag}")
        report["people"][name] = {"exact": e, "alias": a,
                                 "missing": missing, "via": dict(used)}

    print("\n  near-miss columns present in these files (NOT the same data):")
    for c in NEAR_MISSES:
        n = sum(1 for h in headers.values() if c in h)
        if n:
            print(f"    {n:4}/{len(people)}  {c}")

    if args.fill:
        print("\n  --fill: non-empty cell counts for resolved columns")
        agg = Counter()
        rows_tot = 0
        for rel, f, p in people:
            h = headers[rel]
            wanted = []
            for name, exact, alias in REQUESTED:
                hit = next((c for c in exact + alias if c in h), None)
                if hit:
                    wanted.append(hit)
            n, got = fill_counts(p, wanted)
            rows_tot += n
            agg.update(got)
        print(f"    {rows_tot:,} people rows total")
        for name, exact, alias in REQUESTED:
            tot = sum(agg[c] for c in exact + alias)
            if tot or name in ("Country", "Company Enrichments (CPJ)"):
                pct = 100 * tot / rows_tot if rows_tot else 0
                print(f"      {name:32} {tot:8,}  {pct:5.1f}%")
        report["fill"] = dict(agg)
        report["rows_total"] = rows_tot

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\n[json] {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
