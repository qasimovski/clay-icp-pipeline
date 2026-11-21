"""Rail Other Sources people feed (free, local): one Find People row per (Domain, Side, Classification).

    python build_os_people_feed.py [--buyer-geo all|region]

Inputs: ../os_routes.csv (Route CLASSIFY = searchable, not in CE, not held) + ../classify/classified_os.csv (label per
domain, overrides applied). Same row format and Row ID as the CE feed (people/build_people_feed.py, rid()), so the same
published workflow <RAIL_PEOPLE_WF> ("Rail CE People", v2) and collect_people.py work unchanged. CE domains are
not here (user answer 1, 21 Nov 2026), so no Row ID can collide with a CE search.

Buyer geography: the directories reach no Terrapinn edition (events.csv editions blank), so CE's per-edition
Countries Override has no input. --buyer-geo all (default, pending the user's answer): blank override = icp.yaml
buy.default_countries (all five buy columns, 70 countries), as Submarine OS. Sellers: blank (icp.yaml sell countries,
Geo_Scope v2 sell + Germany). Germany LinkedIn-only and sanctioned countries are applied by post_filter / never built.

The 9 portal bodies (../portal_bodies.csv) are NOT in this feed: classified + searched separately (user, 21 Nov 2026).

Outputs: people_feed.csv (shuffled, seed 20261121), companies.csv (one row per searched domain, for the company join).
"""
import collections
import csv
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(OS, "..", "people"))
from build_people_feed import rid  # noqa: E402

csv.field_size_limit(10**9)
COLS = ["Row ID", "Company Name", "Domain", "Side", "Classification", "Countries Override", "Seniority Group", "Fit",
        "Events", "Search", "Editions", "Company IDs"]


def rd(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write(name, rows, cols):
    with open(os.path.join(HERE, name), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "region" in sys.argv:
        raise SystemExit("--buyer-geo region is not built: waiting for the user's answer")
    lab = {r["Key"]: r for r in rd(os.path.join(OS, "classify", "classified_os.csv")) if r["Status"] == "ok"}
    groups = collections.OrderedDict()
    unlabelled = 0
    for r in rd(os.path.join(OS, "os_routes.csv")):
        if r["Route"] != "CLASSIFY":
            continue
        c = lab.get(r["Key"])
        if not c:
            unlabelled += 1
            continue
        g = groups.setdefault((r["Key"], c["Side"], c["Classification"]), {"rows": [], "events": []})
        g["rows"].append(r)
        for e in r["Events"].split(";"):
            e = e.strip()
            if e and e not in g["events"]:
                g["events"].append(e)
    feed, companies = [], []
    for (dom, side, cl), g in groups.items():
        lead = g["rows"][0]
        feed.append({"Row ID": rid(dom, side, cl), "Company Name": lead["Company Name"], "Domain": dom, "Side": side,
                     "Classification": cl, "Countries Override": "", "Seniority Group": "", "Fit": "",
                     "Events": "; ".join(g["events"]), "Search": "main", "Editions": "",
                     "Company IDs": "; ".join(x["company_id"] for x in g["rows"])})
        companies.append({"Company Name": lead["Company Name"], "Domain": dom, "Side": side, "Classification": cl,
                          "Company IDs": "; ".join(x["company_id"] for x in g["rows"]),
                          "Other Names": "; ".join(sorted({x["Company Name"] for x in g["rows"]} - {lead["Company Name"]})),
                          "Event": "; ".join(g["events"]), "Country": lead["Country"], "Compliance": lead["Compliance"]})
    random.Random(20261121).shuffle(feed)
    write("people_feed.csv", feed, COLS)
    write("companies.csv", companies, list(companies[0]))
    print("searches", len(feed), "| unlabelled CLASSIFY rows", unlabelled)
    print(collections.Counter(x["Side"] for x in feed))
    print(collections.Counter((x["Side"][:4], x["Classification"]) for x in feed).most_common(8))


if __name__ == "__main__":
    main()
