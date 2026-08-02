"""Worker queue safety (audit C5).

The queue executes Python against a live Clay session, so the mechanics
around it have to be exact: never compile a half-written command file, never
let two workers drive one queue, and never let a "command name" escape the
queue directory."""

import os
import sys

import pytest

BUILD = os.path.join(os.path.dirname(__file__), "..", "automation",
                     "build_automation")
sys.path.insert(0, BUILD)

import worker_wait  # noqa: E402


@pytest.mark.parametrize("bad", [
    "../../../etc/passwd",
    "..\\..\\secrets",
    "cmd_000/../../x",
    "cmd_abc",
    "",
])
def test_rejects_names_that_are_not_cmd_nnn(bad):
    with pytest.raises(SystemExit):
        worker_wait.main([bad])


def test_rejects_missing_argument():
    with pytest.raises(SystemExit):
        worker_wait.main([])


def test_rejects_non_numeric_timeout():
    with pytest.raises(SystemExit) as exc:
        worker_wait.main(["cmd_000", "soon"])
    assert "timeout" in str(exc.value)


def test_reads_ok_and_err_results(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker_wait, "QUEUE", str(tmp_path))
    (tmp_path / "cmd_007.ok").write_text("all good", encoding="utf-8")
    assert worker_wait.main(["cmd_007"]) == 0
    assert "all good" in capsys.readouterr().out

    (tmp_path / "cmd_008.err").write_text("boom", encoding="utf-8")
    assert worker_wait.main(["cmd_008"]) == 1


def test_settled_read_returns_full_content(tmp_path):
    import worker
    p = tmp_path / "cmd_000.py"
    p.write_text("print('complete file')\n", encoding="utf-8")
    assert worker._read_when_settled(str(p)) == "print('complete file')\n"


def test_settled_read_handles_vanished_file(tmp_path):
    import worker
    assert worker._read_when_settled(str(tmp_path / "gone.py")) is None


def test_queue_lock_is_exclusive(tmp_path, monkeypatch):
    import worker
    monkeypatch.setattr(worker, "LOCK", str(tmp_path / "worker.lock"))
    worker._claim_queue()
    with pytest.raises(SystemExit) as exc:
        worker._claim_queue()
    assert "another worker" in str(exc.value)
