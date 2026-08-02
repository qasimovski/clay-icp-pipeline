"""Apply the "Find Work Email and Validate Email" template to a single named
table (Speakers_normalized) in one workbook.

Mirrors apply_findlinkedin_event.py's structure (--recon / --dry-run / a real
apply that waits for 100% completion), but this template's Configure panel is
materially harder than the previous two, so the field mapping is bespoke.

Configure panel (confirmed via --recon on Automation UK, 2026-07-26):
  LinkedIn URL   auto-mapped to our own "LinkedIn URL" column - skip
  name           -> top-level "Name" column
  org            -> "Find LinkedIn and Enrich Person" > "Company"
  Enrich person  -> ...> "Enrich person" > "Insert all 30 properties"
  company_domain -> REQUIRED (Save stays disabled without it) and there is no
                    domain column on Speakers_normalized. The only domain in
                    the tree is nested at
                      Enrich person > current_experience > [0] > company_domain
                    i.e. the speaker's FIRST current role's company domain.
                    User approved this mapping (2026-07-26) knowing it can be
                    empty/wrong for people with zero or multiple current roles.

Cost note: this template is ~14.5 credits/row (vs ~0.1/row for the Google
Sheet lookup), so it is rolled out in small user-approved batches, not a
single fleet run.
"""

import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402

# The saved template's name in Clay, tried in order — the user renamed it
# "Find Work Email and Validate Email" -> "Email Waterfall and Validate Email"
# (2026-07-26). Keeping both means a rename doesn't break a run mid-fleet; add
# to the front of the list (or set CLAY_WORKEMAIL_TEMPLATE) if it changes again.
TEMPLATE_CANDIDATES = [n for n in (
    os.environ.get("CLAY_WORKEMAIL_TEMPLATE"),
    "Email Waterfall and Validate Email",
    "Find Work Email and Validate Email",
) if n]
# Name that actually matched on the last _open_template() — recorded in state.
TEMPLATE_USED = None

# Columns this template adds. Not yet pinned to a single name (the first real
# apply will tell us), so idempotency checks accept ANY of these as proof the
# template is already on the table. Narrow this once confirmed via the CLI.
SIG_CANDIDATES = ["Work Email", "Find Work Email", "Validate Email",
                  "Email Validation", "Validate email", "Work email"]

# Config-panel field-box locator (same as apply_v1_event.py / findlinkedin):
# find the label's y, then the "Start typing" placeholder box just below it.
# Note the panel renders some labels with non-breaking spaces ("Enrich\xa0person");
# JS \s matches   so the norm() below handles that.
_FIELD_BOX = """(label)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let ly=null;
  for(const el of leaves){const r=el.getBoundingClientRect();
    if(r.x>1250&&r.x<1460&&r.y>200&&r.y<900&&norm(el.textContent)===label){ly=r.y;break;}}
  if(ly===null)return null;
  // A field's own label lives inside its CollapsibleSection trigger BUTTON.
  // Text inside ANY such button is a label, never a mapped value — without
  // this, a closed section reads the NEXT field's label (which sits within
  // 75px below it) as if this field were already mapped.
  const inTrigger=el=>{let n=el;for(let i=0;i<5&&n;i++,n=n.parentElement){
    if(n.tagName==='BUTTON'&&n.getAttribute('aria-expanded')!==null)return true;}
    return false;};
  let box=null, chip=null;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0) continue;
    // Panel column only. The grid's own cells start at x>=1348 and sit at the
    // same y as the panel (the overlay doesn't remove them from layout), so a
    // wider x window reads speaker names as if they were mapped values.
    if(r.x<1255||r.x>1345||r.y<ly+2||r.y>ly+75) continue;
    const t=norm(el.textContent);
    if((el.textContent||'').includes('Start typing')){
      box={x:Math.round(r.x+30),y:Math.round(r.y+r.height/2),empty:true};
      break;
    }
    if(t && t!==label && !t.startsWith('\\u2014') && !inTrigger(el)) chip=t;
  }
  if(box) return box;
  if(chip) return {empty:false, chip:chip};
  return {empty:false, chip:null};   // nothing there: section probably closed
}"""

