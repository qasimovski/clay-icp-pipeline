"""Pre-checks must distinguish "no" from "couldn't tell".

Audit C10: already_built/already_run returned True on a transient WSL error
("fail closed: never risk a duplicate"). The caller then wrote
status ok/already_built, permanently recording an unbuilt table as built —
nothing revisited it. The action was right; the state record was not."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "automation", "cleanup"))

import speakers_template_rollout as rollout  # noqa: E402

AUDIT_REC = {"table_id": "t_1"}


def _fake_run(returncode, stdout):
    def run(*a, **kw):
        return subprocess.CompletedProcess(a, returncode, stdout, "")
    return run


def _boom(*a, **kw):
    raise subprocess.TimeoutExpired("wsl", 120)


def test_already_built_unknown_on_exception(monkeypatch):
    monkeypatch.setattr(rollout.subprocess, "run", _boom)
    assert rollout.already_built({}, AUDIT_REC, lambda m: None) is None


def test_already_built_unknown_on_cli_error(monkeypatch):
    monkeypatch.setattr(rollout.subprocess, "run", _fake_run(1, ""))
    assert rollout.already_built({}, AUDIT_REC, lambda m: None) is None


def test_already_built_unknown_on_empty_output(monkeypatch):
    monkeypatch.setattr(rollout.subprocess, "run", _fake_run(0, "\n"))
    assert rollout.already_built({}, AUDIT_REC, lambda m: None) is None


def test_already_built_true_and_false_are_real_answers(monkeypatch):
    monkeypatch.setattr(rollout.subprocess, "run",
                        _fake_run(0, "Name\nWORK EMAIL\nBio\n"))
    assert rollout.already_built({}, AUDIT_REC, lambda m: None) is True
    monkeypatch.setattr(rollout.subprocess, "run", _fake_run(0, "Name\nBio\n"))
    assert rollout.already_built({}, AUDIT_REC, lambda m: None) is False


def test_no_table_id_is_a_definite_not_built():
    assert rollout.already_built({}, {}, lambda m: None) is False


def test_already_run_unknown_on_failure(monkeypatch):
    monkeypatch.setattr(rollout.subprocess, "run", _boom)
    assert rollout.already_run(AUDIT_REC, lambda m: None) is None
    monkeypatch.setattr(rollout.subprocess, "run", _fake_run(1, "0 0"))
    assert rollout.already_run(AUDIT_REC, lambda m: None) is None


def test_already_run_reads_fill_counts(monkeypatch):
    monkeypatch.setattr(rollout.subprocess, "run", _fake_run(0, "12 300"))
    assert rollout.already_run(AUDIT_REC, lambda m: None) is True
    monkeypatch.setattr(rollout.subprocess, "run", _fake_run(0, "0 300"))
    assert rollout.already_run(AUDIT_REC, lambda m: None) is False
