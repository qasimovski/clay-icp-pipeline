"""The blocklist destination scan is expensive and was re-run for every event
in a 77-workbook fleet (audit E7), though its answer only changes once a
table reaches MAX_SOURCES.

The memo must never let the source cap be exceeded: it is only trusted while
the table still has room even if EVERY send since the scan added a source."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "automation",
                                "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "automation",
                                "build_automation"))

import blocklist_send  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_memo():
    blocklist_send.reset_destination_memo()
    yield
    blocklist_send.reset_destination_memo()


class ScanRecorder:
    """Stands in for the whole Playwright scan; counts how often it runs."""

    def __init__(self, monkeypatch, sources):
        self.scans = 0
        self.sources = sources
        monkeypatch.setattr(blocklist_send, "_rows_and_sources", self._read)
        monkeypatch.setattr(blocklist_send.colcfg, "focus_table_maybe_empty",
                            lambda page, name: None)

    def _read(self, page):
        self.scans += 1
        return 100, self.sources


class FakePage:
    def goto(self, *a, **kw):
        pass

    def wait_for_timeout(self, ms):
        pass

    def get_by_text(self, *a, **kw):
        return self

    def get_by_role(self, *a, **kw):
        return self

    @property
    def first(self):
        return self

    def wait_for(self, **kw):
        pass

    def count(self):
        return 1


def test_second_call_reuses_the_memo(monkeypatch):
    rec = ScanRecorder(monkeypatch, sources=2)
    page = FakePage()
    first = blocklist_send.ensure_destination(page)
    second = blocklist_send.ensure_destination(page)
    assert first == second
    assert rec.scans == 1, "destination was re-scanned despite ample room"


def test_rescans_once_sends_could_have_filled_the_table(monkeypatch):
    # Scanned at 18 sources, cap 20: two more sends and it may be full.
    rec = ScanRecorder(monkeypatch, sources=blocklist_send.MAX_SOURCES - 2)
    page = FakePage()
    blocklist_send.ensure_destination(page)
    assert rec.scans == 1
    blocklist_send.note_send_routed()
    blocklist_send.ensure_destination(page)
    assert rec.scans == 1, "still had room for one more"
    blocklist_send.note_send_routed()
    blocklist_send.ensure_destination(page)
    assert rec.scans == 2, "must re-scan rather than exceed MAX_SOURCES"


def test_memo_never_outlives_the_cap(monkeypatch):
    """Whatever the scan reading, sources_at_scan + sends must stay under the
    cap for the memo to be reused."""
    rec = ScanRecorder(monkeypatch, sources=blocklist_send.MAX_SOURCES - 1)
    page = FakePage()
    blocklist_send.ensure_destination(page)
    blocklist_send.note_send_routed()
    blocklist_send.ensure_destination(page)
    assert rec.scans == 2


def test_reset_forces_a_fresh_scan(monkeypatch):
    rec = ScanRecorder(monkeypatch, sources=0)
    page = FakePage()
    blocklist_send.ensure_destination(page)
    blocklist_send.reset_destination_memo()
    blocklist_send.ensure_destination(page)
    assert rec.scans == 2
