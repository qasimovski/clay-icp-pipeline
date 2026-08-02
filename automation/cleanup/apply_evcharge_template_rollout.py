"""Fleet rollout of the consolidated EVCharge template across the event
workbooks in "10. EVCharge [2026 - Qasim]" / Competitive Events.

Per event: apply "Domain, Enrich Company, Lookup, Add rows" dormant, then
trigger the server-side run (select all -> Actions -> Run N rows). Single-pass
per event — see apply_evcharge_template.py for the mechanics and
config/entity-types/exhibitors_evcharge.yaml for the names.

Resumable: every event's outcome is written to evcharge_tpl_state.json as it
finishes, so a killed batch resumes where it stopped. Events already carrying
the template are skipped by the header check inside apply_evcharge_template, and the
run is not re-triggered for an event this state file already records as done.

Usage:
  python apply_evcharge_template_rollout.py --dry-run
  python apply_evcharge_template_rollout.py --limit 5          # one batch of 5
  python apply_evcharge_template_rollout.py --limit 5 --only "SITCE"
  python apply_evcharge_template_rollout.py --status           # what's left
"""

import argparse
import datetime
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import apply_evcharge_template as template_op  # noqa: E402  (sets CLAY_PIPELINE_ENTITY)
import browser_session                     # noqa: E402
import run_all_columns as runcols        # noqa: E402
import state_io                            # noqa: E402

STATE_PATH = os.path.join(SCRIPT_DIR, "evcharge_tpl_state.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "evcharge_tpl_logs")

# Done by hand / as the reviewed pilot — never re-run by the rollout.
PRE_DONE = {
    "ACT Expo": "applied+run by hand (user)",
    "EV Auto Show": "pilot, applied+run and reviewed 2026-07-27",
}


def load_state():
    # Fail-loud on corruption (state_io), and seed the hand-done events even
    # when a state file already exists — previously the PRE_DONE protection
    # applied only when the file was absent, so deleting/recreating it put the
    # two protected events back in scope.
    state = state_io.load_json(STATE_PATH)
    for name, note in PRE_DONE.items():
        state.setdefault(name, {"status": "ok", "note": note})
    return state


def save_state(state):
    state_io.save_json(STATE_PATH, state, sort_keys=True)


def all_events():
    with open(template_op.WB_IDS, encoding="utf-8") as fh:
        ids = json.load(fh)
    return sorted(((name, wid) for wid, name in ids.items()), key=lambda t: t[0])


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=5,
                    help="events to process this run (batch size); default 5")
    ap.add_argument("--only", action="append", metavar="EVENT",
                    help="restrict to this event (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true", help="print progress and exit")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    state = load_state()
    events = all_events()
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {n for n, _ in events}
        if unknown:
            raise SystemExit(f"unknown event(s): {sorted(unknown)}")
        events = [e for e in events if e[0] in wanted]

    done = {n for n, r in state.items() if r.get("status") == "ok"}
    pending = [(n, w) for n, w in events if n not in done]

    if args.status:
        print(f"{len(done)} done, {len(pending)} pending "
              f"(of {len(all_events())} workbooks)")
        for n, r in sorted(state.items()):
            print(f"  [{r.get('status'):>7}] {n}  {r.get('ran') or r.get('note') or ''}")
        for n, _ in pending:
            print(f"  [pending] {n}")
        return

    batch = pending[:args.limit]
    print(f"template : {template_op.TEMPLATE!r}")
    print(f"table    : {template_op.TABLE!r}")
    print(f"{len(done)} done, {len(pending)} pending; this batch: {len(batch)}")
    for n, w in batch:
        print(f"  - {n}  [{w}]")
    if args.dry_run:
        print("\nDRY RUN — nothing applied.")
        return
    if not batch:
        print("Nothing pending.")
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    say = lambda m: print(m, flush=True)
    runcols.TABLE = template_op.TABLE
    runcols.ALL_COLUMNS_MARKER = template_op.MARKER

    with browser_session.clay_page(headless=not args.headed) as page:
        for name, wid in batch:
            try:
                applied = template_op.apply_template(page, wid, name, False, False, say)
                # run_v1 carries its own already-run guard (marker-column
                # status), so a retried event whose previous attempt failed
                # AFTER the run was triggered is skipped as 'already_run'
                # instead of re-spending Domain + Enrich Company credits.
                res = runcols.run_v1(page, {"workbook_id": wid, "workbook_name": name},
                                 False, say)
                ok = res.get("status") in ("ok", "already_run")
                state[name] = {
                    "status": "ok" if ok else "failed",
                    "applied": applied,
                    "ran": res.get("ran") or res.get("status"),
                    "run_state": res.get("state") or res.get("progress"),
                    "at": datetime.datetime.now().isoformat(timespec="seconds"),
                }
                if not ok:
                    state[name]["reason"] = res.get("reason")
            except Exception as e:
                say(f"FAILED {name}: {e}")
                state[name] = {
                    "status": "failed", "error": str(e),
                    "at": datetime.datetime.now().isoformat(timespec="seconds"),
                }
                try:
                    page.screenshot(path=os.path.join(
                        LOG_DIR, f"fail_{name.replace(os.sep, '_')[:60]}.png"))
                except Exception:
                    pass
            save_state(state)

    print("\n" + "=" * 60)
    for name, _ in batch:
        r = state.get(name, {})
        print(f"  [{r.get('status'):>7}] {name}  {r.get('ran') or r.get('error','')}")
    left = [n for n, _ in all_events()
            if state.get(n, {}).get("status") != "ok"]
    print(f"\n{len(left)} event(s) still pending: {left[:8]}"
          f"{' ...' if len(left) > 8 else ''}")


if __name__ == "__main__":
    main()
