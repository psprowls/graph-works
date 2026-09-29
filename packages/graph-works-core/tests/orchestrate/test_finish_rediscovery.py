"""`record` rediscovers how a landed integration was made, without another merge."""

from _finish_repos import OWNER, TODAY, code_repo, commit_file, git, squash, workspace
from graph_works_core.orchestrate.finish_receipt import run_record_finish
from graph_works_core.workspace.finish import inspect_finish, read_finish_receipt


def record(layout):
    return run_record_finish(layout, OWNER, repo_name="code", today=TODAY)


def recorded(layout):
    _doc, entries, error = read_finish_receipt(layout, OWNER)
    assert error is None
    (entry,) = entries
    return entry


def test_landed_merge_commit_is_rediscovered(tmp_path):
    repo, source = code_repo(tmp_path)
    before = commit_file(repo, "c.txt", "main\n", "main moves")
    git(repo, "merge", "--no-ff", "--no-edit", "feature")

    layout = workspace(tmp_path, repo, source)
    assert record(layout).refusal is None

    entry = recorded(layout)
    assert (entry.strategy, entry.target_before, entry.result_commit) == (
        "merge",
        before,
        git(repo, "rev-parse", "HEAD"),
    )
    assert inspect_finish(layout, OWNER).complete


def test_landed_fast_forward_reads_as_ancestry(tmp_path):
    repo, source = code_repo(tmp_path)
    git(repo, "merge", "--ff-only", "feature")

    layout = workspace(tmp_path, repo, source)
    assert record(layout).refusal is None

    assert (recorded(layout).strategy, recorded(layout).target_before) == ("ancestry", None)


def test_landed_squash_is_rediscovered_after_the_target_moves_on(tmp_path):
    repo, source = code_repo(tmp_path)
    commit_file(repo, "c.txt", "main\n", "main moves")
    before, result = squash(repo)
    commit_file(repo, "d.txt", "later\n", "later work")

    layout = workspace(tmp_path, repo, source)
    assert record(layout).refusal is None

    entry = recorded(layout)
    assert (entry.strategy, entry.target_before, entry.result_commit) == ("squash", before, result)
    assert inspect_finish(layout, OWNER).complete


def test_two_squash_candidates_refuse_as_ambiguous(tmp_path):
    repo, source = code_repo(tmp_path)
    squash(repo)
    git(repo, "rm", "-q", "b.txt")
    git(repo, "commit", "-m", "drop b")
    squash(repo)

    refusal = record(workspace(tmp_path, repo, source)).refusal

    assert refusal is not None and "ambiguous" in refusal


def test_empty_commits_are_not_squash_candidates(tmp_path):
    repo, source = code_repo(tmp_path)
    _before, result = squash(repo)
    git(repo, "commit", "--allow-empty", "-m", "empty")

    layout = workspace(tmp_path, repo, source)
    assert record(layout).refusal is None
    assert recorded(layout).result_commit == result


def test_rerecord_drops_stale_optional_keys(tmp_path):
    repo, source = code_repo(tmp_path)
    commit_file(repo, "c.txt", "main\n", "main moves")
    squash(repo)
    layout = workspace(tmp_path, repo, source)
    assert record(layout).refusal is None and recorded(layout).strategy == "squash"
    git(source, "rebase", "-q", "main")  # the squashed change drops out; feature == main
    assert git(source, "rev-parse", "HEAD") == git(repo, "rev-parse", "main")

    assert record(layout).refusal is None

    entry = recorded(layout)
    assert (entry.strategy, entry.target_before) == ("ancestry", None)
    assert "target_before" not in (layout.bundle_dir / OWNER / "references/04-finish-receipt.md").read_text(
        encoding="utf-8"
    )


def test_new_receipts_are_version_2(tmp_path):
    repo, source = code_repo(tmp_path)
    git(repo, "merge", "--ff-only", "feature")

    layout = workspace(tmp_path, repo, source)
    assert record(layout).refusal is None

    assert "receipt_version: 2\n" in (layout.bundle_dir / OWNER / "references/04-finish-receipt.md").read_text(
        encoding="utf-8"
    )
