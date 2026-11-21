"""Rail Other Sources approval driver (runs/rail_other_sources/run_approved.py): the two guards that stop a Clay
approval loop from paying for rows the user did not approve. Both came from real overspends on 21 Nov 2026:
a 250-row pilot ran a 3,150-row plan, and a --retry-failed loop re-sent 6 always-failing rows 53 times."""
import importlib.util
import json
import os
import types

import pytest

PATH = os.path.join(os.path.dirname(__file__), "..", "runs", "rail_other_sources", "run_approved.py")


def load():
    spec = importlib.util.spec_from_file_location("run_approved", PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_job(tmp_path, feed_ids, chunk_ids):
    (tmp_path / "feed.csv").write_text("Row ID\n" + "".join(f"{i}\n" for i in feed_ids), encoding="utf-8")
    feeds = tmp_path / "work" / "feeds"
    feeds.mkdir(parents=True)
    (feeds / "chunk_000.jsonl").write_text("".join(json.dumps({"id": i, "inputs": {}}) + "\n" for i in chunk_ids),
                                           encoding="utf-8")
    return dict(wf="wf_test", dir=str(tmp_path), feed="feed.csv", work="work", inputs="x", ids="Row ID",
                reuse=[], parallel=1)


def test_scope_check_refuses_ids_outside_the_approved_feed(tmp_path):
    m = load()
    job = make_job(tmp_path, ["a", "b"], ["a", "b", "c"])     # chunk files from an earlier --plan on a bigger feed
    with pytest.raises(SystemExit, match="not approved"):
        m.check_scope(job, job["dir"])


def test_scope_check_allows_a_subset(tmp_path):
    m = load()
    job = make_job(tmp_path, ["a", "b", "c"], ["a", "b"])     # rows copied by --reuse-from are never chunked
    m.check_scope(job, job["dir"])


def test_retry_mode_approves_one_retry_chunk_then_stops(tmp_path, monkeypatch):
    m = load()
    job = make_job(tmp_path, ["a"], ["a"])
    state = tmp_path / "work" / "state.json"
    calls = {"bulk": [], "approved": 0}

    def fake_run(cmd, **kw):
        calls["bulk"].append(list(cmd))
        n = len(calls["bulk"])                              # every call writes a NEW retry chunk awaiting approval
        state.write_text(json.dumps({f"retry_{n:03d}.jsonl": {"status": "awaiting_approval", "approval_id": f"x{n}"}}))
        return types.SimpleNamespace(returncode=0, stdout="chunks 1  pending 1  rows to run 6", stderr="")

    def fake_approve(job_, base):
        calls["approved"] += 1
        return 1

    monkeypatch.setitem(m.JOBS, "t", job)
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    monkeypatch.setattr(m, "approve_pending", fake_approve)
    monkeypatch.setattr(m, "check_scope", lambda *a: None)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    monkeypatch.setattr(m.sys, "argv", ["run_approved.py", "t", "--retry-failed"])
    with pytest.raises(SystemExit, match="already approved one retry chunk"):
        m.main()
    assert calls["approved"] == 1
    assert "--retry-failed" in calls["bulk"][0] and "--retry-failed" not in calls["bulk"][1]
