"""Rail Other Sources: route every OS company and write the Domain Finder + classifier feeds (free, local).

    python build_os_feed.py            # before the finder: classify feed = companies that already have a domain
    python build_os_feed.py --finder   # after the finder: also applies domain_finder/results.csv

User answers, 21 Nov 2026 (Comparative/clay/DECISIONS.md "Rail OTHER SOURCES"):
  1. companies already in the Rail CE run are LEFT OUT (same company_id, or a domain CE classified / searched);
  2. Railway Gazette Knowledge Hub organisations included;
  3. Domain Finder (<RAIL_FINDER_WF>) on every no-website name;
  4. the Rail CE classifier decides side + category (the tab's directory Side is a hint only).
Carried over from CE: sanctioned (EXCLUDE) / HOLD never built; Germany built (LinkedIn-only later); CE held hosts
(people/build_people_feed.py HOLD / ORGANISER / WRONG_SITE) + the directories' own domains (DIRECTORY_OWNER).

Outputs (this folder):
  os_routes.csv                     every OS company_id -> Route + Reason (+ Key when it goes to the classifier)
  domain_finder/feed.csv            no-website names (Row ID = company_id) -> bulk_run --id-cols "Row ID"
  classify/feed_classify_os.csv     one row per domain (Key) -> bulk_run --id-cols "Key"
  supabase/domains.csv              the domains to classify (Supabase company gate input)
"""
import collections
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLAY = os.path.dirname(HERE)
COMP = os.path.dirname(CLAY)
sys.path.insert(0, os.path.join(CLAY, "people"))
from build_people_feed import HOLD, ORGANISER, WRONG_SITE  # noqa: E402

csv.field_size_limit(10**9)
OS_COMPANIES = os.path.join(COMP, "Other Sources", "companies.csv")
CE_COMPANIES = os.path.join(COMP, "Competitor events", "companies.csv")
CE_CLASSIFIED = os.path.join(CLAY, "classify", "classified.csv")
CE_SEARCHED = os.path.join(CLAY, "people", "in_scope.csv")
FINDER_RESULTS = os.path.join(HERE, "domain_finder", "results.csv")
JUNK = {"official hotels partner"}

# The directories' own organisations: like a competitor event's organiser, not a prospect of their own list.
# Held for the user's OK, not dropped.
DIRECTORY_OWNER = {
    "uitp.org": "UITP (#107-109, #112-113)", "unife.org": "UNIFE (#106)", "uic.org": "UIC (#110)",
    "railforum.uk": "Rail Forum (#104)", "mafex.es": "MAFEX (#103)", "alamys.org": "Alamys (#114)",
    "railwaygazette.com": "Railway Gazette (#111)", "railway-news.com": "Railway-News (#102)",
    "rail-suppliers.com": "Rail Suppliers (#101)", "railbusinessdaily.com": "Rail Business Daily (#105)",
    # not a directory owner: UEEIV organises #61 Railway Forum 2026; CE held its ueeiv.eu domain (21 Nov 2026)
    "ueeiv.com": "UEEIV, organiser of #61 Railway Forum 2026 (CE held ueeiv.eu)",
}


# Whole-government portals the Domain Finder returned for a body listed on them (same rule as CE HOLD: a domain
# search would return the whole government, not the listed body). Held for the user's OK (21 Nov 2026).
OS_HOLD = {
    "gouv.cd": "DR Congo government portal (Ministry of Transport & Communications)",
    "gouv.qc.ca": "whole Quebec government (Mobilite Infra Quebec)",
    "ap.gov.in": "whole Andhra Pradesh government (Visakhapatnam Metro Rail)",
    "act.gov.au": "whole ACT government (Transport Canberra)",
    "sarawak.gov.my": "whole Sarawak government (Ministry of Transport Sarawak)",
    "guanajuato.gob.mx": "whole Guanajuato state government (Secretaria de Obra Publica)",
    "beijing.gov.cn": "whole Beijing municipal government (Beijing Municipal Commission of Transport)",
    "sh.gov.cn": "whole Shanghai municipal government (Shanghai Municipal Transportation Commission)",
    "huangshi.gov.cn": "whole Huangshi city government (Huangshi Rail Transit)",
}


