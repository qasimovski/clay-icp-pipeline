"""Apply the "Qasim - CPJ - Labs - People" template to one Sellers/Buyers -
People table in a Competitive Events workbook, mapping all of its inputs from
the table's "Company Table Data" lookup and saving+running.

Per the user (2026-08-03): run this template on the "Sellers - People" and
"Buyers - People" tables in the Competitive Events folder; every input is mapped
from under the "Company Table Data" column; and *nothing here costs credits* —
the created columns are `type: "basic"` references that read a lookup which has
already run, not provider calls. That is why this pass saves AND runs (repo rule
"Formula columns … are created and run"), with no configure-only default.

=== Where the mapping came from ===

Not guessed. The user's finished reference build (ACHEMA / Sellers - People,
columns stamped 14:41-14:44 on 2026-08-03) was read back column by column with
recon_cpj_reference.py, and each created column's reference chip named the
lookup property it reads:

  created column                  chip (= Company Table Data property)
  ------------------------------- ------------------------------------
  Size                            Size
  Type                            Type
  LinkedIn URL                    LinkedIn            <- NOT "LinkedIn URL"
  Founded                         Founded
  Industry                        Industry
  Description                     Resolved Description  <- NOT "Description"
  Specialities                    Specialities
  Sub Industry                    Sub - industry      <- NOT "Sub Industry"
  Primary Offerings               Primary Offerings
  Business Stage                  Business Stage
  Annual Revenue                  Annual Revenue
  Total Funding Amount Range Usd  Total Funding Amount Range Usd

The Configure panel's input labels are the SOURCE table's column names, so
eleven inputs bind name-to-name onto the same-named property. Two do not:

  * the input labelled "Company Table Data" is the one that feeds the created
    "Sub Industry" column, so it takes the "Sub - industry" property. (The
    template's input carries that name because Clay names a template parameter
    after the column it was authored against; there is no property called
    "Company Table Data" to bind it to.)
  * the input labelled "domain" feeds no created column, so the reference build
    cannot say what it holds. Asked and answered by the user (2026-08-03):
    "Company Table Data > Company Domain" -- see DOMAIN_LEAF.

=== Why the viewport is made taller instead of scrolling the panel ===

Thirteen inputs do not fit the 980px viewport clay_page() opens: as opened, the
last three labels sit at y=975/1047/1119, below the fold and outside the y200-900
band apply_people_waterfall._BOX searches.

Scrolling each field into that band was tried first and produced silent
CROSS-CONTAMINATION, not clean failures: with the panel scrolling between the
measurement and the click, "Annual Revenue" came back already set to "Primary
Offerings" and the "Company Table Data" input ended up holding "Annual Revenue"
(dry run, ACHEMA Middle East, 2026-08-03). _BOX locates a field by finding its
label and taking the box 8-60px BELOW it, so a panel that moves mid-fill maps
the right value into the wrong field — and a mapping that is wrong rather than
missing is exactly what the EXPECTED check cannot be relied on to catch.

So the panel is not scrolled at all: headless has no screen to fit, so the
viewport is opened tall enough (PANEL_VIEWPORT) that all thirteen labels are on
screen at once, and _FIND_ROW is rebound with a y window to match. The picker's
own property list still virtualizes (10 of 35 rendered) — that scrolling stays
inside _click_step, which re-measures every attempt.

=== Why fields are located by POSITION, not by label text ===

apply_people_waterfall._BOX finds a field by matching its label text and then
reading the box 8-60px below it. That is safe for two fields; it is actively
dangerous for thirteen, because this template's input labels are the SAME
strings as the values being inserted ("Size" the input, "Size" the chip). Once
the upper fields hold chips, a search for a lower field's label can match an
upper field's chip instead, so clicks land on the wrong row and verification
reads a neighbour's chip.

That is not theoretical: the first run against ACHEMA Middle East / Sellers -
People reported all thirteen fields "ok" and saved, and Clay then showed the last
five columns (Sub Industry, Primary Offerings, Business Stage, Annual Revenue,
Total Funding Amount Range Usd) empty with status "success" — while the source
row (Name='Zeeco, Inc.') plainly had all five populated. Verification confirmed
chips that belonged to other fields.

So fields are enumerated POSITIONALLY instead: labels render at x=1270 and values
at x=1279, so the two are separable by x, and the thirteen labels sorted by y are
matched against PANEL_ORDER. Each field is then read and clicked inside its own y
window, bounded by the NEXT label — a chip can never be attributed to the wrong
field. The label list is re-read before every field, because inserting a chip can
reflow the panel.

Idempotent: MARKER_CANDIDATES is checked against Clay's real column list (WSL
CLI, fail-closed) before the template is opened, so a rerun never applies twice
-- re-applying is how duplicate column sets get created.

  python apply_cpj_people.py <wid> "<event>" "Sellers - People" --recon
  python apply_cpj_people.py <wid> "<event>" "Sellers - People" --dry-run
  python apply_cpj_people.py <wid> "<event>" "Sellers - People"          # applies + runs
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
os.environ.setdefault("CLAY_PEOPLE_TEMPLATE", "Qasim - Labs - CPJ - People - v2")

import clay_ui                            # noqa: E402
import browser_session                    # noqa: E402
import column_config as colcfg            # noqa: E402
import state_io                           # noqa: E402
import apply_people_waterfall as apw      # noqa: E402  (picker primitives)
import add_workemail_waterfall as panel   # noqa: E402  (save_column fallback)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PEOPLE_TABLES = ("Sellers - People", "Buyers - People")
# The Sponsors entity names its people tables differently (config/entity-types/
# sponsors.yaml: tables.seller_people/buyer_people). Same 27-column shape and the
# same "Company Table Data" lookup, so the pass works unchanged -- except that
# Sponsors_normalized has no "Country" column (it carries "Normalized Country"),
# which is what OPTIONAL_LABELS exists for.
SPONSORS_PEOPLE_TABLES = ("Sponsors - Sellers - People",
                          "Sponsors - Buyers - People")
ALL_PEOPLE_TABLES = PEOPLE_TABLES + SPONSORS_PEOPLE_TABLES
COLS_SCRIPT = os.path.join(SCRIPT_DIR, "check_table_columns.py")
STATE = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_CPJ_STATE", "cpj_people_state.json"))

# Proof the template is already on a table. "Sub Industry" leads because it is
# the one created name that cannot arrive any other way: the source column is
# spelled "Sub - industry", so an unhyphenated "Sub Industry" on a People table
# means this template ran. Presence of ANY of these counts as applied -- a
# partially applied table is reported, never silently re-applied over.
MARKER_CANDIDATES = ("Sub Industry", "Total Funding Amount Range Usd",
                     "Primary Offerings", "Business Stage")

GRP_X = apw.GRP_X       # (1290, 1345) groups and their immediate children
LEAF_X = apw.LEAF_X     # (1290, 1400) nested properties, indented further right

LOOKUP = "Company Table Data"

# Every input binds to a PROPERTY under the lookup, including the one confusingly
# labelled "Company Table Data" (it feeds the created "Sub Industry" column, so it
# takes "Sub - industry").
#
# A "take the whole column instead" variant was tried and is deliberately NOT kept:
# the five Claygent-derived columns were empty for a different reason entirely --
# the lookup's stored snapshot predated those columns being filled on the exhibitor
# table. Re-running "Company Table Data" ("Run column") fixed ACHEMA / Buyers -
# People outright (0% -> 50/50/50/100/100%, matching the user's reference) with the
# property binding below unchanged, which is what proves this is the right shape.
# See run_lookup_column.py; the rollout refreshes the lookup before applying.
WHOLE_LOOKUP_LABELS = set()

# The "Company Domain" input takes "Company Table Data > Company Domain" (user,
# 2026-08-03). A plain "Domain" property was asked for first but does not exist on
# these tables -- the exhibitor table carries Company Domain / Official Domain /
# Normalize a Domain / Website and nothing called Domain, which is why the pass
# aborted rather than binding something approximate.
DOMAIN_LEAF = os.environ.get("CLAY_CPJ_DOMAIN_LEAF", "Company Domain")

# Inputs whose pre-filled value must NOT be trusted, even when it looks right.
# v2 pre-fills "Company Domain" from the PEOPLE table's own same-named column,
# which renders an identical chip to the lookup property that is actually wanted --
# text cannot tell them apart, so this field is always re-bound. _click_leaf's
# anchor rule is what guarantees the nested (lookup) row rather than the flat one.
ALWAYS_REBIND = {"Company Domain"}

# NOTHING is optional: Clay keeps the template's Save button DISABLED until every
# input is filled. Measured on Automate Chicago / Sponsors - Sellers - People:
# 15 of 16 mapped -> _SAVE_BTN {'disabled': True}; the 16th filled -> False. An
# earlier attempt to treat "Country" as optional is exactly why all 41 Sponsors
# tables reported saved and created nothing. Leaving an input unmapped is fatal,
# so it is better to abort at the field than after a save that cannot work.
OPTIONAL_LABELS = set()

# Which property the "Country" input takes. Sponsors_normalized has no "Country"
# column at all -- only "Normalized Country" -- so the Sponsors fleet overrides
# this (CLAY_CPJ_COUNTRY_LEAF="Normalized Country"). Consequence worth knowing:
# the created Country column on Sponsors tables then carries the NORMALISED
# country, which is the value the ICP tiering uses anyway.
COUNTRY_LEAF = os.environ.get("CLAY_CPJ_COUNTRY_LEAF", "Country")

# panel input label -> the "Company Table Data" property it takes.
#
# v2 of the template (2026-08-03) renamed the third input from lowercase "domain"
# to "Company Domain" -- which independently confirms the user's answer that it
# takes the company record's Company Domain -- and added three inputs at the
# bottom: Side, Classification, Country. All three are columns on the exhibitor
# table, so they bind name-to-name like the rest.
LEAF_FOR_LABEL = {
    "Size": "Size",
    "Type": "Type",
    "Company Domain": DOMAIN_LEAF,
    "LinkedIn": "LinkedIn",
    "Founded": "Founded",
    "Industry": "Industry",
    "Resolved Description": "Resolved Description",
    "Specialities": "Specialities",
    # feeds the created "Sub Industry" column (recon_cpj_reference.py read this
    # off the user's reference build, and it populates once the lookup is fresh)
    "Company Table Data": "Sub - industry",
    "Primary Offerings": "Primary Offerings",
    "Business Stage": "Business Stage",
    "Annual Revenue": "Annual Revenue",
    "Total Funding Amount Range Usd": "Total Funding Amount Range Usd",
    # v2 additions. NOTE: the exhibitor table carries BOTH "Country" and
    # "Normalized Country"; the input is labelled "Country" and every other input
    # in this template is named after the source column it takes, so "Country" it
    # is. Flagged for the ICP owner rather than silently switched.
    "Side": "Side",
    "Classification": "Classification",
    "Country": COUNTRY_LEAF,
}

# MAPPING is kept in apply_people_waterfall's shape for the recon/verify helpers
# it lends us, but this pass does NOT use apw._fill's walk -- see _fill_field.
MAPPING = {label: {"steps": [(LOOKUP,) + GRP_X, (leaf,) + LEAF_X]}
           for label, leaf in LEAF_FOR_LABEL.items()}
EXPECTED = dict(LEAF_FOR_LABEL)

# Legitimate chip segments. Every leaf name must be here or _FIELD drops it when
# rebuilding the chip text and verification fails on a correct mapping; anything
# landing in the band that is not here is grid content showing through.
CHIP_VOCAB = [LOOKUP] + sorted(set(LEAF_FOR_LABEL.values())) + [
    "Normalized Country",
    "Company Domain", "Official Domain", "Name", "Full Name", "Job Title",
    "LinkedIn Profile", "Add row", "Lookup row", "Lookup in Audiences"]

apw.MAPPING = MAPPING
apw.EXPECTED = EXPECTED
apw.CHIP_VOCAB = CHIP_VOCAB
apw.MARKER = MARKER_CANDIDATES[0]

# Tall enough for all 13 labels (the lowest sits at y=1119 in a fresh panel)
# plus the picker overlay that opens under a field. Width is left at clay_page's
# 1720 because every x coordinate in the inherited picker geometry depends on it.
# Tall enough for all 16 labels (the lowest, "Country", sits at y=1335). Do NOT
# raise this further: at 2200 and 2600 Clay lays the panel out differently and even
# the FIRST field stops binding, so more room is not the fix for the last three
# v2 inputs failing -- see _bring_into_view.
PANEL_VIEWPORT = {"width": 1720,
                  "height": int(os.environ.get("CLAY_CPJ_VIEWPORT_H", "1900"))}
_Y_MAX = PANEL_VIEWPORT["height"] - 40

# apw._FIND_ROW hard-codes the 980px viewport's y window; _click_step reads it as
# a module global, so rebinding it here retargets that proven row-clicking code at
# the taller panel without editing apply_people_waterfall (whose own email pass
# still runs at 980). apw._BOX is deliberately NOT reused — this pass locates
# fields positionally, see _fields().
apw._FIND_ROW = apw._FIND_ROW.replace("r.y>1000", f"r.y>{_Y_MAX}")

# The thirteen inputs top-to-bottom, as the Configure panel renders them
# (recon_cpj_people_panel.py against ACHEMA Middle East, 2026-08-03: y=255 to
# y=1119, evenly spaced ~72px). Order is the identity of each field here, so a
# panel that does not match this list exactly is refused rather than guessed at.
PANEL_ORDER = ["Size", "Type", "Company Domain", "LinkedIn", "Founded",
               "Industry", "Resolved Description", "Specialities",
               "Company Table Data", "Primary Offerings", "Business Stage",
               "Annual Revenue", "Total Funding Amount Range Usd",
               "Side", "Classification", "Country"]

# Labels render at x=1270, their value boxes/chips at x=1279. That 9px is what
# separates "the field called Size" from "a field holding the value Size".
_LABEL_X = (1264, 1276)
_VALUE_X_MIN = 1277

# Every label-column leaf, sorted by y. Unbounded in y so an off-screen field is
# reported rather than silently treated as absent.
_LABELS = """([xmin, xmax])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.height===0||r.x<xmin||r.x>xmax||r.y<180) continue;
    const t=norm(el.textContent);
    if(t) out.push({t, y:Math.round(r.y)});
  }
  out.sort((a,b)=>a.y-b.y);
  return out;
}"""

# One field's state, scoped to ITS OWN y window (label..next label). Returns the
# box's click point when empty, so the caller never computes coordinates from a
# label offset.
#
# `segments` is every vocab-matching part in x order with NO de-duplication. The
# inherited apply_people_waterfall version collapsed consecutive duplicates, which
# rendered the over-deep reference 'Sub - industry > Sub - industry' as a
# perfectly healthy-looking 'Sub - industry' -- that collapse is precisely what
# let two tables save with five silently empty columns. Depth is now visible here,
# in the one panel where it has to be checked BEFORE saving (the Configure panel
# carries no data-id; only the Edit-column panel does).
_FIELD = """([y0, y1, xmin, vocab])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let box=null; const parts=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<xmin||r.x>1700||r.y<=y0||r.y>=y1) continue;
    const t=norm(el.textContent);
    if(!t) continue;
    if(t.includes('Start typing')){
      box={cx:Math.round(r.x+30), cy:Math.round(r.y+r.height/2)};
      continue;
    }
    if(vocab.includes(t)) parts.push({t, x:r.x, cy:r.y+r.height/2});
  }
  if(box) return {empty:true, cx:box.cx, cy:box.cy};
  parts.sort((a,b)=>a.x-b.x);
  const segs=parts.map(p=>p.t);
  const collapsed=[];
  for(const s of segs) if(!collapsed.length||collapsed[collapsed.length-1]!==s)
    collapsed.push(s);
  // A click point is returned for a FILLED field too, so a value that arrived
  // pre-filled with the wrong column can be re-bound rather than accepted.
  const first=parts.length?parts[0]:null;
  return {empty:false, chip:collapsed.join(' > ') || null, segments:segs,
          cx:first?Math.round(first.x+20):null,
          cy:first?Math.round(first.cy):null};
}"""


def _fields(page, say):
    """The panel's VISIBLE fields as [{label, y0, y1}], or None if what is visible
    is not a run of this template's inputs.

    Visible-only, not all sixteen: the panel is taller than any workable viewport
    (raising the viewport past ~1900 makes Clay lay it out differently and even the
    first field stops binding), so the last inputs are reachable only by scrolling
    -- and a field whose picker has no room below it does not bind, which is how
    Side / Classification / Country failed on every table while the first thirteen
    were fine.

    Identity still comes from the label text (labels at x=1270 are separable from
    chips at x=1279), and the visible labels must be a SUBSEQUENCE of PANEL_ORDER
    -- order preserved, no repeats -- so a scrolled panel is accepted while an
    unrecognised one is still refused.

    Re-read before every field: inserting a chip can reflow the rows below it.
    """
    def labelled():
        got = page.evaluate(_LABELS, list(_LABEL_X))
        return got if got and any(r["t"] == PANEL_ORDER[0] for r in got) else None

    rows = _poll(page, labelled, timeout_ms=6000, interval_ms=200)
    if rows is None:
        rows = page.evaluate(_LABELS, list(_LABEL_X))
    labels = []
    for r in rows or []:
        if (r["t"] in LEAF_FOR_LABEL and r["y"] <= _Y_MAX
                and (not labels or labels[-1]["t"] != r["t"])):
            labels.append(r)
    got = [r["t"] for r in labels]
    # subsequence check: same relative order as PANEL_ORDER, no repeats
    it = iter(PANEL_ORDER)
    if len(set(got)) != len(got) or not all(g in it for g in got):
        say(f"  !! panel fields {got} are not a run of {PANEL_ORDER}")
        return None
    out = []
    for i, r in enumerate(labels):
        y1 = labels[i + 1]["y"] if i + 1 < len(labels) else r["y"] + 72
        out.append({"label": r["t"], "y0": r["y"], "y1": y1})
    return out


def _bring_into_view(page, label, say, lo=300, hi=760, tries=14):
    """Scroll the Configure panel until `label` sits in the upper band, so its
    picker has room to render BELOW it. Returns the fresh field list, or None.

    Every caller re-reads coordinates after this, and every field is verified
    individually, so scrolling cannot silently map a value into a neighbour (the
    failure mode an earlier scroll-based version had, before fields were located
    by label and read inside their own y window).
    """
    for _ in range(tries):
        fs = _fields(page, _quiet)
        if fs is not None:
            hit = next((f for f in fs if f["label"] == label), None)
            if hit and lo <= hit["y0"] <= hi:
                return fs
            if hit is None:
                # not rendered yet: it is below if PANEL_ORDER puts it after what
                # we can see, otherwise above.
                visible = [f["label"] for f in fs]
                after = (visible and PANEL_ORDER.index(label)
                         > PANEL_ORDER.index(visible[-1]))
                delta = 300 if after else -300
            else:
                delta = 300 if hit["y0"] > hi else -300
            page.mouse.move(1350, 600)
            page.mouse.wheel(0, delta)
        else:
            page.mouse.move(1350, 600)
            page.mouse.wheel(0, 300)
        page.wait_for_timeout(600)
    fs = _fields(page, say)
    if fs is not None and any(f["label"] == label for f in fs):
        return fs
    say(f"  {label}: could not bring into view")
    return None


def _quiet(_msg):
    """Swallow progress chatter from helpers called inside a poll loop."""


def _poll(page, fn, timeout_ms=9000, interval_ms=250):
    """Wait until fn() returns something truthy; return it, or None on timeout.

    Replaces the fixed sleeps this pass started with. A flat wait_for_timeout
    pays the worst case on every single one of 16 fields per table; polling
    returns as soon as Clay has rendered, while still granting a slow render the
    full budget. Strictly safer than a shorter fixed wait: nothing proceeds until
    the thing it needs is actually there.
    """
    got = fn()
    if got:
        return got
    waited = 0
    while waited < timeout_ms:
        page.wait_for_timeout(interval_ms)
        waited += interval_ms
        got = fn()
        if got:
            return got
    return None


def _click_group(page):
    """Open the lookup group in the picker, waiting for its property list."""
    pt = _poll(page, lambda: page.evaluate(apw._FIND_ROW, [LOOKUP, *GRP_X]))
    if not pt:
        return False
    page.mouse.click(pt["x"], pt["y"])
    # Proceed as soon as an anchor row (the group itself / "Insert all
    # properties") is present, which is what _click_leaf measures against.
    _poll(page, lambda: (page.evaluate(_ROWS, ["", _ANCHORS]) or {}).get("anchors"))
    return True


def _field_state(page, f):
    try:
        return page.evaluate(_FIELD, [f["y0"], f["y1"], _VALUE_X_MIN, CHIP_VOCAB])
    except Exception:
        return None


# The chip's data-id is the ONLY place the resolved reference path is visible.
# The chip's TEXT is identical whether the reference is right or one level too
# deep, which is how a whole table saved with five silently-empty columns.
_PATH = """([y0, y1])=>{
  const out=[];
  for(const el of document.querySelectorAll('[data-id]')){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240||r.x>1700||r.y<=y0||r.y>=y1) continue;
    out.push(el.getAttribute('data-id'));
  }
  return out;
}"""


def _field_path(page, f):
    """The reference path bound to this field, e.g. 'f_0ti….Sub - industry'."""
    try:
        got = page.evaluate(_PATH, [f["y0"], f["y1"]])
    except Exception:
        return None
    return got[0] if got else None


def _depth_ok(state, leaf, whole_lookup=False):
    """True if the field holds exactly the intended reference depth.

    The Configure panel exposes no reference path, so depth is judged from the
    raw chip segments: a correct property binding shows the property once, the
    over-deep one shows it twice ('Sub - industry > Sub - industry').

    `whole_lookup` fields must hold the bare lookup column and NOTHING under it,
    which is the distinction that decides whether five columns populate or sit
    empty.
    """
    if not state or state.get("empty"):
        return False
    segs = state.get("segments") or []
    if whole_lookup:
        return segs.count(LOOKUP) == 1 and len(segs) == 1
    return segs.count(leaf) == 1


def _path_ok(path, leaf):
    """A correct binding is <lookup field id> + exactly ONE property segment.

    Verified against the user's reference build (ACHEMA / Sellers - People):
      works: 'f_0aaaaaaaaaaaaaaaaaa.Sub - industry'
      empty: 'f_0bbbbbbbbbbbbbbbbbb.Sub - industry.Sub - industry'
    The second resolves to nothing and still reports status "success", so this is
    the check that has to hold before anything is saved. No property name in
    LEAF_FOR_LABEL contains a '.', so splitting on '.' is unambiguous.
    """
    if not path:
        return False
    parts = path.split(".")
    return len(parts) == 2 and parts[0].startswith("f_") and parts[1] == leaf


# All rows in the picker whose text is exactly `txt`, plus the y of the anchor
# rows that mark where the expanded group's own properties start. Returning every
# candidate (rather than the first, as _FIND_ROW does) is the whole point: the
# lookup's property and the People table's identically-named flat column both
# match, 3px apart in x, and picking the wrong one binds the wrong data.
_ROWS = """([txt, anchors])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out={rows:[], anchors:{}};
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0) continue;
    const t=norm(el.textContent);
    if(!t) continue;
    if(t===txt && r.x>=1320 && r.x<=1470)
      out.rows.push({x:Math.round(r.x+15), y:Math.round(r.y+r.height/2),
                     top:Math.round(r.y)});
    if(anchors.includes(t) && r.x>=1295 && r.x<=1460)
      out.anchors[t]=Math.round(r.y);
  }
  out.rows.sort((a,b)=>a.top-b.top);
  return out;
}"""

# Rows that sit immediately above an expanded group's properties. "Insert all
# properties" is the tightest anchor (the first property renders one row below
# it); the group row itself is the fallback when the filter hides that option.
_ANCHORS = ["Insert all properties", "Insert all 35 properties", LOOKUP]


def _click_leaf(page, leaf, say, tries=14):
    """Click the lookup's `leaf` property, never a same-named flat column.

    Chooses the first exact-text row BELOW the expanded group's anchor, which is
    where the nested properties render; the People table's own column of the same
    name sorts after them. x cannot decide this (1334 nested vs 1331 flat).

    Scrolls to find the row rather than narrowing the list by typing. Typing a
    filter DID render the row -- and clicking the filtered row produced a
    reference one level too deep ('….Sub - industry.Sub - industry') that resolves
    to empty, for the five custom (Claygent-authored) properties. Scrolling keeps
    the unfiltered list, which is how the eight canonical properties bound
    correctly; _path_ok is what proves it either way.
    """
    def candidates():
        got = page.evaluate(_ROWS, [leaf, _ANCHORS])
        anchor = None
        for name in _ANCHORS:
            if name in got["anchors"]:
                anchor = got["anchors"][name]
                break
        rows = [r for r in got["rows"] if anchor is None or r["top"] > anchor]
        return (rows, anchor, len(got["rows"])) if rows else None

    for attempt in range(tries):
        # Poll briefly for the row to render before resorting to a scroll: the
        # filtered list usually appears within a few hundred ms.
        hit = _poll(page, candidates, timeout_ms=1500, interval_ms=200)
        if hit:
            rows, anchor, total = hit
            if total > 1:
                say(f"    ({leaf!r}: {total} rows matched, taking "
                    f"y={rows[0]['top']} below anchor {anchor})")
            page.mouse.click(rows[0]["x"], rows[0]["y"])
            return True
        # The property list virtualizes (~10 of 35 rendered), so most leaves only
        # exist in the DOM once scrolled to.
        page.mouse.move(1360, 700)
        page.mouse.wheel(0, 260)
    return False


def _pick(fields, label):
    return next((f for f in (fields or []) if f["label"] == label), None)


def _fill_field(page, label, say, scroll=False):
    """Map the input at position `index`: open its picker, expand the lookup,
    filter, click the leaf, then verify inside that field's own y window.

    The filter goes in AFTER the group is expanded, which is the opposite of
    apply_people_waterfall._fill's order: filtering first reports "No properties"
    because the search does not descend into an unexpanded column's schema.
    Filtering after expanding does descend -- and it is the only way to reach the
    25 of 35 properties the picker's virtualized list never renders (it shows
    ~10, ordered by name length, which is why only "Size" and "Type" worked
    before).
    """
    leaf = LEAF_FOR_LABEL[label]
    # Unscrolled by default: moving the panel is itself harmful (with the panel
    # scrolled even "Size" stops binding), so scrolling is a RETRY for a field
    # that failed in place -- which is the only way the last inputs, whose picker
    # has no room below them, can be reached.
    fields = _bring_into_view(page, label, say) if scroll else _fields(page, say)
    if fields is None or _pick(fields, label) is None:
        return "panel_mismatch"
    f = _pick(fields, label)
    st = _field_state(page, f)
    if not st:
        say(f"  {label}: field not readable")
        return "missing"
    if not st.get("empty"):
        if (label not in ALWAYS_REBIND
                and _depth_ok(st, leaf, label in WHOLE_LOOKUP_LABELS)):
            say(f"  {label}: already set to {st.get('chip')!r}")
            return "prefilled"
        # v2 arrives with "Company Domain" pre-filled from the People table's OWN
        # same-named column, which is not what it must hold. Accepting a pre-filled
        # value on trust is how the wrong column gets shipped, so re-bind it.
        say(f"  {label}: pre-filled with {st.get('chip')!r}, rebinding to {leaf!r}")
        if st.get("cx") is None:
            say(f"  {label}: no click point on the filled value — cannot rebind")
            return "failed"

    page.mouse.click(st["cx"], st["cy"])
    # Wait for the picker to actually offer the lookup rather than sleeping.
    if not _poll(page, lambda: page.evaluate(apw._FIND_ROW, [LOOKUP, *GRP_X])):
        say(f"  {label}: picker did not open on {LOOKUP!r}")
        return "failed"
    # The row being in the DOM is not the same as the picker being ready for
    # keystrokes. Polling alone typed the filter before its search input had
    # focus, so the text went nowhere and the whole field failed -- the one place
    # a small fixed settle is genuinely needed.
    page.wait_for_timeout(700)

    if label in WHOLE_LOOKUP_LABELS:
        # Select the column ITSELF, without expanding it. Clicking the row only
        # expands (verified: click-then-Escape left the field empty), so the pick
        # is made from the keyboard — filter to the column, then Enter takes the
        # highlighted row as the value instead of drilling into it.
        say(f"  {label}: taking the whole {LOOKUP!r} column, not drilling in")
        page.keyboard.type(LOOKUP, delay=40)
        page.wait_for_timeout(2500)
        page.keyboard.press("Enter")
        page.wait_for_timeout(2000)
    else:
        if not _click_group(page):
            say(f"  {label}: {LOOKUP!r} group not found")
            return "failed"
        # Same reason: let the expanded list settle and keep focus in the search
        # box before typing the property name.
        page.wait_for_timeout(1300)
        page.keyboard.type(leaf, delay=30)
        page.wait_for_timeout(1200)
        if not _click_leaf(page, leaf, say):
            say(f"  {label}: property {leaf!r} not found under {LOOKUP!r}")
            return "failed"

    # Wait for the value to land in THIS field rather than sleeping a fixed
    # interval; the re-read also picks up any reflow the new chip caused.
    want_whole = label in WHOLE_LOOKUP_LABELS

    def settled():
        fs = _fields(page, _quiet)
        g = _pick(fs, label)
        if g is None:
            return None
        s = _field_state(page, g)
        # Two conditions, both learned the hard way:
        #
        # 1. the value must actually BE the wanted property, not merely present.
        #    "non-empty" returns instantly on a REBIND (the field starts non-empty
        #    holding the old value), so the next field's click landed in this
        #    field's still-open picker -- observed live: "Company Domain" became
        #    'Company Domain > Type' and Type was left empty.
        # 2. the picker must have closed, which is the direct precondition for the
        #    next field's click landing where it is aimed. The panel's own label
        #    sits at x=1270, outside GRP_X, so this only sees picker rows.
        if not _depth_ok(s, leaf, want_whole):
            return None
        if page.evaluate(apw._FIND_ROW, [LOOKUP, *GRP_X]) is not None:
            return None
        return (fs, s)

    got = _poll(page, settled)
    fields = _fields(page, say) if got is None else got[0]
    f = _pick(fields, label)
    if f is None:
        return "panel_mismatch"
    state = _field_state(page, f) or {}
    got = state.get("chip")
    if not _depth_ok(state, leaf, label in WHOLE_LOOKUP_LABELS):
        # Collapsed chip text can read correctly while the reference is one level
        # too deep; the raw segments are what expose it.
        say(f"  {label}: BAD DEPTH segments={state.get('segments')!r} "
            f"(wanted {leaf!r} exactly once)")
        return "bad_depth"
    if got and leaf in got:
        say(f"  {label} = {got!r}")
        return "ok"
    say(f"  {label}: ended as {got!r} (wanted {leaf!r})")
    return "failed"


def _wsl(script, *args, timeout=180):
    path = script.replace("C:\\", "/mnt/c/").replace("\\", "/")
    out = subprocess.run(["wsl", "-d", "Ubuntu", "--", "python3", path, *args],
                         capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    return (out.stdout or "").strip()


ROWS_SCRIPT = os.path.join(SCRIPT_DIR, "check_table_rows.py")


def table_rows(table_id, say):
    """Row count from Clay, or None if unreadable.

    None is NOT zero: check_table_rows prints no number on failure precisely so a
    CLI hiccup cannot be read as "empty" and retire a table (audit C4).
    """
    try:
        out = _wsl(ROWS_SCRIPT, table_id).strip().splitlines()
    except Exception as e:
        say(f"    !! row count failed: {str(e)[:70]}")
        return None
    for line in reversed(out):
        t = line.strip().replace(",", "")
        if t.isdigit():
            return int(t)
    return None


def table_columns(table_id, say):
    """Column names straight from Clay. Empty list means 'could not tell'."""
    try:
        return _wsl(COLS_SCRIPT, table_id).splitlines()
    except Exception as e:
        say(f"    !! column check failed: {str(e)[:70]}")
        return []


def already_applied(table_id, table, say):
    """True if applied, False if not, None if unreadable.

    Fail-closed: an unreadable column list returns None and the caller refuses
    to touch the table. Guessing 'not applied' re-applies the template and
    leaves a duplicate column set behind.
    """
    if not table_id:
        say(f"  !! no table_id for {table} — refusing to touch it")
        return None
    cols = table_columns(table_id, say)
    if not cols:
        say(f"  !! cannot read columns for {table} — refusing to touch it")
        return None
    hit = [c for c in MARKER_CANDIDATES if c in cols]
    if hit and len(hit) < len(MARKER_CANDIDATES):
        say(f"  {table}: PARTIAL — has {hit}, missing "
            f"{[c for c in MARKER_CANDIDATES if c not in cols]}")
    else:
        say(f"  {table}: {len(cols)} columns, marker present={hit or False}")
    if LOOKUP not in cols:
        say(f"  !! {table} has no {LOOKUP!r} column — nothing to map from")
        return None
    return bool(hit)


def _verify(page, say):
    """Chip text per field, each read inside its own y window so a value can
    never be attributed to a neighbouring field (see module docstring)."""
    base = _fields(page, say)
    out = {}
    for label in PANEL_ORDER:
        f = _pick(base, label)
        if f is None:                      # only scroll for what is off-screen
            f = _pick(_bring_into_view(page, label, _quiet), label)
        st = _field_state(page, f) if f else None
        out[label] = None if not st else (
            "<empty>" if st.get("empty") else st.get("chip"))
    return out


def _verify_depths(page, say):
    """Raw chip segments per field — the check that actually proves a binding,
    since collapsed chip text reads the same for a correct and an over-deep one.

    Used instead of _field_path for the pre-save gate: the Configure panel
    carries no data-id, so segment depth is the only pre-save signal. _path_ok
    still applies AFTER saving, against the Edit-column panel.
    """
    base = _fields(page, say)
    out = {}
    for label in PANEL_ORDER:
        f = _pick(base, label)
        if f is None:                      # only scroll for what is off-screen
            f = _pick(_bring_into_view(page, label, _quiet), label)
        out[label] = (_field_state(page, f) or {}).get("segments") if f else None
    return out


def apply_to_table(page, wid, name, table, say, dry_run=False, recon=False,
                   run_after=True, already_open=False):
    """Apply the template to one People table.

    `already_open` lets the rollout do both People tables of a workbook on one
    load (audit E8, tests/test_workbook_navigation_reuse.py) — only pass it when
    the caller can vouch that the page is still on this workbook.
    """
    res = {"workbook_id": wid, "workbook_name": name, "table": table,
           "at": datetime.datetime.now().isoformat(timespec="seconds")}
    if not already_open:
        clay_ui.open_workbook_by_id(page, wid)
    if not colcfg.table_exists(page, table):
        say(f"SKIP {name}/{table}: table does not exist")
        return {**res, "status": "no_table"}
    colcfg.focus_table_maybe_empty(page, table)
    page.wait_for_timeout(1000)

    apw._open_template_retry(page)
    say(f"  template: {apw.TEMPLATE_USED!r}")

    if recon:
        fields = _verify(page, say)
        say(f"  fields: {json.dumps(fields, ensure_ascii=False)}")
        page.keyboard.press("Escape")
        return {**res, "status": "recon", "fields": fields}

    statuses = {}
    for label in PANEL_ORDER:
        st = _fill_field(page, label, say)
        if st not in ("ok", "prefilled"):
            say(f"  {label}: retrying with the panel scrolled")
            st = _fill_field(page, label, say, scroll=True)
        statuses[label] = st
        if st not in ("ok", "prefilled"):
            if label in OPTIONAL_LABELS:
                say(f"  {label}: optional — leaving unmapped ({st}) and going on")
                continue
            # Stop on the field that went wrong rather than carrying on: a
            # misplaced click corrupts its neighbour, so continuing turns one bad
            # field into several and makes the cause unreadable.
            say(f"  stopping after {label!r}: {st}")
            break

    final = _verify(page, say)
    say(f"  mapped: {json.dumps(final, ensure_ascii=False)}")

    if final is None:
        say(f"ABORT {name}/{table}: panel did not match this template")
        page.keyboard.press("Escape")
        return {**res, "status": "aborted", "reason": "panel_mismatch",
                "statuses": statuses}

    bad = {k: v for k, v in final.items()
           if k not in OPTIONAL_LABELS and not (v and EXPECTED[k] in v)}
    if bad:
        say(f"ABORT {name}/{table}: mapping incomplete {bad}")
        page.keyboard.press("Escape")
        return {**res, "status": "aborted", "reason": "mapping_incomplete",
                "fields": final, "statuses": statuses}

    # The real gate. Chip text passing is not proof: the first two tables saved
    # with all thirteen chips reading correctly and five references one level too
    # deep, which Clay resolves to empty while reporting status "success".
    depths = _verify_depths(page, say)
    say(f"  segments: {json.dumps(depths, ensure_ascii=False)}")
    bad_depth = {}
    for k, leaf in LEAF_FOR_LABEL.items():
        if k in OPTIONAL_LABELS:
            continue          # unmapped-by-design, see OPTIONAL_LABELS
        segs = (depths or {}).get(k) or []
        want_whole = k in WHOLE_LOOKUP_LABELS
        if not _depth_ok({"segments": segs}, leaf, want_whole):
            bad_depth[k] = segs
    if bad_depth:
        say(f"ABORT {name}/{table}: reference depth wrong {bad_depth}")
        page.keyboard.press("Escape")
        return {**res, "status": "aborted", "reason": "bad_depth",
                "fields": final, "segments": depths, "statuses": statuses}

    if dry_run:
        say(f"DRYRUN {name}/{table}: mapping complete, not saving")
        page.keyboard.press("Escape")
        return {**res, "status": "dryrun", "fields": final,
                "statuses": statuses}

    # The Save split button sits at the BOTTOM of a 16-field panel (y=1797 in a
    # 1900px viewport), so its dropdown opens below the fold and no option is ever
    # visible -- which is exactly how all 41 Sponsors tables "saved" while creating
    # nothing. Field geometry no longer matters at this point, so the viewport is
    # grown just for the save; the panel is not scrolled (that breaks binding).
    try:
        page.set_viewport_size({"width": PANEL_VIEWPORT["width"],
                                "height": PANEL_VIEWPORT["height"] + 700})
        page.wait_for_timeout(1200)
    except Exception:
        pass

    opts = ([r"Save and run.*in this view", r"Save and run"] if run_after
            else [r"Save and don'?t run"])
    saved = None
    for opt in opts:
        try:
            colcfg.save_via_menu(page, opt)
            saved = "run" if run_after else "no_run"
            break
        except Exception as e:
            say(f"  save_via_menu({opt!r}): {str(e)[:70]}")
    if saved is None:
        # Same fallback the email pass uses: the commit control differs by panel
        # (template = split button, Edit-column = plain Save).
        try:
            saved = panel.save_column(page, say)
        except Exception as e:
            saved = f"save_failed:{str(e)[:40]}"
    say(f"  saved via {saved}")
    page.wait_for_timeout(8000)

    # The marker column appearing is the ONLY proof the template was applied. A
    # save that silently did nothing used to be recorded as "ok" -- 41 tables were
    # reported applied while still carrying their original 27 columns.
    # find_header_scrolling, NOT clay_ui._find_header_rect: the marker column is
    # created at the far right and _find_header_rect only sees the viewport. In the
    # Exhibitors run 18 of 138 tables recorded sig_visible=False and were later
    # verified perfectly fine, so gating on the non-scrolling check would reject
    # correct work.
    confirmed = False
    try:
        confirmed = panel.find_header_scrolling(
            page, MARKER_CANDIDATES[0]) is not None
    except Exception as e:
        say(f"  marker check failed: {str(e)[:70]}")
    try:
        page.set_viewport_size(PANEL_VIEWPORT)
    except Exception:
        pass
    if not confirmed:
        say(f"FAILED {name}/{table}: saved via {saved!r} but "
            f"{MARKER_CANDIDATES[0]!r} never appeared — nothing was applied")
        return {**res, "status": "save_failed", "saved": saved,
                "fields": final, "statuses": statuses, "sig_visible": False,
                "template": apw.TEMPLATE_USED}
    return {**res, "status": "ok", "saved": saved, "fields": final,
            "statuses": statuses, "sig_visible": True,
            "template": apw.TEMPLATE_USED}


def _record(key, res):
    state = state_io.load_json(STATE, {})
    state[key] = res
    state_io.save_json(STATE, state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("table", nargs="?", default="Sellers - People",
                    choices=list(ALL_PEOPLE_TABLES))
    ap.add_argument("--table-id", help="for the pre-apply marker check (WSL CLI)")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-run", action="store_true",
                    help="save without running (the columns stay empty)")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    def say(m):
        print(m, flush=True)

    if not (a.recon or a.dry_run):
        applied = already_applied(a.table_id, a.table, say)
        if applied is None:
            raise SystemExit("refusing to touch an unreadable table")
        if applied:
            say(f"SKIP {a.workbook_name}/{a.table}: already applied")
            return

    with browser_session.clay_page(headless=not a.headed) as page:
        page.set_viewport_size(PANEL_VIEWPORT)
        out = apply_to_table(page, a.workbook_id, a.workbook_name, a.table, say,
                             dry_run=a.dry_run, recon=a.recon,
                             run_after=not a.no_run)
    if out.get("status") in ("ok", "aborted", "no_table"):
        _record(f"{a.workbook_name}|{a.table}", out)
    print("\nRESULT:", json.dumps(out, indent=1, ensure_ascii=False,
                                  default=str))


if __name__ == "__main__":
    main()
