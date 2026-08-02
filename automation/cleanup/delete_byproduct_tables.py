"""Clean one Competitive Events workbook: delete every pipeline-byproduct
table, keeping only the normalized source table(s).

Manifest-driven and fail-closed. A workbook is only touched when its expected
normalized keep-table(s) are actually present; otherwise it is aborted (never
emptied). Only tables that are BOTH in the manifest delete-list AND currently
present as tabs are deleted, and clay_ui.delete_table itself hard-refuses the
KEEP names — so the normalized tables can never be removed.

Not a CLI entry point on its own; delete_byproduct_tables_rollout.py drives it. But it can be
imported for a single-workbook validation run.
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import browser_session         # noqa: E402  (clay_page context manager)

KEEP = clay_ui.KEEP_NEVER_DELETE


def clean_workbook(page, entry, dry_run, say):
    """Clean a single workbook per its manifest `entry`.

    entry: {workbook_id, workbook_name, keep:[...], delete:[...]}
    say:   logging callback (str) -> None
    Returns a result dict describing what happened.
    """
    wid = entry["workbook_id"]
    name = entry["workbook_name"]
    keep = list(entry["keep"])
    delete = list(entry["delete"])

    clay_ui.open_workbook_by_id(page, wid)

    # Safety gate + hydration: require every expected normalized keep-tab to be
    # present (stable, with retries). This both confirms we're on the right
    # workbook and waits for the bottom tab bar to finish hydrating before we
    # judge any table present/absent — an eager snapshot on a half-hydrated bar
    # under-reports and would either wrongly abort or skip real tables.
    present_keep = [k for k in keep if clay_ui.tab_present_stable(page, k)]
    missing = set(keep) - set(present_keep)
    if missing:
        say(f"ABORT {name} [{wid}]: keep table(s) missing {sorted(missing)}; "
            f"visible tabs={clay_ui.list_table_tabs(page)}")
        return {"workbook_id": wid, "workbook_name": name,
                "status": "aborted", "reason": "missing_keep",
                "missing": sorted(missing)}

    if dry_run:
        # Tab bar is hydrated (keep-tab confirmed), so a single pass is accurate.
        present = [t for t in delete if clay_ui._find_tab_rect(page, t)]
        absent = [t for t in delete if t not in present]
        say(f"DRYRUN {name} [{wid}]: keep {sorted(present_keep)} | "
            f"would delete ({len(present)}) {present}"
            + (f" | already absent {absent}" if absent else ""))
        return {"workbook_id": wid, "workbook_name": name, "status": "dryrun",
                "to_delete": present, "already_gone": absent}

    say(f"CLEAN {name} [{wid}]: keep {sorted(present_keep)}; "
        f"attempting {len(delete)} delete-list tables")
    deleted, already_gone, failed = [], [], []
    # Attempt every manifest delete-list table; delete_table itself does the
    # robust present/absent check (retries through post-delete re-render), so we
    # never rely on a single snapshot.
    for t in delete:
        try:
            if clay_ui.delete_table(page, t):
                deleted.append(t)
                say(f"  - deleted {t!r}")
            else:
                already_gone.append(t)
                say(f"  - {t!r} already absent")
        except Exception as e:  # keep going; report per-table
            failed.append({"table": t, "error": str(e)[:200]})
            say(f"  ! FAILED to delete {t!r}: {str(e)[:160]}")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

    # Final verification against the manifest delete-list (bottom-bar tabs).
    remaining = sorted([t for t in delete if clay_ui._find_tab_rect(page, t)])
    keep_lost = sorted([k for k in keep if not clay_ui.tab_present_stable(page, k)])
    status = "ok" if (not failed and not remaining and not keep_lost) else "partial"
    if keep_lost:
        say(f"  !! WARNING keep table(s) no longer present: {keep_lost}")
    say(f"DONE {name}: status={status} deleted={len(deleted)} "
        f"remaining={remaining} failed={len(failed)}")
    return {"workbook_id": wid, "workbook_name": name, "status": status,
            "deleted": deleted, "failed": failed, "remaining": remaining,
            "keep_lost": keep_lost}


def _validate_single(workbook_id_or_name, dry_run=True, headed=True):
    """Convenience: clean/dry-run one workbook by id or name (validation)."""
    import json
    manifest = json.load(open(os.path.join(SCRIPT_DIR, "cleanup_manifest.json"),
                              encoding="utf-8"))
    entry = None
    for e in manifest["workbooks"]:
        if workbook_id_or_name in (e["workbook_id"], e["workbook_name"]):
            entry = e
            break
    if not entry:
        raise SystemExit(f"no manifest entry for {workbook_id_or_name!r}")
    with browser_session.clay_page(headless=not headed) as page:
        return clean_workbook(page, entry, dry_run, lambda m: print(m, flush=True))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Clean a single workbook (validation).")
    ap.add_argument("target", help="workbook id or exact name from the manifest")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default is dry run)")
    ap.add_argument("--headless", action="store_true", help="run headless")
    args = ap.parse_args()
    r = _validate_single(args.target, dry_run=not args.apply, headed=not args.headless)
    print("\nRESULT:", r)
