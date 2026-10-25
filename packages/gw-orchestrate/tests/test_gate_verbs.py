"""`gw work gate run | check`: pending records, joining a live run, and the refusals."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from _gate_helpers import NOW, mint_receipt, raise_, set_max_concurrent, snapshot_dirs
from conftest import git
from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import layout_for
from gw_orchestrate import gate
from gw_orchestrate.gate import gate_max_concurrent, run_gate_check, run_gate_run, runs_dir


def test_run_starts_one_pending_record(env):
    result = run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    assert result.status == "started" and result.run_id == "20260928T120000Z-0a1b2c3d"
    record = json.loads(env.spawned[0].read_text(encoding="utf-8"))
    assert record["tree"] == git(env.repo, "rev-parse", "HEAD^{tree}") and record["command"] == "true"
    assert record["log_path"].endswith(f"gw-gate/feature-a/{result.run_id}.log")


def test_dirty_tree_refuses_and_mints_nothing(env):
    (env.repo / "packages/a/new.py").write_text("x", encoding="utf-8", newline="\n")
    result = run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.refusal == "dirty-tree" and "packages/a/new.py" in result.detail
    assert env.spawned == [] and not runs_dir(env.layout).exists()


def test_satisfied_receipt_runs_nothing(env):
    mint_receipt(env, tree=git(env.repo, "rev-parse", "HEAD^{tree}"), owner="work/other")
    result = run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.status == "satisfied" and result.match.owner == "work/other" and env.spawned == []


def test_concurrent_run_for_same_tree_shares_one_run(env):
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=env.spawn)
    env.mark_runner_alive(first.run_id)
    second = run_gate_run(env.layout, env.path, now=NOW, token="bbbbbbbb", spawn=env.spawn)
    assert (second.status, second.run_id) == ("running", first.run_id) and len(env.spawned) == 1


def test_a_dead_pending_run_does_not_block_a_new_one(env):
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=env.spawn)
    later = NOW + timedelta(minutes=5)  # past the start grace, with no runner lock held
    second = run_gate_run(env.layout, env.path, now=later, token="bbbbbbbb", spawn=env.spawn)
    assert second.status == "started" and second.run_id != first.run_id


def test_missing_gate_full_refuses(env):
    env.set_manifest(gate="")
    result = run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.refusal == "no-gate-configured" and "repositories.code.gate.full" in result.detail


def test_scoped_run_expands_and_scoped_uncovered_refuses(env):
    ok = run_gate_run(env.layout, env.path, scope="scoped", now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert ok.status == "started" and ok.names == ("a",) and ok.command == "echo a"
    env.set_affects(["packages/a", "plugins/x"])
    bad = run_gate_run(env.layout, env.path, scope="scoped", now=NOW, token="b1b2c3d4", spawn=env.spawn)
    assert bad.refusal == "scope-uncovered" and "plugins/x" in bad.detail and "full gate" in bad.detail


def test_scoped_with_no_code_affects_refuses(env):
    env.set_affects(["gw:workspace"])
    result = run_gate_run(env.layout, env.path, scope="scoped", now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.refusal == "scope-uncovered" and "no code `affects`" in result.detail


def test_scoped_without_config_refuses(env):
    env.set_manifest(gate="    gate:\n      full: 'true'\n")
    result = run_gate_run(env.layout, env.path, scope="scoped", now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.refusal == "no-scoped-gate"


def _computed(env):
    """Clear the legacy stamp so the item's computed placement applies; return (worktree, branch)."""
    from gw_pipeline.placement import active_path, branch_name

    env.set_worktree(None)
    branch = branch_name(active_path(env.path), "Feature")
    return env.layout.worktrees_dir / "code" / branch, branch


def test_gate_worktree_missing_when_absent(env):
    computed, branch = _computed(env)
    computed.parent.mkdir(parents=True, exist_ok=True)
    git(env.repo, "worktree", "add", "-b", branch, str(computed))
    git(env.repo, "worktree", "remove", str(computed))
    result = run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.refusal == "worktree-missing"
    assert result.detail == (
        f"{env.path}: {computed} is not a checkout of code on {branch}; run gw work place {env.path}"
    )
    assert env.spawned == []


def test_gate_worktree_missing_on_other_branch(env):
    computed, branch = _computed(env)
    computed.parent.mkdir(parents=True, exist_ok=True)
    git(env.repo, "worktree", "add", "-b", branch, str(computed))
    git(computed, "switch", "-c", "elsewhere")
    result = run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.refusal == "worktree-missing"
    assert run_gate_check(env.layout, env.path).refusal == "worktree-missing"


def test_gate_measures_computed_worktree(env):
    from gw_kernel.workspace.bundle import load_work_bundle
    from gw_pipeline.placement_resolve import item_placement
    from work_tracker_okf.items import load_items

    computed, branch = _computed(env)
    computed.parent.mkdir(parents=True, exist_ok=True)
    git(env.repo, "worktree", "add", "-b", branch, str(computed))
    items = load_items(load_work_bundle(env.layout))
    by_path = {i.path: i for i in items}
    expected = item_placement(env.layout, by_path[env.path], by_path).worktree.resolve()
    target = gate.resolve_target(env.layout, env.path)
    assert isinstance(target, gate.GateTarget) and target.worktree == expected


def test_workspace_only_item_keeps_no_gate_configured(env):
    env.set_affects(["gw:workspace"])
    page = env.layout.bundle_dir / f"{env.path}.md"
    page.write_text(page.read_text(encoding="utf-8").replace("repo: code\n", ""), encoding="utf-8", newline="\n")
    env.set_manifest(gate="")
    result = run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.refusal == "no-gate-configured"


