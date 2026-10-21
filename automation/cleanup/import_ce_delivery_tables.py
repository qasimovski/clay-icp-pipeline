"""Build a Katie handoff folder from a split plan: one workbook per unit.

`--delivery` selects which one. The file keeps its `_ce_` name because the CE
state and plan filenames are persisted and CLAUDE.md forbids renaming those;
the module serves both.

    --delivery ce   ce_event_tables/<event>/*.csv     39 workbooks, 144 tables
                      Exhibitors / Exhibitors Buyside People /
                      Exhibitors Sellside People / Sponsors /
                      Sponsors Buyside People / Sponsors Sellside People

    --delivery ps   ps_delivery_tables/<category>/*   23 workbooks,  45 tables
                      Companies / People
                      No buy/sell split: every company in sell-side P&S is Sell
                      Side by construction, which is why the classifier is not
                      run on that scope at all.

Nothing here is paid — CSV import and table creation cost no credits — and
there is no delete or edit operation anywhere in it.

A SEPARATE FOLDER, NOT THE WORKING ONE. These are delivery copies for review;
the live Competitive Events workbooks are untouched. This pass can only reach
the folder id it is given, exactly as import_highways.py can only reach its own.
The folder is made by hand — nothing in this repo creates folders — and it must
be the *Competitive Events* SUBFOLDER, not its parent.

VERIFICATION IS OVER THE CLI, NOT THE DOM. `clay_ui`'s post-import wait looks
for a `cell-r0-c0` test id and raises after 300s if it never appears. On the
first real run that raised on a table whose 489 rows had imported perfectly —
the data was fine, the selector was not (the column UI was rebuilt 2026-09).
CLAUDE.md is explicit: don't trust the Clay page's own status text, confirm with
the CLI. So the wait is neutralised for this pass and every table is confirmed
by asking Clay for its row count and comparing it to the CSV. A table that
imports but cannot be confirmed is left NOT done, so it is retried rather than
quietly counted.

TABLE BY TABLE, NOT WORKBOOK BY WORKBOOK. The first version created each
workbook with all six CSVs in one call and claimed that avoided a
half-populated workbook. It did the opposite: the call failed on table 1 of 6
and left exactly that. State is now per (event, table), so a failure costs one
table and a re-run resumes mid-workbook.

    python import_ce_delivery_tables.py                  # dry run — the plan
    python import_ce_delivery_tables.py --list           # what is in the folder
    python import_ce_delivery_tables.py --apply --only "Big 5 Construct Saudi"
    python import_ce_delivery_tables.py --apply --limit 3
"""

import argparse
import collections
import io
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import clay_ui           # noqa: E402
import browser_session   # noqa: E402
import state_io          # noqa: E402

ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
# One importer, several deliveries. The CE run hardened this against a dozen
# real failures — memory kills, a wedged browser, dropped driver connections,
# tab accumulation, a duplicate guard that never fired — and a second copy for
# P&S would start again from none of that.
DELIVERIES = {
    "ce": ("ce_event_tables_plan.json", "ce_delivery_tables_state.json",
           "highways_delivery_folder_id"),
    "ps": ("ps_delivery_tables_plan.json", "ps_delivery_tables_state.json",
           "highways_ps_delivery_folder_id"),
    "bs": ("bs_delivery_tables_plan.json", "bs_delivery_tables_state.json",
           "highways_bs_delivery_folder_id"),
    "os": ("os_delivery_tables_plan.json", "os_delivery_tables_state.json",
           "highways_os_delivery_folder_id"),
    # CE rebuilt after the scraper fit filter (split_ce_event_tables.py --fit),
    # into its own folder beside the September `ce` copies, which stay as they
    # are until the user retires them by hand.
    "ce_fit": ("ce_event_tables_fit_plan.json", "ce_fit_delivery_tables_state.json",
               "highways_ce_fit_delivery_folder_id"),
    # Other Sources re-run on workflow v7 (split_os_delivery_tables.py --v7).
    "os_v7": ("os_v7_delivery_tables_plan.json", "os_v7_delivery_tables_state.json",
              "highways_os_v7_delivery_folder_id"),
}
PLAN = STATE = FOLDER_KEY = None


def native_path(p):
    """Accept a plan written on either side of the win32/WSL split.

    `split_ce_event_tables.py` ran on Windows and wrote `C:\\...`;
    `split_ps_delivery_tables.py` ran in WSL and wrote `/mnt/c/...`. The
    importer is always the Windows Playwright process, and Chrome's file
    chooser cannot open a `/mnt/c` path — it fails as "file not found" at the
    first table, AFTER the workbook has been created. Normalise on load
    instead of asking every future splitter to remember which OS it is on.
    """
    if os.name == "nt" and p.startswith("/mnt/") and len(p) > 6:
        return os.path.normpath(f"{p[5].upper()}:{p[6:]}")
    return p


