"""Feeds for the seniority split: re-search the companies that hit the people cap.

Find People returns at most 10 people per search with full profiles (the action
rejects a higher limit unless `identifiers_only` is on, which drops the first/
last name and location the Salesforce file needs). So for every company whose
`ce_fit` search came back with exactly 10, the workflow (v6) is run twice more
with the optional `Seniority Group` input:

    A  founder, owner, board-member, partner, c-suite, vp, director
    B  head, manager, senior, mid-level

Same titles, exclusions and countries; A + B is exactly the ICP seniority list,
so a company gets up to 20 people (user, 2026-10-19). Companies that returned
fewer than 10 already have everyone who matches and are not re-searched.

Input : ce_classification_exports/Highways CE - people feed - fit.csv
        ce_fit_people_results/*.jsonl
Output: ce_classification_exports/Highways CE - people feed - fit - sen A.csv
        ce_classification_exports/Highways CE - people feed - fit - sen B.csv
        = the capped companies' feed rows plus `Seniority Group`

A capped result whose Domain is no longer in the feed is reported and skipped —
that is a search the feed has since replaced (www.gov.uk -> environment-agency
.gov.uk), not a company to re-search.

    python build_ce_fit_split_feed.py                          # CE (default)
    python build_ce_fit_split_feed.py --scope other_sources_v7
    python ce_people_rollout.py --scope ce_fit_sen_a
    python ce_people_rollout.py --scope ce_fit_sen_b
    python export_ce_people.py --scope ce_fit_20
"""

import argparse
import csv
import io
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
# --scope: (feed, base results dir, split feed name pattern). Other Sources
# uses the same split after its v7 re-run (2026-10-19).
SCOPES = {
    "ce_fit": ("Highways CE - people feed - fit.csv", "ce_fit_people_results",
               "Highways CE - people feed - fit - sen {}.csv"),
    "other_sources_v7": ("Highways Other Sources - people feed.csv",
                         "other_sources_v7_people_results",
                         "Highways Other Sources - people feed - v7 - sen {}.csv"),
}
FEED = RESULT_DIR = OUT = None
GROUPS = ("A", "B")
# The Find People node's limit. A search that returned this many may have been
# cut off; one that returned fewer was not.
CAP = 10


def capped_domains():
    out = set()
    for name in sorted(os.listdir(RESULT_DIR)):
        if not name.endswith(".jsonl"):
            continue
        for line in open(os.path.join(RESULT_DIR, name), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            people = ((rec.get("result") or {}).get("result") or {}).get("people") or []
            if rec.get("status") == "complete" and len(people) >= CAP:
                out.add(rec["id"])
    return out


def main():
    global FEED, RESULT_DIR, OUT
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", default="ce_fit", choices=sorted(SCOPES))
    args = ap.parse_args()
    feed, results, out = SCOPES[args.scope]
    FEED = os.path.join(OUT_DIR, feed)
    RESULT_DIR = os.path.join(SCRIPT_DIR, results)
    OUT = os.path.join(OUT_DIR, out)

    with open(FEED, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        rows = list(reader)

    capped = capped_domains()
    rows = [r for r in rows if r["Domain"] in capped
            and (r.get("Fit") or "").strip().upper() != "DROP"]
    stale = sorted(capped - {r["Domain"] for r in rows})
    if len({r["Domain"] for r in rows}) != len(rows):
        # The rollout keys results by Domain: two rows for one domain would
        # leave A and B free to come from different Side/Classification rows.
        raise SystemExit("a capped Domain has more than one feed row — resolve "
                         "which row it should be searched as before splitting")

    for g in GROUPS:
        with open(OUT.format(g), "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols + ["Seniority Group"])
            w.writeheader()
            for r in rows:
                w.writerow({**r, "Seniority Group": g})
        print(f"group {g}: {len(rows)} companies -> {OUT.format(g)}")
    for d in stale:
        print(f"  skipped capped result no longer in the feed: {d}")


if __name__ == "__main__":
    main()
