"""Two-table passes should load the workbook once, not once per table
(audit E8) — but only when the caller can vouch that the page is still on
that workbook."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "automation",
                                "cleanup"))

import apply_gsheet_lookup as gsheet  # noqa: E402


class FakePage:
    def wait_for_timeout(self, ms):
        pass


def _patch(monkeypatch, navs):
    monkeypatch.setattr(gsheet.clay_ui, "open_workbook_by_id",
                        lambda page, wid: navs.append(wid))
    # Table missing -> apply_gsheet returns immediately after the nav decision,
    # which is exactly the branch under test.
    monkeypatch.setattr(gsheet.colcfg, "table_exists",
                        lambda page, table: False)


ENTRY = {"workbook_id": "wb_1", "workbook_name": "Testfair"}


def test_navigates_by_default(monkeypatch):
    navs = []
    _patch(monkeypatch, navs)
    gsheet.apply_gsheet(FakePage(), ENTRY, "Sellers - People", True,
                        lambda m: None)
    assert navs == ["wb_1"]


def test_skips_navigation_when_already_open(monkeypatch):
    navs = []
    _patch(monkeypatch, navs)
    gsheet.apply_gsheet(FakePage(), ENTRY, "Buyers - People", True,
                        lambda m: None, already_open=True)
    assert navs == []


def test_two_tables_cost_one_navigation(monkeypatch):
    """What the rollout does: first table navigates, second reuses."""
    navs = []
    _patch(monkeypatch, navs)
    page, wb_open = FakePage(), False
    for table in ("Sellers - People", "Buyers - People"):
        gsheet.apply_gsheet(page, ENTRY, table, True, lambda m: None,
                            already_open=wb_open)
        wb_open = True
    assert navs == ["wb_1"]
