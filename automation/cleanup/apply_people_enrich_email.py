"""Apply the "Enrich and Validate Email" template to the People table of one
Product & Services workbook, then bind Validate Email to WORK EMAIL.

Per the user (2026-08-02). This is the email pass for the Product & Services
People tables — the same apply -> rebind -> verify -> gated-run method proven on
Speakers_normalized and the Sellers/Buyers - People tables, but for a DIFFERENT
template with a different Configure panel.

=== 1. FIELDS AND VALUES (the Configure panel) ===

Six inputs. The PANEL label is the short form; the user's mapping named the
template's input names, which differ. Recon on Cleanroom Technology 2026-08-02
(recon_enrich_email_panel.py) established the panel labels and the picker paths:

  panel label       value to bind                              how it arrives
  ----------------- ------------------------------------------ --------------
  Full Name         Full Name                                  prefilled
  Domain            Company Table Data > Domain                 MAP
  records           People - Supabase > Lookup in Audiences
                      > records                                 MAP
  Is New            People - Supabase > Is New                  MAP
  Name              Company Table Data > Name                   MAP
  LinkedIn Profile  LinkedIn Profile                           prefilled

Only "Full Name" and "LinkedIn Profile" prefill, because Clay prefills on exact
column-name match and those two are the only inputs whose names match a column
on this table. Domain/Name MUST come from under "Company Table Data" — these
People tables have no top-level Domain/Name column. records/Is New are nested
under the "People - Supabase" enrichment group, NOT flat columns, so each needs
a search filter to surface the group first: the unfiltered picker list is
clipped by the overlay and does not show it.

"Is New" is ambiguous and deliberately disambiguated: it exists under BOTH
"Company Table Data" (the company ledger's flag, about the account) and
"People - Supabase" (the people ledger's flag, about this person). This is a
People table feeding a per-person waterfall, so the person-level one is bound.

=== 2. RUN CONDITIONS (Edit column, after the apply) ===

On the "Validate Email" column the template creates:
  Email Address  <- WORK EMAIL       (arrives as "Please select a valid value")
  Only run if    <- !!{{WORK EMAIL}} (RETYPED, never accepted as inherited)
  Auto-update    <- OFF

The retype is mandatory per table, not a fallback. The template carries its
condition over as a raw field id from the table it was built on — observed as
!!{{f_0tj5chqH5ZwK344zPdH}} on all 11 P&S tables — which resolves to nothing
here, so the gate would be dead. Typing the column name fresh rebinds it to
THIS table's WORK EMAIL. See configure_validate_email.py.

=== 3. SAVE, THEN RUN AFTER ===

Two separate commits, in this order, and the order is the whole point:

  1. apply the template with "Save and don't run"   (split-button menu)
  2. bind Validate Email + retype the condition, plain Save (Edit-column panel
     commits on plain Save; the split button is a template-panel thing)
  3. ONLY THEN trigger the waterfall: select all rows -> Run N rows

Running at step 1 would charge every row while Validate Email is still gated on
a field id that does not exist here. Two guards stand in front of step 3 — the
write-ahead run log (survives a killed worker) and a WORK EMAIL fill count
(does not: results land after the trigger) — checked in that order. --run is
opt-in; the default stops after step 2 having spent nothing.

Scope: tables named exactly "People" in the Product & Services folder
(product_services_people.json). Nothing else is reachable. Chemicals & Reagents
is deliberately absent from that manifest — already done by hand.

The fragile UI primitives (template picker, field-chip reader, picker-tree
walker) are imported from apply_people_waterfall rather than copied, so a Clay
UI change still only has to be fixed in one place.

  python apply_people_enrich_email.py "Cleanroom Technology" --recon    # look only
  python apply_people_enrich_email.py "Cleanroom Technology" --dry-run  # map, no save
  python apply_people_enrich_email.py "Cleanroom Technology"            # configure, no run
  python apply_people_enrich_email.py "Cleanroom Technology" --run      # + spend credits
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
os.environ.setdefault("CLAY_PEOPLE_TEMPLATE", "Enrich and Validate Email")

import browser_session                            # noqa: E402
import clay_ui                                    # noqa: E402
import column_config as colcfg                    # noqa: E402
import state_io                                   # noqa: E402
import apply_people_waterfall as apw              # noqa: E402  (picker primitives)
import configure_validate_email as cve            # noqa: E402
import add_workemail_waterfall as panel           # noqa: E402

# Which folder's People tables are in scope. The manifest is the ONLY thing that
# decides what is reachable, so pointing it elsewhere is the whole scoping
# mechanism (same contract as apply_people_supabase.py).
AUDIT = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_PEOPLE_MANIFEST", "product_services_people.json"))
STATE = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_PEOPLE_EMAIL_STATE", "ps_people_email_state.json"))
# Write-ahead log of run triggers, keyed by table id, written BEFORE the click
# so a worker killed mid-trigger still leaves proof the run happened. Empty
# WORK EMAIL cells are NOT proof a table has not run: results land after the
# trigger, and trusting the fill count re-ran Analytica China's waterfall
# (people_email.py). This is the double-charge guard, kept in its own file so a
# state-file rewrite cannot erase it.
RUNS = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_PEOPLE_EMAIL_RUNS", "ps_people_email_runs.json"))
FILL_SCRIPT = os.path.join(SCRIPT_DIR, "check_column_fill.py")

# Labels that mean the run definitely did NOT happen — safe to retry. Anything
# else (a real "Run N rows" label, or the ambiguous "triggering" written just
# before the click) counts as run and must never be repeated.
RETRYABLE = ("run_not_triggered", "no_table", "not_applied", "None")

TABLE = "People"
COLS_SCRIPT = os.path.join(SCRIPT_DIR, "check_table_columns.py")

# Proof the template is already applied, so a re-run never double-applies (that
# is how duplicate column sets get created — see people_email.py on Analytica
# China). Presence of EITHER column counts.
MARKER_CANDIDATES = ("WORK EMAIL", "Validate Email")

# --- the Configure panel for THIS template -----------------------------------
# Picker geometry is inherited from apply_people_waterfall (recon TechBio UK,
# 2026-07-28): group rows sit at x~1309, children at x~1323, nested PROPERTIES
# at x~1359 — the last overlapping the grid showing through beside the panel, so
# leaf steps use a wider window and rely on exact text.
GRP_X = apw.GRP_X
LEAF_X = apw.LEAF_X

# The panel's real field labels, from recon_enrich_email_panel.py on Cleanroom
# Technology (2026-08-02). The user's mapping keys are the template's input
# names; the PANEL labels are the short forms, which is what the chip reader
# matches on:
#
#   user's "Full Name"          -> panel "Full Name"          (prefilled)
#   user's "Company Domain"     -> panel "Domain"             (needs filling)
#   user's "Company Name"       -> panel "Name"               (needs filling)
#   user's "Social Profile URL" -> panel "LinkedIn Profile"   (prefilled)
#
# Labels sit at x~1270 with their value box ~35px below at x~1279 (empty) or
# x~1323 (filled) — inside the bands apply_people_waterfall._BOX already uses.
#
# No `filter` on the Company Table Data fields: filtering by "Name"/"Domain"
# reports "No properties" because the search does not descend into an
# unexpanded column's schema (learned on the Waterfall template).
MAPPING = {
    "Domain": {
        "steps": [("Company Table Data",) + GRP_X,
                  ("Domain",) + LEAF_X],
    },
    "Name": {
        "steps": [("Company Table Data",) + GRP_X,
                  ("Name",) + LEAF_X],
    },
    # The two inputs the user's mapping did not name, added on their instruction
    # (2026-08-02) to point at the same-named existing columns. Both turned out
    # to be nested under the "People - Supabase" enrichment group rather than
    # flat columns, so each needs a filter to surface the group first — the
    # unfiltered list is clipped by the overlay and does not show it.
    #
    # records: People - Supabase > Lookup in Audiences > records
    #   (siblings: "totalRecords" and an "Insert all 2 properties" shortcut —
    #    do NOT match on substring alone, though "records" is safe against
    #    "totalRecords" because the check is case-sensitive.)
    "records": {
        "filter": "records",
        "steps": [("People - Supabase",) + GRP_X,
                  ("Lookup in Audiences",) + GRP_X,
                  ("records",) + LEAF_X],
    },
    # Is New: DISAMBIGUATED. "Is New" exists under BOTH "Company Table Data"
    # (the company ledger's flag, about the account) and "People - Supabase"
    # (the people ledger's flag, about this person). This is a People table
    # feeding a per-person email waterfall, and the table's own top-level
    # "Is New" column is the output of the People - Supabase pass, so the
    # person-level one is the match. Picking the company-level flag would gate
    # the waterfall on the wrong population.
    "Is New": {
        "filter": "Is New",
        "steps": [("People - Supabase",) + GRP_X,
                  ("Is New",) + LEAF_X],
    },
    # Prefilled by exact name match against this table's own columns; kept here
    # so a table that somehow arrives unmapped can still be filled rather than
    # silently aborting.
    "Full Name": {
        "filter": "Full Name",
        "steps": [("Full Name",) + GRP_X],
    },
    "LinkedIn Profile": {
        "filter": "LinkedIn",
        "steps": [("LinkedIn Profile",) + GRP_X],
    },
}

# What every field must read once configured (substring match). All six inputs
# are covered, so apply_people_template's abort-if-incomplete gate protects the
# whole panel — nothing saves half-mapped.
EXPECTED = {
    "Full Name": "Full Name",
    "Domain": "Domain",
    "Name": "Name",
    "LinkedIn Profile": "LinkedIn Profile",
    "records": "records",
    "Is New": "Is New",
}

# Every input is now mapped; kept as an empty tuple so unspecified_chips() stays
# a no-op rather than being deleted — a future template revision that adds an
# input can list it here and have its state reported instead of ignored.
UNSPECIFIED_FIELDS = ()

# Legitimate chip segments; anything else in the band is grid content showing
# through beside the panel — company names from the grid land in the same x
# window (see apply_email_template for why this matters).
CHIP_VOCAB = ["Full Name", "Company Domain", "Company Name",
              "Social Profile URL", "LinkedIn Profile", "Company Table Data",
              "Domain", "Name", "records", "totalRecords", "Is New",
              "People - Supabase", "Lookup row", "Lookup in Audiences",
              "Google Sheet - Lookup & Send Data", "Add row", "success",
              "Job Title", "Company", "First Name", "Last Name"]

# Rebind apply_people_waterfall's module-level config to this template. Its
# _fill/chips/apply_people_template read these as globals, so overriding them
# retargets the proven code without duplicating it.
apw.MAPPING = MAPPING
apw.EXPECTED = EXPECTED
apw.CHIP_VOCAB = CHIP_VOCAB
apw.MARKER = "WORK EMAIL"


def unspecified_chips(page):
    """Read the two inputs the user's mapping does not cover, so their state is
    always reported rather than silently ignored."""
    out = {}
    for label in UNSPECIFIED_FIELDS:
        try:
            info = page.evaluate(apw._BOX, [label, CHIP_VOCAB])
        except Exception:
            info = None
        out[label] = None if not info else (
            "<empty>" if info.get("empty") else info.get("chip"))
    return out


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


def already_applied(page, entry, table_id, say):
    """True if the template is already on this table.

    Checked in Clay's API first, then the UI: the CLI lags for columns created
    moments ago, and trusting it alone re-applied the template to Analytica
    China and produced a duplicate column set (people_email.py). Presence in
    EITHER source counts as applied. Returns None if neither could be read —
    the caller must then refuse to touch the table.
    """
    cols = table_columns(table_id, say)
    if not cols:
        say(f"  !! cannot read columns for {TABLE} — refusing to touch it")
        return None
    hit = [c for c in MARKER_CANDIDATES if c in cols]
    say(f"  {TABLE}: {len(cols)} columns, marker present={hit or False} (CLI)")
    if hit:
        return True
    try:
        clay_ui.open_workbook_by_id(page, entry["workbook_id"])
        if not colcfg.table_exists(page, TABLE):
            say(f"  !! no {TABLE!r} table in this workbook")
            return None
        colcfg.focus_table_maybe_empty(page, TABLE)
        page.wait_for_timeout(1200)
        for m in MARKER_CANDIDATES:
            if panel.find_header_scrolling(page, m):
                say(f"  {TABLE}: {m!r} visible in the UI — treating as already "
                    f"applied (CLI was stale)")
                return True
    except Exception as e:
        say(f"  !! UI presence check failed: {str(e)[:80]} — refusing to apply")
        return None
    return False


def _load_runs():
    # A corrupt run log must abort, not read as "nothing ever ran" — state_io
    # fails loud precisely so a truncated file cannot authorise a re-charge.
    return state_io.load_json(RUNS)


def already_triggered(table_id):
    rec = _load_runs().get(table_id)
    if not rec:
        return False
    return str(rec.get("label") or "") not in RETRYABLE


def record_trigger(table_id, workbook, label=None):
    runs = _load_runs()
    rec = runs.get(table_id, {})
    rec.update({"workbook": workbook, "table": TABLE,
                "triggered_at": datetime.datetime.now().isoformat(
                    timespec="seconds")})
    if label:
        rec["label"] = label
    runs[table_id] = rec
    state_io.save_json(RUNS, runs)


def work_email_fill(table_id, say):
    """(filled, sampled) for WORK EMAIL, or (None, None) if unknown."""
    try:
        parts = _wsl(FILL_SCRIPT, table_id, "WORK EMAIL").split()
        return int(parts[0]), int(parts[1])
    except Exception as e:
        say(f"    !! fill check failed: {str(e)[:70]}")
        return None, None


def _record(name, payload):
    st = state_io.load_json(STATE)
    rec = st.get(name, {})
    rec.update(payload)
    rec["at"] = datetime.datetime.now().isoformat(timespec="seconds")
    st[name] = rec
    state_io.save_json(STATE, st)


def do_workbook(page, name, entry, say, recon=False, dry_run=False,
                run_after=False):
    table_id = entry["table_id"]
    result = {"workbook": name, "table": TABLE, "table_id": table_id}

    if recon:
        clay_ui.open_workbook_by_id(page, entry["workbook_id"])
        if not colcfg.table_exists(page, TABLE):
            say(f"SKIP {name}: no {TABLE!r} table")
            result["status"] = "no_table"
            return result
        cols = table_columns(table_id, say)
        say(f"  columns ({len(cols)}): {cols}")
        colcfg.focus_table_maybe_empty(page, TABLE)
        page.wait_for_timeout(1000)
        # Open the panel here rather than via apply_people_template(recon=True)
        # so both the mapped and the unspecified fields can be read before the
        # panel is dismissed.
        apw._open_template_retry(page)
        say(f"  template: {apw.TEMPLATE_USED!r}")
        page.wait_for_timeout(1500)
        mapped, extra = apw.chips(page), unspecified_chips(page)
        say(f"  mapped fields:      {mapped}")
        say(f"  unspecified fields: {extra}")
        page.keyboard.press("Escape")
        result.update({"status": "recon", "fields": mapped,
                       "unspecified": extra, "template": apw.TEMPLATE_USED,
                       "columns": cols})
        return result

    applied = already_applied(page, entry, table_id, say)
    if applied is None:
        result["status"] = "presence_check_failed"
        return result

    if applied:
        say(f"  {TABLE}: template already applied — not re-applying")
        result["apply"] = "already"
    else:
        r = apw.apply_people_template(
            page, {"workbook_id": entry["workbook_id"], "workbook_name": name},
            TABLE, dry_run, say, run_after=False)
        result["apply"] = r.get("status")
        result["fields"] = r.get("fields")
        result["template"] = r.get("template")
        if dry_run:
            result["status"] = "dryrun"
            return result
        if r.get("status") != "ok":
            say(f"  SKIP {name}: apply returned {r.get('status')}")
            result["status"] = "apply_failed"
            _record(name, result)
            return result

    # Validate Email: Email Address -> WORK EMAIL, run condition !!{{WORK EMAIL}},
    # Auto-update OFF. Done AFTER the apply and BEFORE any run so nothing is
    # charged while the condition still points at a deleted column.
    try:
        v = cve.configure(page, {"workbook_id": entry["workbook_id"],
                                 "workbook_name": name},
                          say, run_after=False, table=TABLE)
        result["validate"] = v.get("status")
        result["gate_ok"] = (v.get("verify") or {}).get("ok")
        result["email_input"] = v.get("email_input")
    except Exception as e:
        say(f"  !! validate config failed: {str(e)[:140]}")
        result["validate"] = "error"
        result["status"] = "validate_failed"
        _record(name, result)
        return result

    if result.get("validate") != "ok":
        result["status"] = "validate_failed"
        _record(name, result)
        return result

    if not run_after:
        result["status"] = "configured"
        _record(name, result)
        return result

    # --- paid step, explicit opt-in only -------------------------------------
    # Two independent guards, checked in this order because they fail
    # differently: the write-ahead log survives a killed worker, a fill count
    # does not (results arrive after the trigger, so a fresh run reads as 0).
    if already_triggered(table_id):
        prev = _load_runs()[table_id]
        say(f"  {TABLE}: run already triggered {prev.get('triggered_at')} "
            f"({prev.get('label')}) — NOT running again")
        result["status"] = "ok"
        result["ran"] = "skipped_already_triggered"
        _record(name, result)
        return result

    filled, sampled = work_email_fill(table_id, say)
    if filled is None:
        say(f"  {TABLE}: fill unknown — refusing to run")
        result["status"] = "configured"
        _record(name, result)
        return result
    say(f"  {TABLE}: WORK EMAIL {filled}/{sampled}")
    if filled:
        say(f"  {TABLE}: already has emails — not running again")
        record_trigger(table_id, name, label="pre-existing emails")
        result["status"] = "ok"
        result["ran"] = "skipped_already_filled"
        _record(name, result)
        return result

    import run_people_table                        # noqa: E402  (lazy: paid path)
    record_trigger(table_id, name, label="triggering")
    rr = run_people_table.run_table(
        page, {"workbook_id": entry["workbook_id"], "workbook_name": name},
        TABLE, say)
    record_trigger(table_id, name, label=(rr.get("ran") or rr.get("status")))
    result["ran"] = rr.get("ran")
    result["status"] = "ok" if rr.get("status") == "running" else rr.get("status")
    _record(name, result)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_name")
    ap.add_argument("--recon", action="store_true",
                    help="dump columns + the Configure panel, change nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="map every field but do not save")
    ap.add_argument("--run", action="store_true",
                    help="trigger the WORK EMAIL waterfall (SPENDS CREDITS)")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    audit = json.load(open(AUDIT, encoding="utf-8"))
    if a.workbook_name not in audit:
        raise SystemExit(
            f"{a.workbook_name!r} is not in {os.path.basename(AUDIT)} — "
            f"refusing to touch a workbook outside the scoped manifest.\n"
            f"In scope: {sorted(audit)}")
    entry = audit[a.workbook_name]

    with browser_session.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        say(f"=== {a.workbook_name} / {TABLE} "
            f"(template {os.environ['CLAY_PEOPLE_TEMPLATE']!r}) ===")
        out = do_workbook(page, a.workbook_name, entry, say, recon=a.recon,
                          dry_run=a.dry_run, run_after=a.run)
    print("\nRESULT:", json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
