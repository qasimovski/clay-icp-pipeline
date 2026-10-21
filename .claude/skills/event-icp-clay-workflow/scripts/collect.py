"""Turn bulk-run result files into the next step's CSV.

    classified   classify results + feed      -> classified.csv (Side, Classification, enrichment)
    people-feed  classified.csv               -> people_feed.csv (one row per searchable Domain)
    capped       people_feed + people results -> people_feed - sen A.csv / - sen B.csv
    people       classified + people results  -> people.csv (+ excluded.csv, duplicates.csv)

    python collect.py classified  --feed feed.csv --results runs/classify/results --id-cols "Event,Entity,Domain" --out classified.csv
    python collect.py people-feed --classified classified.csv --out people_feed.csv [--countries-col "Countries Override"]
    python collect.py capped      --feed people_feed.csv --results runs/people/results --out-prefix people_feed
    python collect.py people      --classified classified.csv --results runs/people/results \
                                  [--split runs/sen_a/results --split runs/sen_b/results] --icp icp.yaml --out people.csv

Nothing is dropped silently: every removed person is written out with the reason,
and companies that returned nobody are counted by outcome.
"""

import argparse
import collections
import csv
import json
import os
import re
import sys

PERSON_COLS = ["Person Name", "First Name", "Last Name", "Job Title", "LinkedIn URL",
               "Mailing City", "Mailing State Province", "Mailing Country"]
CAP = 10   # Find People's maximum with full profiles


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_results(results_dir):
    """{id: record}; later files win, so retry_*.jsonl supersede chunk_*.jsonl."""
    out = {}
    for n in sorted(os.listdir(results_dir)):
        if n.endswith(".jsonl"):
            for line in open(os.path.join(results_dir, n), encoding="utf-8"):
                if line.strip():
                    rec = json.loads(line)
                    out[rec["id"]] = rec
    return out


def row_id(r, id_cols):
    return "|".join((r.get(c) or "").strip() for c in id_cols)


def people_of(rec):
    res = (rec or {}).get("result") or {}
    inner = res.get("result") or res
    return inner.get("people") or [], str(res.get("textPreview") or "").replace("❌", "").strip()


# ------------------------------------------------------------------ classified
def cmd_classified(a):
    header, feed = read_csv(a.feed)
    res = load_results(a.results)
    id_cols = [c.strip() for c in a.id_cols.split(",")]
    extra_keys, tally = [], collections.Counter()
    for r in feed:
        rec = res.get(row_id(r, id_cols))
        side = cls = ""
        if rec is None:
            outcome = "not run"
        elif rec.get("status") != "complete":
            outcome = "run " + str(rec.get("status"))
        else:
            out = rec.get("result") or {}
            so = out.get("structuredOutputs") or out.get("result") or out
            side, cls = so.get("Side") or "", so.get("Classification") or ""
            # Anything else the final node returned (enrichment from a "Company
            # Output" code node) rides along; a name the feed already uses gets
            # a "Clay " prefix rather than overwriting the scraped value.
            for k, v in so.items():
                if k in ("Side", "Classification") or isinstance(v, (dict, list)):
                    continue
                col = k if k not in header else "Clay " + k
                if col not in extra_keys:
                    extra_keys.append(col)
                r[col] = "" if v is None else v
            outcome = "classified" if side and cls else (so.get("reason") or "skipped")
        r.update({"Side": side, "Classification": cls, "Outcome": outcome})
        tally[outcome if outcome != "classified" else f"classified: {side}"] += 1
    write_csv(a.out, header + ["Side", "Classification", "Outcome"] + extra_keys, feed)
    print(f"{len(feed):,} rows -> {a.out}")
    for k, n in tally.most_common():
        print(f"  {k:40} {n:6,}")
    cats = collections.Counter((r["Side"], r["Classification"]) for r in feed if r["Classification"])
    print("top classifications:")
    for (s, c), n in cats.most_common(15):
        print(f"  {s:10} {c[:50]:52} {n:5}")


# ------------------------------------------------------------------ people-feed
def cmd_people_feed(a):
    header, rows = read_csv(a.classified)
    out, seen = [], set()
    for r in rows:
        d = (r.get("Domain") or "").strip()
        if not d or not r.get("Side") or not r.get("Classification") or d in seen:
            continue
        if (r.get("Domain Flag") or "").startswith(("shared", "hosted")):
            continue                       # fix these domains first; never search a shared host
        seen.add(d)
        out.append({"Company Name": r.get("Company Name", ""), "Domain": d,
                    "Side": r["Side"], "Classification": r["Classification"],
                    "Countries Override": r.get(a.countries_col, "") if a.countries_col else "",
                    "Seniority Group": "", "Fit": ""})
    write_csv(a.out, ["Company Name", "Domain", "Side", "Classification",
                      "Countries Override", "Seniority Group", "Fit"], out)
    print(f"{len(out):,} searchable companies (unique Domain, classified, not a shared host) -> {a.out}")


