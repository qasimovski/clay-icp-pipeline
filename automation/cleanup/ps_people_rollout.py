"""Roll the "People - Supabase" template across the Product & Services People
tables, one browser session for the whole fleet.

Order per the user (2026-08-02), reaffirmed after being offered the cheaper
alternative: apply the template and "Save and run" over ALL rows FIRST, then
switch the HTTP API account to "Qasim - Labs". Rows that are not gated by
"Lookup in Audiences" therefore fire once against the wrong account (401) before
the account is corrected; that is a known and accepted cost of this ordering.

Smallest table first, so a UI regression surfaces on a cheap table rather than
on Testing & Diagnostics (8,671 rows).

Chemicals & Reagents is absent from the manifest on purpose -- already done by
hand -- and Material Sciences is already complete, so both are skipped by the
state guard.

  python ps_people_rollout.py --audit        # show plan, touch nothing
  python ps_people_rollout.py --limit 1      # next table only
  python ps_people_rollout.py                # the rest of the fleet
"""

import argparse
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

os.environ.setdefault("CLAY_SUPABASE_TEMPLATE", "People - Supabase")

import browser_session               # noqa: E402
import state_io                      # noqa: E402
import apply_people_supabase as app  # noqa: E402

# Row counts read from Clay on 2026-08-02; used only for ordering and for
# reporting the blast radius, never as a correctness input. Smallest first so a
# regression surfaces cheaply.
ORDER_PRODUCT_SERVICES = [
    ("Cleanroom Technology", 362),
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

# Buyside P&S. FMCG is deliberately absent: it has 0 rows, so there is nothing
# to run and trigger_run would report a false failure.
ORDER_BUYSIDE = [
    ("Forensics", 319),
    ("Academics", 1447),
    ("Food & Agriculture", 1829),
    ("Water & Energy", 2672),
    ("Non-industry Specific JT Searches", 3746),
    ("Laboratories & Research Organisations", 4230),
    ("Pharma & Biotech", 7724),
    ("Petrochemicals & Materials", 10516),
    ("Diagnostics & Healthcare", 11665),
]

ORDER = (ORDER_BUYSIDE if os.environ.get("CLAY_PEOPLE_MANIFEST", "")
         .startswith("buyside") else ORDER_PRODUCT_SERVICES)

DONE_STATUSES = ("ok", "already_applied")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--limit", type=int, default=len(ORDER))
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    audit = json.load(open(app.AUDIT, encoding="utf-8"))
    state = state_io.load_json(app.STATE, {})

    todo = [(n, r) for n, r in ORDER
            if state.get(n, {}).get("status") not in DONE_STATUSES]
    todo = todo[: args.limit]

    done = [n for n, s in state.items() if s.get("status") in DONE_STATUSES]
    print(f"already complete ({len(done)}): {sorted(done)}", flush=True)
    print(f"to do ({len(todo)}), {sum(r for _, r in todo)} rows:", flush=True)
    for n, r in todo:
        print(f"   {r:>6}  {n}", flush=True)
    if args.audit or not todo:
        return

    with browser_session.clay_page(headless=not args.headed) as page:
        for i, (name, rows) in enumerate(todo, 1):
            print(f"\n=== [{i}/{len(todo)}] {name}  ({rows} rows) ===",
                  flush=True)
            rec = audit[name]
            entry = {"workbook_id": rec["workbook_id"],
                     "workbook_name": name,
                     "table_id": rec.get("table_id")}

            def say(m):
                print(m, flush=True)

            try:
                res = app.apply_people(page, entry, say)
            except Exception as e:
                print(f"!! EXCEPTION {name}: {str(e)[:200]}", flush=True)
                traceback.print_exc()
                res = {"workbook_name": name, "status": "error",
                       "error": str(e)[:400]}
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            res["rows"] = rows
            state[name] = res
            state_io.save_json(app.STATE, state)
            print(f"  -> {res.get('status')} "
                  f"account={res.get('account_after')}", flush=True)

    print("\n===== SUMMARY =====", flush=True)
    for name, rows in ORDER:
        s = state.get(name, {})
        print(f"  {name[:52]:52} {rows:>6}  {str(s.get('status')):<22} "
              f"acct={s.get('account_after')}", flush=True)


if __name__ == "__main__":
    main()
