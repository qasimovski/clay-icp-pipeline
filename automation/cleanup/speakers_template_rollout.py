"""Fleet driver for the "Find Email and Validate Email" template flow.

Per pending Speakers_normalized table:
  1. apply the template (mapping Speaker Name / company_domain / org; LinkedIn
     URL auto-maps), saved WITHOUT running;
  2. map Validate Email's email input to WORK EMAIL and RETYPE its run condition
     as !!{{WORK EMAIL}} — the template carries that gate over as a raw field id
     from its source table, so it must be rebound per workbook;
  3. verify WORK EMAIL's own gate is !!{{Speaker Name}} and trigger its run
     (~1.4 credits/row, only rows that have a speaker name).
Validate Email is left configured but NOT run.

Scope comes from speakers_email_audit.json (build it with
audit_speakers_email.py, in WSL) — only tables whose state is "pending". That
audit is CLI-based on purpose: a browser header scan misses columns that sit
off-screen and would happily add a SECOND set of email columns to an already
built table.

  python speakers_template_rollout.py --limit 5
  python speakers_template_rollout.py --shards 2 --shard 0 --limit 3
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import common                               # noqa: E402
import apply_email_template_event as T      # noqa: E402
import configure_validate_email_event as V  # noqa: E402

CHECK_SCRIPT = os.path.join(SCRIPT_DIR, "check_table_columns.py")


def already_built(entry, audit_rec, say):
    """True if Clay already has a WORK EMAIL column on this table.

    Asks the CLI right now rather than trusting state or the batch-start audit:
    a killed worker can leave columns created but unrecorded.
    """
    tid = (audit_rec or {}).get("table_id")
    if not tid:
        return False
    wsl_path = CHECK_SCRIPT.replace("C:\\", "/mnt/c/").replace("\\", "/")
    try:
        out = subprocess.run(
            ["wsl", "-d", "Ubuntu", "--", "python3", wsl_path, tid],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        names = (out.stdout or "").strip().splitlines()
    except Exception as e:
        say(f"  !! column pre-check failed ({str(e)[:80]}) — skipping for safety")
        return True          # fail closed: never risk a duplicate build
    if not names:
        say("  !! column pre-check returned nothing — skipping for safety")
        return True
    return "WORK EMAIL" in names


FILL_SCRIPT = os.path.join(SCRIPT_DIR, "check_column_fill.py")


def already_run(audit_rec, say):
    """True if WORK EMAIL already holds values, i.e. the waterfall has run.

    Guards the RUN, not just the build. A late-finishing worker overwrote a
    shared state file with a stale snapshot, which made the rollout re-trigger
    SLAS Europe's waterfall and pay for it twice.
    """
    tid = (audit_rec or {}).get("table_id")
    if not tid:
        return False
    wsl_path = FILL_SCRIPT.replace("C:\\", "/mnt/c/").replace("\\", "/")
    try:
        out = subprocess.run(
            ["wsl", "-d", "Ubuntu", "--", "python3", wsl_path, tid, "WORK EMAIL"],
            capture_output=True, text=True, timeout=180,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        filled, total = (out.stdout or "0 0").split()[:2]
        say(f"  WORK EMAIL fill: {filled}/{total}")
        return int(filled) > 0
    except Exception as e:
        say(f"  !! fill pre-check failed ({str(e)[:70]}) — not re-running")
        return True          # fail closed: never risk paying twice


AUDIT_PATH = os.path.join(SCRIPT_DIR, "speakers_email_audit.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "speakers_tpl_logs")
os.makedirs(LOG_DIR, exist_ok=True)

DONE = ("ok", "dryrun")


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
    return (rec.get(step) or {}).get("status") in DONE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--skip-run", action="store_true",
                    help="configure everything but don't trigger the waterfall")
    args = ap.parse_args()

    if not os.path.exists(AUDIT_PATH):
        raise SystemExit("run audit_speakers_email.py (in WSL) first")
    audit = json.load(open(AUDIT_PATH, encoding="utf-8"))
    scope = [{"workbook_id": v["workbook_id"], "workbook_name": n}
             for n, v in audit.items() if v.get("state") == "pending"]
    scope.sort(key=lambda e: e["workbook_name"])

    if args.only:
        scope = [e for e in scope
                 if args.only in (e["workbook_id"], e["workbook_name"])]
        if not scope:
            raise SystemExit(f"--only {args.only!r} matched nothing pending")
    if args.shards > 1:
        scope = [e for i, e in enumerate(scope) if i % args.shards == args.shard]

    suffix = f"_w{args.shard}" if args.shards > 1 else ""
    state_path = os.path.join(SCRIPT_DIR, f"speakers_tpl_state{suffix}.json")
    state = load(state_path, {})

    pending = [e for e in scope
               if not (step_done(state.get(e["workbook_id"], {}), "template")
                       and step_done(state.get(e["workbook_id"], {}), "validate"))]
    if args.limit:
        pending = pending[: args.limit]

    tag = ("dry" if args.dry_run else
           "only" if args.only else f"w{args.shard}" if args.shards > 1 else "all")
    log_path = os.path.join(LOG_DIR, f"run_{tag}.log")
    print(f"[{tag}] pending_in_scope={len(scope)} this_run={len(pending)}",
          flush=True)

    needs_check = []
    with common.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()

        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        say(f"\n===== {tag} run {stamp} (shard {args.shard}/{args.shards}) =====")

        for i, entry in enumerate(pending):
            wid, name = entry["workbook_id"], entry["workbook_name"]
            rec = state.setdefault(wid, {"workbook_name": name})
            say(f"\n--- [{i+1}/{len(pending)}] {name} ---")

            if not step_done(rec, "template"):
                # Ask Clay directly first. State alone is not enough: a worker
                # killed mid-event can leave the columns created but unrecorded,
                # which is how two tables got the template applied twice.
                if not args.dry_run and already_built(entry, audit.get(name), say):
                    say(f"SKIP {name}: WORK EMAIL already exists in Clay "
                        f"(built by an earlier, possibly killed, run)")
                    r = {"status": "ok", "note": "already_built"}
                    rec["template"] = r
                    save(state_path, state)
                else:
                  try:
                    r = T.apply_template(page, entry, args.dry_run, say)
                  except Exception as e:
                    say(f"!! template EXCEPTION on {name}: {str(e)[:180]}")
                    logf.write(traceback.format_exc()); logf.flush()
                    r = {"status": "error", "error": str(e)[:300]}
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                  rec["template"] = r
                if not args.dry_run:
                    save(state_path, state)
                if r.get("status") not in DONE:
                    needs_check.append((name, "template", r.get("status"),
                                        r.get("reason") or r.get("error")))
                    say(f"SKIPPING {name}: template {r.get('status')}")
                    continue

            if args.dry_run:
                continue

            if not step_done(rec, "validate"):
                do_run = not args.skip_run and not already_run(
                    audit.get(name), say)
                try:
                    r = V.configure(page, entry, say, run_after=do_run)
                except Exception as e:
                    say(f"!! validate EXCEPTION on {name}: {str(e)[:180]}")
                    logf.write(traceback.format_exc()); logf.flush()
                    r = {"status": "error", "error": str(e)[:300]}
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                rec["validate"] = r
                save(state_path, state)
                if r.get("status") not in DONE:
                    needs_check.append((name, "validate", r.get("status"),
                                        r.get("reason") or r.get("error")))
                    say(f"NOTE {name}: validate {r.get('status')}")

        say("\n===== SUMMARY =====")
        for e in scope:
            rec = state.get(e["workbook_id"], {})
            t = (rec.get("template") or {}).get("status")
            v = (rec.get("validate") or {}).get("status")
            ran = (rec.get("validate") or {}).get("ran")
            if t or v:
                say(f"  {e['workbook_name']:45} template={t} validate={v} "
                    f"ran={ran}")
        if needs_check:
            say("\nNEEDS MANUAL CHECK:")
            for n, step, st, why in needs_check:
                say(f"  {n:45} {step:9} {st} {why or ''}")
        say(f"\nstate: {state_path}")


if __name__ == "__main__":
    main()