# Locate a Configure field's CollapsibleSection trigger button (the row holding
# the label) so a closed section can be expanded before we look for its value
# box. Returns the button's centre + its aria-expanded value.
_FIELD_SECTION = """(label)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let t=null;
  for(const el of leaves){const r=el.getBoundingClientRect();
    if(r.x>1250&&r.x<1460&&r.y>200&&r.y<900&&norm(el.textContent)===label){t=el;break;}}
  if(!t) return {found:false};
  let n=t;
  for(let i=0;i<6&&n;i++,n=n.parentElement){
    if(n.tagName==='BUTTON'&&n.getAttribute('aria-expanded')!==null){
      const r=n.getBoundingClientRect();
      return {found:true, expanded:n.getAttribute('aria-expanded'),
              x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
    }
  }
  return {found:true, expanded:null, x:null, y:null};
}"""

# Exact-text option finder scoped to the open dropdown popover. The x floor
# matters: the same strings exist as grid column headers (x~1150) and as the
# Configure field LABELS (x~1270); dropdown rows start around x=1309 and indent
# deeper as you descend the tree.
_FIND_OPT = """([txt, xmin, xmax, ymin, ymax])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0) continue;
    if(r.x<xmin||r.x>xmax||r.y<ymin||r.y>ymax) continue;
    if(norm(el.textContent)===txt)
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

_LABEL_SCAN_JS = """()=>{
  const out=[];
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t) continue;
    const r=el.getBoundingClientRect();
    if(r.x>1240&&r.x<1600&&r.y>120&&r.y<900&&r.width>0){
      out.push({text:t, x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width)});
    }
  }
  return out;
}"""


def _sig_present(page):
    """Whether any of this template's known output columns is on the table."""
    for name in SIG_CANDIDATES:
        if clay_ui._find_header_rect(page, name):
            return name
    return None


def _click_opt(page, txt, xmin=1300, xmax=1700, ymin=210, ymax=900, tries=10):
    """Click an exact-text row in the open dropdown; True if it was found."""
    for _ in range(tries):
        pt = page.evaluate(_FIND_OPT, [txt, xmin, xmax, ymin, ymax])
        if pt:
            page.mouse.click(pt["x"], pt["y"])
            page.wait_for_timeout(1100)
            return True
        page.wait_for_timeout(400)
    return False


def _open_template(page):
    va = page.get_by_text("View all enrichments", exact=False)
    try:
        va.first.click(timeout=8000)
    except Exception:
        page.keyboard.press("Control+e"); page.wait_for_timeout(2000)
        va.first.click(timeout=15000)
    page.wait_for_timeout(2500)
    page.get_by_role("tab", name="Templates").first.click(timeout=25000)
    page.wait_for_timeout(2000)
    you = page.get_by_role("radio", name="You")
    if not you.count():
        you = page.get_by_text("You", exact=True)
    try:
        you.first.click(timeout=8000); page.wait_for_timeout(1500)
    except Exception:
        pass
    # The template list virtualizes, so each candidate name gets typed into the
    # search box before we decide it isn't there.
    global TEMPLATE_USED
    tpl = None
    for name in TEMPLATE_CANDIDATES:
        cand = page.get_by_text(name, exact=True)
        for _ in range(4):
            if cand.count() and cand.first.is_visible():
                break
            sb = page.get_by_placeholder("Search for a data point or provider")
            try:
                if sb.count():
                    sb.first.fill(name)
            except Exception:
                pass
            page.wait_for_timeout(1500); cand = page.get_by_text(name, exact=True)
        if cand.count() and cand.first.is_visible():
            tpl, TEMPLATE_USED = cand, name
            break
    if tpl is None:
        raise clay_ui.ClayUIError(
            f"no template found; tried {TEMPLATE_CANDIDATES}")
    tpl.first.click(timeout=20000)
    page.wait_for_timeout(3500)
    if not page.get_by_text("Configure", exact=True).count():
        raise clay_ui.ClayUIError("config panel did not open")


