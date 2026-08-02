"""Fleet driver: per Competitive Events workbook's Speakers_normalized, add
BOTH email steps —

  1. Waterfall > WORK EMAIL, gated !!{{Speaker Name}}, auto-run OFF, then RUN it
     (~1.4 credits/row, only on rows that have a speaker name).
  2. LeadMagic > Validate email, mapped to WORK EMAIL, gated !!{{WORK EMAIL}},
     auto-run OFF, output fields Status / Mx Record / Mx Provider — CONFIGURED
     BUT NOT RUN, mirroring what was done on BioTrinity.

Scope order follows speakers_normalized_workbooks.json; --after skips everything
up to and including a named workbook (default BioTrinity, which is already done).
Anything the user already built by hand is detected by its signature column and
skipped, never rebuilt.

Both steps verify what PERSISTED by reopening the column, and step 1 refuses to
run unless its gate is really there — see add_workemail_waterfall.py for
why (a run condition can look set pre-save and be silently dropped).

  python speakers_email_rollout.py --limit 5                    # one worker
  python speakers_email_rollout.py --shards 2 --shard 0 --limit 5
  python speakers_email_rollout.py --shards 2 --shard 1 --limit 5
  python speakers_email_rollout.py --only "HIMSS" --dry-run
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import browser_session                              # noqa: E402
import add_workemail_waterfall as panel  # noqa: E402
import add_validate_email       # noqa: E402

SCOPE_PATH = os.path.join(SCRIPT_DIR, "speakers_normalized_workbooks.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "speakers_email_logs")
os.makedirs(LOG_DIR, exist_ok=True)

# A step is done when it reaches one of these; anything else stays pending so a
# rerun retries it.
DONE = ("ok", "running", "exists", "ready", "dryrun")
# Statuses meaning "this event genuinely can't proceed" — skipped and reported,
# not retried forever. NOT no_source_column: that just means the waterfall hasn't
# produced WORK EMAIL yet (always true in --dry-run), so it must stay retryable.
SKIP = ("no_table", "no_gate_column")


def load(p, d):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return d


def save(p, s):
    json.dump(s, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def step_done(rec, step):
    return (rec.get(step) or {}).get("status") in DONE + SKIP


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="workbook id or exact name")
    ap.add_argument("--limit", type=int, help="max workbooks this run")
    ap.add_argument("--after", default="BioTrinity",
                    help="skip scope up to and including this workbook name")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--skip-run", action="store_true",
                    help="configure the waterfall but don't trigger its run")
    args = ap.parse_args()

    wbs = json.load(open(SCOPE_PATH, encoding="utf-8"))
    scope = [{"workbook_id": wid, "workbook_name": n} for wid, n in wbs.items()]

    if args.after:
        names = [e["workbook_name"] for e in scope]
        if args.after in names:
            scope = scope[names.index(args.after) + 1:]
        else:
            raise SystemExit(f"--after {args.after!r} not in scope")
    if args.only:
        scope = [e for e in scope
                 if args.only in (e["workbook_id"], e["workbook_name"])]
        if not scope:
            raise SystemExit(f"--only {args.only!r} matched nothing")

    # Disjoint partition by index: no two workers ever touch the same event.
    if args.shards > 1:
        scope = [e for i, e in enumerate(scope) if i % args.shards == args.shard]

    suffix = f"_w{args.shard}" if args.shards > 1 else ""
    state_path = os.path.join(SCRIPT_DIR, f"speakers_email_state{suffix}.json")
    state = load(state_path, {})

    pending = [e for e in scope
               if not (step_done(state.get(e["workbook_id"], {}), "waterfall")
                       and step_done(state.get(e["workbook_id"], {}), "validate"))]
    if args.limit:
        pending = pending[: args.limit]

    tag = ("dry" if args.dry_run else
           "only" if args.only else f"w{args.shard}" if args.shards > 1 else "all")
    log_path = os.path.join(LOG_DIR, f"run_{tag}.log")
    print(f"[{tag}] scope={len(scope)} pending={len(pending)}", flush=True)

    skipped = []
    with browser_session.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()

        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        say(f"\n===== {tag} run {stamp} (shard {args.shard}/{args.shards}) =====")

        for i, entry in enumerate(pending):
            wid, name = entry["workbook_id"], entry["workbook_name"]
            rec = state.setdefault(wid, {"workbook_name": name})
            say(f"\n--- [{i+1}/{len(pending)}] {name} ---")

            # step 1: waterfall (adds, verifies the gate, then runs)
            if not step_done(rec, "waterfall"):
                try:
                    r = panel.add_waterfall(page, entry, args.dry_run, say,
                                        run_after=not (args.dry_run or args.skip_run))
                except Exception as e:
                    say(f"!! waterfall EXCEPTION on {name}: {str(e)[:180]}")
                    logf.write(traceback.format_exc()); logf.flush()
                    r = {"status": "error", "error": str(e)[:300]}
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                rec["waterfall"] = r
                if not args.dry_run:
                    save(state_path, state)
                if r.get("status") not in DONE:
                    skipped.append((name, "waterfall", r.get("status"),
                                    r.get("reason") or r.get("error")))
                    say(f"SKIPPING {name}: waterfall {r.get('status')}")
                    continue

            # step 2: validate email (configured, NOT run)
            if not step_done(rec, "validate"):
                try:
                    r = (add_validate_email.add_validate(page, entry, True, say)
                         if args.dry_run else
                         add_validate_email.ensure_validate(page, entry, say))
                except Exception as e:
                    say(f"!! validate EXCEPTION on {name}: {str(e)[:180]}")
                    logf.write(traceback.format_exc()); logf.flush()
                    r = {"status": "error", "error": str(e)[:300]}
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                rec["validate"] = r
                if not args.dry_run:
                    save(state_path, state)
                if r.get("status") not in DONE:
                    skipped.append((name, "validate", r.get("status"),
                                    r.get("reason") or r.get("error")))
                    say(f"NOTE {name}: validate {r.get('status')} — needs a look")

        say("\n===== SUMMARY =====")
        for e in scope:
            rec = state.get(e["workbook_id"], {})
            w = (rec.get("waterfall") or {}).get("status")
            v = (rec.get("validate") or {}).get("status")
            if w or v:
                say(f"  {e['workbook_name']:45} waterfall={w} validate={v}")
        if skipped:
            say("\nNEEDS MANUAL CHECK:")
            for n, step, st, why in skipped:
                say(f"  {n:45} {step:9} {st} {why or ''}")
        say(f"\nstate: {state_path}")


if __name__ == "__main__":
    main()
