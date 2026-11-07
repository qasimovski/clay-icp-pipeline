"""The CPJ People pass must bind the LOOKUP's property, never the People
table's identically-named flat column.

Both render in the picker 3px apart in x (nested 1334, flat 1331), so x cannot
decide it; the nested one is distinguished only by sorting below the expanded
group's anchor row. Getting this wrong does not fail loudly — it writes a
plausible-looking wrong value into every row of 143 tables (e.g. the People
table's own "Company Domain" instead of the company record's), which is why the
selection rule is pinned here rather than left to a live --recon.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "automation",
                                "cleanup"))

import apply_cpj_people as app  # noqa: E402


class FakeMouse:
    def __init__(self):
        self.clicks = []
        self.wheels = []

    def click(self, x, y):
        self.clicks.append((x, y))

    def move(self, x, y):
        pass

    def wheel(self, dx, dy):
        """_click_leaf scrolls to reach the ~25 of 35 properties the picker
        virtualizes away; recorded so a test can assert it scrolled rather than
        giving up."""
        self.wheels.append((dx, dy))


class FakePage:
    """Returns one canned _ROWS payload; records what got clicked."""

    def __init__(self, rows, anchors):
        self._payload = {"rows": rows, "anchors": anchors}
        self.mouse = FakeMouse()

    def evaluate(self, js, arg=None):
        return self._payload

    def wait_for_timeout(self, ms):
        pass


def _row(y, x=1334):
    return {"x": x + 15, "y": y + 8, "top": y}


def test_prefers_nested_property_over_flat_column():
    """Observed live: nested 'Company Domain' at y=535, the table's own flat
    column at y=571, 'Insert all properties' anchor at y=499."""
    page = FakePage(rows=[_row(535), _row(571, x=1331)],
                    anchors={"Insert all properties": 499,
                             app.LOOKUP: 463})
    assert app._click_leaf(page, "Company Domain", lambda m: None) is True
    assert page.mouse.clicks == [(1349, 543)]


def test_ignores_rows_above_the_anchor():
    """A flat column rendered between the group row and 'Insert all properties'
    must not be chosen just because it sorts first."""
    page = FakePage(rows=[_row(470, x=1331), _row(535)],
                    anchors={"Insert all properties": 499,
                             app.LOOKUP: 463})
    app._click_leaf(page, "Company Domain", lambda m: None)
    assert page.mouse.clicks == [(1349, 543)]


def test_fails_closed_when_only_a_flat_column_matches():
    """No property under the group (filter rendered nothing) — the pass must
    report failure, not fall back to the same-named flat column."""
    page = FakePage(rows=[_row(470, x=1331)],
                    anchors={"Insert all properties": 499, app.LOOKUP: 463})
    assert app._click_leaf(page, "Company Domain", lambda m: None,
                           tries=2) is False
    assert page.mouse.clicks == []
    assert page.mouse.wheels, "should have scrolled before giving up"


def test_whole_lookup_field_must_not_be_drilled_into():
    """The lookup-named input holds the bare column. One level deeper and Clay
    resolves '<lookup>.X.X', which is empty-but-successful — the defect that
    emptied five columns on two tables."""
    assert app._depth_ok({"segments": [app.LOOKUP]}, app.LOOKUP, True)
    assert not app._depth_ok(
        {"segments": [app.LOOKUP, "Sub - industry"]}, app.LOOKUP, True)
    assert not app._depth_ok({"segments": []}, app.LOOKUP, True)
    assert not app._depth_ok({"empty": True}, app.LOOKUP, True)


def test_path_gate_rejects_an_over_deep_reference():
    """The defect that silently emptied five columns on two tables: chip text
    reads correctly while the reference is one property level too deep."""
    assert app._path_ok("f_0aaaaaaaaaaaaaaaaaa.Sub - industry", "Sub - industry")
    assert not app._path_ok(
        "f_0bbbbbbbbbbbbbbbbbb.Sub - industry.Sub - industry", "Sub - industry")
    assert not app._path_ok(None, "Size")
    assert not app._path_ok("f_x.Size", "Type")          # right shape, wrong leaf
    assert not app._path_ok("Size", "Size")              # no lookup field id


def test_falls_back_to_group_row_when_insert_all_is_absent():
    """Filtering can hide 'Insert all properties'; the group row is then the
    anchor, and a property below it is still selectable."""
    page = FakePage(rows=[_row(500)], anchors={app.LOOKUP: 463})
    assert app._click_leaf(page, "Sub - industry", lambda m: None) is True
    assert page.mouse.clicks == [(1349, 508)]


def test_no_anchor_at_all_still_clicks_the_only_match():
    """Degenerate case: if neither anchor rendered, a single match is taken
    rather than stalling the whole fleet."""
    page = FakePage(rows=[_row(600)], anchors={})
    assert app._click_leaf(page, "Founded", lambda m: None) is True
    assert page.mouse.clicks == [(1349, 608)]


def test_every_mapped_label_targets_a_company_table_property():
    """The mapping is the pass's contract: 13 inputs, each bound under the
    lookup, and the two non-obvious ones pinned (they were read off the user's
    reference build, not guessed)."""
    # v2 of the template: 16 inputs (Side / Classification / Country added).
    assert len(app.LEAF_FOR_LABEL) == 16
    # Every input binds to a PROPERTY under the lookup — the five Claygent-derived
    # columns were empty because the lookup's snapshot was stale, not because this
    # one needed the bare column (see run_lookup_column.py).
    assert app.LEAF_FOR_LABEL["Company Table Data"] == "Sub - industry"
    assert app.WHOLE_LOOKUP_LABELS == set()
    assert app.LEAF_FOR_LABEL["Resolved Description"] == "Resolved Description"
    assert app.LEAF_FOR_LABEL["LinkedIn"] == "LinkedIn"
    # Pre-filled by v2 from the People table's OWN same-named column, so it is
    # always re-bound to the lookup's property — identical chip text, different
    # source.
    assert app.LEAF_FOR_LABEL["Company Domain"] == "Company Domain"
    assert "Company Domain" in app.ALWAYS_REBIND
    for new in ("Side", "Classification", "Country"):
        assert app.LEAF_FOR_LABEL[new] == new
    # every leaf must be in CHIP_VOCAB or verification rejects a correct mapping
    for leaf in app.LEAF_FOR_LABEL.values():
        assert leaf in app.CHIP_VOCAB, leaf


def test_panel_order_covers_exactly_the_mapped_labels():
    """PANEL_ORDER is the field identity — fields are filled and verified by
    position, so a label in one and not the other would fill the wrong row."""
    assert app.PANEL_ORDER == list(app.LEAF_FOR_LABEL)
    assert len(set(app.PANEL_ORDER)) == len(app.PANEL_ORDER)


def test_label_and_value_x_bands_do_not_overlap():
    """Labels (x=1270) and their chips (x=1279) are told apart by x alone; an
    overlap here is what let a chip be mistaken for a label."""
    assert app._LABEL_X[1] < app._VALUE_X_MIN
    assert app._LABEL_X[0] <= 1270 <= app._LABEL_X[1]
    assert app._VALUE_X_MIN <= 1279
