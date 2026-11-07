"""Apply the "Companies - Supabase" blocklist-ledger template to one EVCharge
Products & Services `Companies` table.

The exact sequence the user specified (2026-08-07), which differs from the older
Labs pass in `apply_companies_supabase.py` — do not merge the two:

  1. open the template's Configure panel,
  2. map **Name** to the table's own `Name` column (Domain auto-maps already),
  3. **Save without running**,
  4. **Edit column** -> switch the API key to "Qasim - Labs"; the template
     defaults to the wrong account, and the wrong key is not an error the run
     surfaces — it just returns nothing useful for every row,
  5. only then run the **empty or out-of-date** rows.

Steps 1-3 spend nothing. Step 5 is the one paid action this repo runs on
purpose: 1 Action/row on the ledger check, which is what keeps the expensive
columns off already-worked companies (CLAUDE.md).

The entry path is today's UI, not the 2026-08-01 one: Ctrl+E opens Tools, whose
"For you" tab carries a **Templates** card; the older "View all enrichments" and
enrichment-search entry points no longer exist and their absence is why
`apply_companies_supabase.open_template` fails here.

    python apply_ps_supabase.py --list
    python apply_ps_supabase.py "Traffic, Road Safety & Enforcement Systems" --recon
    python apply_ps_supabase.py "Traffic, Road Safety & Enforcement Systems"
    python apply_ps_supabase.py "..." --run
"""

import argparse
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui                      # noqa: E402
import browser_session              # noqa: E402
import column_config as colcfg      # noqa: E402
import build_ps_companies as bps    # noqa: E402
import build_ps_people as bpp       # noqa: E402
import state_io                     # noqa: E402

VerificationError = browser_session.VerificationError

SHOTS_DIR = os.path.join(SCRIPT_DIR, "ps_supabase_shots")
TABLE = "Companies"
TEMPLATE = os.environ.get("CLAY_SUPABASE_TEMPLATE", "Companies - Supabase")
API_ACCOUNT = "Qasim - Labs"

# Columns the template creates. Their presence is the marker that it is applied;
# `Is New` having values is the marker that it has RUN.
MARKER_COLUMNS = ("HTTP API", "Is New")

# NO fixed panel coordinates. Everything in the right-hand panel is located
# relative to its own label or by the element's rect, because two things move it
# independently: dismissing the orange "approaching your credit limit" banner
# shifts the whole page up ~28px, and the Tools panel REMEMBERS its last tab, so
# a second visit opens straight into Templates instead of "For you". Both broke
# a fixed-coordinate version of this pass on the very next table.

# The Tools panel's search box. It is a contenteditable DIV with role=textbox,
# NOT an <input> — every input-based locator returns nothing here. It exists on
# both the "For you" and "Templates" tabs, so its presence says nothing about
# which tab is open; only the Templates CARD does (see open_template).
PANEL_SEARCH_INPUT = """()=>{
  let best=null;
  for(const e of document.querySelectorAll('[contenteditable],[role="textbox"]')){
    const r=e.getBoundingClientRect();
    if(r.width===0||r.x<1240||r.y>340) continue;
    if(!best||r.y<best.y) best={x:Math.round(r.x+r.width/2),
                                y:Math.round(r.y+r.height/2)};
  }
  return best;
}"""


def field_below(page, label, ymin, ymax, dy=32):
    """A click point ~dy below a panel label — banner-shift proof."""
    lab = page.evaluate(PANEL_LEAF, [label, ymin, ymax])
    return None if not lab else {"x": lab["x"] + 100, "y": lab["y"] + dy}

# Any visible leaf whose exact text matches, within the right-hand panel.
PANEL_LEAF = """([txt, ymin, ymax])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    if(norm(el.textContent)!==txt) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240||r.y<ymin||r.y>ymax) continue;
    return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

PANEL_DUMP = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[], seen=new Set();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t||t.length>70) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.height===0||r.x<1240) continue;
    const k=t+'@'+Math.round(r.y);
    if(seen.has(k)) continue; seen.add(k);
    out.push({t, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});
  }
  out.sort((a,b)=>a.y-b.y);
  return out;
}"""


def say(m):
    print(m, flush=True)


def dump_panel(page, label):
    say(f"  --- {label} ---")
    for i in page.evaluate(PANEL_DUMP):
        say(f"     ({i['x']:4d},{i['y']:4d}) {i['t']!r}")


