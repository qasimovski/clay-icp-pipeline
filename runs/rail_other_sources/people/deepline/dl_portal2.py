"""Rail OS: free Deepline search for the 21 whole-government portal bodies (user 21 Nov 2026: "remove the 87, apply
the portal rule"). Own sites: ../../portal_bodies_2_sites.csv (web search by a helper agent; most are portal paths /
subdomains that no people search can target). Per body (free quickenrich_contact_search):
  B. company_url = portal domain, title = the body's department keywords (the user's department keysearch);
  C. only where the body has its OWN registrable domain: company_url = that domain, title = its buyer group's titles.
  -> qe_portal2.jsonl (records + portal, body, query)
"""
import csv, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dl_search import call, title_sets  # noqa: E402
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OS = os.path.join(HERE, "..", "..")
sets = title_sets()
feed = {r["Domain"]: r for r in csv.DictReader(open(os.path.join(OS, "people", "people_feed.csv"), encoding="utf-8-sig"))}
inp = os.path.join(HERE, "qe_input_portal2.json")
out = open(os.path.join(HERE, "qe_portal2.jsonl"), "w", encoding="utf-8")
for b in csv.DictReader(open(os.path.join(OS, "portal_bodies_2_sites.csv"), encoding="utf-8-sig")):
    portal = b["Portal Domain"]
    bare = portal.replace("www.", "")
    f = feed[portal]
    grp = "sell" if f["Side"] == "Sell Side" else "buy: " + f["Classification"]
    kw = [k.strip() for k in b["Department Keywords"].split(";") if k.strip()][:50]
    n = {}
    data, _ = call(inp, [bare], 1, kw) if kw else ([], {})
    for r in data:
        out.write(json.dumps(dict(r, portal=portal, body=b["Body"], query="B portal+dept", group=grp), ensure_ascii=False) + "\n")
    n["B"] = len(data)
    n["C"] = 0
    if b["Own Site Type"] == "registrable" and b["Own Site"]:
        for tc in sets[grp.replace(" | rail bio", "")]:
            data, _ = call(inp, [b["Own Site"].lower()], 1, tc)
            for r in data:
                out.write(json.dumps(dict(r, portal=portal, body=b["Body"], query="C own domain", group=grp), ensure_ascii=False) + "\n")
            n["C"] += len(data)
    print(f"{portal:16s} {b['Body'][:48]:48s} B {n['B']:3d}  C {n['C']:3d}", flush=True)
out.close()
