"""Feed the two Buyside P&S categories that never got a `People` table.

`Major Engineering Contractors / Tier 1 Contractor` (3,863 companies) and
`Sub-engineering contractors` (3,707) have built `Companies` tables but no
`People` table — the 2026-08-15 rollout held them back. Their six siblings were
built through the ClayQL UI pass; these two go through the WORKFLOW instead,
per the user (2026-09-14), because that route needs no browser.

WHAT THAT COSTS, stated plainly. The workflow route cannot express three things
the ClayQL route does (docs/PEOPLE_PASSES.md, and §11 of WORKFLOWS_ROUTE.md):

  * the whole persona in ONE `experiences.count(...)` predicate,
  * company-office location rather than the PERSON's location,
  * the buy-side `titles_exact` + `exact_relevance` branch.

So these two categories are targeted slightly more loosely than the other six in
the same deliverable. That is a real inconsistency inside one sheet and it is
recorded here rather than discovered later; the `Source` column marks which rows
came from which route.

NO `Is New` GATE, deliberately. Both categories had their `Is New` view filter
CLEARED on 2026-08-15 (`clear_isnew_filter.py`): every one of those firms was
already claimed by the sell-side sweeps, but the sell-side tables hold the SALES
people at Balfour Beatty while the buy side wants the Head of Highways and the
bid/procurement team. Different personas, same companies — so company-level
dedupe must not gate them. The full company set is searched.

NO `Countries Override` either: the buy side takes the ICP-level five countries,
which is what the mapping node already applies for `Buy Side`.

    python3 build_bs_gap_people_feed.py            # write the feed
    python3 build_bs_gap_people_feed.py --report    # counts only
"""

import argparse
import csv
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
# stdout is already wrapped for UTF-8 by export_ps_people on import; wrapping
# it a second time closes the first wrapper and breaks argparse on py3.14.

import clay_bin                          # noqa: E402
import export_ps_people as E             # noqa: E402
import ce_people_mapping as M            # noqa: E402

MANIFEST = os.path.join(SCRIPT_DIR, "highways_bs_companies_manifest.json")
OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
OUT = os.path.join(OUT_DIR, "Highways BuySide P&S gap - people feed.csv")
CACHE_DIR = os.path.join(SCRIPT_DIR, "ps_people_cache", "bs_gap_companies")

CATEGORIES = ("Major Engineering Contractors / Tier 1 Contractor",
              "Sub-engineering contractors")

COLUMNS = ("Company Name", "Domain", "Side", "Classification",
           "Company Event Name", "Exhibitor/Sponsor", "Countries Override",
           "Country", "Website")


def companies(binary, name, tid):
    """That category's Companies rows, cached so a re-run costs no Clay calls."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = name.replace("/", "_").strip()
    cp = os.path.join(CACHE_DIR, safe + ".jsonl")
    if os.path.exists(cp):
        return [json.loads(l) for l in open(cp, encoding="utf-8") if l.strip()]
    rows = E.read_table(binary, tid)
    tmp = cp + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, cp)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    man = json.load(open(MANIFEST, encoding="utf-8"))
    binary = clay_bin.pick()
    loaded = M._load()

    keep, skipped, seen = [], {}, set()
    for name in CATEGORIES:
        # The persona must exist before a single search is paid for.
        M.resolve("Buy Side", name, loaded)
        rows = companies(binary, name, man[name]["table_id"])
        n_keep = n_skip = 0
        for r in rows:
            dom = str(r.get("Domain", "")).strip()
            if not dom:
                n_skip += 1
                continue
            # One search per domain across BOTH categories: a firm listed as
            # both a Tier 1 and a sub-contractor is one company, and the buy-side
            # persona it resolves to is the same either way.
            if dom in seen:
                n_skip += 1
                continue
            seen.add(dom)
            keep.append({
                "Company Name": r.get("Name", "") or r.get("Company Name", ""),
                "Domain": dom,
                "Side": "Buy Side",
                "Classification": name,
                "Company Event Name": name,
                "Exhibitor/Sponsor": "BuySide P&S (workflow)",
                "Countries Override": "",
                "Country": r.get("Country", ""),
                "Website": r.get("Website", "") or r.get("Domain", ""),
            })
            n_keep += 1
        skipped[name] = (len(rows), n_keep, n_skip)
        print(f"   {len(rows):5,} companies  {n_keep:5,} searchable  "
              f"{n_skip:4} skipped (no domain / duplicate)   {name[:40]}")

    print(f"\nTOTAL searches: {len(keep):,}")
    bad = [k for k in keep if not k["Domain"].strip()]
    if bad:
        raise SystemExit(f"!! {len(bad)} rows with no Domain")

    if args.report:
        print("--report: nothing written")
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        w.writeheader()
        w.writerows(keep)
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