def _wsl(script, arg):
    path = os.path.join(SCRIPT_DIR, script).replace("C:\\", "/mnt/c/").replace("\\", "/")
    out = subprocess.run(["wsl", "-d", "Ubuntu", "--", "python3", path, arg],
                         capture_output=True, text=True, timeout=300,
                         env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    return (out.stdout or "").strip()


def marker_present(workbook_id, table_id=None):
    """(applied, columns) — does the Companies table already carry the template?

    Asked of Clay's CLI, not the DOM. Reading it off the grid was unreliable:
    the header row's y-position shifts with the credit banner, and a wide table
    virtualizes its right-hand columns out of the DOM entirely, so a scan that
    happened to catch the Tools panel's tabs reported the wrong answer.

    Fails CLOSED as "applied": if the listing cannot be read we skip the
    template step, and the API-account step then fails loudly on a missing
    column — far better than re-applying and creating a duplicate HTTP API
    column that would double-charge the run.
    """
    try:
        # A known table_id skips the workbook listing — one WSL round-trip per
        # call instead of two. Each round-trip spawns a python3 under WSL and
        # costs seconds, and this runs twice per table (entry + pre-save), so
        # the saving is material when the worker's time window is short.
        tid = table_id
        if not tid:
            tables = [r for r in _wsl("list_workbook_tables.py", workbook_id).splitlines()
                      if "|" in r]
            tid = next((r.split("|", 1)[0] for r in tables
                        if r.split("|", 1)[1] == TABLE), None)
        if not tid:
            return True, [f"<no {TABLE} table>"]
        cols = _wsl("check_table_columns.py", tid).splitlines()
    except Exception as e:
        say(f"  !! column pre-check failed ({str(e)[:60]}) — treating as applied")
        return True, ["<unknown>"]
    if not cols:
        say("  !! column pre-check empty — treating as applied")
        return True, ["<unknown>"]
    return MARKER_COLUMNS[0] in cols, cols


# Is the Tools sidebar already open? Ctrl+E TOGGLES it, so pressing it blindly
# on a retry closes the panel that the previous attempt left open — which is
# exactly why retries 2 and 3 reported "no search box yet" and the whole retry
# loop was useless.
# Markers unique to the TEMPLATES panel: it lists "Functions" and "Claygents"
# cards under its search box. Checking for these is what distinguishes it from
# every OTHER Tools panel — critically the "Use AI" / Claygent generator, whose
# prompt box is also a contenteditable near the top of the sidebar. Typing the
# template name into THAT box turns it into an AI research brief on a column
# priced per row; it was only ever unsaved because a later check happened to
# fail first. Never type into this sidebar without confirming the panel first.
TEMPLATES_PANEL = """()=>{
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  const near=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240||r.y>430) continue;
    near.push(norm(el.textContent));
  }
  // The AI / Claygent generator is the panel we must never type into: its
  // prompt box is also a contenteditable near the top of this sidebar, and a
  // template name typed there becomes a per-row AI research brief. Refuse on
  // ANY of its signatures, whatever else is on screen.
  for(const t of ['Use case','Prompt','Generate','Compare models'])
    if(near.includes(t)) return false;
  // Two legitimate shapes of the Templates panel:
  //   browse  -> the "Functions" and "Claygents" cards under the search box
  //   results -> after typing, those cards are replaced by All/People/Companies
  //              tabs over the matches. Accepting only the browse shape made
  //              the guard refuse a panel that was already correct.
  const browse = near.includes('Functions') && near.includes('Claygents');
  const results = near.includes('All') &&
                  (near.includes('People') || near.includes('Companies'));
  return browse || results;
}"""

TOOLS_OPEN = """()=>{
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240||r.y>260) continue;
    const t=norm(el.textContent);
    if(t==='Import'||t==='Enrich'||t==='Export'||t==='Templates') return true;
  }
  return false;
}"""


def _open_template_once(page, shots_dir, slug):
    """One attempt at Tools -> Templates -> search -> Configure. Returns True on
    success; returns False (rather than raising) for the states that a retry
    genuinely fixes, so the caller can re-enter from a clean panel."""
    if not page.evaluate(TOOLS_OPEN):
        page.keyboard.press("Control+E")
        page.wait_for_timeout(4500)
    else:
        say("  Tools panel already open")
    # The Tools panel remembers its last tab. The Templates CARD lives in the
    # "For you" grid well down the panel; the Templates PANEL's own heading sits
    # at the top and reads the same, so bound the search below y=250 — otherwise
    # a remembered-Templates panel "finds" its own title and clicks a no-op.
    card = page.evaluate(PANEL_LEAF, ["Templates", 250, 700])
    if card:
        page.mouse.click(card["x"], card["y"])
        page.wait_for_timeout(5000)
    else:
        say("  Tools already on the Templates tab")

    # HARD GATE: only type once the Templates panel is positively identified.
    if not page.evaluate(TEMPLATES_PANEL):
        say("  not on the Templates panel (no Functions/Claygents cards) — "
            "refusing to type into an unidentified sidebar")
        return False
    box = page.evaluate(PANEL_SEARCH_INPUT)
    if not box:
        say("  no search box yet")
        return False
    page.mouse.click(box["x"], box["y"])
    page.wait_for_timeout(1200)
    # Clear first: a remembered panel can arrive carrying a previous query, and
    # appending to that matches nothing.
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(600)
    page.keyboard.type(TEMPLATE, delay=40)

    # POLL for the filtered result rather than waiting a fixed 5s. The list can
    # take noticeably longer to filter on a cold panel, and a fixed wait made
    # this step fail intermittently across the fleet — it clicked before the
    # result had rendered.
    # Match the RESULT ROW, not the search box. After typing, the box itself
    # contains the template name too, and a smallest-element search returns that
    # — clicking it is a no-op, which is why the Configure panel never rendered.
    # The results list starts below the All/People/Companies tabs (~y190).
    hit = None
    for _ in range(10):
        page.wait_for_timeout(1500)
        hit = page.evaluate(PANEL_LEAF, [TEMPLATE, 195, 900])
        if hit:
            break
    if not hit:
        say(f"  {TEMPLATE!r} not in the filtered list yet")
        return False
    page.mouse.click(hit["x"], hit["y"])

    for _ in range(10):
        page.wait_for_timeout(1500)
        if page.evaluate(PANEL_LEAF, ["Configure", 150, 400]):
            say("  Configure panel open")
            return True
    say("  Configure panel did not render")
    return False


def open_template(page, shots_dir, slug, tries=3):
    """Open the template's Configure panel, retrying from a closed panel.

    Every failure here is a timing/panel-state one, not a missing template, so
    a retry that first ESCAPES back to a clean state fixes it. Raises only after
    `tries` attempts — never silently proceeds without the panel, which would
    save a half-configured column.
    """
    for attempt in range(1, tries + 1):
        if _open_template_once(page, shots_dir, slug):
            return
        say(f"  open_template attempt {attempt}/{tries} failed — resetting")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1500)
        # Close the Tools panel explicitly so the next attempt starts from a
        # known state. Leaving it open half-rendered is what made the retries
        # no-ops.
        if page.evaluate(TOOLS_OPEN):
            page.keyboard.press("Control+E")
            page.wait_for_timeout(2500)
    bps.shot(page, shots_dir, f"{slug}_no_configure")
    raise VerificationError(
        f"could not open {TEMPLATE!r}'s Configure panel after {tries} attempts")


