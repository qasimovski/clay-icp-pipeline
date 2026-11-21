"""Rail OS: qe_portal2.jsonl (21 whole-government portal bodies, dl_portal2.py) -> people rows -> post_filter ->
appended to ../final/"Rail OS - email list (Clay + Deepline).csv" / "... - LinkedIn only (Clay + Deepline).csv".
Employer filter EMPLOYER: only staff of the body itself or its direct transport successor (helper-agent web check,
21 Nov 2026). Sheet rules via post_filter.py (geography, Germany LinkedIn-only, sanctioned, titles); titles failing
only the buy title list are kept and flagged (user, 21 Nov 2026: "Keep them, flagged" for portal-body people).
Excluded: anyone already in the OS lists, the removed-portal file, or the Rail CE review lists.
"""
import csv, json, os, re, subprocess, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dl_build as B  # noqa: E402
C = B.C
FINAL = os.path.join(HERE, "..", "final")
EMPLOYER = {
    "www.gov.br": r"Minist.rio dos Transportes|Minist.rio da Infraestrutura|\bDNIT\b|\bANTT\b",
    "www.rj.gov.br": r"Secretaria de Estado (de Transporte|das Cidades)",
    "nt.gov.au": r"Northern Territory Government|Logistics (and|&) Infrastructure",
    "admin.ch": r"Bundesamt f.r Verkehr|Federal Office of Transport|\bBAV\b|RailCom|SUST|UVEK|DETEC",
    "gouv.mc": r"Gouvernement (de )?Monaco",
    "nl.gob.mx": r"Metrorrey|Nuevo Le.n",
    "wv.gov": r"Mountain Rail|Durbin",
    "gov.md": r"Moldova",
    "www.gob.mx": r"Transporte Ferroviario|ATTRAPI|Comunicaciones y Transportes",
    "www.gob.pe": r"OSITR.N",
}
PRE = os.path.join(HERE, "Rail OS Portal2")


def main():
    feed = {r["Domain"]: r for r in B.rd(os.path.join(HERE, "..", "people_feed.csv"))}
    have = set()
    for f in ("Rail OS - email list (Clay + Deepline).csv", "Rail OS - LinkedIn only (Clay + Deepline).csv",
              "Rail OS - removed portal people.csv", "Rail OS - dropped by post filter.csv"):
        p = os.path.join(FINAL, f)
        if os.path.exists(p):
            have |= {C.slug(r["LinkedIn URL"]) for r in B.rd(p) if r.get("LinkedIn URL")}
    have |= {C.slug(r["LinkedIn URL"]) for f in ("Rail CE - review.csv", "Rail CE - review - LinkedIn only.csv")
             for r in B.rd(os.path.join(B.CE_FINAL, f)) if r.get("LinkedIn URL")}
    iso = B.iso_map()
    en = json.load(open(B.CACHE, encoding="utf-8"))
    cols = ["Row ID", "Company Name", "Domain", "Side", "Classification", "Search", "Countries Override", "Event",
            "Editions", "Description"] + C.PERSON_COLS + ["Capped", "Outcome", "Job Title (Original)", "Person Source", "Title Check"]
    rows, seen, st = [], set(), {}
    for l in open(os.path.join(HERE, "qe_portal2.jsonl"), encoding="utf-8"):
        x = json.loads(l)
        pat = EMPLOYER.get(x["portal"])
        if not pat or not re.search(pat, x.get("company_name") or "", re.I):
            st["other employer"] = st.get("other employer", 0) + 1
            continue
        first, last, title, li = B.na(x.get("first_name")), B.na(x.get("last_name")), B.na(x.get("title")), B.na(x.get("employee_linkedin"))
        s = C.slug(li)
        if not (first and last and title and s) or s in seen or s in have:
            st["no name/title/LinkedIn, duplicate or already listed"] = st.get("no name/title/LinkedIn, duplicate or already listed", 0) + 1
            continue
        seen.add(s)
        f = feed[x["portal"]]
        title = " ".join(title.split())
        row = {"Row ID": f["Row ID"], "Company Name": x["body"], "Domain": x["portal"], "Side": f["Side"],
               "Classification": f["Classification"], "Search": "portal + department keywords (Deepline)",
               "Countries Override": "", "Event": f["Events"], "Editions": "", "Description": "",
               "Person Name": f"{first} {last}", "First Name": first, "Last Name": last, "Job Title": title,
               "LinkedIn URL": li, "Mailing City": B.na(x.get("city")), "Mailing State Province": "",
               "Mailing Country": B.country_name(B.na(x.get("country_code")).upper(), iso), "Capped": "",
               "Outcome": "found (Deepline)", "Person Source": "Deepline",
               "Title Check": f"found on the government portal by department keywords; employer: {x.get('company_name')}"}
        t_en = en.get(title, "")
        if t_en and t_en.lower() != title.lower():
            row.update({"Job Title": t_en, "Job Title (Original)": title})
        rows.append(row)
    B.wr(PRE + " - people.csv", rows, cols)
    print(st, "->", len(rows), "people")
    subprocess.run([sys.executable, os.path.join(B.SKILL, "post_filter.py"), "--people", PRE + " - people.csv",
                    "--icp", os.path.join(B.CFG, "icp.yaml"), "--rules", os.path.join(B.CFG, "post_filter.yaml"),
                    "--out-prefix", PRE], check=True)
    email = B.rd(PRE + " - email list.csv") if os.path.exists(PRE + " - email list.csv") else []
    lio = B.rd(PRE + " - LinkedIn only.csv") if os.path.exists(PRE + " - LinkedIn only.csv") else []
    dropped = B.rd(PRE + " - dropped by post filter.csv") if os.path.exists(PRE + " - dropped by post filter.csv") else []
    keep_flag = [dict(r, **{"Title Check": (r.get("Title Check") or "") + "; kept for the reviewer (user 21 Nov 2026): title not on the sheet buy list"})
                 for r in dropped if (r.get("Outcome") or "").startswith("found") and "title not on the buy-side" in json.dumps(r)]
    for name, add in (("email list", email + keep_flag), ("LinkedIn only", lio)):
        p = os.path.join(FINAL, f"Rail OS - {name} (Clay + Deepline).csv")
        with open(p, encoding="utf-8-sig", newline="") as fh:
            rd = csv.DictReader(fh); fcols = rd.fieldnames; cur = list(rd)
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fcols, extrasaction="ignore"); w.writeheader(); w.writerows(cur + add)
        print(f"{name}: +{len(add)} -> {len(cur) + len(add)}")


if __name__ == "__main__":
    main()
