"""(Copied 21 Nov 2026 from scrapers_submarine/_run_v2/other_sources/delivery for the Rail Other Sources review copies.)
Import the remaining Rail Other Sources review workbooks one per invocation (delivery.md: low-memory rules).

Free (CSV import). Before each workbook: wait while free memory < 0.9 GB; then run the importer with --limit 1.
Stops when the state has every plan table done, or after 3 invocations in a row that confirm nothing new.
    python import_loop.py
"""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = r"C:\Users\qasim\terrapinn\clay-icp-pipeline\automation\cleanup"
PLAN, STATE = os.path.join(HERE, "plan.json"), os.path.join(HERE, "import_state.json")
FOLDER = os.environ["RAIL_OS_FOLDER_ID"]  # Rail > Other Sources review copies (user, 21 Nov 2026)


def free_gb():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"], capture_output=True, text=True)
    try:
        return int(out.stdout.strip()) / 1024 / 1024
    except ValueError:
        return 9.9


def done():
    if not os.path.exists(STATE):
        return 0
    return sum(1 for v in json.load(open(STATE, encoding="utf-8")).values() if v.get("status") == "done")


def main():
    total = len(json.load(open(PLAN, encoding="utf-8")))
    stuck = 0
    env = dict(os.environ, CLAY_USE_CDP="1", PYTHONIOENCODING="utf-8")
    while done() < total and stuck < 3:
        while free_gb() < 0.9:
            print(f"  free memory {free_gb():.2f} GB - waiting", flush=True)
            time.sleep(60)
        before = done()
        subprocess.run([sys.executable, "import_ce_delivery_tables.py", "--plan", PLAN, "--state", STATE,
                        "--folder-id", FOLDER, "--apply", "--limit", "1"], cwd=REPO, env=env)
        after = done()
        stuck = stuck + 1 if after == before else 0
        print(f"== tables confirmed {after}/{total}", flush=True)
        if stuck == 1:
            # 18 Nov 2026: a click retry can leave an EMPTY table + a "(2)" copy with the rows; the importer then skips
            # that workbook forever and the loop stops. User: "if something stops, keep resuming ... fix those". The
            # repair script deletes ONLY 0-row tables and numbered duplicates (never a table holding unique rows); the
            # next pass re-imports the table under its real name.
            print("  no progress - running repair_delivery_empties (0-row tables + numbered copies only)", flush=True)
            subprocess.run([sys.executable, "repair_delivery_empties.py", "--plan", PLAN, "--state", STATE,
                            "--folder-id", FOLDER, "--apply"], cwd=REPO, env=env)
    print("ALL DONE" if done() >= total else f"STOPPED: {done()}/{total} confirmed, no progress in 3 tries")


if __name__ == "__main__":
    main()
