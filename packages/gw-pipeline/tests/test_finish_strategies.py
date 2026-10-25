"""Each receipt strategy verifies exactly the history shape it claims."""

import subprocess

import pytest
from gw_kernel.workspace.errors import WorkspaceConfigError
from gw_pipeline.finish import (
    FinishRefused,
    VerifiedIntegration,
    finish_strategy,
    read_finish_receipt,
    resolve_finish_targets,
    verify_integration,
)
from gw_pipeline.test_support.finish_repos import (
    OWNER,
    code_repo,
    commit_file,
    git,
    squash,
    target,
    workspace,
    write_receipt,
)
from okf_io import load_bundle
from work_tracker_okf.items import IGNORE, load_items


def entry(source_sha, result, strategy, before):
    return VerifiedIntegration("code", "feature", source_sha, "main", result, strategy, "verified", before)


def test_squash_verifies_unmoved_and_after_the_target_moves(tmp_path):
    repo, source = code_repo(tmp_path)
    commit_file(repo, "c.txt", "main\n", "main moves")
    before, result = squash(repo)
    claimed = entry(git(source, "rev-parse", "HEAD"), result, "squash", before)

    assert verify_integration(target(repo, source), claimed) is None
    commit_file(repo, "d.txt", "later\n", "target moves on")
    assert verify_integration(target(repo, source), claimed) is None


def test_tampered_squash_tree_is_unverified(tmp_path):
    repo, source = code_repo(tmp_path)
    before, _ = squash(repo)
    commit_file(repo, "extra.txt", "smuggled\n", "squash feature")
    git(repo, "reset", "--soft", "HEAD~2")
    git(repo, "commit", "-m", "squash plus extra")
    tampered = git(repo, "rev-parse", "HEAD")

    reason = verify_integration(
        target(repo, source), entry(git(source, "rev-parse", "HEAD"), tampered, "squash", before)
    )

    assert reason is not None and "tree differs" in reason


def test_squash_whose_parent_is_not_target_before_is_unverified(tmp_path):
    repo, source = code_repo(tmp_path)
    commit_file(repo, "c.txt", "main\n", "main moves")
    before, result = squash(repo)
    wrong = git(repo, "rev-parse", f"{before}^")

    reason = verify_integration(target(repo, source), entry(git(source, "rev-parse", "HEAD"), result, "squash", wrong))

    assert reason is not None and "parent" in reason


def test_squash_whose_merge_tree_conflicts_is_unverified(tmp_path):
    repo, source = code_repo(tmp_path)
    commit_file(source, "a.txt", "source side\n", "source edits a")
    before = commit_file(repo, "a.txt", "target side\n", "target edits a")
    fake = git(repo, "commit-tree", f"{before}^{{tree}}", "-p", before, "-m", "fake squash")
    git(repo, "update-ref", "refs/heads/main", fake)

    reason = verify_integration(target(repo, source), entry(git(source, "rev-parse", "HEAD"), fake, "squash", before))

    assert reason is not None and "merge-tree" in reason


def test_strategy_shape_mismatch_is_unverified(tmp_path):
    repo, source = code_repo(tmp_path)
    before = commit_file(repo, "c.txt", "main\n", "main moves")
    git(repo, "merge", "--no-ff", "--no-edit", "feature")
    merge_commit = git(repo, "rev-parse", "HEAD")
    source_sha = git(source, "rev-parse", "HEAD")
    t = target(repo, source)

    assert verify_integration(t, entry(source_sha, merge_commit, "merge", before)) is None
    assert (
        verify_integration(t, entry(source_sha, merge_commit, "ff", before))
        == "fast-forward result is not the source commit"
    )
    single = git(repo, "commit-tree", f"{merge_commit}^{{tree}}", "-p", before, "-m", "one parent")
    git(repo, "update-ref", "refs/heads/main", single)
    reason = verify_integration(t, entry(source_sha, single, "merge", before))
    assert reason is not None and "parents" in reason


def test_ff_verifies_when_the_result_is_the_source(tmp_path):
    repo, source = code_repo(tmp_path)
    before = git(repo, "rev-parse", "HEAD")
    git(repo, "merge", "--ff-only", "feature")
    source_sha = git(source, "rev-parse", "HEAD")

    assert verify_integration(target(repo, source), entry(source_sha, source_sha, "ff", before)) is None


