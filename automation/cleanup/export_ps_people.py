"""Export the Products & Services / Buyside P&S `People` tables to one CSV (WSL).

Reads every category's `People` table through the Clay CLI — `tables columns
list` for the field-id→name map, `tables rows list` for the cells — and writes a
single deliverable in the same shape as the Competitive Events one, with the
same exclusion and dedupe rules applied (`people_export_rules`).

RESUMABLE, BECAUSE THE FIRST RUN WAS KILLED. Reading 23 categories in one
process was stopped by the environment's memory watchdog partway through and
lost everything (CLAUDE.md: "Long background tasks get killed at a variable
cadence"). Each category's rows are now cached to `ps_people_cache/<icp>/` as
they are read, and a category whose cache file exists is not re-read. So a kill
costs at most the in-flight category, re-running the export rules costs no Clay
calls at all, and `--limit` lets the read be done in several short sessions.

NO BROWSER. The earlier export path drove the UI's Export tab
(`export_event_tables.py`), which needs Chrome on the debug port and a logged-in
session. `clay tables rows list` returns the same rows over the CLI, so this
pass is resumable, auditable and runnable from WSL like the rest of the
verification tooling. Cells come back keyed by FIELD ID, not by name, so the
column map is fetched per table and the two are joined here.

WHY THE RULES ARE RE-APPLIED AT EXPORT. The 28 Highways `People` tables were
built 2026-08-14/15. The 54-term job-title exclusion list did not exist until
2026-09-12, so those tables contain titles that are now out of scope. Re-running
the searches to fix that would re-spend on ~77k companies; filtering here costs
nothing. The two buy-side tables built after the list exists are filtered again
harmlessly — the rule is idempotent.

THE RESCUE LIST IS EXACT TITLES ONLY. Rescuing on everything the search asked
for saved 528 of 528 buy-side rows carrying an excluded term — the buy-side
title lists are function words (`Operations`, `Environment`, `Procurement`) that
appear in most corporate titles. See rule 2 in people_export_rules.

    python3 export_ps_people.py --icp highways            # sell side
    python3 export_ps_people.py --icp highways_buyside    # buy side
    python3 export_ps_people.py --icp highways --only "Parking Technology"
    python3 export_ps_people.py --icp highways --list     # scope only, no read
"""

import argparse
import collections
import csv
import io
import json
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import clay_bin                          # noqa: E402
import people_export_rules as rules      # noqa: E402
import ps_query_config as psq            # noqa: E402

OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
CACHE_DIR = os.path.join(SCRIPT_DIR, "ps_people_cache")
PEOPLE = "People"

MANIFEST = {"highways": "highways_ps_companies_manifest.json",
            "highways_buyside": "highways_bs_companies_manifest.json"}
LABEL = {"highways": "Highways P&S", "highways_buyside": "Highways BuySide P&S"}
SIDE = {"highways": "Sell Side", "highways_buyside": "Buy Side"}

# Source column on the People table -> column in the deliverable. Deliberately
# narrow: this file exists to be emailed and loaded, not to carry every
# enrichment. `Company Table Data` is an action column whose value is a blob.
COLUMNS = (("Full Name", "Person Name"),
           ("First Name", "First Name"),
           ("Last Name", "Last Name"),
           ("Job Title", "Job Title"),
           ("LinkedIn Profile", "LinkedIn URL"),
           ("Company", "Company Name"),
           ("City", "City"),
           ("State or Province", "State or Province"),
           ("Country", "Country"))
ADDED = ("Category", "Side", "Source")

# Categories with no Clay `People` table, whose people came from the WORKFLOW
# route instead. Read from the routine results and mapped into the same
# column shape `read_table` yields, so every rule below applies identically and
# the deliverable stays ONE file. `Source` marks which route a row came from —
# the workflow persona is looser (see build_bs_gap_people_feed.py).
WORKFLOW_RESULTS = {
    "highways_buyside": ("bs_gap_people_results",
                         "Highways BuySide P&S gap - people feed.csv"),
}


