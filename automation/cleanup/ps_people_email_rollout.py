"""Roll the "Enrich and Validate Email" pass across the Product & Services
People tables, one browser session for the whole batch.

Per table: apply the template (Save and don't run) -> bind Validate Email
(Email Address = WORK EMAIL, run condition !!{{WORK EMAIL}}, Auto-update OFF)
-> trigger the waterfall run. Configure-before-run is deliberate: nothing is
charged until the run condition points at THIS table's WORK EMAIL, because the
template arrives gated on a field id from whichever table it was built on.

Ordered smallest-first from live row counts read on 2026-08-02 (NOT the stale
list in ps_people_rollout.py — that one predates Material Sciences' People
table and had no count for it). Smallest first so a UI regression surfaces on a
225-row table rather than on Testing & Diagnostics (8,671).

Cleanroom Technology is absent: applied by this pass and run by the user
2026-08-02, verified 297/362 WORK EMAIL filled. Chemicals & Reagents is absent
from product_services_people.json itself — done by hand earlier.

  python ps_people_email_rollout.py --audit          # show the plan, touch nothing
  python ps_people_email_rollout.py --limit 3        # next batch of 3
  python ps_people_email_rollout.py --only "Material Sciences"
  python ps_people_email_rollout.py --configure-only --limit 3   # no credits
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import browser_session                        # noqa: E402
import state_io                               # noqa: E402
import apply_people_enrich_email as app       # noqa: E402

LOG_DIR = os.path.join(SCRIPT_DIR, "ps_people_email_logs")

# (workbook name, live row count). Row counts are used ONLY for ordering and for
# reporting blast radius, never as a correctness input.
ORDER = [
    ("Material Sciences", 225),
    ("Laboratory Data, Integration & Connectivity Software", 842),
    ("Laboratory Automation & Robotics", 894),
    ("Digital & AI Services", 1085),
    ("Environment and energy tech & monitoring", 1240),
    ("FMCG testing solutions", 1810),
    ("Lab Equipment & Instrumentation Suppliers", 1828),
    ("Distributors of lab equipment", 2844),
    ("Food and Agri tech & monitoring", 3045),
    ("Testing & Diagnostics", 8671),
]

# A table is finished only once its run is accounted for. "configured" means the
# columns are in place but the waterfall has not been triggered, so it must be
# re-entered — the run guards inside do_workbook (write-ahead log, then fill
# count) decide whether that actually costs anything.
DONE_STATUSES = ("ok",)


def pending(audit, state):
    out = []
    for name, rows in ORDER:
        if name not in audit:
            print(f"  !! {name!r} not in the manifest — skipped", flush=True)
            continue
        st = (state.get(name) or {}).get("status")
        if st in DONE_STATUSES:
            continue
        out.append((name, rows, st))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3,
                    help="how many tables this batch (default 3)")
    ap.add_argument("--only", help="one workbook name, ignores --limit")
    ap.add_argument("--audit", action="store_true",
                    help="print the plan and exit, opening no browser")
    ap.add_argument("--configure-only", action="store_true",
                    help="apply + bind Validate Email, do NOT run (no credits)")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    audit = json.load(open(app.AUDIT, encoding="utf-8"))
    state = state_io.load_json(app.STATE)
    todo = pending(audit, state)

    if a.only:
        todo = [t for t in todo if t[0] == a.only]
        if not todo:
            raise SystemExit(
                f"{a.only!r} is not pending (either finished, or not in "
                f"{os.path.basename(app.AUDIT)}). Pending: "
                f"{[t[0] for t in pending(audit, state)]}")
    else:
        todo = todo[:max(a.limit, 0)]

    total = sum(r for _, r, _ in todo)
    print(f"=== plan: {len(todo)} table(s), {total} rows "
          f"({'CONFIGURE ONLY' if a.configure_only else 'WILL SPEND CREDITS'}) ===",
          flush=True)
    for name, rows, st in todo:
        print(f"  {rows:>6}  {name}" + (f"  [resume from {st}]" if st else ""),
              flush=True)
    remaining = [t[0] for t in pending(audit, state)][len(todo):]
    if remaining:
        print(f"  ...{len(remaining)} left after this batch: {remaining}",
              flush=True)
    if a.audit or not todo:
        return 0

    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"run_{stamp}.log")
    results = []

    with open(log_path, "w", encoding="utf-8") as log:
        def say(m):
            print(m, flush=True)
            log.write(m + "\n")
            log.flush()

        say(f"log: {log_path}")
        with browser_session.clay_page(headless=not a.headed) as page:
            for i, (name, rows, st) in enumerate(todo, 1):
                say(f"\n=== [{i}/{len(todo)}] {name} ({rows} rows) ===")
                try:
                    r = app.do_workbook(page, name, audit[name], say,
                                        run_after=not a.configure_only)
                except Exception as e:
                    say(f"  !! {type(e).__name__}: {str(e)[:200]}")
                    say(traceback.format_exc()[-800:])
                    r = {"workbook": name, "status": "exception",
                         "error": f"{type(e).__name__}: {str(e)[:200]}"}
                    # Record the failure so a rerun can see it, but never mark
                    # it done — the next batch re-enters this table.
                    app._record(name, r)
                results.append(r)
                say(f"  -> {r.get('status')} ran={r.get('ran')}")

    print("\n=== batch summary ===", flush=True)
    for r in results:
        print(f"  {r.get('status'):<20} ran={str(r.get('ran')):<28} "
              f"{r.get('workbook')}", flush=True)
    still = [t[0] for t in pending(audit, state_io.load_json(app.STATE))]
    print(f"\n{len(still)} table(s) still pending: {still}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