def _open_template_retry(page, tries=3):
    last = None
    for _ in range(tries):
        try:
            _open_template(page); return
        except Exception as e:
            last = e
            try:
                page.keyboard.press("Escape"); page.wait_for_timeout(2500)
            except Exception:
                pass
    raise clay_ui.ClayUIError(f"could not open template after {tries}: {last}")


# --------------------------------------------------------------------------
# field mapping — one function per field, each returns a short status string
# --------------------------------------------------------------------------

def _open_field(page, label):
    """Expand the field's collapsible section, then click its value box.
    Returns 'missing' | 'prefilled' | 'open'.

    Each Configure field is a CollapsibleSection whose label lives inside a
    BUTTON[aria-expanded]; while that section is CLOSED the value box is not in
    the DOM at all (probed on BioTrinity, 2026-07-26). The old locator only
    looked for the "Start typing" box, so it read every closed field as
    'prefilled' and skipped it — which, now that every field on this template is
    "— Optional", would have let Save enable with nothing but the auto-mapped
    LinkedIn URL. Expand first, then decide.
    """
    sec = None
    for _ in range(12):
        sec = page.evaluate(_FIELD_SECTION, label)
        if sec and sec.get("found"):
            break
        page.wait_for_timeout(1000)
    if not (sec and sec.get("found")):
        return "missing"
    # Two attempts: expand if closed, look for the box; if nothing is there at
    # all (neither placeholder nor a mapped value) the click may have toggled a
    # section that was already open, so re-read the state and try once more.
    info, chip = None, None
    for attempt in range(2):
        s = page.evaluate(_FIELD_SECTION, label) if attempt else sec
        if s and s.get("expanded") == "false" and s.get("x"):
            page.mouse.click(s["x"], s["y"])
            page.wait_for_timeout(1200)
        # Poll for the placeholder box specifically. A chip-looking read is NOT
        # accepted early: filling the previous field leaves its column-picker
        # open, and that popover's option rows ("Event", "Name", "Find LinkedIn
        # and Enrich Person", ...) render in exactly this band and read as a
        # mapped value. Only a chip that survives the whole poll is believed.
        for _ in range(10):
            info = page.evaluate(_FIELD_BOX, label)
            if info and info.get("empty"):
                break
            if info and info.get("chip"):
                chip = info["chip"]
            page.wait_for_timeout(600)
        if info and info.get("empty"):
            break
    if not info:
        return "missing"
    if info.get("empty"):
        page.mouse.click(info["x"], info["y"])
        page.wait_for_timeout(1100)
        return "open"
    if chip:
        return "prefilled"
    return "missing"              # box never rendered — don't call it prefilled


_CLEAR_BTN = """(label)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let ly=null;
  for(const el of leaves){const r=el.getBoundingClientRect();
    if(r.x>1250&&r.x<1460&&r.y>200&&r.y<900&&norm(el.textContent)===label){ly=r.y;break;}}
  if(ly===null) return null;
  // the value box's clear (x) control sits at its right edge, left of the chevron
  for(const b of document.querySelectorAll('button,[role="button"],svg')){
    const r=b.getBoundingClientRect();
    if(r.width===0||r.x<1620||r.x>1655||r.y<ly+18||r.y>ly+60) continue;
    return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""


def _clear_field(page, label):
    """Clear a mapped value so the field can be re-opened and re-mapped."""
    pt = page.evaluate(_CLEAR_BTN, label)
    if not pt:
        return False
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_timeout(900)
    return True


def _poll_chip_exact(page, label, value, tries=10):
    """Wait for a field's chip to settle on exactly `value`."""
    got = None
    for _ in range(tries):
        got = field_chips(page).get(label)
        if got == value:
            return got
        page.wait_for_timeout(700)
    return got


def _poll_chip(page, label, prefix, tries=6):
    """Wait for a field's chip to settle on a value starting with `prefix`."""
    got = None
    for _ in range(tries):
        got = field_chips(page).get(label)
        if (got or "").startswith(prefix):
            return got
        page.wait_for_timeout(600)
    return got


