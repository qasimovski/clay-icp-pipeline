"""Tests for automation/cleanup/state_io.py — the atomic, fail-loud state-file
I/O every rollout resumes from. These files guard credit spend, so the
contract under test is: never silently treat a corrupt file as empty, and
never leave a truncated file on disk."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "automation", "cleanup"))
import state_io  # noqa: E402


def test_round_trip(tmp_path):
    p = tmp_path / "state.json"
    data = {"wb_1": {"status": "ok", "note": "already_applied"},
            "ünïcode": {"n": 1}}
    state_io.save_json(str(p), data)
    assert state_io.load_json(str(p)) == data


def test_missing_file_returns_default(tmp_path):
    p = str(tmp_path / "absent.json")
    assert state_io.load_json(p) == {}
    assert state_io.load_json(p, default=[1, 2]) == [1, 2]


def test_corrupt_file_fails_loud_not_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('{"wb_1": {"status": "ok"', encoding="utf-8")  # truncated
    with pytest.raises(SystemExit) as exc:
        state_io.load_json(str(p))
    assert "unreadable" in str(exc.value)
    # the corrupt file must be left in place for the operator to inspect
    assert p.exists()


def test_save_is_atomic_on_serialization_failure(tmp_path):
    p = tmp_path / "state.json"
    original = {"wb_1": {"status": "ok"}}
    state_io.save_json(str(p), original)
    with pytest.raises(TypeError):
        state_io.save_json(str(p), {"bad": object()})  # not JSON-serializable
    # original content untouched, no temp litter
    assert state_io.load_json(str(p)) == original
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []


def test_save_replaces_whole_document(tmp_path):
    p = tmp_path / "state.json"
    state_io.save_json(str(p), {"a": 1, "b": 2})
    state_io.save_json(str(p), {"a": 1})
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk == {"a": 1}
