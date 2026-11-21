# Rail OTHER SOURCES copy (21 Nov 2026): unchanged; H = this folder, so companies.csv here is the OS company list.
"""Rail people export keyed by Row ID (19 Nov 2026; copy of scrapers_submarine/_run_v2/people/collect_people.py,
only the ICP path and the Editions source changed). Free, local only.

The skill's `collect.py people` / `capped` assume ONE search per Domain. This run has up to two per company (the main
search + the also_sell capacity-sales search for carriers / cable owners), keyed by people_feed.csv "Row ID", so this
script does the same job per search row, reusing collect.py's helpers (names, ISO countries, exclusions, slugs):

    python collect_people.py export --feed people_feed.csv --results runs/people/results [--split runs/sen_merged] \
        --out pilot/people.csv [--only-done]
      one row per (search row, person); company columns from companies.csv (Event, Editions, Description);
      icp.yaml exclude_titles -> '<out> - excluded.csv'; the same profile twice in ONE search side -> duplicates.
      A profile found by both searches of a company (buy + also_sell) is kept on both rows here; post_filter
      judges each against its own side's rules and `dedupe` keeps one.
    python collect_people.py capped --feed people_feed.csv --results runs/people/results --out-prefix <p> [--groups A]
      search rows that came back full (10) -> one feed per seniority group (same Row ID + group suffix).
    python collect_people.py dedupe --in "<S> - email list.csv" ["<S> - LinkedIn only.csv"]
      one row per LinkedIn profile across the post-filter outputs: main search beats also_sell; the dropped twins
      go to '<file> - cross-side duplicates.csv'.
"""
import argparse, collections, csv, json, os, sys

sys.path.insert(0, r"C:\Users\qasim\terrapinn\clay-icp-pipeline\.claude\skills\event-icp-clay-workflow\scripts")
import collect as C  # noqa: E402
import yaml  # noqa: E402

H = os.path.dirname(os.path.abspath(__file__))
ICP = r"C:\Users\qasim\terrapinn\clay-icp-pipeline\config\icps\rail\icp.yaml"
csv.field_size_limit(10**9)


def read(p):
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def results(dirs):
    out = {}
    for d in dirs:
        out.update(C.load_results(d))
    return out


def export(a):
    sys.stdout.reconfigure(encoding="utf-8")
    icp = yaml.safe_load(open(ICP, encoding="utf-8"))
    excl = [(t, C.word_re(t)) for t in icp.get("exclude_titles") or []]
    feed = read(a.feed)
    comp = {}
    for c in read(os.path.join(H, "companies.csv")):
        comp.setdefault(c["Domain"], c)
    res = results(a.results)
    split = results(a.split) if a.split else {}
    if a.only_done:
        feed = [r for r in feed if r["Row ID"] in res]
    failed = [r["Row ID"] for r in feed if r["Row ID"] in res and res[r["Row ID"]].get("status") != "complete"]
    missing = [r["Row ID"] for r in feed if r["Row ID"] not in res]
    if failed or missing:
        raise SystemExit(f"{len(failed)} failed / {len(missing)} not run - retry or pass --only-done")
    allp = [p for rec in list(res.values()) + list(split.values()) for p in C.people_of(rec)[0]]
    iso = C.iso_names(allp)
    ccols = ["Row ID", "Company Name", "Domain", "Side", "Classification", "Search", "Countries Override",
             "Event", "Editions", "Description"]
    header = ccols + C.PERSON_COLS + ["Capped", "Outcome"]
    kept, excluded, dupes, tally = [], [], [], collections.Counter()
    for r in feed:
        c = comp.get(r["Domain"], {})
        base = [r["Row ID"], r["Company Name"], r["Domain"], r["Side"], r["Classification"], r["Search"],
                r["Countries Override"], c.get("Event", r.get("Events", "")), r.get("Editions", ""), c.get("Description", "")]
        first = C.people_of(res[r["Row ID"]])[0]
        capped = len(first) >= C.CAP
        people = list(first)
        for k, rec in split.items():
            if k.startswith(r["Row ID"] + "~"):
                people += C.people_of(rec)[0]
        if not people:
            tally["no people"] += 1
            continue
        seen, n = set(), 0
        for p in people:
            cells = base + C.person_cells(p, iso) + ["yes" if capped else ""]
            s = C.slug(p.get("url")) or json.dumps(p, sort_keys=True)[:200]
            if s in seen:
                dupes.append(cells + ["duplicate in the same search"]); continue
            seen.add(s)
            hit = next((t for t, rx in excl if rx.search(p.get("title") or "")), None)
            if hit:
                excluded.append(cells + [f"excluded: {hit}"]); continue
            kept.append(cells + ["found"]); n += 1
        tally["found" if n else "all excluded"] += 1
        tally["capped"] += capped
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    for path, rows in [(a.out, kept), (a.out[:-4] + " - excluded.csv", excluded), (a.out[:-4] + " - duplicates.csv", dupes)]:
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh); w.writerow(header); w.writerows(rows)
    print(f"{len(feed):,} searches: {dict(tally)}; people kept {len(kept):,}, excluded {len(excluded):,}, "
          f"same-search duplicates {len(dupes):,} -> {a.out}")


def capped(a):
    icp = yaml.safe_load(open(ICP, encoding="utf-8"))
    groups = [g.strip() for g in a.groups.split(",")] if a.groups else list(icp["seniority_groups"])
    feed = read(a.feed)
    res = results(a.results)
    cap = [r for r in feed if res.get(r["Row ID"], {}).get("status") == "complete"
           and len(C.people_of(res[r["Row ID"]])[0]) >= C.CAP]
    print(f"{len(cap):,} of {len(feed):,} searches came back full ({C.CAP})")
    for g in groups:
        p = f"{a.out_prefix} - sen {g}.csv"
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(feed[0])); w.writeheader()
            w.writerows([{**r, "Row ID": f"{r['Row ID']}~{g}", "Seniority Group": g} for r in cap])
        print(f"group {g}: {len(cap):,} searches -> {p}")


def dedupe(a):
    rows_by_file = {f: read(f) for f in a.inp}
    best = {}
    for f, rows in rows_by_file.items():
        for i, r in enumerate(rows):
            s = C.slug(r["LinkedIn URL"])
            if not s:
                continue
            rank = (0 if r["Search"] == "main" else 1)
            if s not in best or rank < best[s][0]:
                best[s] = (rank, f, i)
    for f, rows in rows_by_file.items():
        keep, drop = [], []
        for i, r in enumerate(rows):
            s = C.slug(r["LinkedIn URL"])
            (keep if not s or best[s][1:] == (f, i) else drop).append(r)
        cols = list(rows[0]) if rows else []
        for path, data in [(f, keep), (f[:-4] + " - cross-side duplicates.csv", drop)]:
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(data)
        print(f"{os.path.basename(f)}: kept {len(keep):,}, cross-side duplicates {len(drop):,}")


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("--feed", required=True); e.add_argument("--results", nargs="+", required=True)
    e.add_argument("--split", nargs="*"); e.add_argument("--out", required=True); e.add_argument("--only-done", action="store_true")
    c = sub.add_parser("capped"); c.add_argument("--feed", required=True); c.add_argument("--results", nargs="+", required=True)
    c.add_argument("--out-prefix", required=True); c.add_argument("--groups")
    d = sub.add_parser("dedupe"); d.add_argument("--in", dest="inp", nargs="+", required=True)
    a = ap.parse_args()
    {"export": export, "capped": capped, "dedupe": dedupe}[a.cmd](a)


if __name__ == "__main__":
    main()