def use_delivery(name):
    global PLAN, STATE, FOLDER_KEY
    plan, state, key = DELIVERIES[name]
    PLAN = os.path.join(SCRIPT_DIR, plan)
    STATE = os.path.join(SCRIPT_DIR, state)
    FOLDER_KEY = key


use_delivery("ce")


def _id(key):
    v = browser_session.local_setting(key, "CLAY_" + key.upper())
    if not v:
        raise SystemExit(f"missing `{key}` in config/local.yaml — create the "
                         f"delivery folder by hand and pin the Competitive "
                         f"Events SUBFOLDER id (see config/local.yaml.example)")
    return v


def folder_url(fid):
    return (f"{clay_ui.CLAY_URL}/workspaces/"
            f"{_id('workspace_id')}/home/{fid}")


# A workbook row is /workbooks/<id>; a subfolder row is /home/<id>. Used to tell
# an empty folder from one that never hydrated, and to warn if the pinned id is
# the parent rather than the Competitive Events subfolder.
_ROWS_JS = r"""() => {
  const out = {folders: [], workbooks: []};
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    const name = (a.textContent || '').replace(/\s+/g, ' ').trim();
    if (!name) continue;
    let m = href.match(/\/workbooks\/([A-Za-z0-9_]+)/);
    if (m) { out.workbooks.push([m[1], name]); continue; }
    m = href.match(/\/home\/([A-Za-z0-9_]+)/);
    if (m) out.folders.push([m[1], name]);
  }
  return out;
}"""

# Chrome present on a rendered folder page whether or not it holds rows.
_FOLDER_CHROME = ("All files", "Recents", "Favorites", "Filters", "New")


def scan(page):
    return page.evaluate(_ROWS_JS)


def confirm_empty(page):
    """True only when the folder page RENDERED and genuinely holds nothing.

    `clay_ui.list_workbooks` treats an empty listing as a failed hydration —
    right for the working folder ("an empty listing is never real", ~90
    workbooks) and wrong for a delivery folder on its first run. Mistaking an
    unhydrated page for an empty one would create 39 duplicate workbooks, so
    require positive evidence the page drew before believing it.
    """
    try:
        body = page.evaluate("() => document.body.innerText") or ""
    except Exception:
        return False
    if sum(1 for c in _FOLDER_CHROME if c in body) < 2:
        return False
    try:
        return not (scan(page).get("workbooks") or [])
    except Exception:
        return False


def open_folder(page, fid):
    url = folder_url(fid)
    for _ in range(4):
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        if fid in page.url:
            return
    raise SystemExit(f"could not open the delivery folder ({page.url[:90]})")


def live_workbooks(page, fid):
    """{name: workbook_id} in the delivery folder. Note the direction: the
    caller looks workbooks up BY NAME, and `clay_ui.list_workbooks` returns
    {id: name} — testing an event name against that dict is always true, which
    silently disables the duplicate guard."""
    try:
        return {name: wid for wid, name in clay_ui.list_workbooks(page).items()}
    except Exception as e:
        if not confirm_empty(page):
            raise SystemExit(f"could not list the delivery folder ({e}); "
                             f"refusing to create workbooks blind")
        print("(folder renders but holds no workbooks — treating as empty)")
        return {}


def wsl(args):
    r = subprocess.run(["wsl", "bash", "-lc",
                        f"cd /mnt/c/Users/qasim/terrapinn/clay-icp-pipeline/"
                        f"automation/cleanup && {args}"],
                       capture_output=True, env=ENV)
    return r.returncode, r.stdout.decode("utf-8", "replace").strip()


def live_tables(wid):
    """{table name: row count} from the CLI — the authority on what landed.

    ONE process per workbook. The first version listed in one call and counted
    in another per table, so verifying a six-table workbook after every import
    cost ~40 WSL spawns; on a memory-constrained machine that contributed to the
    driver connection drops, not just to the wall clock.
    """
    code, out = wsl(f"python3 list_workbook_table_rows.py {wid}")
    if code != 0:
        return None
    tables = {}
    for line in out.splitlines():
        if "|" not in line:
            continue
        name, rows = line.rsplit("|", 1)
        # Empty means UNREADABLE, never zero — see the helper's docstring.
        tables[name.strip()] = int(rows) if rows.strip().isdigit() else None
    return tables


