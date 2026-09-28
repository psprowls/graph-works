"""`gw work gate run | check`: pending records, joining a live run, and the refusals."""

from __future__ import annotations

import json
from datetime import timedelta

from _gate_helpers import NOW, mint_receipt, raise_, snapshot_dirs
from conftest import git, make_repo
from graph_works_core.orchestrate import gate
from graph_works_core.orchestrate.gate import run_gate_check, run_gate_run, runs_dir


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
    assert env.spawned == [] and not runs_dir(env.layout, env.path).exists()


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


def test_no_worktree_refuses(env):
    env.set_worktree(None)
    assert run_gate_run(env.layout, env.path, now=NOW, token="a1b2c3d4", spawn=env.spawn).refusal == "no-worktree"


def test_unknown_item_refuses(env):
    assert run_gate_run(env.layout, "work/nope", now=NOW, token="a1b2c3d4", spawn=env.spawn).refusal == "unknown-item"


def test_foreign_worktree_flag_refuses(env, tmp_path):
    other = make_repo(tmp_path / "other")
    result = run_gate_run(env.layout, env.path, worktree=other, now=NOW, token="a1b2c3d4", spawn=env.spawn)
    assert result.refusal == "foreign-worktree"


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
    record = next(runs_dir(env.layout, env.path).glob("*.json"))
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
    env.set_worktree(None)
    assert run_gate_check(env.layout, env.path).refusal == "no-worktree"


def test_check_writes_nothing(env):
    before = snapshot_dirs(env)
    run_gate_check(env.layout, env.path)
    assert snapshot_dirs(env) == before


def test_resolve_target_returns_the_target_for_a_clean_tree(env):
    target = gate.resolve_target(env.layout, env.path, worktree=None)
    assert isinstance(target, gate.GateTarget) and target.repo == "code" and target.tree == env.tree


def test_unreadable_records_are_skipped_when_joining(env):
    directory = runs_dir(env.layout, env.path)
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
