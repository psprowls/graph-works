"""Each receipt strategy verifies exactly the history shape it claims."""

from _finish_repos import code_repo, commit_file, git, squash, target
from graph_works_core.workspace.finish import VerifiedIntegration, verify_integration


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
