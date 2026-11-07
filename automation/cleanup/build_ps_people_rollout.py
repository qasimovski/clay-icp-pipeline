"""Fleet driver for build_ps_people.py — "Find people at these companies" over
every EVCharge Products & Services workbook.

Scope comes from the companies pass's own state file (`ps_companies_state_<icp>.json`):
only categories whose `Companies` table was actually built are in play, so this
pass can never run against a workbook that has no companies to expand.

Like the single pass, the default is CONFIGURE-ONLY; `--run` is what saves the
People table and spends credits.

State lives in ps_people_state_<icp>.json via state_io (atomic, fails loud on a
corrupt file). Ordering is ascending by the companies table's row count, so a
selector regression surfaces on the 104-company workbook rather than the
19,943-company one — the repo's "smallest table first" convention.

    python build_ps_people_rollout.py --list        # scope only, no browser
    python build_ps_people_rollout.py --dry-run
    python build_ps_people_rollout.py              # configure all, spend nothing
    python build_ps_people_rollout.py --run        # SPENDS CREDITS
    python build_ps_people_rollout.py --run --only "Finance, Investment & Funding"
    python build_ps_people_rollout.py --run --shards 3 --shard 0
"""

import argparse
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import browser_session              # noqa: E402
import build_ps_companies as bps    # noqa: E402
import build_ps_people as bpp       # noqa: E402
import ps_people_config as ppc      # noqa: E402
import ps_buyside_people_config as bpc  # noqa: E402
import ps_query_config as psq       # noqa: E402
import state_io                     # noqa: E402

from build_ps_companies_rollout import preview_count  # noqa: E402

LOG_DIR = os.path.join(SCRIPT_DIR, "ps_people_logs")


def state_path(icp, shard=None):
    tag = f"_{shard}" if shard else ""
    return os.path.join(SCRIPT_DIR, f"ps_people_state_{icp}{tag}.json")


def company_sizes(icp):
    """{category: companies preview count} — the ordering key. Missing counts
    sort last rather than first, so an unmeasured workbook never leads the run."""
    path = os.path.join(SCRIPT_DIR, f"ps_companies_state_{icp}.json")
    state = state_io.load_json(path)
    return {n: preview_count(r.get("preview")) for n, r in state.items()}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    psq.add_cli_args(ap)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--after")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--run", action="store_true",
                    help="save the People tables (SPENDS CREDITS)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=bpp.SHOTS_DIR)
    args = ap.parse_args()

    # Same selection as build_ps_people.main(): an ICP that ships a people.yaml
    # is buy-side (per-category job titles); otherwise it is the seller-side
    # seller_people.yaml (one persona for every workbook). Patching only the
    # single-table pass left this driver loading the wrong config and failing.
    args.buyside = os.path.exists(bpc.config_path(args.icp))
    if args.buyside:
        cfg = bpc.load(args.icp)
        bpc.validate(cfg)
    else:
        cfg = ppc.load(args.icp)
        ppc.validate(cfg)
    args.people_cfg = cfg

    wbs = bpp.company_workbooks(args.icp)
    anchored = bpp.anchored_categories(args.icp)
    sizes = company_sizes(args.icp)

    tag = f"w{args.shard}" if args.shards > 1 else None
    path = state_path(args.icp, tag)
    state = state_io.load_json(path)

    names = sorted(wbs, key=lambda n: (sizes.get(n) is None, sizes.get(n) or 0, n))
    if args.only:
        unknown = [n for n in args.only if n not in wbs]
        if unknown:
            print(f"unknown category/categories: {unknown}")
            return 1
        names = [n for n in names if n in args.only]
    if args.after:
        if args.after not in names:
            print(f"--after {args.after!r} is not in scope")
            return 1
        names = names[names.index(args.after) + 1:]
    if args.shards > 1:
        names = [n for i, n in enumerate(names) if i % args.shards == args.shard]

    done = {"built"} if args.run else {"built", "configured"}
    pending = [n for n in names
               if args.redo or state.get(n, {}).get("status") not in done]
    if args.limit:
        pending = pending[:args.limit]

    if args.list or args.dry_run:
        mode = "RUN (spends credits)" if args.run else "configure-only"
        print(f"icp={args.icp}  mode={mode}  state={os.path.basename(path)}")
        print(f"{len(pending)} pending of {len(names)} in scope "
              f"(ordered by companies count):")
        for n in names:
            st = state.get(n, {})
            mark = "pending" if n in pending else st.get("status", "?")
            people = preview_count(st.get("preview"))
            print(f"  {mark:11s} companies={str(sizes.get(n)):>6s} "
                  f"people={str(people) if people is not None else '-':>6s}  "
                  f"{'non-sector' if n in anchored else 'sector':11s} {n}")
        return 0

    os.makedirs(LOG_DIR, exist_ok=True)
    failures = []
    for i, name in enumerate(pending, 1):
        print(f"\n=== [{i}/{len(pending)}] {name}", flush=True)
        args.slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
        args.non_sector = name in anchored
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                result = bpp.build(page, name, wbs[name], args)
        except Exception as e:
            traceback.print_exc()
            failures.append(name)
            state[name] = {**state.get(name, {}), "status": "failed",
                           "error": str(e)[:300]}
        else:
            # Drop a previous attempt's `error`: merging over the old record
            # otherwise leaves a `built` row still carrying the failure text
            # from the run before it, which reads as a half-finished build.
            prev = {k: v for k, v in state.get(name, {}).items() if k != "error"}
            state[name] = {**prev, **result}
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