def import_one(page, fid, ev, wid, r, live):
    """Import one CSV, creating the workbook first if it does not exist yet.
    Returns the workbook id."""
    if not wid:
        open_folder(page, fid)
        clay_ui.create_workbook_with_csvs(page, ev, [r["csv"]])
        wid = (page.url.split("/workbooks/")[1].split("?")[0]
               if "/workbooks/" in page.url else None)
        if not wid:
            raise RuntimeError(f"created {ev!r} but could not read its id "
                               f"from {page.url[:80]}")
        live[ev] = wid
    else:
        clay_ui.add_csv_table(page, r["csv"])
    return wid


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--limit", type=int, help="at most N workbooks this run")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--delivery", default="ce", choices=sorted(DELIVERIES),
                    help="which handoff folder to build into")
    # A new event/ICP (the event-icp-clay-workflow skill) brings its own plan
    # and folder; these three replace the --delivery entry without a code edit.
    # All three together, so a stray --folder-id can never aim an existing
    # delivery's plan at another folder.
    ap.add_argument("--plan", help="plan JSON (split_delivery.py output)")
    ap.add_argument("--state", help="state JSON for this delivery (created if absent)")
    ap.add_argument("--folder-id", help="Clay folder id (f_...), made by hand")
    args = ap.parse_args()
    use_delivery(args.delivery)
    custom = [args.plan, args.state, args.folder_id]
    if any(custom) and not all(custom):
        raise SystemExit("--plan, --state and --folder-id go together")
    global PLAN, STATE
    if all(custom):
        PLAN, STATE = os.path.abspath(args.plan), os.path.abspath(args.state)

    if not os.path.exists(PLAN):
        raise SystemExit(f"no plan at {PLAN} — run split_ce_event_tables.py")
    plan = json.load(open(PLAN, encoding="utf-8"))
    for e in plan:
        e["csv"] = native_path(e["csv"])
    missing = [e["csv"] for e in plan if not os.path.exists(e["csv"])]
    if missing:
        raise SystemExit(f"!! {len(missing)} CSV(s) in the plan do not exist, "
                         f"first: {missing[0]} — refusing to import blind")
    state = state_io.load_json(STATE)

    by_event = collections.OrderedDict()
    for e in plan:
        if args.only and e["event"] not in args.only:
            continue
        by_event.setdefault(e["event"], []).append(e)

    def key(ev, table):
        return f"{ev}||{table}"

    done = sum(1 for v in state.values() if v.get("status") == "done")
    print(f"plan: {len(plan)} tables across {len(by_event)} workbooks "
          f"({done} table(s) already done)")
    if not args.apply and not args.list:
        for ev, rows in list(by_event.items())[:5]:
            print(f"   {ev[:38]:40} " +
                  ", ".join(f"{r['table']} ({r['rows']})" for r in rows))
        if len(by_event) > 5:
            print(f"   … {len(by_event) - 5} more events")
        print("\ndry run — nothing created. Add --apply.")
        return 0

    fid = args.folder_id or _id(FOLDER_KEY)
    with browser_session.clay_page(headless=not args.show) as page:
        # `is_logged_in` waits for networkidle and then for a signed-in
        # affordance; under load both can miss and report a false logout. It
        # did so twice mid-build on a session that was demonstrably valid — 41
        # tables had already imported through it. Retry before believing it,
        # rather than sending the user to re-authenticate a working login.
        for attempt in range(3):
            if clay_ui.is_logged_in(page):
                break
            if attempt < 2:
                print("   (login probe failed — retrying)")
                page.wait_for_timeout(5000)
        else:
            raise SystemExit("not logged in to Clay after 3 probes — start "
                             "Chrome with --remote-debugging-port=9222 and "
                             "sign in")

        # NOTE: this used to close the stray tabs `clay_page` leaves behind
        # (it opens a new one every run and never closes the old ones — five
        # had piled up by the 33rd table, which costs memory on a machine that
        # is already killing this process for it).
        #
        # DO NOT REINSTATE IT. Closing a tab that is sitting on a workbook
        # takes the whole CDP target down: the very next call fails with
        # "Target page, context or browser has been closed", and if the close
        # runs before the login probe you instead get a false "not logged in"
        # on a session that is demonstrably valid. Moving it after the probe
        # only moved the crash from the probe to `open_folder`. The tabs are
        # reclaimed by recycling Chrome between batches
        # (`restart_clay_chrome.ps1`), which is what the batch loop does.
        open_folder(page, fid)
        live = live_workbooks(page, fid)
        print(f"\ndelivery folder holds {len(live)} workbook(s)")
        if args.list:
            for name, wid in sorted(live.items()):
                tabs = live_tables(wid) or {}
                real = {k: v for k, v in tabs.items()
                        if k not in ("Stop", "History", "—")}
                print(f"   {name[:40]:42} {wid}")
                for t, n in sorted(real.items()):
                    print(f"      {t:32} {n if n is not None else '?'} rows")
            return 0

        # The post-import DOM wait is unreliable after the 2026-09 UI rebuild
        # and raised on a table whose rows had imported perfectly. Neutralise
        # it and confirm over the CLI instead (see the module docstring).
        clay_ui._wait_for_table_data = lambda page, timeout=0: None

        failures = []
        todo = [(ev, rows) for ev, rows in by_event.items()
                if any(state.get(key(ev, r["table"]), {}).get("status") != "done"
                       for r in rows)]
        if args.limit:
            todo = todo[:args.limit]
        print(f"workbooks to touch: {len(todo)}")

        for ev, rows in todo:
            wid = live.get(ev)
            print(f"\n{ev}" + (f"  (existing {wid})" if wid else "  (new)"))
            have = {}
            if wid:
                clay_ui.open_workbook_by_id(page, wid)
                have = live_tables(wid) or {}

            for r in rows:
                if state.get(key(ev, r["table"]), {}).get("status") == "done":
                    continue
                if r["table"] in have:
                    # PRESENT IS NOT DONE. This branch used to rubber-stamp any
                    # existing table as complete without checking its size, so a
                    # table left behind EMPTY by an earlier failed attempt was
                    # marked done with rows=0 and never repaired. The final
                    # sweep found three of them (Big 5 Egypt Exhibitors 0/208,
                    # two Sponsors 0/3 and 0/4) that state swore were fine.
                    if have[r["table"]] == r["rows"]:
                        print(f"   SKIP {r['table']:32} already present "
                              f"({have[r['table']]} rows)")
                        # Record the workbook id here too. Without it this
                        # branch wrote workbook_id=None, and `verify` resolves
                        # a workbook BY THAT ID (the CLI has no folder concept)
                        # — so a table that is present and correct was reported
                        # "NOT BUILT". That happened to three P&S workbooks the
                        # moment the repair pass cleared their state and this
                        # branch re-confirmed them.
                        state[key(ev, r["table"])] = {"status": "done",
                                                      "rows": have[r["table"]],
                                                      "workbook_id": wid,
                                                      "note": "already present"}
                        state_io.save_json(STATE, state)
                        continue
                    # Wrong size. Importing again would make `Sponsors (2)`
                    # beside the broken `Sponsors`, which is exactly how the two
                    # duplicates appeared. Leave it for the repair pass.
                    print(f"   !! {r['table']:32} present but {have[r['table']]} "
                          f"rows, expected {r['rows']} — needs repair, skipping")
                    failures.append((ev, r["table"], "wrong row count"))
                    continue

                print(f"   + {r['table']:32} {r['rows']:5} rows", flush=True)
                # ONE TABLE'S DOM FAILURE MUST NOT ABANDON THE FLEET. The first
                # full run died on a transient "Browse files" click 18 tables
                # in and left the remaining 33 workbooks untouched — the same
                # mistake as polling in order and blocking on one straggler.
                # Retry once, then move on and leave the table pending.
                try:
                    try:
                        wid = import_one(page, fid, ev, wid, r, live)
                    except Exception as first:
                        print(f"     .. retrying after {type(first).__name__}")
                        page.wait_for_timeout(3000)
                        if wid:
                            clay_ui.open_workbook_by_id(page, wid)
                        else:
                            open_folder(page, fid)
                        wid = import_one(page, fid, ev, wid, r, live)
                except Exception as e:
                    failures.append((ev, r["table"], type(e).__name__))
                    print(f"     !! {type(e).__name__}: {str(e)[:90]} "
                          f"— left pending, continuing")
                    continue

                # ASK CLAY. A table that cannot be confirmed stays not-done and
                # is retried; counting it done on a DOM guess is how a missing
                # table ships.
                #
                # Poll rather than asking once: the table has just been created
                # and the API listing is eventually consistent, so an immediate
                # read legitimately misses it. Six tries over ~35s — short
                # enough not to stall the fleet, long enough that a table is
                # not re-imported merely because Clay had not caught up.
                got = None
                for wait in (0, 3, 5, 8, 8, 10):
                    if wait:
                        page.wait_for_timeout(wait * 1000)
                    got = (live_tables(wid) or {}).get(r["table"])
                    if got is not None:
                        break
                if got is None:
                    print(f"     !! could not confirm {r['table']} — left pending")
                    continue
                if got != r["rows"]:
                    print(f"     !! {r['table']}: Clay has {got} rows, "
                          f"the CSV has {r['rows']} — left pending")
                    continue
                state[key(ev, r["table"])] = {"status": "done", "rows": got,
                                              "workbook_id": wid}
                state_io.save_json(STATE, state)
                print(f"     confirmed {got} rows")

    done = sum(1 for v in state.values() if v.get("status") == "done")
    print(f"\ntables confirmed: {done}/{len(plan)}")
    if failures:
        print(f"{len(failures)} table(s) left pending — re-run to retry:")
        for ev, table, why in failures[:12]:
            print(f"   {ev[:34]:36} {table:30} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