# ------------------------------------------------------------------ capped
def cmd_capped(a):
    header, rows = read_csv(a.feed)
    res = load_results(a.results)
    capped = [r for r in rows if res.get(r["Domain"], {}).get("status") == "complete"
              and len(people_of(res[r["Domain"]])[0]) >= CAP]
    for g in ("A", "B"):
        p = f"{a.out_prefix} - sen {g}.csv"
        write_csv(p, header, [{**r, "Seniority Group": g} for r in capped])
        print(f"group {g}: {len(capped):,} companies -> {p}")


# ------------------------------------------------------------------ people
def word_re(term):
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.I)


def person_cells(p):
    loc = p.get("structured_location") or {}
    country = loc.get("country") or ""
    city, state = ((v if v != country else "") for v in (loc.get("city") or "", loc.get("state") or ""))
    return [p.get("name", ""), p.get("first_name") or "", p.get("last_name") or "",
            p.get("title", "") or "", p.get("url", ""), city, state, country]


def slug(url):
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", str(url or "").lower())
    return m.group(1) if m else ""


def cmd_people(a):
    import yaml
    icp = yaml.safe_load(open(a.icp, encoding="utf-8")) or {}
    excl = [(t, word_re(t)) for t in icp.get("exclude_titles") or []]
    rescue = [word_re(t) for t in icp.get("rescue_titles") or []]

    header, companies = read_csv(a.classified)
    res = load_results(a.results)
    by_domain = {d: people_of(rec) for d, rec in res.items() if rec.get("status") == "complete"}
    failed = {d for d, rec in res.items() if rec.get("status") != "complete"}
    if a.split:
        halves = [load_results(d) for d in a.split]
        doms = set(halves[0])
        if any(set(h) != doms for h in halves[1:]):
            raise SystemExit("split result dirs cover different Domains")
        bad = [d for d in doms if any(h[d].get("status") != "complete" for h in halves)]
        if bad:
            raise SystemExit(f"{len(bad)} split searches incomplete (e.g. {bad[:3]}) — retry them first")
        for d in doms:
            ppl = [p for h in halves for p in people_of(h[d])[0]]
            by_domain[d] = (ppl, "" if ppl else "no people")
        print(f"seniority split applied to {len(doms):,} companies")
    if failed - set(by_domain):
        raise SystemExit(f"{len(failed - set(by_domain))} people searches failed — "
                         f"bulk_run.py --retry-failed before exporting")

    comp_cols = [c for c in header if c not in ("Outcome",)]
    out_header = comp_cols + PERSON_COLS + ["Outcome"]
    kept_rows, excluded, dupes = [], [], []
    seen = {}
    tally = collections.Counter()
    for c in companies:
        d = (c.get("Domain") or "").strip()
        base = [c.get(k, "") for k in comp_cols]
        if d not in by_domain:
            tally["not searched"] += 1
            continue
        people, note = by_domain[d]
        if not people:
            tally[note or "no people"] += 1
            continue
        n_kept = 0
        for p in people:
            title = p.get("title", "") or ""
            hit = next((t for t, rx in excl if rx.search(title)), None)
            if hit and not any(rx.search(title) for rx in rescue):
                excluded.append(base + person_cells(p) + [f"excluded: {hit}"])
                continue
            s = slug(p.get("url"))
            if s and s in seen:
                dupes.append(base + person_cells(p) + [f"duplicate of {seen[s]}"])
                continue
            if s:
                seen[s] = d
            kept_rows.append(base + person_cells(p) + ["found"])
            n_kept += 1
        tally["found" if n_kept else "all people excluded or duplicate"] += 1

    def dump(path, rows):
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(out_header)
            w.writerows(rows)
    dump(a.out, kept_rows)
    dump(a.out[:-4] + " - excluded.csv", excluded)
    dump(a.out[:-4] + " - duplicates.csv", dupes)
    found = len(kept_rows) + len(excluded) + len(dupes)
    print(f"people found {found:,} = delivered {len(kept_rows):,} + excluded {len(excluded):,} "
          f"+ duplicates {len(dupes):,}")
    for k, n in tally.most_common():
        print(f"  companies {k:34} {n:6,}")
    print(f"-> {a.out}  (+ excluded / duplicates beside it)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classified")
    c.add_argument("--feed", required=True)
    c.add_argument("--results", required=True)
    c.add_argument("--id-cols", default="Event,Entity,Domain")
    c.add_argument("--out", required=True)
    f = sub.add_parser("people-feed")
    f.add_argument("--classified", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--countries-col", default="")
    k = sub.add_parser("capped")
    k.add_argument("--feed", required=True)
    k.add_argument("--results", required=True)
    k.add_argument("--out-prefix", required=True)
    p = sub.add_parser("people")
    p.add_argument("--classified", required=True)
    p.add_argument("--results", required=True)
    p.add_argument("--split", action="append", default=[])
    p.add_argument("--icp", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"classified": cmd_classified, "people-feed": cmd_people_feed,
     "capped": cmd_capped, "people": cmd_people}[a.cmd](a)


if __name__ == "__main__":
    main()
