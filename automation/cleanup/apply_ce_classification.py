"""Create the "Highways Registrar" Claygent on the Highways / Competitive
Events tables — `exhibitors_normalized` first, then `sponsors_normalized`.

A sibling of apply_highways_cpj.py (same manifests, same `--table` switch, same
state-file namespacing) but it builds a column from scratch rather than applying
a saved template. The column recipe is the one build_automation/build_workbook.py
`step_registrar` used for the Labs equivalent, re-pointed at the Highways prompt
and the Competitive Events column names.

CONFIGURE ONLY. A Claygent is a paid provider priced per row, so per CLAUDE.md
it is created and fully configured and **never triggered**. There is deliberately
no --run flag here, exactly as in apply_highways_cpj.py: the ask was to apply the
classifier, not to run it. Auto-update is left OFF, so the saved column sits
dormant at 0%.

The column, per the user (2026-09-12):

    name             Highways Registrar
    model            GPT 4.1 Mini
    prompt           ce_classification_prompt.PROMPT (verbatim)
    inputs           Description -> `Resolved Description`     (optional)
                     Primary Offerings -> `Primary Offerings`  (optional)
                     Company Domain -> `Domain`                (mandatory)
    outputs          Side, Classification
    run condition    !!{{Domain}}   ("only if Domain is not empty")
    auto-update      OFF

Each table therefore gets TWO columns, in order: the free `Resolved Description`
formula, then the Claygent that reads it. The formula is created AND saved —
formula columns cost nothing and CLAUDE.md's run-now rule covers them. It exists
because neither raw description column is usable alone: measured 2026-09-12,
`Description` is 0/13 on National Diesel Dirt & Turf Expo and 60/60 on
Geotechnica, and `Description (2)` is the exact inverse. See
ce_classification_prompt.RESOLVED_DESCRIPTION_FORMULA.

`Domain` (not `Official Domain`) is the user's explicit choice, made knowing
`Domain` is ~77-93% filled against `Official Domain`'s ~100%. The rows `Domain`
misses go unclassified by design; do not switch the binding.

THREE THINGS THAT BITE, all guarded below:

 1. **The "/" picker fuzzy-matches and commits on Enter.** These tables carry
    `Description` AND `Description (2)`, and `Domain` AND `Official Domain` AND
    `Verified Domain`. Typing "Domain" can silently bind `Official Domain` — no
    error, just a column classified off the wrong field and a run condition
    gating on the wrong coverage. `verify_bindings()` reads the committed chips
    back out of the editor and ABORTS BEFORE SAVE if an ambiguous spelling won.
    The same check runs again on the run-condition editor, which uses the same
    picker.

 2. **colcfg.rename_last_column is not safe on these tables.** Its guard is
    "the rightmost header is not in KNOWN_HEADERS", and KNOWN_HEADERS is the
    Labs/Interphex column set — it does not contain `Is New`, `HTTP API`,
    `Primary Offerings` or `Subindustry`. If the new column has not rendered
    yet, the rightmost header is `Is New`, and the fallback rename would rename
    the ledger gate column to "Highways Registrar". So: the column is named in
    the PANEL (rename_panel_title) before it is ever saved, and the live column
    list is unioned into KNOWN_HEADERS before any fallback rename can run.

 3. **`Side` and `Classification` do not appear in the grid until the Claygent
    runs.** Claygent multi-output fields create no child columns while dormant
    (column_config.py header). An apparently output-less column after a correct
    save is expected, not a failure — do not "fix" it by re-applying.

Re-entry safety is the repo's usual dual signal: the state file, AND a live
column listing asked of the Clay CLI (aps.marker_present, which fails CLOSED as
"already applied"). A duplicate Claygent is a second paid column on the table.

    python apply_ce_classification.py --list
    python apply_ce_classification.py --only "Geotechnica" --headed --recon
    python apply_ce_classification.py --only "National Diesel Dirt & Turf Expo" --headed
    python apply_ce_classification.py --limit 1
    python apply_ce_classification.py --table sponsors_normalized

Needs a real Chrome on CDP (CLAY_USE_CDP=1) — see CLAUDE.md "Environment
gotchas". Never calls browser.close() on a CDP connection.
"""