def cli(binary, args, tries=8):
    """One CLI call, with exponential backoff on transport failures.

    Patient on purpose. A full sell-side read is ~1,300 paged calls over the
    better part of an hour, so a single transient blip is near-certain; the
    original 4 tries at a flat 2s exhausted itself mid-cursor on the 21st of 22
    categories and threw away that category's partial rows. Backoff runs
    2,4,8,...,128s — about four minutes of patience before giving up.

    A structured JSON error still fails immediately: `upgrade_required` or a bad
    id will not fix itself, and retrying it just delays the real message.
    """
    delay = 2
    for attempt in range(tries):
        ok, d, err = clay_bin.run(binary, args)
        if ok:
            return d
        if d is not None:
            raise SystemExit(f"clay {' '.join(args)} failed: {err}")
        if attempt < tries - 1:
            time.sleep(delay)
            delay = min(delay * 2, 128)
    raise SystemExit(f"clay {' '.join(args)} failed after {tries} tries")


def workbook_tables(binary):
    """{workbook_id: [(table_id, name)]} — the workspace listing, paged once."""
    out, cursor = {}, None
    for _ in range(60):
        args = ["tables", "list", "--limit", "100"]
        if cursor:
            args += ["--cursor", cursor]
        d = cli(binary, args)
        rows = d.get("data") if isinstance(d, dict) else d
        for t in rows or []:
            wb = (t.get("workbook") or {}).get("id")
            if wb:
                out.setdefault(wb, []).append((t.get("id"), t.get("name")))
        cursor = d.get("cursor") if isinstance(d, dict) else None
        if not cursor or not rows:
            return out
    raise SystemExit("table listing did not terminate")


