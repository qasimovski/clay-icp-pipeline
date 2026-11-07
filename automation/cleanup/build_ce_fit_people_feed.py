"""Stamp the CE people feed with the ICP fit-filter decision, for the re-run of
the people search through the `Fit Keep?` gate.

The scraper-side fit filter (`scrapers_highways/apply_fit_filter.py`, rules in
each event's `FIT_FILTER.md`) dropped companies AFTER the CE classification and
people passes had already run. Classification is kept as-is (sunk cost on the
dropped rows, per the user 2026-10-12); the people search is re-run so that
dropped companies stop at the workflow's `Fit Keep?` gate and only kept
companies are searched.

Input : ce_classification_exports/Highways CE - people feed.csv   (unchanged)
Output: ce_classification_exports/Highways CE - people feed - fit.csv
        = the same rows and columns plus `Fit` (KEEP / DROP)

A company is DROP when its name was in the event's pre-filter original
(`<event>/_pre_fit_filter/<type>_normalized.csv`) and is not in the live
filtered file. Everything else is KEEP — including every company of an event or
data type the filter never touched. Names that are in NEITHER file stop the run:
that is a feed/scraper mismatch to look at, not something to guess.

DROP rows are kept in the feed on purpose: the gate is what skips them, and the
run's `Skipped - Fit Filter` output is the audit that it did.

    python build_ce_fit_people_feed.py --event FloodEx      # pilot feed
    python build_ce_fit_people_feed.py                       # every event
"""

import argparse
import collections
import csv
import io
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
FEED = os.path.join(OUT_DIR, "Highways CE - people feed.csv")
OUT = os.path.join(OUT_DIR, "Highways CE - people feed - fit.csv")
# --event writes the pilot feed instead (ce_people_rollout.py --scope ce_fit_pilot)
PILOT_OUT = os.path.join(OUT_DIR, "Highways CE - people feed - fit - pilot.csv")
SCRAPERS = os.environ.get("CLAY_HIGHWAYS_SCRAPERS_ROOT",
                          r"C:\Users\qasim\terrapinn\scrapers_highways")
SOURCE_FILE = {"Exhibitor": "exhibitors_normalized.csv",
               "Sponsor": "sponsors_normalized.csv"}


# Rows whose enriched `Domain` is a SHARED host, so the people search (keyed on
# Domain) returned people from the whole host rather than the company. Keyed by
# (event, company name casefolded); the value replaces `Domain` in the feed and
# in the company tables. Each entry must be verified, not inferred:
DOMAIN_FIXES = {
    # www.gov.uk is every UK government body. The September search there
    # returned an HMRC Director General and other non-EA staff. Verified
    # 2026-10-12: Find People resolves environment-agency.gov.uk to Clay
    # company 471037 (= linkedin.com/company/environment-agency), returning EA
    # directors; it is also EA's own email/website domain.
    ("FloodEx", "environment agency"): "environment-agency.gov.uk",
}


def norm(s):
    return " ".join(str(s or "").split()).casefold()


def fix_domain(row):
    """Apply DOMAIN_FIXES to a feed/company row in place. True if changed."""
    new = DOMAIN_FIXES.get((row.get("Company Event Name"), norm(row.get("Company Name"))))
    if new and row.get("Domain") != new:
        row["Domain"] = new
        return True
    return False


def names(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {norm(r["Company Name"]) for r in csv.DictReader(fh)}


_cache = {}


def fit_sets(event, kind):
    """(original_names, live_names) or None when this event/type was never
    filtered."""
    key = (event, kind)
    if key not in _cache:
        fname = SOURCE_FILE[kind]
        original = names(os.path.join(SCRAPERS, event, "_pre_fit_filter", fname))
        if original is None:
            _cache[key] = None
        else:
            # A data type the filter removed entirely has no live file.
            live = names(os.path.join(SCRAPERS, event, fname)) or set()
            _cache[key] = (original, live)
    return _cache[key]


def decide(row):
    sets = fit_sets(row["Company Event Name"], row["Exhibitor/Sponsor"])
    if sets is None:
        return "KEEP"
    original, live = sets
    n = norm(row["Company Name"])
    if n in live:
        return "KEEP"
    if n in original:
        return "DROP"
    raise SystemExit(f"{row['Company Event Name']} / {row['Exhibitor/Sponsor']}: "
                     f"{row['Company Name']!r} is in neither the original nor the "
                     f"filtered scraper file — refusing to guess its fit")


def main():
    # Here, not at import: split_ce_event_tables imports decide() and wraps
    # stdout itself — a second wrap at import closes the first one's buffer.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", action="append", default=[],
                    help="restrict to this Company Event Name (repeatable)")
    args = ap.parse_args()

    with open(FEED, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        rows = list(reader)
    out = OUT
    if args.event:
        unknown = set(args.event) - {r["Company Event Name"] for r in rows}
        if unknown:
            raise SystemExit(f"no feed rows for: {sorted(unknown)}")
        rows = [r for r in rows if r["Company Event Name"] in args.event]
        out = PILOT_OUT

    tally = collections.Counter()
    for r in rows:
        old = r["Domain"]
        if fix_domain(r):
            print(f"  domain fix: {r['Company Event Name']} / {r['Company Name']}: "
                  f"{old} -> {r['Domain']}")
        r["Fit"] = decide(r)
        tally[(r["Company Event Name"], r["Exhibitor/Sponsor"], r["Fit"])] += 1

    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols + ["Fit"])
        w.writeheader()
        w.writerows(rows)

    for (ev, kind, fit), n in sorted(tally.items()):
        print(f"  {ev:42} {kind:10} {fit:5} {n:5}")
    keep = sum(n for k, n in tally.items() if k[2] == "KEEP")
    print(f"\n{len(rows)} feed rows -> {keep} KEEP, {len(rows) - keep} DROP  -> {out}")


if __name__ == "__main__":
    main()
