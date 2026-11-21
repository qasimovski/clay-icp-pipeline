"""Rail OS: find the 5 still-empty portal bodies' LinkedIn company pages in Blitz (user 21 Nov 2026: "check blitz and
clay to see if you find people for these bodies"). Per body: domain-to-linkedin on its own site and on the portal
(1 record when found), and a company keyword search on its name words (1 record per company returned, max 5).
Prints candidates; nothing is searched for people here.  -> find_bodies.json
"""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\qasim\terrapinn\scrapers_solar\_run_v2\blitz")
from blitz import call  # noqa: E402
B = {
    "sarawak.gov.my": ("mot.sarawak.gov.my", ["Ministry", "Transport", "Sarawak"]),
    "gouv.cd": ("transports.gouv.cd", ["Ministere", "Transports", "Congo"]),
    "beijing.gov.cn": ("jtw.beijing.gov.cn", ["Beijing", "Municipal", "Commission", "Transport"]),
    "sh.gov.cn": ("jtw.sh.gov.cn", ["Shanghai", "Municipal", "Transportation", "Commission"]),
    "huangshi.gov.cn": ("huangshi.gov.cn", ["Huangshi", "Rail", "Transit"]),
}
out = {}
for portal, (own, words) in B.items():
    rec = {}
    for d in dict.fromkeys([own, portal]):
        s, j = call("POST", "/v2/enrichment/domain-to-linkedin", {"domain": d})
        rec[d] = j.get("company_linkedin_url") if s == 200 and j.get("found") else None
    s, j = call("POST", "/v2/search/companies", {"company": {"keywords": {"include": words}}, "max_results": 5})
    res = j.get("results") or j.get("companies") or [] if isinstance(j, dict) else []
    rec["search"] = [((r.get("company") or r).get("name"), (r.get("company") or r).get("linkedin_url"),
                      (r.get("company") or r).get("employees_on_linkedin") or (r.get("company") or r).get("size")) for r in res]
    out[portal] = rec
    print(portal, json.dumps(rec, ensure_ascii=False)[:600])
json.dump(out, open("find_bodies.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
