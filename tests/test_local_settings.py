"""Live workspace/workbook ids must come from the environment or gitignored
config/local.yaml, never from tracked source (docs/SENSITIVE_DATA.md).

Audit: blocklist_send hard-coded a full live workspace/workbook/table/view
URL and clay_ui defaulted WORKSPACE_ID to a real workspace, both of which
that policy forbids."""

import os
import re
import sys

import pytest

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, "automation", "clay_sync"))
sys.path.insert(0, os.path.join(REPO, "automation", "build_automation"))

import browser_session  # noqa: E402

TRACKED_SOURCES = [
    os.path.join(REPO, "automation", "build_automation", "blocklist_send.py"),
    os.path.join(REPO, "automation", "clay_sync", "clay_ui.py"),
]


@pytest.mark.parametrize("path", TRACKED_SOURCES)
def test_no_live_clay_ids_in_tracked_source(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    # Clay id shapes: wb_/t_/gv_ followed by a long opaque token, and a
    # numeric workspace id sitting in a URL path.
    assert not re.search(r"\b(?:wb|t|gv)_[A-Za-z0-9]{12,}", src), (
        f"{os.path.basename(path)} contains a literal Clay object id")
    assert not re.search(r"/workspaces/\d{4,}", src), (
        f"{os.path.basename(path)} contains a literal workspace id")


def test_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAY_TEST_KEY", "from-env")
    assert browser_session.local_setting("k", "CLAY_TEST_KEY") == "from-env"


def test_reads_quoted_and_unquoted_values(monkeypatch, tmp_path):
    cfg = tmp_path / "local.yaml"
    cfg.write_text('quoted: "wb_abc123"\nplain: t_xyz\n', encoding="utf-8")
    monkeypatch.setattr(browser_session, "LOCAL_CONFIG", str(cfg))
    monkeypatch.delenv("CLAY_UNSET", raising=False)
    assert browser_session.local_setting("quoted", "CLAY_UNSET") == "wb_abc123"
    assert browser_session.local_setting("plain", "CLAY_UNSET") == "t_xyz"


def test_hash_inside_a_value_is_not_truncated(monkeypatch, tmp_path):
    """The older reader in import_evcharge split on '#' unconditionally, so
    any value containing one was silently cut short."""
    cfg = tmp_path / "local.yaml"
    cfg.write_text('name: "Labs #2 Blocklist"\ntrailing: abc  # a comment\n',
                   encoding="utf-8")
    monkeypatch.setattr(browser_session, "LOCAL_CONFIG", str(cfg))
    monkeypatch.delenv("CLAY_UNSET", raising=False)
    assert browser_session.local_setting("name", "CLAY_UNSET") == "Labs #2 Blocklist"
    assert browser_session.local_setting("trailing", "CLAY_UNSET") == "abc"


def test_missing_key_raises_with_instructions(monkeypatch, tmp_path):
    monkeypatch.setattr(browser_session, "LOCAL_CONFIG",
                        str(tmp_path / "absent.yaml"))
    monkeypatch.delenv("CLAY_UNSET", raising=False)
    with pytest.raises(browser_session.VerificationError) as exc:
        browser_session.local_setting("blocklist_table_id", "CLAY_UNSET")
    assert "CLAY_UNSET" in str(exc.value)
    assert "local.yaml" in str(exc.value)


# --- the cleanup fleet ------------------------------------------------------
# The original test named two files by hand, so every pass written since could
# hard-code ids freely — and did: six landed in the Highways Workflows route
# before anyone noticed. This scans the whole active pass directory instead.
#
# `wf_`/`wfn_`/`f_` were absent from the original pattern, so workflow, node and
# folder ids did not even register as ids.
CLEANUP = os.path.join(REPO, "automation", "cleanup")

# Pre-existing offenders, recorded rather than silently tolerated. Shrink this list; never add to it.
# Emptied 2026-11-07: recon_cpj_reference.py's live default workbook id was removed (CLAY_CPJ_REF_WB is
# now required) in the sweep that brought the untracked scripts into the repo.
LEGACY_WITH_IDS = set()

# Deliberately NOT matched: ids in docstrings. `apply_cpj_people.py` quotes two
# field ids in a worked example of a formula bug and `apply_people_enrich_email`
# quotes the dead run-condition it was written to explain — prose about a real
# incident, which CLAUDE.md treats as the decision log. They cannot mis-target
# anything. A quoted id in CODE can, which is what this pattern catches.

ID_RE = re.compile(r"""["'](?:wf|wfn|wb|gv|f|t)_[A-Za-z0-9]{12,}["']""")


def _cleanup_modules():
    for name in sorted(os.listdir(CLEANUP)):
        if name.endswith(".py") and name not in LEGACY_WITH_IDS:
            yield name


@pytest.mark.parametrize("name", list(_cleanup_modules()))
def test_no_live_clay_ids_in_cleanup_passes(name):
    with open(os.path.join(CLEANUP, name), encoding="utf-8") as fh:
        src = fh.read()
    hits = sorted(set(ID_RE.findall(src)))
    assert not hits, (
        f"{name} hard-codes live Clay id(s) {hits}. They belong in the "
        f"gitignored config/local.yaml, read via browser_session.local_setting "
        f"(docs/SENSITIVE_DATA.md).")


def test_legacy_allowlist_is_still_accurate():
    """A file on the allowlist that no longer has ids should leave it, or the
    list quietly becomes permission to reintroduce them."""
    stale = []
    for name in sorted(LEGACY_WITH_IDS):
        p = os.path.join(CLEANUP, name)
        if not os.path.exists(p):
            stale.append(f"{name} (missing)")
            continue
        with open(p, encoding="utf-8") as fh:
            if not ID_RE.search(fh.read()):
                stale.append(f"{name} (clean now)")
    assert not stale, f"remove from LEGACY_WITH_IDS: {stale}"
