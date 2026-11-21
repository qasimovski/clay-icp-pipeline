"""Rail Other Sources -> per-directory Clay review workbooks (user, 21 Nov 2026: folder <RAIL_OS_FOLDER_ID>,
Submarine OS layout, reviewer <reviewer>). One workbook per directory (14), named by its folder name (as Rail CE),
with Companies / Buyside People / Sellside People. The Companies table holds EVERY company the directory listed, with
its Decision and Reason; a person sits under every directory where their company is a KEEP row (deliver.py split).

    python build_decisions.py   -> decisions.csv + delivery/Rail OS - review.csv + delivery/... LinkedIn only.csv
    then deliver.py split --decisions decisions.csv --people "delivery/Rail OS - review.csv" \
         --linkedin-only "delivery/Rail OS - review - LinkedIn only.csv" --linkedin-only-label "LinkedIn only (Germany)" \
         --out event_tables --plan plan.json
    and  python rename_tables.py   (Companies Buyside People -> Buyside People; BEFORE the import)

Decision per directory row (../os_routes.csv):
  KEEP        searched for people (Clay Find People + B1 + Deepline); Domain = the domain searched
              (portal bodies: the government portal, people found by department keywords, user 21 Nov 2026)
  DROP        already in the Rail CE run (user answer 1, 21 Nov 2026: not included twice) | sanctioned / HOLD country
  HOLD        organiser / directory owner domain | portal body with no people found
  NO WEBSITE  no website in the directory and none found by the Domain Finder
"""
import csv
import glob
import json
import os
import re

csv.field_size_limit(10**9)
HERE = os.path.dirname(os.path.abspath(__file__))
OSD = os.path.dirname(HERE)
COMP = os.path.normpath(os.path.join(OSD, "..", ".."))


def rd(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def wr(p, rows, cols):
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    folder = {}
    for r in rd(os.path.join(COMP, "Other Sources", "events.csv")):
        folder[f"#{r['#']} {r['event']}"] = r["folder"].split("/", 1)[1]
    lab = {r["Key"]: r for r in rd(os.path.join(OSD, "classify", "classified_os.csv")) if r["Status"] == "ok"}
    portal_lab = {}
    for f in glob.glob(os.path.join(OSD, "classify", "runs_portal", "results", "*.jsonl")):
        for line in open(f, encoding="utf-8"):
            d = json.loads(line)
            so = (d.get("result") or {}).get("structuredOutputs") or {}
            portal_lab[d["id"]] = (so.get("Side", ""), so.get("Classification", ""))
    portal1 = {r["Portal Domain"]: (r["Own Site"] or r["Portal Domain"]) for r in rd(os.path.join(OSD, "portal_bodies.csv"))}
    portal2 = {r["Portal Domain"] for r in rd(os.path.join(OSD, "portal_bodies_2.csv"))}
    review = rd(os.path.join(OSD, "people", "final", "Rail OS - review.csv")) + \
        rd(os.path.join(OSD, "people", "final", "Rail OS - review - LinkedIn only.csv"))
    with_people = {r["Domain"] for r in review}

    out, n = [], {}
    for r in rd(os.path.join(OSD, "os_routes.csv")):
        dom, side, cl = r["Domain"], "", ""
        route = r["Route"]
        if route == "CLASSIFY":
            c = lab.get(r["Key"])
            side, cl = (c["Side"], c["Classification"]) if c else ("", "")
            dec = "KEEP"
            why = "searched: " + (cl or "classified")
            if dom in portal2:
                why = "whole-government portal: people only by department keywords (user, 21 Nov 2026)" + \
                      ("" if dom in with_people else "; none found")
        elif route == "HELD" and dom in portal1:
            side, cl = portal_lab.get(portal1[dom], ("", ""))
            if dom in with_people:
                dec, why = "KEEP", "whole-government portal: people only by department keywords (user, 21 Nov 2026)"
            else:
                dec, why = "HOLD", "whole-government portal: own site not searchable, no people by department keywords"
        elif route == "HELD":
            dec, why = "HOLD", r["Reason"]
        elif route == "CE":
            dec, why = "DROP", "already in the Rail Competitive Events run (user, 21 Nov 2026: not included twice)"
        elif route == "NO WEBSITE":
            dec, why = "NO WEBSITE", "no website in the directory and none found by the Domain Finder"
        else:
            dec, why = "DROP", r["Reason"]
        for e in [x.strip() for x in r["Events"].split(";") if x.strip()]:
            n[dec] = n.get(dec, 0) + 1
            out.append({"Event": folder[e], "Entity": "Companies", "Company Name": r["Company Name"], "Website": dom,
                        "Domain": dom, "Country": r["Country"], "Side": side, "Classification": cl, "Decision": dec,
                        "Reason": why})
    wr(os.path.join(HERE, "decisions.csv"), out, list(out[0]))
    print(f"{len(out):,} company rows over {len({o['Event'] for o in out})} directories -> decisions.csv", n)

    os.makedirs(os.path.join(HERE, "delivery"), exist_ok=True)
    for name in ("Rail OS - review.csv", "Rail OS - review - LinkedIn only.csv"):
        rows = rd(os.path.join(OSD, "people", "final", name))
        for r in rows:
            r["Event"] = "; ".join(folder[x.strip()] for x in r["Event"].split(";") if x.strip())
        wr(os.path.join(HERE, "delivery", name), rows, list(rows[0]))
        print("delivery/" + name, len(rows))
    keep_dom = {o["Domain"] for o in out if o["Decision"] == "KEEP"}
    orphan = [r for r in review if r["Domain"] not in keep_dom]
    print("people whose domain is not a KEEP row (would be left out of Clay):", len(orphan),
          sorted({r["Domain"] for r in orphan})[:10])


if __name__ == "__main__":
    main()
