"""Rail OS: company data from BlitzAPI for companies Clay's Enrich Company returned nothing for (user 21 Nov 2026:
"Also use blitz for companies that come out with no data"). Same method as scrapers_climatech/_other_sources/people/
blitz/blitz_company.py: POST /v2/enrichment/domain-to-linkedin, then /v2/enrichment/company (1 record each when found).
Government portals were removed first (portal rule), so only real companies are sent.  -> blitz_company_cache.json
"""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\qasim\terrapinn\scrapers_solar\_run_v2\blitz")
from blitz import call  # noqa: E402
HERE = os.path.dirname(os.path.abspath(__file__))
DOMS = ["gmvnsl.com", "socomec.co.uk", "hochbahnwache.de"]
CACHE = os.path.join(HERE, "blitz_company_cache.json")
cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
for d in DOMS:
    if d in cache:
        continue
    s, j = call("POST", "/v2/enrichment/domain-to-linkedin", {"domain": d})
    rec = {"lookup_status": s, "lookup": {k: v for k, v in j.items() if k != "fair_usage"}}
    if s == 200 and j.get("found") and j.get("company_linkedin_url"):
        s2, c = call("POST", "/v2/enrichment/company", {"company_linkedin_url": j["company_linkedin_url"]})
        rec["company_status"], rec["company"] = s2, (c.get("company") if isinstance(c, dict) else None)
    cache[d] = rec
json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for d in DOMS:
    c = cache[d].get("company") or {}
    print(d, "|", c.get("name"), "|", c.get("industry"), "|", c.get("size"), "|", c.get("linkedin_url"))
