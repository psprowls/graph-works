"""`gw work integrate` merges with exact flags, restores on failure and records v2 evidence."""

import os
import stat

import pytest
from _finish_repos import OWNER, TODAY, code_repo, commit_file, git, workspace
from graph_works_core.orchestrate import integrate as integrate_module
from graph_works_core.orchestrate.finish_receipt import FinishReceiptResult
from graph_works_core.orchestrate.integrate import run_integrate
from graph_works_core.workspace.finish import inspect_finish, read_finish_receipt


def run(layout, strategy=None, apply=True, repo="code"):
    return run_integrate(layout, OWNER, repo_name=repo, strategy=strategy, today=TODAY, apply=apply)


def parents(repo, ref="HEAD"):
    return git(repo, "rev-list", "--parents", "-n", "1", ref).split()[1:]


def pristine(repo, before):
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "status", "--porcelain") == ""
    for name in ("MERGE_HEAD", "SQUASH_MSG"):
        assert not (repo / git(repo, "rev-parse", "--git-path", name)).exists()


@pytest.mark.parametrize(
    ("strategy", "diverge", "parent_count"), [("ff", False, 1), ("merge", True, 2), ("squash", True, 1)]
)
def test_each_strategy_produces_its_shape_and_a_verified_receipt(tmp_path, strategy, diverge, parent_count):
    repo, source = code_repo(tmp_path)
    before = commit_file(repo, "c.txt", "main\n", "main moves") if diverge else git(repo, "rev-parse", "HEAD")
    layout = workspace(tmp_path, repo, source)

    result = run(layout, strategy)

    assert (result.refusal, result.outcome, result.applied) == (None, "integrated", True)
    assert result.result_commit == git(repo, "rev-parse", "HEAD") and len(parents(repo)) == parent_count
    if strategy == "ff":
        assert result.result_commit == git(source, "rev-parse", "HEAD")
    if strategy == "squash":
        assert git(repo, "log", "-1", "--format=%s") == "Example feature"
        assert f"Squash of feature ({git(source, 'rev-parse', 'HEAD')}) for {OWNER}" in git(
            repo, "log", "-1", "--format=%b"
        )
    (entry,) = read_finish_receipt(layout, OWNER)[1]
    assert (entry.strategy, entry.evidence, entry.target_before) == (strategy, "verified", before)
    assert inspect_finish(layout, OWNER).complete


def test_plan_reports_the_strategy_and_its_source(tmp_path):
    repo, source = code_repo(tmp_path)
    layout = workspace(tmp_path, repo, source, finish_config="    finish:\n      strategy: merge\n")

    flagged = run(layout, "ff", apply=False)
    configured = run(layout, apply=False)

    assert (flagged.strategy, flagged.strategy_source, flagged.outcome, flagged.applied) == (
        "ff",
        "flag",
        "planned",
        False,
    )
    assert (configured.strategy, configured.strategy_source) == ("merge", "config")
    assert git(repo, "rev-parse", "HEAD") == configured.target_before


def test_unconfigured_default_is_squash(tmp_path):
    repo, source = code_repo(tmp_path)

    planned = run(workspace(tmp_path, repo, source), apply=False)

    assert (planned.strategy, planned.strategy_source) == ("squash", "default")


def test_ff_on_a_diverged_target_refuses_and_changes_nothing(tmp_path):
    repo, source = code_repo(tmp_path)
    before = commit_file(repo, "c.txt", "main\n", "main moves")
    layout = workspace(tmp_path, repo, source)

    for apply in (False, True):
        assert run(layout, "ff", apply=apply).refusal == "not-fast-forward"
    pristine(repo, before)
    assert read_finish_receipt(layout, OWNER)[0] is None


@pytest.mark.parametrize("strategy", ["merge", "squash"])
def test_conflict_refuses_lists_paths_and_restores(tmp_path, strategy):
    repo, source = code_repo(tmp_path)
    commit_file(source, "a.txt", "source side\n", "source edits a")
    before = commit_file(repo, "a.txt", "target side\n", "target edits a")

    result = run(workspace(tmp_path, repo, source), strategy)

    assert (result.refusal, result.conflicts) == ("conflict", ("a.txt",))
    pristine(repo, before)


