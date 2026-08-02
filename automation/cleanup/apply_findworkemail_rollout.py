"""SUPERSEDED — use add_workemail_waterfall.py instead.

add_workemail_waterfall.py:4-8 replaced this template-based approach on
2026-07-27 ("the saved template proved unreliable to apply"). This module and
its event script are kept for reference only. Because they still spend ~14.5
credits/row, running them requires an explicit opt-in:

    CLAY_ALLOW_SUPERSEDED=1 python apply_findworkemail_rollout.py ...

Fleet driver: apply the "Email Waterfall and Validate Email" template to
every Competitive Events workbook's "Speakers_normalized" table.

Same scope and shape as apply_findlinkedin_rollout.py (43 workbooks from
speakers_normalized_workbooks.json), and it runs AFTER that pass — three of
this template's four Configure fields map into the columns the Find-LinkedIn
template added, so a workbook without those columns cannot be mapped.

**Cost: ~14.5 credits/row** (vs ~0.1/row for the Find-LinkedIn pass). Speaker
tables run 9-254 rows, so a single workbook is 130-3,700 credits and the full
fleet is 30,000+. Run it in small, explicitly approved batches — never a bare
fleet run. Each workbook's actual spend is logged from the "Save and run N rows
in this view / X total" label the event script reads off the run menu, and
stored in state as `ran`.

  python apply_findworkemail_rollout.py --recon --only BioTrinity   # 0 credits
  python apply_findworkemail_rollout.py --dry-run --only BioTrinity # 0 credits
  python apply_findworkemail_rollout.py --only BioTrinity
  python apply_findworkemail_rollout.py --limit 5
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import browser_session                        # noqa: E402
import apply_findworkemail_event as A  # noqa: E402

SCOPE_PATH = os.path.join(SCRIPT_DIR, "speakers_normalized_workbooks.json")
STATE_PATH = os.path.join(SCRIPT_DIR, "workemail_state.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "workemail_logs")
os.makedirs(LOG_DIR, exist_ok=True)

TABLE = "Speakers_normalized"
# Only these count as done. "unconfirmed" / "incomplete" / "aborted" / "error"
# stay pending so a rerun retries them (an incomplete run resumes its wait
# rather than re-applying — see apply_findworkemail_event.apply_findworkemail).
_FINAL_STATUSES = ("ok", "dryrun", "no_table")


def load(p, d):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return d


def save(p, s):
    json.dump(s, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def workbook_done(state, wid):
    return state.get(wid, {}).get("status") in _FINAL_STATUSES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recon", action="store_true",
                    help="dump each Configure panel read-only; never saves")
    ap.add_argument("--dry-run", action="store_true",
                    help="fill the mapping, report Save state, then Escape")
    ap.add_argument("--only", help="workbook id or exact name")
    ap.add_argument("--limit", type=int, help="max workbooks to process this run")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    wbs = json.load(open(SCOPE_PATH, encoding="utf-8"))
    # Sorted by name, not dict insertion order: --shard partitions and
    # --after cursors are computed from this list, so regenerating the
    # scope JSON must not re-partition the fleet under running workers.
    scope = [{"workbook_id": wid, "workbook_name": name} for wid, name in wbs.items()]
    scope.sort(key=lambda e: e["workbook_name"])
    if args.only:
        scope = [e for e in scope if args.only in (e["workbook_id"], e["workbook_name"])]
        if not scope:
            raise SystemExit(f"--only {args.only!r} matched nothing in scope")

    state = load(STATE_PATH, {})
    if args.recon:
        pending = scope
    else:
        pending = [e for e in scope if not workbook_done(state, e["workbook_id"])]
    if args.limit:
        pending = pending[: args.limit]

    tag = ("recon" if args.recon else
           "dry" if args.dry_run else
           "only" if args.only else "all")
    log_path = os.path.join(LOG_DIR, f"run_{tag}.log")
    done_ct = sum(1 for e in scope if workbook_done(state, e["workbook_id"]))
    print(f"[{tag}] scope={len(scope)} pending={len(pending)} already_done={done_ct}",
          flush=True)

    cf = 0
    with browser_session.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        say(f"\n===== {tag} run {stamp} =====")
        for i, entry in enumerate(pending):
            wid = entry["workbook_id"]
            say(f"\n--- [{i+1}/{len(pending)}] {entry['workbook_name']} ---")
            r = None
            last_exc = None
            for dns_try in range(3):
                try:
                    if args.recon:
                        r = A.recon(page, entry, TABLE, say)
                    else:
                        r = A.apply_findworkemail(page, entry, TABLE,
                                                  args.dry_run, say)
                    last_exc = None
                    break
                except Exception as e:
                    last_exc = e
                    if "ERR_NAME_NOT_RESOLVED" in str(e) and dns_try < 2:
                        say(f"   DNS blip, pausing 30s (try {dns_try+1}/3)")
                        try:
                            page.keyboard.press("Escape")
                        except Exception:
                            pass
                        page.wait_for_timeout(30000)
                        continue
                    break
            if last_exc is not None:
                say(f"!! EXCEPTION on {entry['workbook_name']}: {str(last_exc)[:200]}")
                logf.write(traceback.format_exc()); logf.flush()
                cf += 1
                if not (args.dry_run or args.recon):
                    state[wid] = {"status": "error", "error": str(last_exc)[:300]}
                    save(STATE_PATH, state)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            else:
                if not (args.dry_run or args.recon):
                    state[wid] = r
                    save(STATE_PATH, state)
                    if r.get("ran"):
                        say(f"   credits: {r['ran']}")
                cf = cf + 1 if r["status"] not in _FINAL_STATUSES + ("recon",) else 0
            if cf >= 3:
                say("!! 3 consecutive failures — aborting"); sys.exit(2)
        say("\nSUMMARY: see workemail_state.json for per-workbook results")


def _refuse_unless_opted_in():
    """This pass is superseded and costs ~14.5 credits/row; 30,000+ for a
    fleet run. Nothing should reach it by habit or by an old shell-history
    line, so require a deliberate environment opt-in."""
    if not os.environ.get("CLAY_ALLOW_SUPERSEDED"):
        raise SystemExit(
            "apply_findworkemail_* is SUPERSEDED by add_workemail_waterfall.py "
            "(see that module's header) and spends ~14.5 credits/row.\n"
            "If you really mean to run it, set CLAY_ALLOW_SUPERSEDED=1.")


if __name__ == "__main__":
    _refuse_unless_opted_in()
    main()
