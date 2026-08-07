"""Apply the "Enrich Company for People Data" template to the People table of
one Product & Services workbook, mapping Domain and running it.

Per the user (2026-08-03): map the template's Domain input to the table's
"Domain" — which lives under the "Company Table Data" dropdown, not as a
top-level column — then "Save and run" for each table.

=== The one mapping ===

  panel label   value to bind                  how it arrives
  ------------- ------------------------------ --------------
  Domain        Company Table Data > Domain    MAP

These People tables have no top-level Domain column (confirmed against
Cleanroom Technology's column list, 2026-08-03), so the value can only come
from under the "Company Table Data" lookup group. That is the identical walk
yesterday's "Enrich and Validate Email" pass used for its Domain input, so the
picker geometry is inherited rather than re-derived — a Clay UI change still
only has to be fixed in apply_people_waterfall.

No `filter` on the step: filtering by "Domain" reports "No properties" because
the picker search does not descend into an unexpanded column's schema (learned
on the Waterfall template, see apply_people_waterfall.MAPPING).

Any other input the template exposes is left exactly as it arrives. The user
asked for Domain and nothing else, and UNMAPPED_REPORT below means those inputs
are still *reported* every run rather than silently ignored.

=== Run-now, deliberately ===

The repo's standing rule is that paid providers are configured but never
triggered (CLAUDE.md, "Run-now vs configure-only"). Enrich Company is a paid
per-row provider, so this pass is an explicit, user-instructed exception:
"save and run it for each table". Every row in the view is charged.

Idempotency is what keeps that from being charged twice: MARKER_CANDIDATES is
checked against Clay's real column list before the template is opened, and a
table that already carries the column is skipped, not re-applied. Re-applying
is how duplicate column sets get created (see people_email.py on Analytica
China).

Scope: tables named exactly "People" in the Product & Services folder
(product_services_people.json). Nothing else is reachable. Chemicals & Reagents
is deliberately absent from that manifest — the user did it by hand.

  python apply_enrich_company_people.py "Material Sciences" --recon    # look only
  python apply_enrich_company_people.py "Material Sciences" --dry-run  # map, no save
  python apply_enrich_company_people.py "Material Sciences" --run      # map + spend
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

# apply_people_waterfall reads its template name from this at import time.
os.environ.setdefault("CLAY_PEOPLE_TEMPLATE", "Enrich Company for People Data")

import browser_session                        # noqa: E402
import state_io                               # noqa: E402
import apply_people_waterfall as apw          # noqa: E402  (picker primitives)
import apply_companies_supabase as acs        # noqa: E402  (PANEL dump for recon)

TABLE = "People"
COLS_SCRIPT = os.path.join(SCRIPT_DIR, "check_table_columns.py")

# The manifest is the ONLY thing that decides what is reachable — same scoping
# contract as apply_people_supabase.py / apply_people_enrich_email.py.
AUDIT = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_PEOPLE_MANIFEST", "product_services_people.json"))
STATE = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_ENRICH_COMPANY_STATE", "ps_enrich_company_state.json"))

# Proof the template is already on the table, so a re-run never double-applies
# and never double-charges. Presence of ANY of these counts as applied;
# confirmed against the first table this pass touches and widened if the
# template turns out to name its output column differently.
MARKER_CANDIDATES = ("Enrich Company for People Data", "Enrich Company")

# Picker geometry, inherited (recon TechBio UK 2026-07-28, reconfirmed by the
# email pass on Cleanroom Technology 2026-08-02).
GRP_X = apw.GRP_X
LEAF_X = apw.LEAF_X

# The panel label for the template's input, which is LOWERCASE "domain" —
# confirmed by --recon on Material Sciences 2026-08-03 (label at x=1270 y=255,
# its value box 35px below at x=1279). Note the case difference from the column
# being bound INTO it, "Company Table Data > Domain": the input is the
# template's own parameter name, the column is Clay's. _BOX matches exact text,
# so "Domain" here reads as an absent field and the pass aborts before saving
# rather than saving half-mapped.
#
# recon also confirmed this is the template's ONLY input, so nothing else on
# the panel is touched.
DOMAIN_LABEL = os.environ.get("CLAY_ENRICH_DOMAIN_LABEL", "domain")

MAPPING = {
    DOMAIN_LABEL: {
        "steps": [("Company Table Data",) + GRP_X,
                  ("Domain",) + LEAF_X],
    },
}

# What the field must read once configured (substring match). apply_people_
# template aborts before saving if this is not satisfied, so nothing ever saves
# half-mapped.
EXPECTED = {DOMAIN_LABEL: "Domain"}

# Legitimate chip segments; anything else landing in the panel's x-band is grid
# content showing through beside it (see apply_email_template for why).
CHIP_VOCAB = ["Domain", "Company Domain", "Company Table Data", "Name",
              "Full Name", "LinkedIn Profile", "Company", "Job Title",
              "Lookup row", "Lookup in Audiences", "People - Supabase",
              "Is New", "records", "totalRecords"]

# Rebind apply_people_waterfall's module-level config to this template. Its
# _fill/chips/apply_people_template read these as globals, so overriding them
# retargets the proven code without duplicating it.
apw.MAPPING = MAPPING
apw.EXPECTED = EXPECTED
apw.CHIP_VOCAB = CHIP_VOCAB
apw.MARKER = MARKER_CANDIDATES[0]


def _wsl(script, *args, timeout=180):
    path = script.replace("C:\\", "/mnt/c/").replace("\\", "/")
    out = subprocess.run(["wsl", "-d", "Ubuntu", "--", "python3", path, *args],
                         capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    return (out.stdout or "").strip()


def table_columns(table_id, say):
    """Column names straight from Clay. Empty list means 'could not tell'."""
    try:
        return _wsl(COLS_SCRIPT, table_id).splitlines()
    except Exception as e:
        say(f"    !! column check failed: {str(e)[:70]}")
        return []


def already_applied(table_id, say):
    """True if the template is already on this table, None if unreadable.

    Fail-closed: an unreadable column list returns None and the caller refuses
    to touch the table. Guessing 'not applied' here would re-apply the template
    and charge every row a second time.
    """
    cols = table_columns(table_id, say)
    if not cols:
        say(f"  !! cannot read columns for {TABLE} — refusing to touch it")
        return None
    hit = [c for c in MARKER_CANDIDATES if c in cols]
    say(f"  {TABLE}: {len(cols)} columns, marker present={hit or False}")
    return bool(hit)


def _record(name, res):
    state = state_io.load_json(STATE, {})
    state[name] = res
    state_io.save_json(STATE, state)


def do_workbook(page, name, rec, say, run_after=False, dry_run=False,
                recon=False):
    entry = {"workbook_id": rec["workbook_id"], "workbook_name": name}
    res = {"workbook": name,
           "at": datetime.datetime.now().isoformat(timespec="seconds")}

    if not (recon or dry_run):
        applied = already_applied(rec.get("table_id"), say)
        if applied is None:
            return {**res, "status": "unreadable", "ran": False}
        if applied:
            say(f"  SKIP {name}: template already applied")
            return {**res, "status": "already_applied", "ran": False}

    out = apw.apply_people_template(page, entry, TABLE, dry_run, say,
                                    recon=recon, run_after=run_after)
    res.update(out)
    res["ran"] = bool(run_after) and out.get("status") == "ok"

    if recon:
        # chips() only reads labels in EXPECTED, which cannot confirm a label
        # this pass has not seen yet — dump the whole panel instead, so the
        # real label is visible before anything is saved.
        try:
            rows = page.evaluate(acs.PANEL)
            say("  --- full panel dump ---")
            for r in rows:
                say(f"    x={r['x']:<5} y={r['y']:<5} {r['t']!r}")
            res["panel"] = [r["t"] for r in rows]
        except Exception as e:
            say(f"  panel dump failed: {str(e)[:80]}")

    if not (recon or dry_run):
        _record(name, res)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_name")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true",
                    help="Save and run in this view (SPENDS CREDITS)")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    audit = json.load(open(AUDIT, encoding="utf-8"))
    if a.workbook_name not in audit:
        raise SystemExit(
            f"{a.workbook_name!r} is not in {os.path.basename(AUDIT)} "
            f"(in scope: {', '.join(sorted(audit))})")

    with browser_session.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        out = do_workbook(page, a.workbook_name, audit[a.workbook_name], say,
                          run_after=a.run, dry_run=a.dry_run, recon=a.recon)
    print("\nRESULT:", json.dumps(out, indent=1, default=str))
