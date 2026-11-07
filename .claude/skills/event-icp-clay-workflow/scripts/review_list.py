"""The one canonical list the marketing manager reviews: Find People results + speakers, with the
company's firmographics on every row (Mining, Oct 2026).

    domains  every company domain in the given CSVs that has no Enrich Company result yet
             -> the Enrich Company by domain feed (build_tool_workflows.py enrich-company; measured
             1 action, 0 data credits)
    build    post_filter.py's email list + LinkedIn-only file (+ speakers.py people rows)
             + Enrich Company results  ->  "<prefix>.csv" and "<prefix> - LinkedIn only.csv"

    python review_list.py domains --in "<S> - email list.csv" "<S> - LinkedIn only.csv" spk/speakers.csv \
        [--skip-results runs/enrich_company/results] --out company_feed.csv
    python review_list.py build --people "<S> - email list.csv" --linkedin-only "<S> - LinkedIn only.csv" \
        [--speakers "spk/<S> - speakers.csv"] [--company-results runs/enrich_company/results] \
        --rules post_filter.yaml --out-prefix "<S> - review" [--drop-cols "Title Check,Countries Override"]

A speaker who is already in the people list is ONE row, tagged Source "Find People + Speaker" with
their speaker events; never two. Speaker-only rows (Source "Speaker") are routed to the LinkedIn-only
file when they sit in a `linkedin_only_countries` country, exactly like everyone else. Which columns
the manager sees is the user's call (--drop-cols): Mining dropped Title Check, Countries Override
and the company's Country, because Mailing Country (the person's) is the one used.
"""

import argparse
import collections
import csv
import hashlib
import os
import re
import sys
import unicodedata
from urllib.parse import quote, unquote

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_feed import norm_domain  # noqa: E402
from collect import load_results  # noqa: E402

csv.field_size_limit(10**9)
# Solar, 28 Oct 2026: + Company Description, Specialities and Total Funding - the Salesforce schema has all three
# and Enrich Company returns them; without them the upload left those columns empty.
COMPANY_COLS = ["Company Size", "Company Type", "Company Industry", "Sub - Industry", "Primary Offerings",
                "Business Stage", "Annual Revenue", "Founded", "Company LinkedIn URL", "Company Website",
                "Company Description", "Specialities", "Total Funding Amount Range USD"]
SPEAKER_COLS = ["Source", "Speaker Event(s)", "Speaker Event Title"]


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write(path, rows, cols):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def slug(u):
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", (u or "").lower())
    return m.group(1).strip("/") if m else (u or "").lower()


def canonical_linkedin(u):
    """https://www.linkedin.com/in/<slug>: no trailing slash, query, fragment or country subdomain.
    Clay writes ".../in/x/" and Blitz ".../in/x"; the email step dedupes on the URL TEXT, so two spellings
    of one profile would be enriched (and paid for) twice. Solar, 29 Oct 2026: 285 profiles had both."""
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", u or "")
    if not m:
        return (u or "").strip()
    # One spelling per profile, also for non-ASCII slugs (Solar, Nov 2026: "zu%c3%b1iga", "zu%25c3%25b1iga"
    # and the garbled "zuã±iga" were three spellings of one person): decode, repair, lower-case, re-encode.
    s = m.group(1)
    for _ in range(2):
        s = unquote(s)
    try:
        s = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return "https://www.linkedin.com/in/" + quote(s.strip("/").lower(), safe="-_.~")


def person_id(u):
    """A short stable id per LinkedIn profile (Clay bulk ids max 64 chars; 1,137 Solar URLs were longer):
    p_ + sha1(canonical URL)[:16]. The email run keys on it, so one profile is one paid lookup."""
    return "p_" + hashlib.sha1(canonical_linkedin(u).encode("utf-8")).hexdigest()[:16]


