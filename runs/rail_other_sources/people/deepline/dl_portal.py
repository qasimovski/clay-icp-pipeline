"""Rail OTHER SOURCES: free Deepline people search for the 9 portal bodies (21 Nov 2026).

User, 21 Nov 2026: "Search them again and see if you can find their specific websites/portals. If not, then when
finding people for these 9 bodies, use keysearch relevant to their department to get relevant people." Own sites were
found (../../portal_bodies.csv) but Clay Find People cannot resolve those subdomains (4-row test, 0 people). User:
"Deepline first, then Clay". Two free quickenrich_contact_search queries per body:
  A. company_name = the body's names (listing name + common English name), title = its buyer group's icp.yaml titles;
  B. company_url = the portal domain, title = the body's department keywords (portal_bodies.csv), i.e. the
     department keysearch on the whole-government domain.
post_filter.py then applies the sheet's buy titles, geography and Germany / sanctioned rules, as for everyone.

    python dl_portal.py      -> qe_portal.jsonl (raw records + body, query)
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dl_search import call, title_sets  # noqa: E402

OS = os.path.join(HERE, "..", "..")
LABEL = {  # classifier answers, ../../classify/runs_portal (21 Nov 2026)
    "act.gov.au": "Passenger rail and metro operators", "huangshi.gov.cn": "Passenger rail and metro operators",
    "ap.gov.in": "Project owners and developers", "gouv.qc.ca": "Project owners and developers",
}
NAMES = {
    "act.gov.au": ["Transport Canberra"], "ap.gov.in": ["Andhra Pradesh Metro Rail Corporation", "Visakhapatnam Metro Rail"],
    "gouv.qc.ca": ["Mobilité Infra Québec", "Mobilite Infra Quebec"], "sarawak.gov.my": ["Ministry of Transport Sarawak"],
    "guanajuato.gob.mx": ["Secretaría de Obra Pública Guanajuato", "Secretaria de Obra Publica del Estado de Guanajuato"],
    "gouv.cd": ["Ministère des Transports, Voies de Communication et Désenclavement", "Ministry of Transport DRC"],
    "beijing.gov.cn": ["Beijing Municipal Commission of Transport"],
    "sh.gov.cn": ["Shanghai Municipal Transportation Commission"], "huangshi.gov.cn": ["Huangshi Rail Transit"],
}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sets = title_sets()
    inp = os.path.join(HERE, "qe_input_portal.json")
    out = open(os.path.join(HERE, "qe_portal.jsonl"), "w", encoding="utf-8")
    for b in csv.DictReader(open(os.path.join(OS, "portal_bodies.csv"), encoding="utf-8-sig")):
        dom = b["Portal Domain"]
        group = LABEL.get(dom, "Infrastructure owners and authorities")
        n = {}
        recs = []
        for tc in sets["buy: " + group]:
            data, _ = _name_call(inp, NAMES[dom], tc)
            recs += [dict(r, body=dom, query="A name", group="buy: " + group) for r in data]
        n["A"] = len(recs)
        kw = [k.strip() for k in b["Department Keywords (fallback)"].split(";") if k.strip()]
        data, _ = call(inp, [dom], 1, kw)
        recs += [dict(r, body=dom, query="B portal+dept", group="buy: " + group) for r in data]
        n["B"] = len(data)
        for r in recs:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{dom:20s} {b['Body'][:45]:45s} A(name) {n['A']:3d}  B(portal+dept) {n['B']:3d}", flush=True)
    out.close()


def _name_call(inp, names, tchunk):
    import subprocess
    q = {"company_name": {"include": names}, "title": {"include": tchunk}, "per_page": 100, "page": 1}
    json.dump(q, open(inp, "w", encoding="utf-8"), ensure_ascii=False)
    p = subprocess.run(f'deepline tools execute quickenrich_contact_search --input "@{inp}" --json --timeout 3m',
                       shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    raw = json.loads(p.stdout)["toolResponse"]["rawV2"]
    return (raw.get("data") or []), (raw.get("meta") or {})


if __name__ == "__main__":
    main()
