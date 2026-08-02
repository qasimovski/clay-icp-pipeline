"""The already-run guard on the fleet's only credit-spending batch trigger
(run_all_columns.run_v1). Audit C1: with no guard, a lost state file meant
'Run N rows' re-fired the whole paid template across every workbook.

UI calls are monkeypatched; the guard decisions themselves are browser-free."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "automation", "cleanup"))

import run_all_columns  # noqa: E402


class FakePage:
    def wait_for_timeout(self, ms):
        pass


ENTRY = {"workbook_id": "wb_x", "workbook_name": "Testfair"}


def _run(monkeypatch, marker_present, progress, dry_run=True, force=False):
    monkeypatch.setattr(run_all_columns.clay_ui, "open_workbook_by_id",
                        lambda page, wid: None)
    monkeypatch.setattr(run_all_columns.colcfg, "focus_table",
                        lambda page, table: None)
    monkeypatch.setattr(run_all_columns.clay_ui, "_find_header_rect",
                        lambda page, name: {"x": 1} if marker_present else None)
    monkeypatch.setattr(run_all_columns.colcfg, "column_status",
                        lambda page, name: progress)
    msgs = []
    r = run_all_columns.run_v1(FakePage(), ENTRY, dry_run, msgs.append,
                               force=force)
    return r, msgs


def test_no_marker_column_skips(monkeypatch):
    r, _ = _run(monkeypatch, marker_present=False, progress=None)
    assert r["status"] == "skip"


def test_unreadable_status_aborts_instead_of_running(monkeypatch):
    r, msgs = _run(monkeypatch, marker_present=True, progress=None,
                   dry_run=False)
    assert r["status"] == "aborted"
    assert r["reason"] == "marker_status_unreadable"


def test_non_dormant_marker_means_already_run(monkeypatch):
    for progress in ("", "37%", "100%"):
        r, _ = _run(monkeypatch, marker_present=True, progress=progress,
                    dry_run=False)
        assert r["status"] == "already_run", progress
        assert r["progress"] == progress


def test_dormant_marker_proceeds_to_dry_run(monkeypatch):
    r, _ = _run(monkeypatch, marker_present=True, progress="0%", dry_run=True)
    assert r["status"] == "dryrun"


def test_force_bypasses_the_guard(monkeypatch):
    r, _ = _run(monkeypatch, marker_present=True, progress="100%",
                dry_run=True, force=True)
    assert r["status"] == "dryrun"