import argparse
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import clay_ui                          # noqa: E402
import browser_session                  # noqa: E402
import column_config as colcfg          # noqa: E402
import formula_columns                  # noqa: E402
import clay_panel                      # noqa: E402
import apply_ps_supabase as aps         # noqa: E402
import state_io                         # noqa: E402
import ce_classification_prompt as P    # noqa: E402

SHOTS_DIR = os.path.join(SCRIPT_DIR, "ce_classification_shots")

# Presence of this column is proof the pass already ran on a table. Checked
# against the live Clay column listing, never the manifest snapshot, because a
# duplicate means a second paid Claygent.
MARKERS = (P.COLUMN_NAME,)

VerificationError = browser_session.VerificationError
say = aps.say


def manifest_path(table):
    return os.path.join(SCRIPT_DIR,
                        f"highways_ce_{table.split('_')[0]}_manifest.json")


def state_path(table):
    return os.path.join(SCRIPT_DIR,
                        f"highways_classification_state_{table.split('_')[0]}.json")


def already_applied(rec):
    """Manifest-snapshot pre-filter for --list only. The authoritative check is
    the live one in one(); a stale 'not applied' here can never duplicate."""
    return P.COLUMN_NAME in (rec.get("columns") or [])


def _binding_after(txt, label):
    """The text the picker actually committed after `label`, up to end of line.

    fill_prompt types the label then inserts a column chip; the chip renders as
    its column name in inner_text(). So the remainder of that line IS the bound
    column, which is what makes an ambiguous match detectable.

    Scanned from the END: fill_prompt appends the input block after the whole
    prompt body, and the body discusses "Description" and "Company Domain" in
    prose. A forward scan could match a sentence instead of the binding.
    """
    want = label.strip()
    for line in reversed(txt.splitlines()):
        stripped = line.strip()
        if stripped.startswith(want):
            return stripped[len(want):].strip(" ​﻿{}")
    return None


def verify_bindings(txt):
    """Abort unless every input bound the column we asked for.

    Not a nicety: `Official Domain` is populated on ~100% of these rows and
    `Domain` on ~91%, so a mis-bind produces a plausible, fuller, WRONG column
    and nothing downstream would flag it.
    """
    for label, column in P.INPUTS:
        got = _binding_after(txt, label)
        if got is None:
            raise VerificationError(f"input line {label!r} missing from prompt")
        if not got:
            raise VerificationError(
                f"input {label!r} has no column bound (picker did not commit)")
        for bad in P.AMBIGUOUS.get(column, ()):
            if bad.lower() in got.lower():
                raise VerificationError(
                    f"input {label!r} bound {got!r}, not {column!r} "
                    f"(ambiguous match on {bad!r}) — NOT saving")
        if column.lower() not in got.lower():
            raise VerificationError(
                f"input {label!r} bound {got!r}, expected {column!r} — NOT saving")
        say(f"    bound {label.strip()} -> {got!r}")


def verify_run_condition(page):
    """Same picker, same ambiguity — read the condition back before saving."""
    js = """() => {
      const out = [];
      document.querySelectorAll('[contenteditable="true"]').forEach((el, i) => {
        const r = el.getBoundingClientRect();
        if (r.x > 1240 && r.width > 150 && r.y > 150 && r.y < 935)
          out.push({i, y: Math.round(r.y)});
      });
      return out;
    }"""
    eds = page.evaluate(js)
    if not eds:
        raise VerificationError("run condition editor not found for verification")
    cond = page.locator('[contenteditable="true"]').nth(eds[-1]["i"]).inner_text()
    cond = cond.replace("​", "").replace("﻿", "").strip()
    say(f"    run condition reads {cond!r}")
    for bad in P.AMBIGUOUS.get(P.RUN_CONDITION_COLUMN, ()):
        if bad.lower() in cond.lower():
            raise VerificationError(
                f"run condition bound {bad!r}, not {P.RUN_CONDITION_COLUMN!r} "
                f"— NOT saving")
    if P.RUN_CONDITION_COLUMN.lower() not in cond.lower():
        raise VerificationError(f"run condition missing {P.RUN_CONDITION_COLUMN!r}")
    if not cond.startswith(P.RUN_CONDITION_PREFIX):
        raise VerificationError(
            f"run condition {cond!r} does not start with "
            f"{P.RUN_CONDITION_PREFIX!r} — would run on empty domains")
    return cond


