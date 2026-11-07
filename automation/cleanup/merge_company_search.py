"""Merge the per-search results of clay_company_search.py into ONE company CSV,
one row per company, each company filed under exactly one workbook.

Written for the Solar buy side (2026-11-05). The user imports the CSV into Clay
themselves: one workbook per `Workbook` value (= the buyer group).

    python automation/cleanup/merge_company_search.py --work <run>/search --out "<run>/Solar - Buy Side companies.csv"
        --exclude "<csv>|<domain col>[,<domain col>...]|<name col>|<label>"   (repeatable)

Rules:
  * One row per company. Identity = normalised domain (supabase_lookup.norm_domain, the
    same normaliser the team's Supabase uses), else the Clay company id.
  * A company found by several searches goes to the FIRST in plan order (= the sheet's
    buyer-group order), so the people search runs once per company — never pay twice.
    The other searches are listed in `Also Matched`.
  * `--exclude` drops companies already worked elsewhere: matched on domain, or, when
    the excluded row has no domain, on the folded company name. Dropped rows go to
    `<out> - dropped.csv` with the reason, so the cut can be audited.
  * Rows with neither a domain nor a name are dropped as unusable.
"""

import argparse
import csv
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, os.path.join(REPO, ".claude", "skills", "supabase-known-lookup", "scripts"))
from supabase_lookup import norm_domain, fold_name  # noqa: E402

TIER = {"1": "A", "2": "A", "3": "A", "4": "A", "5": "B", "6": "B", "7": "B", "8": "C"}

COLUMNS = ["Workbook", "Sub-segment", "Found By", "Priority", "Tier", "Also Matched",
           "Company Name", "Domain", "LinkedIn URL", "HQ Country", "Location",
           "Industry", "Size", "Type", "Annual Revenue", "Description", "Clay Company ID"]


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_excludes(specs):
    """{domain: label}, {folded name: label} from `csv|domcols|namecol|label` specs."""
    by_domain, by_name = {}, {}
    for spec in specs:
        path, domcols, namecol, label = spec.split("|")
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                doms = [norm_domain(row.get(c)) for c in domcols.split(",")]
                doms = [d for d in doms if d]
                for d in doms:
                    by_domain.setdefault(d, label)
                if not doms and row.get(namecol):
                    by_name.setdefault(fold_name(row[namecol]), label)
    return by_domain, by_name


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="merge even if some searches have not finished")
    args = ap.parse_args()

    plan = json.load(open(os.path.join(args.work, "plan.json"), encoding="utf-8"))["searches"]
    state = json.load(open(os.path.join(args.work, "state.json"), encoding="utf-8"))
    unfinished = [s["name"] for s in plan if state.get(s["name"], {}).get("run", {}).get("status") != "done"]
    if unfinished and not args.allow_incomplete:
        raise SystemExit(f"{len(unfinished)} searches not done: {unfinished}")

    by_domain, by_name = load_excludes(args.exclude)

    kept, order, dropped = {}, [], []
    found = {}
    for s in plan:
        path = os.path.join(args.work, "results", slug(s["name"]) + ".jsonl")
        rows = read_jsonl(path) if os.path.exists(path) else []
        found[s["name"]] = len(rows)
        group_no = s["group"].split(".")[0].strip()
        for r in rows:
            dom = norm_domain(r.get("domain"))
            key = dom or f"clay:{r.get('clay_company_id')}"
            if not dom and not r.get("name"):
                dropped.append((r, s, "no domain and no name"))
                continue
            sub = s.get("subsegment") or s["name"]
            if key in kept:
                k = kept[key]
                if sub != k["Sub-segment"] and sub not in k["_also"]:
                    k["_also"].append(sub)
                continue
            reason = by_domain.get(dom) if dom else None
            if not reason and not dom:
                reason = by_name.get(fold_name(r.get("name")))
            if reason:
                dropped.append((r, s, reason))
                kept[key] = {"_dropped": True, "Sub-segment": sub, "_also": []}
                continue
            kept[key] = {
                "Workbook": s["group"], "Sub-segment": sub, "Found By": s["name"], "Priority": s.get("priority", ""),
                "Tier": s["tier"] if "tier" in s else TIER.get(group_no, ""), "_also": [],
                "Company Name": r.get("name") or "", "Domain": dom,
                "LinkedIn URL": r.get("linkedin_url") or "", "HQ Country": r.get("country") or "",
                "Location": r.get("location") or "", "Industry": r.get("industry") or "",
                "Size": r.get("size") or "", "Type": r.get("type") or "",
                "Annual Revenue": r.get("annual_revenue") or "",
                "Description": (r.get("description") or "").replace("\r", " ").replace("\n", " "),
                "Clay Company ID": r.get("clay_company_id") or "",
            }
            order.append(key)

    out_rows = []
    for key in order:
        k = kept[key]
        k["Also Matched"] = "; ".join(k.pop("_also"))
        out_rows.append(k)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)
    drop_path = re.sub(r"\.csv$", "", args.out) + " - dropped.csv"
    with open(drop_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Reason", "Search", "Company Name", "Domain", "Clay Company ID"])
        for r, s, reason in dropped:
            w.writerow([reason, s["name"], r.get("name"), r.get("domain"), r.get("clay_company_id")])

    # Counts only.
    print(f"raw results: {sum(found.values()):,} across {len(plan)} searches")
    print(f"kept: {len(out_rows):,} companies -> {args.out}")
    reasons = {}
    for _, _, reason in dropped:
        reasons[reason] = reasons.get(reason, 0) + 1
    for reason, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"dropped: {n:,}  {reason}")
    per_wb = {}
    for k in out_rows:
        per_wb[k["Workbook"]] = per_wb.get(k["Workbook"], 0) + 1
    # Numeric group order: "10. Installers" after "9. ...", not after "1. ...".
    for wb in sorted(per_wb, key=lambda w: (int(w.split('.')[0]) if w.split('.')[0].isdigit() else 999, w)):
        print(f"  {per_wb[wb]:>8,}  {wb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
