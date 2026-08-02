"""A Clay CLI failure must not read as "0 rows".

Audit C4: check_table_rows printed 0 and exited 1 when the CLI failed after
4 tries; people_email_rollout read that as an empty table and removed the
workbook from every future batch, reported as "skipping N empty table(s)"."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "automation", "cleanup"))

import people_email_rollout as rollout  # noqa: E402

EVENT = {"workbook_name": "Testfair", "todo": ["Sellers - People"],
         "tables": {"Sellers - People": {"table_id": "t_1"}}}


def _fake_run(returncode, stdout):
    def run(*a, **kw):
        return subprocess.CompletedProcess(a, returncode, stdout, "")
    return run


def test_cli_failure_yields_unknown_not_zero(monkeypatch):
    monkeypatch.setattr(rollout.subprocess, "run", _fake_run(1, "0\n"))
    assert rollout.row_total(EVENT) is None


def test_cli_exception_yields_unknown(monkeypatch):
    def boom(*a, **kw):
        raise subprocess.TimeoutExpired("wsl", 120)
    monkeypatch.setattr(rollout.subprocess, "run", boom)
    assert rollout.row_total(EVENT) is None


def test_genuine_zero_is_reported_as_zero(monkeypatch):
    monkeypatch.setattr(rollout.subprocess, "run", _fake_run(0, "0\n"))
    assert rollout.row_total(EVENT) == 0


def test_counts_sum_across_pending_tables(monkeypatch):
    monkeypatch.setattr(rollout.subprocess, "run", _fake_run(0, "12\n"))
    ev = {"todo": ["a", "b"],
          "tables": {"a": {"table_id": "t_a"}, "b": {"table_id": "t_b"}}}
    assert rollout.row_total(ev) == 24


def test_only_real_zeros_are_dropped_unknowns_sort_last():
    # Mirrors the filter/sort in main(): unknown (None) is kept, ordered last.
    events = [{"workbook_name": "known-empty", "rows": 0},
              {"workbook_name": "unknown", "rows": None},
              {"workbook_name": "small", "rows": 5}]
    kept = [e for e in events if e["rows"] is None or e["rows"] > 0]
    kept.sort(key=lambda e: (e["rows"] is None, e["rows"] or 0,
                             e["workbook_name"]))
    assert [e["workbook_name"] for e in kept] == ["small", "unknown"]
