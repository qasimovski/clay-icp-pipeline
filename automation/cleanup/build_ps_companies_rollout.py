"""Fleet driver for build_ps_companies.py — the EVCharge Products & Services
company searches, one workbook per Top level category.

Scope is the category list in config/icps/<icp>/products_services.yaml; there is
no manifest of live table ids to regenerate, because every workbook this pass
touches is one it created itself inside the Products & Services folder.

Like the single pass, the default is CONFIGURE-ONLY: workbooks are created, the
Find companies search is built and the query pasted, and nothing is triggered.
`--run` is what materialises rows into tables and spends credits.

State lives in ps_companies_state_<icp>.json, namespaced per ICP so two ICPs
never share a state file, and written through state_io (atomic, fails loud on a
corrupt file). Each category records its workbook id, the preview count and the
status, so a relaunch after the environment's background-task kills resumes
instead of re-walking the fleet.

Ordering is ascending by observed preview count once known, so a selector
regression surfaces on the smallest search rather than the largest — the repo's
"smallest table first" convention. Before any count is known the config order
is used.

    python build_ps_companies_rollout.py --list          # scope only, no browser
    python build_ps_companies_rollout.py --dry-run       # what it would touch
    python build_ps_companies_rollout.py                 # configure all, spend nothing
    python build_ps_companies_rollout.py --only "Finance, Investment & Funding"
    python build_ps_companies_rollout.py --after "Energy, Power & Grid Solutions"
    python build_ps_companies_rollout.py --limit 3 --headed
    python build_ps_companies_rollout.py --run           # SPENDS CREDITS
"""

import argparse
import os
import re
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import browser_session          # noqa: E402
import build_ps_companies as bps  # noqa: E402
import ps_query_config as psq   # noqa: E402
import state_io                 # noqa: E402

LOG_DIR = os.path.join(SCRIPT_DIR, "ps_companies_logs")


def state_path(icp, shard=None):
    tag = f"_{shard}" if shard else ""
    return os.path.join(SCRIPT_DIR, f"ps_companies_state_{icp}{tag}.json")


def preview_count(preview):
    """The match total out of a preview line like '50 of 5,922 (~5,922 found)'.
    None when the panel never printed one — never 0, which would read as a real
    empty result and sort first."""
    for line in preview or []:
        m = re.search(r"of ([\d,]+)", line) or re.search(r"([\d,]+) found", line)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def ordered(names, state):
    """Ascending by known preview count; unknown counts keep config order last.

    Smallest first is deliberate: a DOM regression should break on the narrowest
    search, not on the 5,900-row one.
    """
    known = [(preview_count(state.get(n, {}).get("preview")), i, n)
             for i, n in enumerate(names)]
    return [n for c, i, n in sorted(
        known, key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0, t[1]))]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    psq.add_cli_args(ap)
    ap.add_argument("--list", action="store_true",
                    help="scope and state only, no browser")
    ap.add_argument("--dry-run", action="store_true",
                    help="what it would touch, no browser")
    ap.add_argument("--only", action="append", default=[],
                    help="run just this category (repeatable)")
    ap.add_argument("--after", help="resume past a named category")
    ap.add_argument("--limit", type=int, help="cap how many categories run")
    ap.add_argument("--order", choices=["count", "config"], default="count")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--run", action="store_true",
                    help="materialise results into tables (SPENDS CREDITS)")
    ap.add_argument("--replace", action="store_true",
                    help="rebuild after a query edit: DELETE the workbook's "
                         "existing Companies table, then build a new one. "
                         "Requires --run. Destructive; rows go to Clay's Trash.")
    ap.add_argument("--measure", action="store_true",
                    help="re-price edited queries against already-built "
                         "workbooks; configures only, never spends")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--redo", action="store_true",
                    help="re-visit categories already marked done in state")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=bps.SHOTS_DIR)
    args = ap.parse_args()

    if args.replace and not args.run:
        ap.error("--replace requires --run")
    if args.measure and args.run:
        ap.error("--measure and --run are mutually exclusive")

    cats = dict(psq.categories(args.icp))
    tag = f"w{args.shard}" if args.shards > 1 else None
    path = state_path(args.icp, tag)
    state = state_io.load_json(path)

    names = list(cats)
    if args.order == "count":
        names = ordered(names, state)
    if args.only:
        names = [n for n in names if n in args.only]
        unknown = [n for n in args.only if n not in cats]
        if unknown:
            print(f"unknown category/categories: {unknown}")
            return 1
    if args.after:
        if args.after not in names:
            print(f"--after {args.after!r} is not in scope")
            return 1
        names = names[names.index(args.after) + 1:]
    if args.shards > 1:
        names = [n for i, n in enumerate(names) if i % args.shards == args.shard]

    # `built` is terminal (rows exist, credits spent); `configured` is not — a
    # later --run pass must be able to pick it up.
    done = {"built"} if args.run else {"built", "configured"}
    pending = [n for n in names
               if args.redo or state.get(n, {}).get("status") not in done]
    if args.limit:
        pending = pending[:args.limit]

    if args.list or args.dry_run:
        mode = "RUN (spends credits)" if args.run else "configure-only"
        print(f"icp={args.icp}  mode={mode}  state={os.path.basename(path)}")
        print(f"{len(pending)} pending of {len(names)} in scope:")
        for n in names:
            st = state.get(n, {})
            cnt = preview_count(st.get("preview"))
            mark = "pending" if n in pending else st.get("status", "?")
            print(f"  {mark:11s} {str(cnt) if cnt is not None else '-':>7s}  {n}")
        return 0

    os.makedirs(LOG_DIR, exist_ok=True)
    failures = []
    for i, name in enumerate(pending, 1):
        print(f"\n=== [{i}/{len(pending)}] {name}", flush=True)
        args.slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                result = bps.build(page, name, cats[name], args)
        except Exception as e:
            traceback.print_exc()
            failures.append(name)
            prev = state.get(name, {})
            state[name] = {**prev, "status": "failed", "error": str(e)[:300]}
        else:
            state[name] = {**state.get(name, {}), **result}
        # Save after every category: a kill between two categories must not
        # lose the record of the one that just finished.
        state_io.save_json(path, state)

    print(f"\ndone: {len(pending) - len(failures)}/{len(pending)}")
    if failures:
        print("failed:")
        for n in failures:
            print(f"  {n}")
    print(f"state: {path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