def test_already_integrated_source_records_without_merging(tmp_path):
    repo, source = code_repo(tmp_path)
    git(repo, "merge", "--ff-only", "feature")
    tip = git(repo, "rev-parse", "HEAD")
    layout = workspace(tmp_path, repo, source)

    result = run(layout, "merge")

    assert (result.refusal, result.outcome) == (None, "already-integrated")
    assert git(repo, "rev-parse", "HEAD") == tip
    assert read_finish_receipt(layout, OWNER)[1][0].strategy == "ancestry"


def test_replay_after_squash_is_already_integrated(tmp_path):
    repo, source = code_repo(tmp_path)
    commit_file(repo, "c.txt", "main\n", "main moves")
    layout = workspace(tmp_path, repo, source)
    first = run(layout, "squash")

    again = run(layout, "squash")

    assert (again.refusal, again.outcome, again.result_commit) == (None, "already-integrated", first.result_commit)
    assert git(repo, "rev-parse", "HEAD") == first.result_commit


def test_squash_that_stages_nothing_refuses(tmp_path):
    # The same change landed inside a larger commit: not rediscoverable as a
    # squash (its tree also holds c.txt), and squashing again stages nothing.
    repo, source = code_repo(tmp_path)
    (repo / "b.txt").write_text("feature\n", encoding="utf-8", newline="\n")
    (repo / "c.txt").write_text("other\n", encoding="utf-8", newline="\n")
    git(repo, "add", "b.txt", "c.txt")
    git(repo, "commit", "-m", "same change landed with other work")
    before = git(repo, "rev-parse", "HEAD")

    result = run(workspace(tmp_path, repo, source), "squash")

    assert result.refusal == "nothing-to-integrate" and "accept-integration" in result.detail
    pristine(repo, before)


def test_workspace_target_refuses(tmp_path):
    repo, source = code_repo(tmp_path)

    assert run(workspace(tmp_path, repo, source), repo="_workspace").refusal == "workspace-target"


def test_dirty_target_refuses(tmp_path):
    repo, source = code_repo(tmp_path)
    (repo / "stray.txt").write_text("x\n", encoding="utf-8", newline="\n")

    assert run(workspace(tmp_path, repo, source), "merge").refusal == "dirty"


def test_leftover_squash_state_refuses_dirty(tmp_path):
    repo, source = code_repo(tmp_path)
    squash_msg = repo / git(repo, "rev-parse", "--git-path", "SQUASH_MSG")
    squash_msg.write_text("someone else's squash\n", encoding="utf-8", newline="\n")

    result = run(workspace(tmp_path, repo, source), "squash")

    assert result.refusal == "dirty" and "SQUASH_MSG" in result.detail
    assert squash_msg.exists()


def test_not_at_finish_and_unknown_path_refuse(tmp_path):
    repo, source = code_repo(tmp_path)
    layout = workspace(tmp_path, repo, source, phase="execute")

    assert run(layout, "merge").refusal == "not-at-finish"
    assert run_integrate(layout, "work/nope", repo_name="code", strategy="merge", today=TODAY).refusal == "unknown-path"


@pytest.mark.parametrize("strategy", ["merge", "squash"])
@pytest.mark.skipif(os.name == "nt", reason="POSIX hook script")
def test_rejecting_hook_restores_the_target(tmp_path, strategy):
    repo, source = code_repo(tmp_path)
    before = commit_file(repo, "c.txt", "main\n", "main moves")
    hooks = tmp_path / "no-hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-commit", "pre-merge-commit"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)

    result = run(workspace(tmp_path, repo, source), strategy)

    assert result.refusal == "git-unavailable" and "refused" in result.detail
    pristine(repo, before)


def test_receipt_failure_after_merge_reports_the_landed_commit(tmp_path, monkeypatch):
    repo, source = code_repo(tmp_path)
    commit_file(repo, "c.txt", "main\n", "main moves")
    monkeypatch.setattr(
        integrate_module, "record_finish_in", lambda *a, **k: FinishReceiptResult("transaction refused", False, None)
    )

    result = run(workspace(tmp_path, repo, source), "merge")

    assert (result.refusal, result.applied) == ("receipt-refused", True)
    assert result.result_commit == git(repo, "rev-parse", "HEAD") and result.result_commit in result.detail
