"""render_build_prompt must fail with a clear message rather than a traceback
or a plausible-looking-but-wrong spec (audit C14).

Four defects, all confirmed by running it in Phase 1:
  a) --entity exhibitors_evcharge died with `KeyError: 'gating'`
  b) the unresolved-placeholder check scanned the keys it had just
     substituted, so it could never catch a placeholder missing from
     `replacements`
  c) the no---out form the RUNBOOK documents died with UnicodeEncodeError on
     a Windows cp1252 console (the spec contains -> and other non-ASCII)
  d) a local.yaml still full of REPLACE_ME rendered "successfully"
"""

import os
import subprocess
import sys

import pytest

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, "template"))

import render_build_prompt as rbp  # noqa: E402

REAL_LOCAL = {"workspace_url": "https://app.clay.com/workspaces/1",
              "workspace_name": "WS", "events_folder": "Competitive Events",
              "blocklist_table": "Blocklist"}


def test_missing_key_names_the_file_and_the_key():
    with pytest.raises(SystemExit) as exc:
        rbp.require({}, "run_conditions", "config/entity-types/x.yaml", " (hint)")
    msg = str(exc.value)
    assert "config/entity-types/x.yaml" in msg
    assert "run_conditions" in msg
    assert "(hint)" in msg


def test_missing_config_file_is_not_a_traceback():
    with pytest.raises(SystemExit) as exc:
        rbp.load_yaml(os.path.join(REPO, "config", "entity-types", "nope.yaml"))
    assert "config file not found" in str(exc.value)


def test_evcharge_entity_reports_what_it_lacks(monkeypatch):
    monkeypatch.setattr(rbp, "load_local_config", lambda: REAL_LOCAL)
    with pytest.raises(SystemExit) as exc:
        rbp.render("exhibitors_evcharge", "labs")
    assert "exhibitors_evcharge.yaml" in str(exc.value)


def test_replace_me_config_is_refused(monkeypatch):
    monkeypatch.setattr(rbp, "load_local_config",
                        lambda: dict(REAL_LOCAL, workspace_url="REPLACE_ME"))
    with pytest.raises(SystemExit) as exc:
        rbp.render("exhibitors", "labs")
    assert "REPLACE_ME" in str(exc.value)
    assert "{{WORKSPACE_URL}}" in str(exc.value)


def test_placeholder_regex_matches_our_tokens_not_clay_fields():
    found = rbp.PLACEHOLDER_RE.findall(
        "keep {{Side}} and {{Description}} but flag {{NEW_TOKEN}}")
    assert found == ["{{NEW_TOKEN}}"]


def test_unsubstituted_placeholder_is_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(rbp, "load_local_config", lambda: REAL_LOCAL)
    tpl = tmp_path / "template"
    tpl.mkdir()
    (tpl / "BUILD_PROMPT.template.md").write_text(
        "spec with {{ICP_NAME}} and an unwired {{BRAND_NEW_TOKEN}}",
        encoding="utf-8")
    for sub in ("config/entity-types", "config/icps/labs"):
        os.makedirs(tmp_path / sub, exist_ok=True)
    for src, dst in (("config/entity-types/exhibitors.yaml",
                      "config/entity-types/exhibitors.yaml"),
                     ("config/icps/labs/icp.yaml", "config/icps/labs/icp.yaml")):
        (tmp_path / dst).write_bytes(open(os.path.join(REPO, src), "rb").read())
    monkeypatch.setattr(rbp, "REPO_ROOT", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        rbp.render("exhibitors", "labs")
    assert "{{BRAND_NEW_TOKEN}}" in str(exc.value)


def test_renders_to_utf8_stdout_without_encoding_error():
    """The RUNBOOK's no---out form, forced through a cp1252 console."""
    script = os.path.join(REPO, "template", "render_build_prompt.py")
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    out = subprocess.run(
        [sys.executable, script, "--entity", "exhibitors", "--icp", "labs"],
        capture_output=True, env=env)
    # Either it renders (bytes are valid UTF-8) or it stops on the
    # REPLACE_ME/config guard - never on a UnicodeEncodeError.
    assert b"UnicodeEncodeError" not in out.stderr
    if out.returncode == 0:
        out.stdout.decode("utf-8")  # must not raise