def read_workflow(icp, category):
    """Workflow-sourced people for one category, in read_table's shape."""
    if icp not in WORKFLOW_RESULTS:
        return []
    res_dir, feed_name = WORKFLOW_RESULTS[icp]
    res_dir = os.path.join(SCRIPT_DIR, res_dir)
    feed_path = os.path.join(OUT_DIR, feed_name)
    if not os.path.isdir(res_dir) or not os.path.exists(feed_path):
        return []

    with open(feed_path, encoding="utf-8-sig", newline="") as fh:
        feed = {r["Domain"]: r for r in csv.DictReader(fh)
                if r.get("Classification") == category}
    if not feed:
        return []

    rows = []
    for name in sorted(os.listdir(res_dir)):
        if not name.endswith(".jsonl"):
            continue
        for line in open(os.path.join(res_dir, name), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            co = feed.get(rec.get("id"))
            if co is None:
                continue          # a different category's company
            people = ((rec.get("result") or {}).get("result") or {}).get("people") or []
            for pp in people:
                loc = pp.get("structured_location") or {}
                rows.append({
                    "Full Name": pp.get("name", "") or "",
                    "First Name": pp.get("first_name", "") or "",
                    "Last Name": pp.get("last_name", "") or "",
                    "Job Title": pp.get("title", "") or "",
                    "LinkedIn Profile": pp.get("url", "") or "",
                    "Company": co.get("Company Name", ""),
                    "City": loc.get("city") or "",
                    "State or Province": loc.get("state") or "",
                    "Country": loc.get("country") or "",
                })
    return rows


def cache_path(icp, category):
    safe = re.sub(r"[^A-Za-z0-9 &._-]", "_", category).strip()
    return os.path.join(CACHE_DIR, icp, safe + ".jsonl")


def read_table(binary, tid):
    """[{column name: value}] for every row, joining cells to the column map."""
    d = cli(binary, ("tables", "columns", "list", tid))
    cols = d.get("data") if isinstance(d, dict) else d
    names = {c.get("id"): c.get("name") for c in cols or []}

    rows, cursor = [], None
    for _ in range(2000):
        args = ["tables", "rows", "list", tid, "--limit", "100"]
        if cursor:
            args += ["--cursor", cursor]
        d = cli(binary, args)
        page = d.get("data") if isinstance(d, dict) else d
        for r in page or []:
            rec = {}
            for fid, cell in (r.get("cells") or {}).items():
                name = names.get(fid)
                if name:
                    v = cell.get("value") if isinstance(cell, dict) else cell
                    rec[name] = "" if v is None else str(v)
            rows.append(rec)
        cursor = d.get("cursor") if isinstance(d, dict) else None
        if not cursor or not page:
            return rows
    raise SystemExit(f"row listing for {tid} did not terminate")




def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--icp", default="highways", choices=sorted(MANIFEST))
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--list", action="store_true", help="scope only, read nothing")
    ap.add_argument("--strict-exclusions", action="store_true",
                    help="an excluded term wins even over an asked-for title")
    ap.add_argument("--keep-duplicates", action="store_true")
    ap.add_argument("--keep-out-of-country", action="store_true",
                    help="do NOT filter to the category's ICP countries")
    ap.add_argument("--drop-unknown-country", action="store_true",
                    help="also drop rows with a blank Country (default: keep "
                         "them; their employer is in-country by construction)")
    ap.add_argument("--limit", type=int,
                    help="read at most N uncached categories this invocation")
    ap.add_argument("--refresh", action="store_true",
                    help="discard the row cache and re-read every category")
    ap.add_argument("--out")
    args = ap.parse_args()

    man = json.load(open(os.path.join(SCRIPT_DIR, MANIFEST[args.icp]),
                         encoding="utf-8"))
    categories = [c["name"] for c in psq.load(args.icp)["categories"]
                  if c["name"] in man and (not args.only or c["name"] in args.only)]

    if args.refresh:
        d = os.path.join(CACHE_DIR, args.icp)
        for f in (os.listdir(d) if os.path.isdir(d) else []):
            os.remove(os.path.join(d, f))
        print("cache cleared")

    binary = clay_bin.pick()
    tables = workbook_tables(binary)

    scope = []
    for name in categories:
        wid = man[name]["workbook_id"]
        tid = next((t for t, n in (tables.get(wid) or []) if n == PEOPLE), None)
        scope.append((name, wid, tid))

    print(f"{LABEL[args.icp]}: {len(scope)} categories")
    for name, _, tid in scope:
        print(f"   {'ok ' if tid else 'NO PEOPLE TABLE':<16} {name}")
    missing = [n for n, _, t in scope if not t]
    if missing:
        print(f"\n{len(missing)} category(ies) have no '{PEOPLE}' table: {missing}")
    if args.list:
        return 0

    # ---- read phase: cache each category's rows, skip what is already there
    os.makedirs(os.path.join(CACHE_DIR, args.icp), exist_ok=True)
    todo = [(n, t) for n, _, t in scope
            if t and not os.path.exists(cache_path(args.icp, n))]
    if args.limit:
        todo = todo[:args.limit]
    if todo:
        print(f"\nreading {len(todo)} category(ies) not yet cached:")
    for name, tid in todo:
        rows = read_table(binary, tid)
        # Written atomically: a half-written cache file read back as complete
        # would silently drop the tail of a category.
        cp = cache_path(args.icp, name)
        tmp = cp + ".part"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, cp)
        print(f"   cached {len(rows):6} rows  {name[:44]}")

    uncached = [n for n, _, t in scope
                if t and not os.path.exists(cache_path(args.icp, n))]
    if uncached:
        print(f"\n{len(uncached)} category(ies) still unread — re-run to continue:")
        for n in uncached:
            print(f"   {n}")
        print("\nnot writing a partial deliverable")
        return 1

    # ---- assemble phase: pure local work, no Clay calls
    patterns = rules.exclusion_patterns()
    # Per category, the ICP country list the people must sit in. Same source
    # the company search used, so the two can never drift apart on geography.
    countries = {c["name"]: set(c.get("countries") or psq.load(args.icp).get("countries") or [])
                 for c in psq.load(args.icp)["categories"]}
    seen = {}
    kept, dropped, duped, offshore = [], [], [], []
    drop_terms, out_terms, per_cat = (collections.Counter(),
                                      collections.Counter(), {})

    for name, wid, tid in scope:
        asked = rules.asked_patterns(rules.rescue_titles(args.icp, name))
        if tid:
            rows = [json.loads(l) for l in
                    open(cache_path(args.icp, name), encoding="utf-8") if l.strip()]
            route = LABEL[args.icp]
        else:
            rows = read_workflow(args.icp, name)
            route = LABEL[args.icp] + " (workflow)"
            if not rows:
                continue
        n_keep = n_drop = n_dup = n_out = 0
        for r in rows:
            base = {dst: r.get(src, "") for src, dst in COLUMNS}
            base["Category"] = name
            base["Side"] = SIDE[args.icp]
            base["Source"] = route
            verdict, detail = rules.classify(
                r.get("Job Title", ""), r.get("LinkedIn Profile", ""), seen,
                patterns, asked, strict=args.strict_exclusions,
                dedupe=not args.keep_duplicates,
                country=r.get("Country", ""),
                allowed=None if args.keep_out_of_country else countries.get(name),
                drop_unknown=args.drop_unknown_country)
            if verdict == rules.OUT_OF_COUNTRY:
                offshore.append({**base, "Outcome": "outside ICP countries: " + detail})
                out_terms[detail] += 1
                n_out += 1
            elif verdict == rules.EXCLUDED:
                dropped.append({**base, "Outcome": "excluded: " + detail})
                drop_terms[detail] += 1
                n_drop += 1
            elif verdict == rules.DUPLICATE:
                duped.append({**base, "Outcome": "duplicate of " + str(detail)})
                n_dup += 1
            else:
                seen[detail] = name
                kept.append({**base, "Outcome": "found"})
                n_keep += 1
        per_cat[name] = (len(rows), n_keep, n_drop, n_dup, n_out)
        print(f"   {len(rows):6} rows  {n_keep:6} kept  {n_drop:4} excl  "
              f"{n_out:5} offshore  {n_dup:4} dup   {name[:34]}")

    found = sum(v[0] for v in per_cat.values())
    rules.reconcile(len(kept), len(dropped), len(duped), found, len(offshore))

    header = [dst for _, dst in COLUMNS] + list(ADDED) + ["Outcome"]
    os.makedirs(OUT_DIR, exist_ok=True)
    base_name = args.out or f"{LABEL[args.icp]} - people.csv"
    path = os.path.join(OUT_DIR, base_name)

    def write(p, recs):
        with open(p, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=header)
            w.writeheader()
            w.writerows(recs)

    write(path, kept)
    print(f"\npeople found      : {found:,}")
    print(f"  delivered       : {len(kept):,}")
    print(f"  excluded        : {len(dropped):,}")
    print(f"  outside country : {len(offshore):,}")
    print(f"  duplicates      : {len(duped):,}")
    print(f"  reconciles      : {len(kept):,} + {len(dropped):,} + "
          f"{len(offshore):,} + {len(duped):,} = {found:,}")
    if drop_terms:
        print("\ntop exclusion terms:")
        for t, n in drop_terms.most_common(10):
            print(f"      {n:5}  {t}")
    if out_terms:
        print("\ntop out-of-country values:")
        for t, n in out_terms.most_common(10):
            print(f"      {n:6}  {t}")
    if offshore:
        p = path[:-4] + " - out of country.csv"
        write(p, offshore)
        print(f"   -> {p}")
    if dropped:
        p = path[:-4] + " - excluded.csv"
        write(p, dropped)
        print(f"   -> {p}")
    if duped:
        p = path[:-4] + " - duplicates.csv"
        write(p, duped)
        print(f"   -> {p}")
    print(f"\n{len(kept):,} rows -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