def _map_column(page, label, column, say, tries=3):
    """Map `label` to the table's own `column`, verifying what actually landed.

    Coordinate-clicking a picker row is unreliable on its own: the list settles
    /re-renders after it opens, so a row located a moment earlier can move under
    the cursor — that is how 'Speaker Name' silently ended up on 'Created At'.
    Typing the column name first narrows the list, and the chip is read back
    afterwards; a mismatch is cleared and retried rather than reported ok.
    """
    for attempt in range(tries):
        st = _open_field(page, label)
        if st != "open":
            say(f"  {label}: {st}")
            return st
        page.keyboard.type(column, delay=30)
        page.wait_for_timeout(1300)
        _click_opt(page, column)
        page.wait_for_timeout(600)
        # Close the picker before reading back: while it is open its own rows
        # (and the "Start typing..." box) sit in the chip's band and the value
        # reads as unsettled. Then poll — the chip lands a beat after the click.
        _dismiss_picker(page)
        got = _poll_chip_exact(page, label, column)
        if got == column:
            say(f"  {label} = {column!r}")
            return "ok"
        say(f"  {label}: got {got!r}, expected {column!r} "
            f"(attempt {attempt+1}/{tries})")
        # A placeholder read means the value simply hasn't committed yet — give
        # it longer before deciding the mapping went to the wrong column.
        if (got or "").startswith("Start typing"):
            if _poll_chip_exact(page, label, column, tries=12) == column:
                say(f"  {label} = {column!r} (settled late)")
                return "ok"
        if not _clear_field(page, label):
            break
    # Last chance: it may have landed while we were giving up.
    if _poll_chip_exact(page, label, column, tries=6) == column:
        say(f"  {label} = {column!r} (settled late)")
        return "ok"
    return "failed"


def _fill_name(page, say):
    return _map_column(page, "name", "Name", say)


def _fill_org(page, say):
    """org -> Find LinkedIn and Enrich Person > Enrich person > org.

    User-confirmed 2026-07-27, matching the mapping they had configured by hand
    (screenshot). An earlier revision of this script pointed org at the enrich
    group's 'Company' instead."""
    st = _open_field(page, "org")
    if st != "open":
        say(f"  org: {st}"); return st
    for step, kwargs in [("Find LinkedIn and Enrich Person", {}),
                         ("Enrich person", {"xmin": 1320}),
                         ("org", {"xmin": 1320})]:
        if not _click_opt(page, step, **kwargs):
            say(f"  org: step {step!r} not found")
            return "failed"
    _dismiss_picker(page)
    got = _poll_chip(page, "org", "Enrich person")
    if not (got or "").startswith("Enrich person"):
        say(f"  org: landed on {got!r}, expected an Enrich person path")
        return "failed"
    say("  org = Enrich person > org")
    return "ok"


def _fill_enrich_person(page, say):
    st = _open_field(page, "Enrich person")
    if st != "open":
        say(f"  Enrich person: {st}"); return st
    if not _click_opt(page, "Find LinkedIn and Enrich Person"):
        say("  Enrich person: enrich group not found"); return "failed"
    if not _click_opt(page, "Enrich person", xmin=1320):
        say("  Enrich person: nested object not found"); return "failed"
    ok = _click_opt(page, "Insert all 30 properties")
    say("  Enrich person = all 30 properties" if ok
        else "  Enrich person: 'Insert all 30 properties' not found")
    return "ok" if ok else "failed"


def _fill_company_domain(page, say):
    """Enrich person > current_experience > [0] > company_domain.

    Typing 'domain' first collapses the tree to the single matching branch,
    which makes each expand step unambiguous."""
    st = _open_field(page, "company_domain")
    if st != "open":
        say(f"  company_domain: {st}"); return st
    page.keyboard.type("domain", delay=30)
    page.wait_for_timeout(1800)
    for step, kwargs in [("Find LinkedIn and Enrich Person", {}),
                         ("Enrich person", {"xmin": 1320}),
                         ("current_experience", {"xmin": 1340}),
                         ("0", {"xmin": 1370, "xmax": 1400}),
                         ("company_domain", {"xmin": 1350})]:
        if not _click_opt(page, step, **kwargs):
            say(f"  company_domain: step {step!r} not found")
            return "failed"
    _dismiss_picker(page)
    got = _poll_chip(page, "company_domain", "Enrich person")
    if not (got or "").startswith("Enrich person"):
        say(f"  company_domain: landed on {got!r}, expected an Enrich person path")
        return "failed"
    say("  company_domain = Enrich person > current_experience > [0] > company_domain")
    return "ok"