def rd(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main():
    use_finder = "--finder" in sys.argv
    os_rows = rd(OS_COMPANIES)
    ce_keys = {r["Key"].lower() for r in rd(CE_CLASSIFIED)}
    ce_search = set()
    for r in rd(CE_SEARCHED):
        for k in ("Domain", "Search Domain"):
            if r.get(k):
                ce_search.add(r[k].lower())
    ce_names = {norm(r["company"]) for r in rd(CE_COMPANIES) if r["company"].strip()}
    # names with no website that match a company whose domain is known elsewhere (free; listed for review)
    name2dom = {}
    for r in rd(CE_COMPANIES) + os_rows:
        if r["domain"]:
            name2dom.setdefault(norm(r["company"]), r["domain"].lower())
    found = {}
    if use_finder:
        for r in rd(FINDER_RESULTS):
            if r["Status"] == "found" and r["Domain"]:
                found[r["Row ID"]] = r["Domain"].lower()

    routes, finder_feed, classify = [], [], {}
    for r in os_rows:
        name = r["company"].strip()
        dom = r["domain"].strip().lower()
        src = ""
        route, reason = "", ""
        if not name or name.lower() in JUNK:
            route, reason = "DROP", "no company name"
        elif r["compliance"].startswith(("EXCLUDE", "HOLD")):
            route, reason = "DROP", "compliance " + r["compliance"]
        elif r["also_at_competitor_events"]:
            route, reason = "CE", "in Rail CE: " + r["also_at_competitor_events"][:120]
        elif dom and (dom in ce_keys or dom in ce_search):
            route, reason = "CE", "domain classified / searched in Rail CE"
        elif not dom and norm(name) in ce_names:
            route, reason = "CE", "name equals a Rail CE company"
        if not route and not dom:
            if r["company_id"] in found:
                dom, src = found[r["company_id"]], "clay finder"
            elif norm(name) in name2dom:
                dom, src = name2dom[norm(name)], "name match"
            if dom and (dom in ce_keys or dom in ce_search):
                route, reason = "CE", f"{src} domain {dom} is in Rail CE"
        if not route and not dom:
            if use_finder:
                route, reason = "NO WEBSITE", "Domain Finder found none"
            else:
                route, reason = "FINDER", "no website"
                finder_feed.append({
                    "Row ID": r["company_id"], "Company Name": name,
                    "Event": ("Other Sources: " + r["events"])[:400], "Country": r["country"],
                    "Description": (r["description"] or r["category"])[:800],
                    "Group": "2 directory", "Compliance": r["compliance"]})
        if not route:
            if dom in HOLD or dom in OS_HOLD:
                route, reason = "HELD", "shared host: " + (HOLD.get(dom) or OS_HOLD[dom])
            elif dom in ORGANISER:
                route, reason = "HELD", "competitor-event organiser: " + ORGANISER[dom]
            elif dom in DIRECTORY_OWNER:
                route, reason = "HELD", "directory owner: " + DIRECTORY_OWNER[dom]
            elif dom in WRONG_SITE and WRONG_SITE[dom].search(name):
                route, reason = "HELD", f"wrong website: private company on {dom}"
            else:
                route, reason = "CLASSIFY", src or "scraped website"
                f = classify.setdefault(dom, {"Key": dom, "Company Name": name, "Domain": dom, "Description": "",
                                              "Primary Offerings": "", "Group": "2 directory", "n_companies": 0,
                                              "Sources": set(), "Compliance": set()})
                f["n_companies"] += 1
                desc = r["description"] or ""
                if len(desc) > len(f["Description"]):
                    f["Description"] = desc[:1500]
                c = r["category"]
                if c and c not in f["Primary Offerings"]:
                    f["Primary Offerings"] = "; ".join(x for x in (f["Primary Offerings"], c) if x)[:800]
                f["Sources"].update(e.strip() for e in r["events"].split(";") if e.strip())
                f["Compliance"].add(r["compliance"].split(" (")[0])
        routes.append({"company_id": r["company_id"], "Company Name": name, "Domain": dom, "Domain Source": src,
                       "Country": r["country"], "Compliance": r["compliance"], "Events": r["events"],
                       "Route": route, "Reason": reason, "Key": dom if route == "CLASSIFY" else ""})

    feed = []
    for f in classify.values():
        f = dict(f)
        # the directory names help the classifier (e.g. "UITP members - Operator"); CE put categories here too
        f["Primary Offerings"] = "; ".join(x for x in (f["Primary Offerings"], "Listed in: " + ", ".join(
            sorted(f["Sources"]))) if x)[:800]
        f["Sources"] = "; ".join(sorted(f["Sources"]))
        f["Compliance"] = "; ".join(sorted(f["Compliance"]))
        feed.append(f)
    feed.sort(key=lambda x: x["Key"])

    write(os.path.join(HERE, "os_routes.csv"), routes, list(routes[0]))
    write(os.path.join(HERE, "classify", "feed_classify_os.csv"), feed,
          ["Key", "Company Name", "Domain", "Description", "Primary Offerings", "Group", "n_companies", "Sources",
           "Compliance"])
    write(os.path.join(HERE, "supabase", "domains.csv"), [{"Domain": f["Key"], "Company Name": f["Company Name"]}
                                                          for f in feed], ["Domain", "Company Name"])
    if not use_finder:
        finder_feed.sort(key=lambda x: x["Row ID"])
        write(os.path.join(HERE, "domain_finder", "feed.csv"), finder_feed,
              ["Row ID", "Company Name", "Event", "Country", "Description", "Group", "Compliance"])

    c = collections.Counter(x["Route"] for x in routes)
    print("routes:", dict(c))
    print("classify domains:", len(feed), "| finder names:", len(finder_feed))
    print("held:", collections.Counter(x["Reason"].split(":")[0] for x in routes if x["Route"] == "HELD"))
    print("name matches:", [(x["Company Name"], x["Domain"]) for x in routes if x["Domain Source"] == "name match"])
    print("classify compliance:", collections.Counter(x["Compliance"] for x in feed).most_common())
    src = collections.Counter()
    for x in feed:
        for s in x["Sources"].split("; "):
            src[s] += 1
    print("classify domains per source:", dict(src))
    fsrc = collections.Counter()
    for x in finder_feed:
        for s in x["Event"].replace("Other Sources: ", "").split(";"):
            fsrc[s.strip()] += 1
    print("finder names per source:", dict(fsrc))


if __name__ == "__main__":
    main()