def ensure_resolved_description(page, cols, shots_dir, slug):
    """Create the free `Resolved Description` formula if the table lacks it.

    Written through the HANDWRITTEN path, not AI generation: the expression is
    fixed and known-good (it is build_workbook.py's, verbatim), and
    build_formula_handwritten inserts literal {{Column}} syntax with
    insert_text — no "/" picker, so no fuzzy-match risk on these two bindings.

    Free: a formula column costs 0 credits, which is why this one is created
    AND saved rather than left dormant like the Claygent.

    Returns True when it created the column, False when it was already there.
    """
    if P.RESOLVED_DESCRIPTION in cols:
        say(f"    {P.RESOLVED_DESCRIPTION!r} already present")
        return False

    # A previous run may have SAVED the formula and then failed to rename it —
    # Clay's auto-name is "Formula". Creating another one here would leave two
    # identical formula columns and make the table's state ambiguous, so stop
    # and make a human decide. (Renaming is currently broken against the new
    # Clay UI: formula_columns' header geometry no longer matches, so the pass
    # cannot fix this itself.)
    strays = [c for c in cols if c == "Formula" or c.startswith("Formula ")]
    if strays:
        raise VerificationError(
            f"found unrenamed formula column(s) {strays} on this table — a "
            f"previous run saved the formula but could not rename it. Rename "
            f"it to {P.RESOLVED_DESCRIPTION!r} by hand (or delete it), then "
            f"re-run. Refusing to create a second one.")
    missing = [c for c in P.RESOLVED_DESCRIPTION_INPUTS if c not in cols]
    if missing:
        raise VerificationError(
            f"cannot build {P.RESOLVED_DESCRIPTION!r}: table lacks {missing}")

    say(f"    creating {P.RESOLVED_DESCRIPTION!r} formula")
    text, preview = formula_columns.build_formula_handwritten(
        page, P.RESOLVED_DESCRIPTION_FORMULA,
        os.path.join(shots_dir, f"{slug}_resolved_desc"))
    if not text.strip():
        formula_columns.cancel_panel(page)
        raise VerificationError("formula editor returned empty text")
    for token in P.RESOLVED_DESCRIPTION_INPUTS:
        if token not in text:
            formula_columns.cancel_panel(page)
            raise VerificationError(
                f"formula text missing {token!r}: {text[:160]!r}")
    nonempty = [p for p in (preview or []) if p and p != "None"]
    say(f"    formula preview: {len(nonempty)}/{len(preview or [])} non-empty")
    formula_columns.save_column(page, os.path.join(shots_dir, f"{slug}_resolved_desc"))
    colcfg.rename_last_column(page, P.RESOLVED_DESCRIPTION, timeout_s=60)
    say(f"    {P.RESOLVED_DESCRIPTION!r} saved")
    return True


def guard_known_headers(columns):
    """Make every real column on THIS table un-renameable by the fallback.

    colcfg.KNOWN_HEADERS is the Labs set; on a CE table `Is New` is absent from
    it, and rename_last_column renames "the rightmost header not in
    KNOWN_HEADERS". Unioning the live list in means the worst case is a failed
    rename, never a renamed ledger column.
    """
    before = len(colcfg.KNOWN_HEADERS)
    colcfg.KNOWN_HEADERS |= set(columns)
    say(f"    KNOWN_HEADERS guarded: {before} -> {len(colcfg.KNOWN_HEADERS)}")