# What a correctly configured panel looks like. Verified as a whole AFTER all
# fields are filled: per-field readbacks race the UI (a chip can take seconds to
# commit while Clay pops the next field's picker), so the settled panel is the
# only trustworthy source of truth — and the thing that gates spending credits.
EXPECTED_MAPPING = {
    "name": ("exact", "Name"),
    "company_domain": ("prefix", "Enrich person"),
    "Speaker Name": ("exact", "Name"),
    "org": ("prefix", "Enrich person"),
    "LinkedIn URL": ("exact", "LinkedIn URL"),
}


def _mapping_problems(chips):
    bad = {}
    for label, (kind, want) in EXPECTED_MAPPING.items():
        got = chips.get(label)
        ok = (got == want) if kind == "exact" else (got or "").startswith(want)
        if not ok:
            bad[label] = f"got {got!r}, want {kind} {want!r}"
    return bad


def verify_mapping(page, tries=10):
    """Poll the whole Configure panel until every field matches EXPECTED_MAPPING.
    Returns (chips, problems)."""
    chips, bad = {}, {}
    for _ in range(tries):
        chips = field_chips(page)
        bad = _mapping_problems(chips)
        if not bad:
            return chips, {}
        page.wait_for_timeout(800)
    return chips, bad


def _fill_speaker_name(page, say):
    """'Speaker Name' — a field that did not exist in the 2026-07-26 panel.
    Maps to the table's own 'Name' column (user-confirmed 2026-07-27), same as
    'name'; Speakers_normalized has no column literally called 'Speaker Name'."""
    return _map_column(page, "Speaker Name", "Name", say)


# Panel order as of BioTrinity 2026-07-26 (top to bottom): name,
# company_domain, Speaker Name, org, LinkedIn URL. Fill top-down — filling one
# field can auto-open the next one's dropdown, which would swallow a later
# click. 'Enrich person' is NOT a Configure field on this template any more
# (it only exists as a column the earlier Find-LinkedIn pass added), so
# _fill_enrich_person is kept for older template revisions but not called; the
# nested company_domain path still walks that column's tree.
_FIELD_ORDER = ("name", "company_domain", "Speaker Name", "org")


_PANEL_OPEN = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    if(norm(el.textContent)==='Configure'){
      const r=el.getBoundingClientRect();
      if(r.x>1240) return true;
    }
  }
  return false;
}"""


def _panel_open(page):
    try:
        return bool(page.evaluate(_PANEL_OPEN))
    except Exception:
        return False


def _dismiss_picker(page):
    """Close the column-picker Clay auto-opens after a fill.

    Filling a field pops the NEXT empty field's picker open, and that popover
    covers the following section's trigger row — clicking there hits an option
    row instead of the section, so the next field can never be opened (this is
    what left 'org' unfillable). Escape closes just the popover; the Configure
    panel survives, which the return value verifies.
    """
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.wait_for_timeout(900)
    return _panel_open(page)


def configure_fields(page, say):
    """Map every fillable Configure field, top-down. Returns {label: status}."""
    out = {}
    for label, fn in (("name", _fill_name),
                      ("company_domain", _fill_company_domain),
                      ("Speaker Name", _fill_speaker_name),
                      ("org", _fill_org)):
        out[label] = fn(page, say)
        if not _dismiss_picker(page):
            say(f"  !! Configure panel closed after {label} — stopping here")
            break
    return out


_CHIPS = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  const inTrigger=el=>{let n=el;for(let i=0;i<5&&n;i++,n=n.parentElement){
    if(n.tagName==='BUTTON'&&n.getAttribute('aria-expanded')!==null)return true;}
    return false;};
  const out={};
  for(const b of document.querySelectorAll('button[aria-expanded]')){
    const r=b.getBoundingClientRect();
    if(r.width===0||r.x<1260||r.x>1500||r.y<200||r.y>900) continue;
    const label=norm(b.textContent).replace(/\\u2014 Optional$/,'').trim();
    if(!label||label==='Configure'||label==='Providers') continue;
    const parts=[];
    for(const el of leaves){
      const q=el.getBoundingClientRect();
      if(q.width===0||q.x<1255||q.x>1345||q.y<r.y+r.height||q.y>r.y+r.height+40)
        continue;
      const t=norm(el.textContent);
      if(t&&!inTrigger(el)) parts.push(t);
    }
    out[label]=parts.join(' > ') || null;
  }
  return out;
}"""