def dedupe_people(rows):
    """One row per person per company. Clay and Blitz can return DIFFERENT URLs for one person (an old slug vs the
    current vanity one): Solar had 1,348 such people, each about to be enriched twice. Same name + same company
    domain = one person; keep the profile both sources returned (Source has "+"), else Blitz's (current), else
    the best-ranked. Same profile on several rows (several companies or events) is NOT touched here: the email
    run sends one lookup per Person ID. -> (kept, dropped), each dropped row with "Duplicate Of"."""
    def nm(s):
        return " ".join(re.findall(r"[a-z]+", unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore")
                                    .decode().lower()))
    groups = collections.defaultdict(list)
    for r in rows:
        k = (nm(r.get("Person Name")), (r.get("Domain") or "").strip().lower())
        groups[k if k[0] else ("", r.get("LinkedIn URL"))].append(r)

    def rank(r):
        src = r.get("Source") or ""
        try:
            n = int(r.get("Rank In Company") or 9999)
        except ValueError:
            n = 9999
        return (0 if "+" in src else 1 if "Blitz" in src else 2, n)
    keep_url = {}
    for k, rs in groups.items():
        if len({canonical_linkedin(r.get("LinkedIn URL")) for r in rs}) > 1:
            keep_url[k] = canonical_linkedin(min(rs, key=rank).get("LinkedIn URL"))
    kept, dropped = [], []
    for r in rows:
        k = (nm(r.get("Person Name")), (r.get("Domain") or "").strip().lower())
        want = keep_url.get(k)
        if want and canonical_linkedin(r.get("LinkedIn URL")) != want:
            dropped.append({**r, "Duplicate Of": want})
        else:
            kept.append(r)
    return kept, dropped


def join(v):
    return ", ".join(str(x) for x in v if x) if isinstance(v, list) else (str(v) if v else "")


def companies(dirs):
    out = {}
    for d in dirs:
        for k, v in load_results(d).items():
            res = v.get("result") or {}
            p = res.get("result") or res
            if v.get("status") != "complete" or not p.get("name"):
                continue
            dd = p.get("derived_datapoints") or {}
            out[norm_domain(k)] = {
                "Company Size": p.get("size") or "", "Company Type": p.get("type") or "",
                "Company Industry": p.get("industry") or "", "Sub - Industry": join(dd.get("subindustry")),
                "Primary Offerings": join(dd.get("primary_offerings")),
                "Business Stage": join(dd.get("business_stage")), "Annual Revenue": p.get("annual_revenue") or "",
                "Founded": str(p.get("founded") or ""), "Company LinkedIn URL": p.get("url") or "",
                "Company Website": p.get("website") or "",
                "Company Description": " ".join((p.get("description") or "").split()),
                "Specialities": join(p.get("specialties")),
                "Total Funding Amount Range USD": p.get("total_funding_amount_range_usd") or ""}
    return out


def companies_from_csv(paths):
    """Company columns from a company list that already holds firmographics (a Clay company search:
    merge_company_search.py output) - the 'skip Enrich Company' route (Solar buy side, 6 Nov 2026)."""
    out = {}
    for path in paths:
        for r in read(path):
            d = norm_domain(r.get("Domain"))
            if not d or d in out:
                continue
            out[d] = {"Company Size": r.get("Size") or "", "Company Type": r.get("Type") or "",
                      "Company Industry": r.get("Industry") or "", "Annual Revenue": r.get("Annual Revenue") or "",
                      "Company LinkedIn URL": r.get("LinkedIn URL") or "", "Company Website": d,
                      "Company Description": " ".join((r.get("Description") or "").split())}
    return out


def cmd_domains(a):
    done = set()
    for d in a.skip_results:
        done |= {norm_domain(k) for k, v in load_results(d).items() if v.get("status") == "complete"}
    doms = []
    for path in a.inputs:
        for r in read(path):
            d = norm_domain(r.get(a.col))
            if d and d not in done and d not in doms:
                doms.append(d)
    write(a.out, [{"Company Domain": d} for d in doms], ["Company Domain"])
    print(f"{len(doms):,} domains to enrich ({len(done):,} already have a result) -> {a.out}")


