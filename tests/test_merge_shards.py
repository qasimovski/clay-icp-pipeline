"""merge_shards folds per-worker shard files back into their master state
file. Shards are disjoint, so an overlay is correct — but it must never drop
master entries that no shard covers, and it must now work for any sharded
family (audit C3: the people_email run logs had no way to be merged back)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "automation", "cleanup"))

import merge_shards  # noqa: E402
import state_io  # noqa: E402


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(merge_shards, "SCRIPT_DIR", str(tmp_path))
    return tmp_path


def test_merges_shards_and_keeps_uncovered_master_entries(workdir):
    state_io.save_json(str(workdir / "people_email_runs.json"),
                       {"t_old": {"label": "Run 10 rows"}})
    state_io.save_json(str(workdir / "people_email_runs_w0.json"),
                       {"t_a": {"label": "Run 5 rows"}})
    state_io.save_json(str(workdir / "people_email_runs_w1.json"),
                       {"t_b": {"label": "triggering"}})

    _, master, merged = merge_shards.merge_family("people_email_runs")

    assert merged == 2
    assert set(master) == {"t_old", "t_a", "t_b"}
    assert master["t_b"]["label"] == "triggering"


def test_shard_record_wins_over_stale_master(workdir):
    state_io.save_json(str(workdir / "s.json"), {"t_a": {"label": "triggering"}})
    state_io.save_json(str(workdir / "s_w0.json"),
                       {"t_a": {"label": "Run 250 rows"}})

    _, master, _ = merge_shards.merge_family("s")

    assert master["t_a"]["label"] == "Run 250 rows"


def test_no_shards_leaves_master_intact(workdir):
    state_io.save_json(str(workdir / "s.json"), {"t_a": {"status": "ok"}})

    _, master, merged = merge_shards.merge_family("s")

    assert merged == 0
    assert master == {"t_a": {"status": "ok"}}


def test_absent_master_starts_empty(workdir):
    state_io.save_json(str(workdir / "s_w0.json"), {"t_a": {"status": "ok"}})

    _, master, merged = merge_shards.merge_family("s")

    assert merged == 1
    assert master == {"t_a": {"status": "ok"}}