def field_chips(page):
    """{field label: what it currently maps to} — read back after mapping so a
    dry-run proves the values landed, not merely that clicks were dispatched."""
    try:
        return page.evaluate(_CHIPS)
    except Exception as e:
        return {"error": str(e)[:80]}


def recon(page, entry, table_name, say, screenshot=None):
    """Open the template on `table_name` and report the Configure panel's
    contents WITHOUT filling or saving. Always Escapes at the end."""
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    if not B.table_exists(page, table_name):
        say(f"SKIP {name}/{table_name}: table does not exist")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "no_table"}
    B.focus_table_maybe_empty(page, table_name)
    page.wait_for_timeout(800)
    already = _sig_present(page)
    _open_template_retry(page)
    rows = page.evaluate(_LABEL_SCAN_JS)
    rows.sort(key=lambda r: (r["y"], r["x"]))
    say(f"RECON {name}/{table_name}: already_applied={already!r} "
        f"template={TEMPLATE_USED!r}")
    for r in rows:
        say(f"  y={r['y']:<4} x={r['x']:<4} w={r['w']:<4} {r['text']!r}".encode(
            "ascii", "backslashreplace").decode("ascii"))
    if screenshot:
        page.screenshot(path=screenshot)
        say(f"screenshot saved: {screenshot}")
    page.keyboard.press("Escape")
    return {"workbook_id": wid, "workbook_name": name, "table": table_name,
            "status": "recon", "already_applied": already,
            "template": TEMPLATE_USED, "panel": rows}


def _save_disabled(page):
    s = page.get_by_role("button", name="Save", exact=True).last
    return s.evaluate("el=>el.disabled||el.getAttribute('data-disabled')!==null")


def _pct(page):
    t = page.evaluate("()=>document.body.innerText")
    m = re.search(r"(\d+)% of table completed", t)
    return int(m.group(1)) if m else None


def _wait_for_full_completion(page, say, table_name, max_wait_s=1800, poll_s=12):
    start = time.time()
    last = None
    while time.time() - start < max_wait_s:
        pct = _pct(page)
        if pct == 100:
            return True, 100
        if pct != last:
            say(f"   ...{table_name} progress {pct}%")
            last = pct
        page.wait_for_timeout(poll_s * 1000)
    return False, last