def cmd_build(a):
    rules = yaml.safe_load(open(a.rules, encoding="utf-8")) or {}
    li_countries = set(rules.get("linkedin_only_countries") or [])
    comp = companies_from_csv(a.company_csv)
    comp.update(companies(a.company_results))  # an Enrich Company result beats the search's columns
    people = read(a.people)
    li_only = read(a.linkedin_only) if a.linkedin_only else []
    if not people:
        raise SystemExit(f"{a.people} is empty")
    base_cols = list(people[0].keys())

    spk = collections.OrderedDict()
    for r in (read(a.speakers) if a.speakers else []):
        spk.setdefault(slug(r["LinkedIn URL"]), r)

    tagged = 0
    for lst in (people, li_only):
        for p in lst:
            p.setdefault("Source", "Find People")
            p.setdefault("Speaker Event(s)", "")
            p.setdefault("Speaker Event Title", "")
            p.update(comp.get(norm_domain(p.get("Domain")), {}))
            s = spk.pop(slug(p.get("LinkedIn URL")), None)
            if s:
                p.update({"Source": "Find People + Speaker", "Speaker Event(s)": s.get("Speaker Event(s)", ""),
                          "Speaker Event Title": s.get("Speaker Event Title", "")})
                tagged += 1
    added = collections.Counter()
    for s in spk.values():
        row = {c: "" for c in base_cols}
        row.update(s)
        row.update(comp.get(norm_domain(s.get("Domain"))) or {c: s.get(c, "") for c in COMPANY_COLS})
        li = s.get("Mailing Country") in li_countries
        (li_only if li else people).append(row)
        added["LinkedIn only" if li else "main list"] += 1

    drop = {c.strip() for c in (a.drop_cols or "").split(",") if c.strip()}
    cols = [c for c in dict.fromkeys(base_cols + SPEAKER_COLS + COMPANY_COLS) if c not in drop]
    out_main, out_li = a.out_prefix + ".csv", a.out_prefix + " - LinkedIn only.csv"
    for p in people + li_only:
        p["LinkedIn URL"] = canonical_linkedin(p.get("LinkedIn URL"))
        p["Person ID"] = person_id(p["LinkedIn URL"])
    cols = ["Person ID"] + [c for c in cols if c != "Person ID"]
    people, d1 = dedupe_people(people)
    li_only, d2 = dedupe_people(li_only)
    if d1 or d2:
        write(a.out_prefix + " - duplicates removed.csv", d1 + d2, cols + ["Duplicate Of"])
        print(f"people-level duplicates removed (same person + company, two LinkedIn URLs): {len(d1) + len(d2):,}")
    write(out_main, people, cols)
    write(out_li, li_only, cols)
    print(f"speakers: {tagged:,} already in the people lists (tagged), added {dict(added)}")
    print(f"{out_main}: {len(people):,} | {out_li}: {len(li_only):,}")
    firmo = sum(1 for p in people if p.get("Company Size") or p.get("Company Industry"))
    sub = sum(1 for p in people if p.get("Sub - Industry"))
    print(f"company columns filled: firmographics {firmo:,}/{len(people):,}, Sub - Industry {sub:,}/{len(people):,}")
    missing = {norm_domain(p.get("Domain")) for p in people + li_only} - set(comp) - {""}
    if missing and a.company_results:
        print(f"  {len(missing):,} domains have no Enrich Company result - `review_list.py domains` lists them")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("domains")
    d.add_argument("--in", dest="inputs", nargs="+", required=True)
    d.add_argument("--col", default="Domain")
    d.add_argument("--skip-results", nargs="*", default=[])
    d.add_argument("--out", required=True)
    b = sub.add_parser("build")
    b.add_argument("--people", required=True, help="post_filter.py email list")
    b.add_argument("--linkedin-only", help="post_filter.py LinkedIn-only file")
    b.add_argument("--speakers", help="speakers.py people output")
    b.add_argument("--company-results", nargs="*", default=[])
    b.add_argument("--company-csv", nargs="*", default=[],
                   help="company list(s) that already carry firmographics (Domain, Size, Type, Industry, "
                        "Annual Revenue, LinkedIn URL, Description): used instead of Enrich Company")
    b.add_argument("--rules", required=True)
    b.add_argument("--drop-cols", default="")
    b.add_argument("--out-prefix", required=True)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"domains": cmd_domains, "build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    main()