def one(page, name, rec, args):
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    clay_ui.open_workbook_by_id(page, rec["workbook_id"])
    page.wait_for_timeout(8000)
    colcfg.focus_table_maybe_empty(page, rec.get("table_name"))
    page.wait_for_timeout(2500)

    # LIVE re-check, never the manifest snapshot. Fails closed as "applied".
    aps.MARKER_COLUMNS = MARKERS
    applied, cols = aps.marker_present(rec["workbook_id"], rec.get("table_id"))

    # "Fails closed" and "is genuinely done" are NOT the same answer, and the
    # difference has to survive into the state file. marker_present returns
    # (True, ["<unknown>"]) when it could not read Clay at all — skipping this
    # table is right, but RECORDING it as applied is not: the next run trusts
    # the state file, skips again, and the pass reports a fleet of successes
    # having done nothing. That happened here on 2026-09-12 (a stale clay
    # binary made every listing empty — see clay_bin.py), and the bogus entry
    # had to be deleted by hand. Persist only a positively-proven column.
    inconclusive = not cols or any(c.startswith("<") and c.endswith(">")
                                   for c in cols)
    if applied and inconclusive:
        raise VerificationError(
            f"column pre-check could not read this table ({cols}) — refusing "
            f"to record a result either way. Fix the CLI read path first "
            f"(try: wsl python3 check_table_columns.py {rec.get('table_id')})")
    if applied:
        say(f"  {P.COLUMN_NAME!r} already present — skipping")
        return {"status": "applied", "saved": "already_applied",
                "workbook_id": rec["workbook_id"], "columns": cols}
    guard_known_headers(cols)

    # Required inputs must exist before we spend a panel on this table.
    # `Resolved Description` is excluded: this pass creates it a few lines down.
    missing = [c for _, c in P.INPUTS
               if c not in cols and c != P.RESOLVED_DESCRIPTION]
    missing += [c for c in P.RESOLVED_DESCRIPTION_INPUTS if c not in cols]
    if P.RUN_CONDITION_COLUMN not in cols:
        missing.append(P.RUN_CONDITION_COLUMN)
    if missing:
        raise VerificationError(
            f"table is missing input columns {sorted(set(missing))} — "
            f"has {cols}")

    if args.recon:
        # Read-only probe: open the Claygent panel, dump it, back out. Runs
        # BEFORE the formula step so --recon never creates a column.
        clay_panel.open_claygent(page, args.shots_dir, slug)
        aps.dump_panel(page, "Claygent panel (recon)")
        shot = aps.bps.shot(page, args.shots_dir, f"{slug}_recon")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1200)
        say(f"  recon only — {P.RESOLVED_DESCRIPTION!r} "
            f"{'present' if P.RESOLVED_DESCRIPTION in cols else 'NOT yet created'}")
        return {"status": "recon", "shot": shot, "columns": cols}

    # Free formula first — the Claygent's Description input binds to it.
    made_formula = ensure_resolved_description(page, cols, args.shots_dir, slug)
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)

    clay_panel.open_claygent(page, args.shots_dir, slug)
    colcfg.set_model_gpt41mini(page)
    say(f"    model set: {P.MODEL}")

    txt = colcfg.fill_prompt(page, P.PROMPT, P.INPUTS)
    for marker in P.PROMPT_MARKERS:
        if marker not in txt:
            raise VerificationError(f"prompt missing marker {marker!r}")
    verify_bindings(txt)
    aps.bps.shot(page, args.shots_dir, f"{slug}_prompt")

    colcfg.rename_panel_title(page, P.COLUMN_NAME)
    colcfg.auto_update_off(page)          # collapses Configuration, reveals outputs
    if not colcfg.rename_response_output(page, P.OUTPUTS[0]):
        raise VerificationError("response output not found")
    colcfg.add_output(page, P.OUTPUTS[1])
    colcfg.add_run_condition(page, P.RUN_CONDITION_PREFIX, P.RUN_CONDITION_COLUMN)
    verify_run_condition(page)
    shot_before = aps.bps.shot(page, args.shots_dir, f"{slug}_before_save")

    if args.configure_only:
        say("  --configure-only: panel built, NOT saved")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1200)
        return {"status": "configured_unsaved", "shot": shot_before,
                "resolved_description_created": made_formula}

    colcfg.save_plain(page)               # auto-run OFF -> saves dormant
    try:
        colcfg.rename_last_column(page, P.COLUMN_NAME, timeout_s=45)
    except Exception as e:
        say(f"  WARN post-save rename flaked ({str(e)[:70]}); panel title "
            f"should already carry the name — verifying live")

    # POST-SAVE PROOF, from Clay's own listing rather than the DOM.
    aps.MARKER_COLUMNS = MARKERS
    ok, cols_after = aps.marker_present(rec["workbook_id"], rec.get("table_id"))
    if not ok:
        raise VerificationError(
            f"{P.COLUMN_NAME!r} not in live columns after save: {cols_after}")
    if P.RESOLVED_DESCRIPTION not in cols_after:
        raise VerificationError(
            f"{P.RESOLVED_DESCRIPTION!r} not in live columns after save — the "
            f"Claygent's Description input is bound to a column that does not "
            f"exist: {cols_after}")
    shot = aps.bps.shot(page, args.shots_dir, f"{slug}_applied")
    say(f"  saved dormant; live columns now end {cols_after[-3:]}")
    return {"status": "applied", "saved": "save_plain", "shot": shot,
            "workbook_id": rec["workbook_id"], "columns": cols_after,
            "resolved_description_created": made_formula}


