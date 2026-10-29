"""Speakers: find each speaker's LinkedIn profile without false positives, enrich it, and turn it
into people rows the review list, the per-event tables and Salesforce can take.

Event sites list a speaker's name, title and company only. A wrong profile is far worse than a
miss (the person gets contacted), so the match is strict and every step is checked (Mining, Oct
2026: 1,168 speakers -> 96 already in the people search, 580 strict TinyFish matches, the rest to a
Claygent; 20 profiles the two methods disagreed on were excluded).

    prep         remap_prior.py feed_speakers.csv -> speakers.csv (one row per name + company, stable id)
    search       TinyFish web search '"<name>" "<company>" linkedin' per speaker -> cache (resumable)
    match        stage 0: same person already in the people results (name tokens + company domain);
                 stage 1: strict match on the cached search (see judge()) -> matched.csv
    finder-feed  the unresolved -> the LinkedIn Finder Claygent's feed (PAID: ~1 action + 0.1 credit each)
    enrich-feed  strict matches + Claygent "found"/High -> the Enrich Person feed (Row ID, LinkedIn URL)
    people       Enrich Person (+ Enrich Company by domain) results -> people-shaped speaker rows

    python speakers.py prep    --speakers remap/feed_speakers.csv --feed remap/feed_companies.csv                                --decisions remap/decisions.csv --out spk/speakers.csv
    python speakers.py search  --speakers spk/speakers.csv --cache spk/lookups.jsonl [--limit 50] [--env-file .env]
    python speakers.py match   --speakers spk/speakers.csv --cache spk/lookups.jsonl --people people.csv --out spk/matched.csv
    python speakers.py finder-feed --matched spk/matched.csv --out spk/finder_feed.csv [--confirm]
    python speakers.py enrich-feed --matched spk/matched.csv [--finder-feed spk/finder_feed.csv \
        --finder-results runs/finder/results] --out spk/enrich_feed.csv
    python speakers.py people  --enrich-feed spk/enrich_feed.csv --person-results runs/enrich_person/results \
        [--company-results runs/enrich_company/results] --matched spk/matched.csv \
        --icp icp.yaml --rules post_filter.yaml --out "spk/<Series> - speakers.csv"

Variables in post_filter.yaml `speakers:` (all optional):
    company_stop_words: [mining, minerals, ...]  the ICP's industry words: too common in company names
                                                 to prove a match ("Gold Mining Ltd")
    titles: flag | filter    flag (default) keeps every speaker in an in-scope country and marks a title
                             that fails the sheet's rules in Title Check; filter drops them (ask the user:
                             Mining kept all speakers, "filter on country only")
The TinyFish key is read from TINYFISH_API_KEY or --env-file; it is never printed.
"""

import argparse
import collections
import csv
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from urllib.parse import unquote

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_feed import norm_domain  # noqa: E402
from collect import load_results  # noqa: E402
from post_filter import Rules  # noqa: E402
from remap_prior import company_key  # noqa: E402

csv.field_size_limit(10**9)
SEARCH_ENDPOINT = "https://api.search.tinyfish.ai"
HONORIFIC = re.compile(r"\b(dr|mr|mrs|ms|miss|prof|professor|eng|engr|ir|hon|sir|dame|h\.?e|ceng|phd|mba|"
                       r"pr\.?eng|frs|obe|mbe|cbe|jr|sr|ii|iii)\b\.?", re.I)
CO_STOP = {"the", "and", "of", "group", "ltd", "limited", "inc", "incorporated", "plc", "llc", "corp",
           "corporation", "company", "co", "sa", "s", "a", "ag", "gmbh", "pty", "pt", "tbk", "spa",
           "srl", "bv", "nv", "ab", "as", "asa", "oy", "jsc", "llp", "lp", "international", "global",
           "holdings", "holding", "services", "solutions", "for", "de", "la", "du", "des", "et", "y",
           "africa", "south", "middle", "east", "north", "west", "asia", "europe"}