def test_moved_source_branch_is_unverified(tmp_path):
    repo, source = code_repo(tmp_path)
    before = git(repo, "rev-parse", "HEAD")
    git(repo, "merge", "--ff-only", "feature")
    source_sha = git(source, "rev-parse", "HEAD")
    commit_file(source, "late.txt", "late\n", "late work")

    reason = verify_integration(target(repo, source), entry(source_sha, source_sha, "ff", before))

    assert reason == "source branch moved since the receipt"


def test_attested_entry_needs_only_reachability(tmp_path):
    repo, source = code_repo(tmp_path)
    tip = commit_file(repo, "c.txt", "main\n", "hand-integrated elsewhere")
    attested = VerifiedIntegration(
        "code", "feature", git(source, "rev-parse", "HEAD"), "main", tip, "attested", "accepted", None, "pat", "rebased"
    )

    assert verify_integration(target(repo, source), attested) is None


def finish_target(layout):
    plan = resolve_finish_targets(layout, load_items(load_bundle(layout.bundle_dir, ignore=IGNORE)), OWNER)
    assert plan.blockers == ()
    return plan.targets[0]


def test_default_strategy_is_squash_when_unconfigured(tmp_path):
    repo, source = code_repo(tmp_path)
    layout = workspace(tmp_path, repo, source)

    assert finish_strategy(layout, "code") == ("squash", "default")
    assert finish_target(layout).default_strategy == "squash"


def test_configured_strategy_is_the_default(tmp_path):
    repo, source = code_repo(tmp_path)
    layout = workspace(tmp_path, repo, source, finish_config="    finish:\n      strategy: merge\n")

    assert finish_strategy(layout, "code") == ("merge", "config")
    assert finish_target(layout).default_strategy == "merge"


def test_local_overlay_strategy_wins(tmp_path):
    repo, source = code_repo(tmp_path)
    layout = workspace(tmp_path, repo, source, finish_config="    finish:\n      strategy: merge\n")
    layout.local_manifest_path.write_text(
        "repositories:\n  code:\n    finish:\n      strategy: ff\n", encoding="utf-8", newline="\n"
    )

    assert finish_strategy(layout, "code") == ("ff", "config")


@pytest.mark.parametrize(
    "block", ["    finish:\n      strategy: rebase\n", "    finish: squash\n", "    finish:\n      mode: ff\n"]
)
def test_invalid_strategy_configuration_is_a_config_error(tmp_path, block):
    repo, source = code_repo(tmp_path)
    layout = workspace(tmp_path, repo, source, finish_config=block)

    with pytest.raises(WorkspaceConfigError, match=r"repositories\.code\.finish"):
        finish_strategy(layout, "code")


def test_update_ref_compare_and_delete_refuses_a_moved_branch(tmp_path):
    """Pins the git contract finish-cleanup.md relies on."""
    repo, source = code_repo(tmp_path)
    stale = git(source, "rev-parse", "HEAD")
    commit_file(source, "late.txt", "late\n", "late work")
    git(repo, "worktree", "remove", str(source))

    refused = subprocess.run(
        ["git", "-C", str(repo), "update-ref", "-d", "refs/heads/feature", stale],
        capture_output=True,
        text=True,
        check=False,
    )

    assert refused.returncode != 0 and git(repo, "rev-parse", "refs/heads/feature") != stale


def test_finish_refused_carries_its_code_detail_and_conflicts():
    refusal = FinishRefused("conflict", "resolve it", ("a.txt",))

    assert (refusal.refusal, refusal.detail, refusal.conflicts) == ("conflict", "resolve it", ("a.txt",))
    assert str(refusal) == "conflict: resolve it"


def test_receipt_naming_a_repository_twice_is_malformed(tmp_path):
    repo, source = code_repo(tmp_path)
    layout = workspace(tmp_path, repo, source)
    sha = git(source, "rev-parse", "HEAD")
    write_receipt(layout, [VerifiedIntegration("code", "feature", sha, "main", sha)] * 2)

    assert read_finish_receipt(layout, OWNER)[2] == "duplicate finish receipt repository"


def test_finish_block_without_a_strategy_defaults_to_squash(tmp_path):
    repo, source = code_repo(tmp_path)
    layout = workspace(tmp_path, repo, source, finish_config="    finish: {}\n")

    assert finish_strategy(layout, "code") == ("squash", "default")