def main():
    # Clay's panel text is not cp1252-safe (card labels use U+2219, tables carry
    # accented company names). Without this a pure logging call raises
    # UnicodeEncodeError mid-pass and reads like a selector failure.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", default="exhibitors_normalized",
                    choices=("exhibitors_normalized", "sponsors_normalized"))
    ap.add_argument("--list", action="store_true", help="scope only, no browser")
    ap.add_argument("--recon", action="store_true",
                    help="open the Claygent panel and dump it; change nothing")
    ap.add_argument("--configure-only", action="store_true",
                    help="build the panel but do not save it")
    ap.add_argument("--only", action="append", default=[],
                    help="event name (repeatable)")
    ap.add_argument("--after", help="resume past this event name")
    ap.add_argument("--limit", type=int, help="cap the batch")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()

    manifest = state_io.load_json(manifest_path(args.table))
    if not manifest:
        raise SystemExit(f"no manifest at {manifest_path(args.table)}; "
                         f"run build_highways_ce_manifest.py in WSL first")
    state = state_io.load_json(state_path(args.table))

    # Smallest table first — the repo convention. A selector regression then
    # surfaces on a 13-row table, not on a 637-row one.
    names = sorted(manifest, key=lambda n: (manifest[n].get("rows") or 0, n))
    if args.only:
        wanted = {o.lower() for o in args.only}
        names = [n for n in names if n.lower() in wanted]
        unknown = wanted - {n.lower() for n in names}
        if unknown:
            raise SystemExit(f"--only: no such event(s) in manifest: {sorted(unknown)}")
    if args.after:
        if args.after not in names:
            raise SystemExit(f"--after: {args.after!r} not in scope")
        names = names[names.index(args.after) + 1:]

    pending = [n for n in names
               if state.get(n, {}).get("status") != "applied"
               and not already_applied(manifest[n])]
    if args.limit:
        pending = pending[:args.limit]

    if args.list:
        print(f"{args.table}: {len(manifest)} tables, {len(pending)} pending")
        for n in names:
            st = state.get(n, {}).get("status") or (
                "applied(manifest)" if already_applied(manifest[n]) else "-")
            mark = ">" if n in pending else " "
            print(f" {mark} {manifest[n].get('rows', '?'):>5}  {st:<18} {n}")
        return

    if not pending:
        print("nothing pending")
        return

    os.makedirs(args.shots_dir, exist_ok=True)
    print(f"{len(pending)} table(s): {pending}")

    with browser_session.clay_page(headless=not args.headed) as page:
        for n in pending:
            say(f"\n=== {n} ({manifest[n].get('rows')} rows) ===")
            try:
                res = one(page, n, manifest[n], args)
            except Exception as e:
                say(f"  FAILED: {e}")
                traceback.print_exc()
                # Failures are NOT written as applied — re-running is safe, and
                # the live pre-check is what stops a duplicate column.
                state[n] = {"status": "failed", "error": str(e)[:300]}
                state_io.save_json(state_path(args.table), state)
                break
            if res.get("status") == "applied":
                state[n] = res
                state_io.save_json(state_path(args.table), state)
            say(f"result: {res.get('status')}")


if __name__ == "__main__":
    main()
