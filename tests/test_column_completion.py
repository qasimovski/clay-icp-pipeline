"""The done-signal must be column-scoped, not table-scoped.

Audit C6: the table-wide "N% of table completed" banner is not a given
column's progress. Used alone it marked workbooks permanently done with an
unfinished column (documented in add_workemail_waterfall.py and observed on
Analytica India). Completion now requires both signals to agree."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "automation", "cleanup"))

import column_completion as cc  # noqa: E402


class FakePage:
    def __init__(self, body):
        self.body = body

    def evaluate(self, _js):
        return self.body


class FakeColcfg:
    def __init__(self, status, raises=False):
        self.status = status
        self.raises = raises

    def column_status(self, page, column):
        if self.raises:
            raise RuntimeError("panel not readable")
        return self.status


P100 = FakePage("stuff 100% of table completed stuff")
P40 = FakePage("40% of table completed")
PNONE = FakePage("no banner here")


def test_table_pct_parses_and_tolerates_absence():
    assert cc.table_pct(P100) == 100
    assert cc.table_pct(P40) == 40
    assert cc.table_pct(PNONE) is None


def test_stale_table_100_with_dormant_column_is_not_complete():
    complete, why = cc.already_complete(P100, "WORK EMAIL", FakeColcfg("0%"))
    assert complete is False
    assert "still running" in why


def test_table_100_and_column_finished_is_complete():
    for status in ("", "100%"):
        complete, _ = cc.already_complete(P100, "WORK EMAIL",
                                          FakeColcfg(status))
        assert complete is True, status


def test_partial_column_percentage_is_not_complete():
    complete, _ = cc.already_complete(P100, "WORK EMAIL", FakeColcfg("4%"))
    assert complete is False


def test_unreadable_column_status_is_not_complete():
    complete, why = cc.already_complete(P100, "WORK EMAIL",
                                        FakeColcfg(None, raises=True))
    assert complete is False
    assert "unreadable" in why


def test_table_not_at_100_short_circuits():
    complete, why = cc.already_complete(P40, "WORK EMAIL", FakeColcfg(""))
    assert complete is False
    assert why == "table 40%"


def test_column_finished_reports_none_when_unreadable():
    assert cc.column_finished(P100, "c", FakeColcfg(None, raises=True)) is None
    assert cc.column_finished(P100, "c", FakeColcfg(None)) is None