def test_unknown_item_refuses(env):
    assert run_gate_run(env.layout, "work/nope", now=NOW, token="a1b2c3d4", spawn=env.spawn).refusal == "unknown-item"


def test_git_failure_refuses_git_unavailable(env, monkeypatch):
    monkeypatch.setattr(gate.gate_git, "snapshot", raise_(gate.gate_git.GitUnavailable("boom")))
    result = run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert (result.refusal, result.detail) == ("git-unavailable", "boom")


def test_unresolvable_git_refuses_git_unavailable(env, monkeypatch):
    failure = gate.provenance.GitFailure("missing", "no git")
    monkeypatch.setattr(gate.provenance, "gate_git", lambda layout: failure)
    result = run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.refusal == "git-unavailable" and "no git" in result.detail
    assert run_gate_check(env.layout, env.path).refusal == "git-unavailable"


def test_a_spawn_failure_marks_the_record_dead(env):
    def boom(record):
        raise OSError("cannot exec")

    result = run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=boom)
    assert (result.refusal, result.detail) == ("runner-failed", "cannot exec")
    record = next(runs_dir(env.layout).glob("*.json"))
    assert json.loads(record.read_text(encoding="utf-8"))["result"] == {"exit": None, "error": "cannot exec"}


def test_bad_token_is_rejected(env):
    import pytest

    with pytest.raises(ValueError, match="8 lowercase hex"):
        run_gate_run(env.layout, env.path, now=NOW, token="nope", spawn=env.spawn)


def test_check_reports_satisfied_and_each_unsatisfied_reason(env):
    tree = git(env.repo, "rev-parse", "HEAD^{tree}")
    assert run_gate_check(env.layout, env.path).detail == "no receipt"
    mint_receipt(env, tree=tree, exit=1)
    assert run_gate_check(env.layout, env.path).detail == "only red/scoped/stale receipts"
    mint_receipt(env, tree=tree)
    checked = run_gate_check(env.layout, env.path)
    assert checked.status == "satisfied" and checked.tree == tree
    (env.repo / "packages/a/new.py").write_text("x", encoding="utf-8", newline="\n")
    dirty = run_gate_check(env.layout, env.path)
    assert (dirty.status, dirty.refusal, dirty.detail) == ("unsatisfied", None, "dirty")


def test_check_refusals_pass_through(env):
    env.set_manifest(gate="")
    assert run_gate_check(env.layout, env.path).refusal == "no-gate-configured"
    _computed(env)
    assert run_gate_check(env.layout, env.path).refusal == "worktree-missing"


def test_check_writes_nothing(env):
    before = snapshot_dirs(env)
    run_gate_check(env.layout, env.path)
    assert snapshot_dirs(env) == before


def test_resolve_target_returns_the_target_for_a_clean_tree(env):
    target = gate.resolve_target(env.layout, env.path)
    assert isinstance(target, gate.GateTarget) and target.repo == "code" and target.tree == env.tree


def test_unreadable_records_are_skipped_when_joining(env):
    directory = runs_dir(env.layout)
    directory.mkdir(parents=True)
    (directory / "bad.json").write_bytes(b"\xff")
    (directory / "list.json").write_text("[]", encoding="utf-8", newline="\n")
    result = run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.status == "started"


def test_a_malformed_gate_block_refuses_run_and_check(env):
    env.set_manifest(gate="    gate:\n      full: '   '\n")
    assert (
        run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn).refusal == "no-gate-configured"
    )
    assert run_gate_check(env.layout, env.path).refusal == "no-gate-configured"


def test_an_undeclared_repo_refuses(env):
    page = env.layout.bundle_dir / f"{env.path}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("repo: code", "repo: ghost"), encoding="utf-8", newline="\n"
    )
    assert (
        run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn).refusal == "no-gate-configured"
    )


def test_max_concurrent_is_unlimited_when_unset(env):
    assert gate_max_concurrent(env.layout) is None


def test_max_concurrent_reads_a_positive_integer(env):
    set_max_concurrent(env, "2")
    assert gate_max_concurrent(env.layout) == 2


def test_max_concurrent_reads_the_local_overlay(env):
    (env.root / "workflow.local.yaml").write_text(
        "workflow:\n  gate:\n    max_concurrent: 1\n", encoding="utf-8", newline="\n"
    )
    assert gate_max_concurrent(env.layout) == 1


@pytest.mark.parametrize("value", ["0", "-1", "true", "'two'"])
def test_a_bad_max_concurrent_refuses_gate_run(env, value):
    set_max_concurrent(env, value)
    with pytest.raises(WorkspaceError, match="max_concurrent"):
        run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=env.spawn)
    assert env.spawned == []


def test_max_concurrent_refusal_names_the_workflow_file(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(
        "version: 1\nworkflow_config: workflow.yaml\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / "workflow.yaml").write_text(
        "workflow:\n  gate:\n    max_concurrent: 0\n", encoding="utf-8", newline="\n"
    )
    with pytest.raises(
        WorkspaceError, match=r"workflow\.yaml: workflow\.gate\.max_concurrent: expects a positive integer, got 0"
    ):
        gate_max_concurrent(layout_for(tmp_path))


def test_gate_check_resolves_worktree_via_accessor(env):
    from gw_pipeline.placement import active_path, branch_name

    env.set_worktree(None)
    branch = branch_name(active_path(env.path), "Feature")
    computed = env.layout.worktrees_dir / "code" / branch
    computed.parent.mkdir(parents=True, exist_ok=True)
    git(env.repo, "worktree", "add", "-b", branch, str(computed))
    result = run_gate_check(env.layout, env.path)
    assert result.refusal != "worktree-missing"
