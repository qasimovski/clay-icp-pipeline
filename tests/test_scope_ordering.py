"""Fleet scope must be ordered deterministically.

Audit C15: three rollouts built scope from a JSON dict's insertion order,
then computed --shard partitions and --after cursors from that list.
Regenerating the scope file re-partitioned the fleet, so two workers could
process the same workbook across a regeneration."""

import ast
import os

import pytest

CLEANUP = os.path.join(os.path.dirname(__file__), "..", "automation", "cleanup")

ROLLOUTS = ["apply_findworkemail_rollout.py", "apply_findlinkedin_rollout.py",
            "speakers_email_rollout.py", "speakers_template_rollout.py"]


def _source(name):
    with open(os.path.join(CLEANUP, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("name", ROLLOUTS)
def test_scope_is_sorted_before_sharding(name):
    src = _source(name)
    assert 'scope.sort(key=lambda e: e["workbook_name"])' in src, (
        f"{name} must sort scope by workbook name; shard partitions and "
        f"--after cursors are derived from this ordering")


@pytest.mark.parametrize("name", ROLLOUTS)
def test_sort_precedes_shard_partition(name):
    src = _source(name)
    sort_at = src.index("scope.sort(")
    shard_at = src.find("% args.shards")
    if shard_at != -1:
        assert sort_at < shard_at, f"{name} shards before sorting"


def test_after_cursor_has_no_baked_in_default():
    """A one-off resume marker as a *default* silently skipped the fleet up
    to BioTrinity on a bare run, and raised once that name left the scope."""
    tree = ast.parse(_source("speakers_email_rollout.py"))
    defaults = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "add_argument"
                and node.args
                and getattr(node.args[0], "value", None) == "--after"):
            defaults = [kw.value for kw in node.keywords if kw.arg == "default"]
    assert defaults, "--after argument not found"
    assert getattr(defaults[0], "value", "sentinel") is None