def set_name_field(page, shots_dir, slug):
    """Map the template's `Name` input to the table's own `Name` column.

    Domain auto-maps; Name does not, and an unmapped Name silently sends a null
    company name to the ledger, which stores a row keyed on domain alone.
    """
    fld = field_below(page, "Name", 200, 400)
    if not fld:
        bps.shot(page, shots_dir, f"{slug}_no_name_label")
        raise VerificationError("'Name' label not found in the Configure panel")
    page.mouse.click(fld["x"], fld["y"])
    page.wait_for_timeout(2500)
    page.keyboard.type("Name", delay=60)
    page.wait_for_timeout(3000)
    # The dropdown renders below the input; take the exact-text option in that band.
    opt = page.evaluate(PANEL_LEAF, ["Name", fld["y"] + 20, fld["y"] + 130])
    if not opt:
        bps.shot(page, shots_dir, f"{slug}_no_name_option")
        dump_panel(page, "name dropdown")
        raise VerificationError("'Name' column not offered in the Name dropdown")
    page.mouse.click(opt["x"], opt["y"])
    page.wait_for_timeout(3000)
    say(f"  Name mapped via {opt}")
    return "ok"


CPJ_GROUP = "Terrapinn - Company Enrichments (CPJ)"

# An exact-text leaf in the open column dropdown, INDENTED (x>1330) so a nested
# group member is told apart from the Configure panel's own field labels, which
# sit further left (~x1287).
PICKER_LEAF = """([txt, ymin, ymax])=>{
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    if(norm(el.textContent)!==txt) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1330||r.y<ymin||r.y>ymax) continue;
    return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""


def set_name_from_cpj(page, shots_dir, slug):
    """Map the template's `Name` to `Name` inside the CPJ enrichment group.

    On the Competitive Events `exhibitors_normalized` tables the company name
    the ledger needs is NOT the table's own column — it lives under the
    **Terrapinn - Company Enrichments (CPJ)** lookup (user, 2026-08-08). That is
    a collapsed group in the column picker, and its `Name` sits below the fold:
    the group lists ~15 fields with Name after "Total Funding Amount Range Usd",
    so the dropdown must be scrolled before that entry exists in the DOM.
    """
    fld = field_below(page, "Name", 200, 420)
    if not fld:
        bps.shot(page, shots_dir, f"{slug}_no_name_label")
        raise VerificationError("'Name' label not found in the Configure panel")
    page.mouse.click(fld["x"], fld["y"])
    page.wait_for_timeout(2500)
    page.keyboard.type("Terrapinn", delay=60)   # filter the long, virtualized list
    page.wait_for_timeout(3500)

    grp = page.evaluate(PANEL_LEAF, [CPJ_GROUP, 250, 700])
    if not grp:
        bps.shot(page, shots_dir, f"{slug}_no_cpj_group")
        raise VerificationError(f"{CPJ_GROUP!r} not offered in the Name picker")
    page.mouse.click(grp["x"], grp["y"])
    page.wait_for_timeout(3000)

    opt = None
    for _ in range(14):
        opt = page.evaluate(PICKER_LEAF, ["Name", grp["y"] + 20, 940])
        if opt:
            break
        page.mouse.move(grp["x"], grp["y"] + 200)
        page.mouse.wheel(0, 260)
        page.wait_for_timeout(700)
    if not opt:
        bps.shot(page, shots_dir, f"{slug}_no_cpj_name")
        raise VerificationError(
            f"'Name' not found inside {CPJ_GROUP!r} after scrolling")
    page.mouse.click(opt["x"], opt["y"])
    page.wait_for_timeout(3000)

    if page.evaluate(PANEL_LEAF, ["Start typing or select a column", 200, 420]):
        bps.shot(page, shots_dir, f"{slug}_name_still_empty")
        raise VerificationError("Name still shows its placeholder after picking")
    say(f"  Name mapped from {CPJ_GROUP} via {opt}")
    return "ok"


ACCOUNT_LABEL = "Select HTTP API (Headers) account"


def fix_api_account(page, shots_dir, slug):
    """Edit column -> switch the HTTP API account to `Qasim - Labs`.

    The template ships pointing at "Data Cloud and Clay", which this workspace
    cannot use — the panel says so outright ("You don't have access to this
    account"). Left unfixed the run still executes and still costs an Action a
    row; it just returns nothing useful, so this must happen BEFORE any run.

    Per the clay-save-and-run note: the Edit-column panel commits with a PLAIN
    Save (no split menu), and the account persists on selection — but verify by
    reopening rather than trusting the panel.
    """
    clay_ui.open_column_menu(page, "HTTP API")
    page.wait_for_timeout(2000)
    edit = page.evaluate(bps.FIND, "Edit column")
    if not edit:
        bps.shot(page, shots_dir, f"{slug}_no_edit_column")
        raise VerificationError("'Edit column' not in the HTTP API column menu")
    page.mouse.click(edit["x"], edit["y"])
    page.wait_for_timeout(7000)

    fld = field_below(page, ACCOUNT_LABEL, 250, 500)
    if not fld:
        bps.shot(page, shots_dir, f"{slug}_no_account_label")
        raise VerificationError(f"{ACCOUNT_LABEL!r} not in the Edit column panel")
    if page.evaluate(PANEL_LEAF, [API_ACCOUNT, fld["y"] - 20, fld["y"] + 20]):
        say(f"  account already {API_ACCOUNT!r}")
        page.keyboard.press("Escape")
        return "already"

    page.mouse.click(fld["x"], fld["y"])
    page.wait_for_timeout(3500)
    opt = page.evaluate(PANEL_LEAF, [API_ACCOUNT, fld["y"] + 40, fld["y"] + 180])
    if not opt:
        bps.shot(page, shots_dir, f"{slug}_no_account_option")
        dump_panel(page, "account dropdown")
        raise VerificationError(f"account {API_ACCOUNT!r} not in the dropdown")
    page.mouse.click(opt["x"], opt["y"])
    page.wait_for_timeout(3500)

    if not page.evaluate(PANEL_LEAF, [API_ACCOUNT, fld["y"] - 20, fld["y"] + 20]):
        bps.shot(page, shots_dir, f"{slug}_account_not_set")
        raise VerificationError(f"account did not change to {API_ACCOUNT!r}")
    say(f"  account set to {API_ACCOUNT!r}")

    save = page.get_by_role("button", name="Save", exact=True).last
    bb = save.bounding_box()
    if not bb or bb["x"] < 1400:
        raise VerificationError(f"Save button not in panel position ({bb})")
    page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(6000)
    bps.shot(page, shots_dir, f"{slug}_account_saved")
    return "ok"


def verify_api_account(page, shots_dir, slug):
    """Reopen Edit column and read the account back. Clay's panel text can be a
    stale leftover, so the only trustworthy check is a fresh open."""
    clay_ui.open_column_menu(page, "HTTP API")
    page.wait_for_timeout(2000)
    edit = page.evaluate(bps.FIND, "Edit column")
    if not edit:
        raise VerificationError("'Edit column' missing on re-open")
    page.mouse.click(edit["x"], edit["y"])
    page.wait_for_timeout(7000)
    fld = field_below(page, ACCOUNT_LABEL, 250, 500)
    ok = bool(fld and page.evaluate(PANEL_LEAF,
                                    [API_ACCOUNT, fld["y"] - 20, fld["y"] + 20]))
    say(f"  verified on reopen: account is {API_ACCOUNT!r}: {ok}")
    if not ok:
        bps.shot(page, shots_dir, f"{slug}_account_verify_failed")
        dump_panel(page, "account on reopen")
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)
    return ok


# "Run N empty or out-of-date rows" — N varies per table AND changes FORM with
# size: Clay prints "104" and "387" in full but abbreviates from about a
# thousand ("Run 1.2K empty or out-of-date rows"), so the count can carry a
# decimal point and a K/M suffix. A digits-and-commas-only pattern matched the
# small tables and silently missed every large one.
#
# Deliberately NOT "Force run all N rows", which sits right beside it in the
# same submenu: empty-or-out-of-date is re-entrant, so a relaunch after a killed
# worker tops up the unfinished rows instead of re-charging every row that
# already resolved. The mismatch above failed CLOSED onto this rule — it
# refused rather than taking the neighbouring option.
RUN_EMPTY_RE = re.compile(r"^Run [\d,.]+[KM]? empty or out-of-date rows$", re.I)


def run_empty_or_stale(page, shots_dir, slug, column=None):
    """Column menu -> Run column -> "Run N empty or out-of-date rows".

    This is the ONE /run/i control this repo clicks on purpose. CLAUDE.md's
    blanket rule against it exists to stop paid providers firing per row; the
    blocklist ledger check is the documented exception (1 Action/row) because it
    is what keeps the far more expensive columns off already-worked companies.
    Nothing else in this pass may click a run control.
    """
    col = column or MARKER_COLUMNS[0]
    clay_ui.open_column_menu(page, col)
    page.wait_for_timeout(2000)
    run = page.evaluate(bps.FIND, "Run column")
    if not run:
        bps.shot(page, shots_dir, f"{slug}_no_run_column")
        raise VerificationError(f"'Run column' not in the {col!r} column menu")
    page.mouse.click(run["x"], run["y"])
    page.wait_for_timeout(3000)

    items = [i.strip() for i in clay_ui.visible_menuitems(page)]
    target = next((i for i in items if RUN_EMPTY_RE.match(i)), None)
    if not target:
        # Distinguish "nothing left to run" from "the menu never opened".
        # When every row is already resolved and current, Clay stops offering
        # the empty-or-out-of-date item entirely and leaves only "Force run
        # all" — that is a COMPLETE table, not a failure. Requiring a sibling
        # item to be present proves the submenu really rendered, so a genuine
        # miss (menu didn't open, label form changed) still fails loudly.
        submenu_open = any(i.lower().startswith("force run all") or
                           i.lower().startswith("choose number of rows")
                           for i in items)
        if submenu_open:
            say("  nothing to run — no empty or out-of-date rows remain")
            page.keyboard.press("Escape")
            return "nothing_to_run"
        bps.shot(page, shots_dir, f"{slug}_no_run_empty")
        raise VerificationError(
            "the Run column submenu did not render (no 'Force run all' or "
            "'Choose number of rows' either) — refusing to guess")
    say(f"  clicking {target!r}")
    hit = page.evaluate(bps.FIND, target)
    if not hit:
        raise VerificationError(f"{target!r} listed but not locatable")
    page.mouse.click(hit["x"], hit["y"])
    page.wait_for_timeout(8000)
    bps.shot(page, shots_dir, f"{slug}_running")
    return target


def save_menu_options(page):
    """The Save split-button menu's options, without clicking any of them."""
    save = page.get_by_role("button", name="Save", exact=True).last
    bb = save.bounding_box()
    if not bb or bb["x"] < 1400:
        raise VerificationError(f"Save button not in panel position ({bb})")
    page.mouse.click(bb["x"] + bb["width"] - 12, bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(2000)
    out = []
    for role in ("menuitem", "button"):
        for el in page.get_by_role(role).all():
            try:
                if el.is_visible():
                    t = " ".join((el.inner_text() or "").split())
                    if t and t not in out:
                        out.append(t)
            except Exception:
                pass
    return out


def save_without_running(page, shots_dir, slug, workbook_id,
                         table_id=None):
    """Commit the template WITHOUT triggering it.

    Re-checks the marker column FIRST when given a workbook id. The entry-time
    check can be stale by the time we reach the save — a previous run may have
    created the column between them — and saving anyway produces a second
    `HTTP API` / `Is New` pair. That duplicate is worse than a wasted run: it is
    an independent ledger check that costs an Action a row AND returns
    `is_new: false` for every row (the ledger is write-once), so its values are
    actively misleading.

    The paid run is deliberately deferred until after the API account is fixed:
    running first would spend an Action per row against the wrong Supabase
    account, and a wrong key does not error — it just returns nothing useful.
    """
    # workbook_id is REQUIRED, not optional. It was optional, and a caller that
    # omitted it (apply_ce_sponsors_cpj) silently lost this guard and produced
    # duplicate paid columns across many tables. A missing id must be a
    # TypeError at import/callsite, not a quietly disabled safety check.
    applied, _ = marker_present(workbook_id, table_id)
    if True:
        if applied:
            say(f"  {MARKER_COLUMNS[0]!r} appeared since entry — NOT saving, "
                f"that would create a duplicate ledger column")
            page.keyboard.press("Escape")
            page.wait_for_timeout(1500)
            return "already_applied_race"
    try:
        colcfg.save_via_menu(page, r"Save and don'?t run")
        say("  saved via 'Save and don't run'")
        return "no_run"
    except Exception as e:
        say(f"  save_via_menu failed ({str(e)[:80]}); reading the menu")
        opts = save_menu_options(page)
        say(f"  save menu offers: {opts}")
        bps.shot(page, shots_dir, f"{slug}_save_menu")
        raise VerificationError(
            f"no 'Save and don't run' option; menu offered {opts}")


def configure_target(args):
    """Point the module at a table/template pair.

    The Companies and People ledger columns are the same flow over different
    objects, so the pass is parameterised rather than duplicated. Set as module
    constants because the helpers (marker_present, open_template) read them
    directly; done once at startup, never mid-run.
    """
    global TABLE, TEMPLATE
    TABLE = args.table
    TEMPLATE = args.template
    return TABLE, TEMPLATE


def add_target_args(ap):
    ap.add_argument("--table", default="Companies",
                    help="table to apply the template to (Companies | People)")
    ap.add_argument("--template", default=TEMPLATE,
                    help="Clay template name")
    ap.add_argument("--name-source", choices=["column", "cpj"], default="column",
                    help="where the template's Name field comes from: the "
                         "table's own column (P&S), or the Terrapinn - Company "
                         "Enrichments (CPJ) group (Competitive Events)")
    ap.add_argument("--no-map", action="store_true",
                    help="the template's input fields are already mapped — skip "
                         "the mapping step (the People template ships mapped)")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_target_args(ap)
    ap.add_argument("category", nargs="?")
    ap.add_argument("--icp", default="evcharge")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--recon", action="store_true",
                    help="open Configure, dump it, change nothing")
    ap.add_argument("--stop-after-name", action="store_true",
                    help="map Name then dump the panel without saving")
    ap.add_argument("--key-only", action="store_true",
                    help="skip the template step; only fix the API account on a "
                         "table where the column already exists")
    ap.add_argument("--run", action="store_true",
                    help="after fixing the key, run the empty/out-of-date rows "
                         "(SPENDS 1 Action per row)")
    ap.add_argument("--recon-run", action="store_true",
                    help="dump the 'Run column' submenu without clicking it")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()

    configure_target(args)
    wbs = bpp.company_workbooks(args.icp)
    if args.list:
        for n in sorted(wbs):
            print(f"  {n}")
        return 0
    if not args.category or args.category not in wbs:
        ap.error("a known category is required (see --list)")

    slug = "".join(c if c.isalnum() else "_" for c in args.category)[:60]
    with browser_session.clay_page(headless=not args.headed) as page:
        clay_ui.open_workbook_by_id(page, wbs[args.category])
        page.wait_for_timeout(8000)
        colcfg.focus_table_maybe_empty(page, TABLE)
        page.wait_for_timeout(2500)
        if args.recon_run:
            clay_ui.open_column_menu(page, "HTTP API")
            page.wait_for_timeout(2000)
            run = page.evaluate(bps.FIND, "Run column")
            say(f"  'Run column' at {run}")
            page.mouse.click(run["x"], run["y"])
            page.wait_for_timeout(3000)
            bps.shot(page, args.shots_dir, f"{slug}_run_submenu")
            say(f"  submenu: {clay_ui.visible_menuitems(page)}")
            page.keyboard.press("Escape")
            return 0

        if args.key_only:
            say(f"  api account: {fix_api_account(page, args.shots_dir, slug)}")
            say(f"  verified: {verify_api_account(page, args.shots_dir, slug)}")
            return 0

        applied, headers = marker_present(wbs[args.category])
        say(f"  columns: {headers[-3:]}")
        if applied:
            say(f"  {MARKER_COLUMNS[0]!r} already present — skipping the "
                f"template step, going straight to the API account")
            saved = "already_applied"
        else:
            open_template(page, args.shots_dir, slug)
            if args.recon:
                dump_panel(page, "configure")
                bps.shot(page, args.shots_dir, f"{slug}_configure")
                page.keyboard.press("Escape")
                return 0
            if args.no_map:
                say("  --no-map: template fields already mapped, not touching them")
            elif args.name_source == "cpj":
                set_name_from_cpj(page, args.shots_dir, slug)
                bps.shot(page, args.shots_dir, f"{slug}_name_mapped")
            else:
                set_name_field(page, args.shots_dir, slug)
                bps.shot(page, args.shots_dir, f"{slug}_name_mapped")
            if args.stop_after_name:
                dump_panel(page, "after mapping Name (not saved)")
                say(f"  save menu would offer: {save_menu_options(page)}")
                return 0
            saved = save_without_running(page, args.shots_dir, slug, wbs[args.category])
            page.wait_for_timeout(6000)
            bps.shot(page, args.shots_dir, f"{slug}_saved")
        say(f"saved: {saved}")

        acct = fix_api_account(page, args.shots_dir, slug)
        say(f"api account: {acct}")
        if not verify_api_account(page, args.shots_dir, slug):
            raise VerificationError(
                f"account is not {API_ACCOUNT!r} on reopen — refusing to run, "
                f"the wrong key costs an Action a row and returns nothing")

        if not args.run:
            say(f"CONFIGURED (not run) {args.category}")
            return 0
        say(f"ran: {run_empty_or_stale(page, args.shots_dir, slug)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
