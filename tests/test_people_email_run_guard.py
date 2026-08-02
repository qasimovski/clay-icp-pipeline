"""The double-charge guard on the "Enrich and Validate Email" pass.

The waterfall is the most expensive thing this repo triggers (~31K credits
across the 11 Product & Services People tables), and it is triggered by a click
whose result arrives asynchronously. So "has this already run?" cannot be
answered from the data: a run that fired one second ago still reads as zero
filled cells. apply_people_enrich_email answers it from a write-ahead log
written BEFORE the click instead, and only falls back to a fill count.

These tests pin that ordering and the retry semantics. Browser-free.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "automation", "cleanup"))

import apply_people_enrich_email as app  # noqa: E402


@pytest.fixture
def runs_file(tmp_path, monkeypatch):
    p = tmp_path / "runs.json"
    monkeypatch.setattr(app, "RUNS", str(p))
    return p


def test_no_record_means_never_triggered(runs_file):
    assert app.already_triggered("t_new") is False


def test_a_real_run_label_blocks_a_second_trigger(runs_file):
    app.record_trigger("t_1", "Some Workbook", label="Run 225 rows")
    assert app.already_triggered("t_1") is True


def test_triggering_is_treated_as_run(runs_file):
    """'triggering' is written just before the click, so a worker killed
    mid-trigger leaves it behind. It is ambiguous — the run may or may not have
    fired — and the safe reading of ambiguous is 'already ran'."""
    app.record_trigger("t_2", "Some Workbook", label="triggering")
    assert app.already_triggered("t_2") is True


@pytest.mark.parametrize("label", app.RETRYABLE)
def test_proven_failures_are_retryable(runs_file, label):
    """Labels that prove the click never landed must NOT strand the table —
    Lab Equipment & Instrumentation Suppliers hit run_not_triggered on
    2026-08-02 and had to be retried."""
    app.record_trigger("t_3", "Some Workbook", label=label)
    assert app.already_triggered("t_3") is False


def test_record_trigger_preserves_earlier_fields_on_update(runs_file):
    """The label is rewritten after the click; the workbook/table recorded
    before it must survive, or the log stops identifying what ran."""
    app.record_trigger("t_4", "Some Workbook", label="triggering")
    app.record_trigger("t_4", "Some Workbook", label="Run 842 rows")
    rec = json.loads(runs_file.read_text(encoding="utf-8"))["t_4"]
    assert rec["label"] == "Run 842 rows"
    assert rec["workbook"] == "Some Workbook"
    assert rec["table"] == "People"
    assert rec["triggered_at"]


def test_corrupt_run_log_aborts_rather_than_authorising_a_recharge(runs_file):
    """A truncated log must never read as 'nothing ever ran' — that would
    re-fire every waterfall in the fleet."""
    runs_file.write_text('{"t_5": {"label": "Run 1', encoding="utf-8")
    with pytest.raises(SystemExit):
        app.already_triggered("t_5")


def test_fill_check_failure_does_not_authorise_a_run(monkeypatch):
    """An unreadable fill count is 'unknown', not 'empty'. Returning (None,
    None) is what makes do_workbook stop at 'configured' instead of running."""
    monkeypatch.setattr(app, "_wsl",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("wsl down")))
    assert app.work_email_fill("t_6", lambda m: None) == (None, None)


def test_every_mapped_field_is_verified_after_mapping():
    """EXPECTED is the abort gate: a field mapped but absent from EXPECTED
    would save unverified. Every input in MAPPING must therefore appear in it."""
    assert set(app.MAPPING) <= set(app.EXPECTED)
    assert set(app.EXPECTED) == {"Full Name", "Domain", "Name",
                                 "LinkedIn Profile", "records", "Is New"}


def test_is_new_binds_to_the_person_ledger_not_the_company_one():
    """'Is New' exists under both Company Table Data and People - Supabase.
    On a People table the person-level flag is the correct gate; binding the
    company one would gate the waterfall on the wrong population."""
    assert app.MAPPING["Is New"]["steps"][0][0] == "People - Supabase"