def apply_findworkemail(page, entry, table_name, dry_run, say):
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)

    if not B.table_exists(page, table_name):
        say(f"SKIP {name}/{table_name}: table does not exist")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "no_table"}

    B.focus_table_maybe_empty(page, table_name)
    page.wait_for_timeout(800)

    already = _sig_present(page)
    if already:
        pct = _pct(page)
        if pct == 100:
            say(f"SKIP {name}/{table_name}: already applied ({already!r}) and 100% complete")
            return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                    "status": "ok", "note": "already_applied"}
        say(f"RESUME {name}/{table_name}: already applied ({already!r}) but at {pct}%")
        done, last_pct = _wait_for_full_completion(page, say, table_name)
        if not done:
            say(f"INCOMPLETE {name}/{table_name}: still at {last_pct}% after max wait")
            return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                    "status": "incomplete", "note": "already_applied", "state": last_pct}
        say(f"DONE {name}/{table_name}: already-applied run finished all rows (100%)")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "ok", "note": "already_applied_resumed"}

    _open_template_retry(page)
    say(f"  template matched: {TEMPLATE_USED!r}")
    statuses = configure_fields(page, say)

    chips, problems = verify_mapping(page)
    say(f"  mapped values: {chips}")
    if problems:
        say(f"  !! mapping problems: {problems}")
    else:
        # A field whose per-field readback raced the UI is genuinely fine once
        # the panel settles — don't leave a scary 'failed' in the state file.
        statuses = {k: ("ok_verified" if v not in ("ok", "prefilled") else v)
                    for k, v in statuses.items()}

    if dry_run:
        say(f"DRYRUN {name}/{table_name}: field statuses {statuses}; "
            f"save_disabled={_save_disabled(page)}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "dryrun", "template": TEMPLATE_USED,
                "fields": statuses, "mapped": chips}

    # Every field on this template is "— Optional", so an enabled Save is NOT
    # proof the mapping took: an all-skipped run would save happily and burn
    # ~14.5 credits/row producing nothing. Require each intended field to be
    # mapped (or already mapped) before we spend anything.
    if problems:
        say(f"ABORT {name}/{table_name}: mapping incomplete {problems}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "aborted", "reason": "mapping_incomplete",
                "fields": statuses, "mapped": chips, "problems": problems}

    # Never click a disabled Save - if the mapping didn't fully take, bail out
    # loudly with the per-field statuses rather than half-applying.
    enabled = False
    for _ in range(12):
        if not _save_disabled(page):
            enabled = True
            break
        page.wait_for_timeout(1000)
    if not enabled:
        say(f"ABORT {name}/{table_name}: Save stayed disabled; fields={statuses}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "aborted", "reason": "save_disabled", "fields": statuses}

    save = page.get_by_role("button", name="Save", exact=True).last
    r = save.bounding_box()
    page.mouse.click(r["x"] + r["width"] - 5, r["y"] + r["height"] / 2)
    page.wait_for_timeout(1200)
    ti = None
    for mi in page.get_by_role("menuitem").all():
        try:
            t = mi.inner_text().strip()
            if mi.is_visible() and re.search(r"Save and run.*in this view", t, re.I):
                ti = mi; break
        except Exception:
            pass
    if ti is None:
        items = [m.inner_text().strip().replace("\n", " ") for m in
                 page.get_by_role("menuitem").all() if m.is_visible()]
        say(f"ABORT {name}/{table_name}: no 'Save and run ... in this view': {items[-4:]}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "aborted", "reason": "no_run_option"}
    lbl = ti.inner_text().strip().replace("\n", " ")
    ti.click(timeout=8000)
    page.wait_for_timeout(16000)

    confirmed = None
    for _ in range(8):
        confirmed = _sig_present(page)
        if confirmed:
            break
        page.wait_for_timeout(2500)
    if not confirmed:
        st = _pct(page)
        say(f"UNCONFIRMED {name}/{table_name}: clicked {lbl!r} but no known output "
            f"column appeared (checked {SIG_CANDIDATES}) | {st}%")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "unconfirmed", "ran": lbl, "state": st, "fields": statuses}

    done, last_pct = _wait_for_full_completion(page, say, table_name)
    if not done:
        say(f"INCOMPLETE {name}/{table_name}: applied + {lbl!r} but still at {last_pct}%")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "incomplete", "ran": lbl, "state": last_pct,
                "template": TEMPLATE_USED}
    say(f"DONE {name}/{table_name}: applied + {lbl!r} | 100% of table completed")
    return {"workbook_id": wid, "workbook_name": name, "table": table_name,
            "status": "ok", "ran": lbl, "state": "100% of table completed",
            "template": TEMPLATE_USED, "fields": statuses, "mapped": chips}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Ad-hoc single workbook/table run")
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("table_name")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--screenshot")
    a = ap.parse_args()
    entry = {"workbook_id": a.workbook_id, "workbook_name": a.workbook_name}
    with common.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        if a.recon:
            out = recon(page, entry, a.table_name, say, screenshot=a.screenshot)
        else:
            out = apply_findworkemail(page, entry, a.table_name, a.dry_run, say)
        print("\nRESULT:", out)