PROFILE = re.compile(r"linkedin\.com/in/([^/?#\s]+)", re.I)
UNRESOLVED = ("no match", "ambiguous", "not searched")


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write(path, rows, cols=None):
    cols = cols or (list(rows[0].keys()) if rows else [])
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def fold(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def name_tokens(name):
    s = HONORIFIC.sub(" ", fold(re.sub(r"\(.*?\)", " ", name or "")))
    return [t for t in re.findall(r"[a-z]+", s) if len(t) > 1]


def skey(name, company):
    """Stable speaker id: name tokens + company key. Never the row position, which shifts whenever
    the kept speakers change (a positional id misaligned the Mining search cache once)."""
    return " ".join(name_tokens(name)) + "|" + company_key(company)


def row_id(sid):
    return hashlib.sha1(sid.encode("utf-8")).hexdigest()[:16]     # Clay bulk ids must be <= 64 chars


def slug(u):
    m = PROFILE.search(unquote(u or ""))
    return m.group(1).lower().strip("/") if m else ""


def speaker_cfg(rules_path):
    if not rules_path:
        return {}
    return (yaml.safe_load(open(rules_path, encoding="utf-8")) or {}).get("speakers") or {}


# ------------------------------------------------------------------ prep
def cmd_prep(a):
    dom = {company_key(r["Company Name"]): r["Domain"] for r in read(a.feed) if r.get("Domain")}
    for d in read(a.decisions) if a.decisions else []:       # every kept row's name -> its domain
        if d.get("Domain") and d.get("Decision") == "KEEP":
            dom.setdefault(company_key(d["Company Name"]), d["Domain"])
    out = {}
    for r in read(a.speakers):
        nt = name_tokens(r["Name"])
        if len(nt) < 2:
            continue                      # a single name can't be matched safely
        sid = skey(r["Name"], r["Company"])
        if sid in out:
            out[sid]["Events"] = "; ".join(sorted(set(out[sid]["Events"].split("; ")) | {r["Event"]}))
            continue
        out[sid] = {"sid": sid, "Name": r["Name"], "Job Title": r.get("Job Title", ""),
                    "Company": r.get("Company", ""), "Domain": dom.get(company_key(r.get("Company")), ""),
                    "Country": r.get("Country", ""), "Side": r.get("Side", ""),
                    "Classification": r.get("Classification", ""), "Events": r["Event"]}
    rows = list(out.values())
    write(a.out, rows)
    print(f"{len(rows):,} unique speakers (name + company) -> {a.out}; with a company domain: "
          f"{sum(bool(r['Domain']) for r in rows):,}")


# ------------------------------------------------------------------ search
def api_key(env_file):
    key = os.environ.get("TINYFISH_API_KEY")
    if not key and env_file and os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            if k.strip() == "TINYFISH_API_KEY":
                key = v.strip().strip('"').strip("'")
    if not key:
        raise SystemExit("TINYFISH_API_KEY not set (environment or --env-file) - ask the user where it is")
    return key


QUERY = re.compile(r'^"(.+?)" "(.*)" linkedin$')


def cached(path):
    """sid -> cached search. A record whose sid is not a stable id (an older positional one) is
    re-keyed from its query, which holds the name and company."""
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip():
                d = json.loads(line)
                m = QUERY.match(d.get("query", ""))
                k = d.get("skey") or (d["sid"] if "|" in str(d.get("sid")) else
                                      (skey(m.group(1), m.group(2)) if m else d["sid"]))
                out[k] = d
    return out


def cmd_search(a):
    import requests
    done, key = cached(a.cache), api_key(a.env_file)
    todo = [s for s in read(a.speakers) if s["sid"] not in done]
    todo = todo[:a.limit] if a.limit else todo
    print(f"search: {len(todo):,} speakers at {a.per_minute}/min (TinyFish search; resumable)", flush=True)
    with open(a.cache, "a", encoding="utf-8") as f:
        for n, s in enumerate(todo, 1):
            name = " ".join(w.capitalize() for w in name_tokens(s["Name"]))
            query = f'"{name}" "{s["Company"].strip()}" linkedin'
            res, t0 = None, time.time()
            for _ in range(8):
                t0 = time.time()
                try:
                    resp = requests.get(SEARCH_ENDPOINT, headers={"X-API-Key": key}, params={"query": query},
                                        timeout=30)
                except requests.RequestException:
                    time.sleep(10)
                    continue
                if resp.status_code == 429:
                    time.sleep(int(resp.headers.get("retry-after", "30")) + 1)
                    continue
                if resp.ok:
                    res = resp.json().get("results", [])
                break
            if res is not None:
                f.write(json.dumps({"sid": s["sid"], "query": query,
                                    "results": [{"title": x.get("title"), "url": x.get("url"),
                                                 "snippet": (x.get("snippet") or "")[:400]} for x in res[:8]]},
                                   ensure_ascii=False) + "\n")
                f.flush()
            time.sleep(max(0, 60 / a.per_minute - (time.time() - t0)))
            if n % 100 == 0:
                print(f"  {n:,}/{len(todo):,}", flush=True)
    print("search done")


# ------------------------------------------------------------------ match
def company_tokens(company, domain, stop):
    toks = {t for t in re.findall(r"[a-z0-9]+", fold(company)) if t not in stop and len(t) > 2}
    stem = re.sub(r"^www\.", "", domain or "").split(".")[0]
    if len(stem) > 2:
        toks.add(stem)
    return toks


def judge(s, results, stop):
    """-> (status, url, evidence). A result passes only if ALL hold: a personal /in/ profile URL;
    the name part of the result title holds the speaker's first AND last name tokens; a distinctive
    company token (or the domain stem) appears in the title or snippet. Exactly ONE distinct profile
    may pass: two different people passing is ambiguous, and ambiguous is rejected."""
    nt = name_tokens(s["Name"])
    first, last = nt[0], nt[-1]
    ctoks = company_tokens(s["Company"], s["Domain"], stop)
    passing = {}
    for x in results:
        m = PROFILE.search(x.get("url") or "")
        if not m:
            continue
        title = fold(x.get("title") or "")
        ptoks = set(re.findall(r"[a-z]+", re.split(r"\s[-–|]\s|\|", title)[0]))
        if not (first in ptoks and last in ptoks):
            continue
        in_title = ctoks & set(re.findall(r"[a-z0-9]+", title))
        hit = in_title | (ctoks & set(re.findall(r"[a-z0-9]+", fold(x.get("snippet") or ""))))
        if hit:
            passing.setdefault(m.group(1).lower(), (x, sorted(hit), bool(in_title)))
    if len(passing) == 1:
        sl, (x, hit, in_title) = next(iter(passing.items()))
        return ("matched" if in_title else "matched, company only in snippet (may have moved)",
                f"https://www.linkedin.com/in/{sl}", f"{x.get('title')} | company words: {', '.join(hit)}")
    if len(passing) > 1:
        return "ambiguous", "", " || ".join(sorted(passing))
    return "no match", "", ""


def cmd_match(a):
    stop = CO_STOP | {w.lower() for w in speaker_cfg(a.rules).get("company_stop_words") or []}
    people = collections.defaultdict(list)
    for p in read(a.people):
        people[norm_domain(p.get("Domain"))].append(p)
    found = cached(a.cache)
    out, tally = [], collections.Counter()
    for s in read(a.speakers):
        nt = set(name_tokens(s["Name"]))
        p = next((p for p in people.get(norm_domain(s["Domain"]), []) if nt <= set(name_tokens(p["Person Name"]))), None)
        if p:
            row = dict(s, Status="already found", **{"LinkedIn URL": p["LinkedIn URL"],
                       "Evidence": "same name at the same company in the people search"})
        elif s["sid"] in found:
            st, url, ev = judge(s, found[s["sid"]]["results"], stop)
            row = dict(s, Status=st, **{"LinkedIn URL": url, "Evidence": ev})
        else:
            row = dict(s, Status="not searched", **{"LinkedIn URL": "", "Evidence": ""})
        tally[row["Status"]] += 1
        out.append(row)
    write(a.out, out)
    print(f"{len(out):,} speakers -> {a.out}")
    for k, n in tally.most_common():
        print(f"  {k:52} {n:6,}")


# ------------------------------------------------------------------ finder / enrich feeds
FINDER_FIELDS = ["Row ID", "Speaker ID", "Speaker Name", "Job Title", "Company", "Company Website", "Country",
                 "Events", "Side", "Classification"]


def cmd_finder_feed(a):
    rows = []
    for m in read(a.matched):
        if m["Status"] in UNRESOLVED or (a.confirm and m["Status"].startswith("matched")):
            rows.append({"Row ID": row_id(m["sid"]), "Speaker ID": m["sid"], "Speaker Name": m["Name"],
                         "Job Title": m["Job Title"], "Company": m["Company"], "Company Website": m["Domain"],
                         "Country": m["Country"], "Events": m["Events"], "Side": m["Side"],
                         "Classification": m["Classification"]})
    write(a.out, rows, FINDER_FIELDS)
    print(f"{len(rows):,} speakers -> {a.out}  (LinkedIn Finder Claygent: PAID, price it from the draft "
          f"test; Mining measured 1 action + 0.1 data credit each)")


def finder_answers(feed, results):
    """speaker id -> the Claygent's structured answer."""
    sid_of = {r["Row ID"]: r["Speaker ID"] for r in read(feed)}
    out = {}
    for k, v in load_results(results).items():
        so = (v.get("result") or {}).get("structuredOutputs") or (v.get("result") or {})
        if k in sid_of and v.get("status") == "complete":
            out[sid_of[k]] = so
    return out


def cmd_enrich_feed(a):
    finder = finder_answers(a.finder_feed, a.finder_results) if a.finder_results else {}
    rows, tally = [], collections.Counter()
    for m in read(a.matched):
        sid, url, src = m["sid"], "", ""
        ans = finder.get(sid) or {}
        claygent = ans.get("linkedin_url") if ans.get("status") == "found" and ans.get("confidence") == "High" else ""
        if m["Status"] == "already found":
            tally["already in the people search (tagged by review_list.py)"] += 1
            continue
        if m["Status"].startswith("matched"):
            url, src = m["LinkedIn URL"], "TinyFish strict"
            if ans.get("status") == "found" and slug(ans.get("linkedin_url")) != slug(url):
                tally["EXCLUDED: TinyFish and the Claygent found different profiles"] += 1
                continue
            if claygent:
                src = "TinyFish strict + Claygent"
        elif claygent:
            url, src = claygent, "Claygent High"
        else:
            tally["not resolved (" + (ans.get("status") or m["Status"]) + ")"] += 1
            continue
        tally[src] += 1
        rows.append({"Row ID": row_id(sid), "Speaker ID": sid, "LinkedIn URL": "https://www.linkedin.com/in/"
                     + slug(url), "Profile Source": src, **{k: m[k] for k in
                     ("Name", "Job Title", "Company", "Domain", "Country", "Side", "Classification", "Events")}})
    write(a.out, rows)
    print(f"{len(rows):,} speaker profiles -> {a.out}  (Enrich Person by LinkedIn URL: measured 1 action, 0 data credits)")
    for k, n in tally.most_common():
        print(f"  {k:62} {n:6,}")


# ------------------------------------------------------------------ people
def payload(rec):
    res = (rec or {}).get("result") or {}
    return res.get("result") or res


def join(v):
    return ", ".join(str(x) for x in v if x) if isinstance(v, list) else (str(v) if v else "")


def company_cols(c):
    dd = c.get("derived_datapoints") or {}
    return {"Company Size": c.get("size") or "", "Company Type": c.get("type") or "",
            "Company Industry": c.get("industry") or "", "Sub - Industry": join(dd.get("subindustry")),
            "Primary Offerings": join(dd.get("primary_offerings")),
            "Business Stage": join(dd.get("business_stage")), "Annual Revenue": c.get("annual_revenue") or "",
            "Founded": str(c.get("founded") or ""), "Company LinkedIn URL": c.get("url") or "",
            "Company Website": c.get("website") or "", "Company Description": c.get("description") or "",
            "Specialities": join(c.get("specialties")),
            "Total Funding Amount Range USD": c.get("total_funding_amount_range_usd") or ""}


def location(p):
    """(city, state, country). Enrich Person's nested `structured_location` first (city / state split by
    the provider), then the free-text `location_name`. The country is the profile's `country`."""
    country = p.get("country") or ""
    sl = p.get("structured_location") or {}
    if sl.get("city") or sl.get("state"):
        return sl.get("city") or "", sl.get("state") or "", country
    parts = [x.strip() for x in (p.get("location_name") or "").split(",") if x.strip()]
    if parts and country and parts[-1].lower() == country.lower():
        parts = parts[:-1]
    return (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else ""), country


def cmd_people(a):
    R = Rules(yaml.safe_load(open(a.icp, encoding="utf-8")) or {}, yaml.safe_load(open(a.rules, encoding="utf-8")) or {})
    cfg = speaker_cfg(a.rules)
    stop = CO_STOP | {w.lower() for w in cfg.get("company_stop_words") or []}
    filter_titles = (cfg.get("titles") or "flag") == "filter"
    persons = load_results(a.person_results)
    comps = {}
    for d in a.company_results:
        comps.update({norm_domain(k): payload(v) for k, v in load_results(d).items()
                      if v.get("status") == "complete" and payload(v).get("name")})
    out, checks, tally = [], [], collections.Counter()
    for s in read(a.enrich_feed):
        p = payload(persons.get(s["Row ID"]))
        if not p.get("name"):
            tally["Enrich Person returned nothing"] += 1
            continue
        ce = (p.get("current_experience") or [{}])[0]
        dom = norm_domain(ce.get("company_domain") or "")
        c = comps.get(dom, {})
        company = c.get("name") or ce.get("company") or p.get("org") or ""
        title = ce.get("title") or p.get("title") or s["Job Title"]
        city, state, country = location(p)
        moved = bool(s["Company"]) and company and not (company_tokens(s["Company"], "", stop)
                                                         & company_tokens(company, "", stop)) \
            and not (s["Domain"] and s["Domain"].split(".")[0] in dom)
        reason, f = R.judge(s["Side"], s["Classification"], title, country)
        in_geo = not reason.startswith(("country", "unrecognised"))
        title_ok = not reason
        notes = [] if title_ok or not in_geo else ["speaker: title not on the sheet's list"]
        if moved:
            notes.append("moved since the event")
        row = {"Company Name": company, "Domain": dom or s["Domain"], "Side": s["Side"],
               "Classification": s["Classification"], "Fit": "KEEP", "Events": "",
               "Person Name": p.get("name", ""), "First Name": p.get("first_name") or "",
               "Last Name": p.get("last_name") or "", "Job Title": title,
               "LinkedIn URL": "https://www.linkedin.com/in/" + slug(p.get("url") or s["LinkedIn URL"]),
               "Mailing City": city, "Mailing State Province": state, "Mailing Country": country,
               "Outcome": "speaker", "Persona": f.get("Persona", ""),
               "Title Check": "; ".join([f.get("Title Check", "")] * bool(f.get("Title Check")) + notes),
               "Geo Tier": R.geo_tier(s["Side"], country) if in_geo else "",
               "Source": "Speaker", "Speaker Event(s)": s["Events"], "Speaker Event Title": s["Job Title"],
               "Speaker Event Company": s["Company"], "Profile Source": s["Profile Source"],
               "Headline": p.get("headline") or "", **company_cols(c)}
        # nested current_experience fills what Enrich Company can't (no company match, or a company page only)
        row["Company LinkedIn URL"] = row["Company LinkedIn URL"] or ce.get("url") or ""
        row["Company Website"] = row["Company Website"] or ce.get("company_domain") or ""
        checks.append({**row, "Country In Scope": "yes" if in_geo else "no",
                       "Title Passes Sheet Rules": "yes" if title_ok else "no", "Dropped Because": reason})
        if not in_geo:
            tally["dropped: country not in the side's list"] += 1
        elif filter_titles and not title_ok:
            tally["dropped: title (speakers.titles = filter)"] += 1
        else:
            tally["kept" + (" (title flagged)" if not title_ok else "")] += 1
            out.append(row)
    write(a.out, out, list(checks[0].keys())[:-3] if checks else None)
    write(a.out[:-4] + " - checks.csv", checks)
    print(f"{len(out):,} speaker people rows -> {a.out}  (+ - checks.csv with every profile and why)")
    for k, n in tally.most_common():
        print(f"  {k:52} {n:6,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep")
    p.add_argument("--speakers", required=True)
    p.add_argument("--feed", required=True)
    p.add_argument("--decisions", help="remap_prior.py decisions.csv: more name -> domain matches")
    p.add_argument("--out", required=True)
    s = sub.add_parser("search")
    s.add_argument("--speakers", required=True)
    s.add_argument("--cache", required=True)
    s.add_argument("--limit", type=int)
    s.add_argument("--per-minute", type=int, default=10)
    s.add_argument("--env-file")
    m = sub.add_parser("match")
    m.add_argument("--speakers", required=True)
    m.add_argument("--cache", required=True)
    m.add_argument("--people", required=True, help="the people search's output (Domain, Person Name, LinkedIn URL)")
    m.add_argument("--rules", help="post_filter.yaml (speakers.company_stop_words)")
    m.add_argument("--out", required=True)
    f = sub.add_parser("finder-feed")
    f.add_argument("--matched", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--confirm", action="store_true", help="also send strict matches, as a second opinion (PAID)")
    e = sub.add_parser("enrich-feed")
    e.add_argument("--matched", required=True)
    e.add_argument("--finder-feed")
    e.add_argument("--finder-results")
    e.add_argument("--out", required=True)
    q = sub.add_parser("people")
    q.add_argument("--enrich-feed", required=True)
    q.add_argument("--person-results", required=True)
    q.add_argument("--company-results", action="append", default=[])
    q.add_argument("--icp", required=True)
    q.add_argument("--rules", required=True)
    q.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.cmd == "enrich-feed" and bool(a.finder_feed) != bool(a.finder_results):
        raise SystemExit("--finder-feed and --finder-results go together")
    {"prep": cmd_prep, "search": cmd_search, "match": cmd_match, "finder-feed": cmd_finder_feed,
     "enrich-feed": cmd_enrich_feed, "people": cmd_people}[a.cmd](a)


if __name__ == "__main__":
    main()
