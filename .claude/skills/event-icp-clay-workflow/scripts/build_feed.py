"""Combine an event series' scraped CSVs into one company feed for the classify workflow.

The column map is the user's decision, confirmed after `inspect_inputs.py`:

    python build_feed.py --out feed.csv \
        --input "C:/…/*/exhibitors_normalized.csv=Exhibitor" \
        --input "C:/…/*/sponsors_normalized.csv=Sponsor" \
        --name-col "Company Name" --website-col "Website" --description-col "Description" \
        [--event-col Event | --event-from-folder] [--country-col Country] [--keep-cols "Year,Booth"]

Output columns: Event, Entity, Company Name, Domain, Website, Description, Country,
Domain Flag, plus any --keep-cols. One row per (Event, Entity, Domain), or per
(Event, Entity, name) when there is no domain — the same company at two events
stays two rows, because it is two deliverable rows.

`Domain Flag` marks hosts that are not the company's own site (gov.uk, linkedin,
facebook, a directory). A people search keyed on `www.gov.uk` returned a whole
government's staff on Highways; show the flagged rows to the user before the
people step and fix the domains they confirm.
"""

import argparse
import collections
import csv
import glob
import os
import re
import sys

# Flagged only as the WHOLE domain: `www.gov.uk` stands for every UK department,
# but `cma.gov.sa` is the CMA's own site and `cloud.google.com` is Google Cloud.
SHARED_EXACT = {
    "gov.uk", "gov.au", "gov.sa", "gov.ae", "gov.qa", "gov.bh", "europa.eu",
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "crunchbase.com",
}
# Flagged as the domain OR any subdomain: a site hosted on a platform resolves to
# the platform's company, not the exhibitor's.
HOSTING = {
    "wixsite.com", "wordpress.com", "squarespace.com", "github.io", "sites.google.com",
    "linktr.ee", "bit.ly", "medium.com", "eventbrite.com", "webflow.io", "carrd.co",
}


def norm_domain(url):
    s = str(url or "").strip().lower()
    if not s or " " in s:
        return ""
    s = re.sub(r"^[a-z]+://", "", s).split("/")[0].split("?")[0].split("#")[0]
    s = re.sub(r"^www\d*\.", "", s).strip(".")
    return s if "." in s else ""


def flag(domain):
    if not domain:
        return "no domain"
    if domain in SHARED_EXACT:
        return f"shared host ({domain})"
    for h in HOSTING:
        if domain == h or domain.endswith("." + h):
            return f"hosted site ({h})"
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", action="append", required=True,
                    help="GLOB=Entity, e.g. '…/*/sponsors_normalized.csv=Sponsor'")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name-col", required=True)
    ap.add_argument("--website-col", required=True)
    ap.add_argument("--description-col", default="")
    ap.add_argument("--country-col", default="")
    ap.add_argument("--event-col", default="")
    ap.add_argument("--event-from-folder", action="store_true",
                    help="event = the CSV's parent folder name")
    ap.add_argument("--keep-cols", default="", help="extra input columns to carry through")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if bool(a.event_col) == a.event_from_folder:
        raise SystemExit("pass exactly one of --event-col / --event-from-folder")
    keep = [c.strip() for c in a.keep_cols.split(",") if c.strip()]

    out, seen = [], set()
    tally = collections.Counter()
    for spec in a.input:
        pattern, _, entity = spec.rpartition("=")
        if not pattern or not entity:
            raise SystemExit(f"--input needs GLOB=Entity: {spec!r}")
        files = sorted(glob.glob(pattern))
        if not files:
            raise SystemExit(f"no files match {pattern!r}")
        for f in files:
            with open(f, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            for r in rows:
                if a.name_col not in r:
                    raise SystemExit(f"{f}: no column {a.name_col!r} — re-check the column map")
                event = (r.get(a.event_col) if a.event_col
                         else os.path.basename(os.path.dirname(f))).strip()
                name = " ".join((r.get(a.name_col) or "").split())
                if not name:
                    tally["skipped: no company name"] += 1
                    continue
                dom = norm_domain(r.get(a.website_col))
                key = (event, entity, dom or "name:" + name.lower())
                if key in seen:
                    tally["skipped: duplicate within event"] += 1
                    continue
                seen.add(key)
                row = {"Event": event, "Entity": entity, "Company Name": name, "Domain": dom,
                       "Website": (r.get(a.website_col) or "").strip(),
                       "Description": (r.get(a.description_col) or "").strip() if a.description_col else "",
                       "Country": (r.get(a.country_col) or "").strip() if a.country_col else "",
                       "Domain Flag": flag(dom)}
                for c in keep:
                    row[c] = r.get(c, "")
                out.append(row)
                tally[f"{entity}"] += 1
                if row["Domain Flag"]:
                    tally["flag: " + row["Domain Flag"].split(" (")[0]] += 1
    header = ["Event", "Entity", "Company Name", "Domain", "Website", "Description",
              "Country", "Domain Flag"] + keep
    with open(a.out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(out)
    print(f"{len(out):,} feed rows across {len({r['Event'] for r in out})} events -> {a.out}")
    for k, n in sorted(tally.items()):
        print(f"  {k:36} {n:6,}")
    # The same companies listed as BOTH exhibitors and sponsors of one event (Seamless:
    # Money20-20 Europe/USA, 150/150 and 113/113) — usually a scrape that read one list
    # twice. Each domain is only paid for once either way, but the delivery tables would
    # show them twice; ask the user which list is real.
    by_ev = collections.defaultdict(lambda: collections.defaultdict(set))
    for r in out:
        if r["Domain"]:
            by_ev[r["Event"]][r["Entity"]].add(r["Domain"])
    for ev, ents in sorted(by_ev.items()):
        names = sorted(ents)
        for i, x in enumerate(names):
            for y in names[i + 1:]:
                a_, b_ = ents[x], ents[y]
                # Sponsors that also exhibit are normal (a small list inside a big
                # one); flag only lists that are near-identical in BOTH directions.
                if min(len(a_), len(b_)) >= 5 and len(a_ & b_) / max(len(a_), len(b_)) >= 0.9:
                    print(f"overlap: {ev}: {x} and {y} share {len(a_ & b_)} of "
                          f"{len(a_)}/{len(b_)} domains — ask which list is real")
    shared = collections.Counter(r["Domain"] for r in out
                                 if r["Domain Flag"].startswith(("shared", "hosted")))
    if shared:
        print("shared or hosted domains (show the user; fix the real domains before the people run):")
        for d, n in shared.most_common(10):
            print(f"  {d:30} {n}")


if __name__ == "__main__":
    main()
