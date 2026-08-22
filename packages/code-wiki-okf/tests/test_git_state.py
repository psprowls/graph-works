import subprocess
from pathlib import Path

import pytest
from code_wiki_okf.git_state import (
    StateGate,
    changed_files_since,
    compute_state_gate,
    earliest_common_ancestor,
    find_renames,
    head_commit,
    is_clean_on_branches,
    ls_files,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "scratch"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "keep.txt").write_text("a\n")
    (repo / "skip.lock").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_head_commit_returns_sha(scratch_repo: Path) -> None:
    sha = head_commit(scratch_repo)
    assert sha is not None
    assert len(sha) == 40


def test_head_commit_none_outside_a_repo(tmp_path: Path) -> None:
    assert head_commit(tmp_path) is None


def test_is_clean_on_branches_true_on_allowed_clean_branch(scratch_repo: Path) -> None:
    ok, reason = is_clean_on_branches(scratch_repo, ["main"])
    assert ok is True
    assert reason == ""


def test_is_clean_on_branches_false_on_disallowed_branch(scratch_repo: Path) -> None:
    ok, reason = is_clean_on_branches(scratch_repo, ["release"])
    assert ok is False
    assert "not in" in reason


def test_is_clean_on_branches_false_when_dirty(scratch_repo: Path) -> None:
    (scratch_repo / "keep.txt").write_text("changed\n")
    ok, reason = is_clean_on_branches(scratch_repo, ["main"])
    assert ok is False
    assert reason == "working tree is dirty"


def test_changed_files_since_lists_changed_paths(scratch_repo: Path) -> None:
    first_sha = head_commit(scratch_repo)
    assert first_sha is not None
    (scratch_repo / "keep.txt").write_text("changed\n")
    _git(scratch_repo, "commit", "-a", "-q", "-m", "second")
    assert changed_files_since(scratch_repo, first_sha) == ["keep.txt"]


def test_changed_files_since_none_for_unknown_sha(scratch_repo: Path) -> None:
    assert changed_files_since(scratch_repo, "0" * 40) is None


def test_ls_files_lists_everything_without_exclude(scratch_repo: Path) -> None:
    assert ls_files(scratch_repo) == ["keep.txt", "skip.lock"]


def test_ls_files_honors_exclude_glob(scratch_repo: Path) -> None:
    assert ls_files(scratch_repo, exclude=["*.lock"]) == ["keep.txt"]


def test_ls_files_none_outside_a_repo(tmp_path: Path) -> None:
    assert ls_files(tmp_path) is None


def test_compute_state_gate_disabled_is_always_allowed(scratch_repo: Path) -> None:
    gate = compute_state_gate(scratch_repo, enabled=False, branches=["main"])
    assert gate.allowed is True
    assert gate.reason == "state gate disabled in workspace.yaml"


def test_compute_state_gate_enabled_allowed_on_clean_main(scratch_repo: Path) -> None:
    gate = compute_state_gate(scratch_repo, enabled=True, branches=["main"])
    expected = StateGate(
        allowed=True,
        reason="clean and on an allowed branch",
        head_commit=head_commit(scratch_repo),
    )
    assert gate == expected


def test_compute_state_gate_enabled_refused_when_dirty(scratch_repo: Path) -> None:
    (scratch_repo / "keep.txt").write_text("changed\n")
    gate = compute_state_gate(scratch_repo, enabled=True, branches=["main"])
    assert gate.allowed is False
    assert gate.reason == "working tree is dirty"


def test_is_clean_on_branches_outside_repo(tmp_path: Path) -> None:
    ok, reason = is_clean_on_branches(tmp_path, ["main"])
    assert ok is False
    assert reason == "not a git repo"


def test_changed_files_since_with_empty_sha(scratch_repo: Path) -> None:
    assert changed_files_since(scratch_repo, "") is None


def test_changed_files_since_no_changes(scratch_repo: Path) -> None:
    sha = head_commit(scratch_repo)
    assert sha is not None
    assert changed_files_since(scratch_repo, sha) == []


def test_ls_files_with_multiple_excludes(scratch_repo: Path) -> None:
    result = ls_files(scratch_repo, exclude=["*.lock", "keep.*"])
    assert result == []


def test_changed_files_since_with_sub_paths(scratch_repo: Path) -> None:
    first_sha = head_commit(scratch_repo)
    assert first_sha is not None
    (scratch_repo / "keep.txt").write_text("changed\n")
    (scratch_repo / "skip.lock").write_text("changed\n")
    _git(scratch_repo, "commit", "-a", "-q", "-m", "second")
    result = changed_files_since(scratch_repo, first_sha, sub_paths=["skip.lock"])
    assert result == ["skip.lock"]


def test_compute_state_gate_disabled_has_head_commit(scratch_repo: Path) -> None:
    gate = compute_state_gate(scratch_repo, enabled=False, branches=["main"])
    assert gate.head_commit is not None
    assert len(gate.head_commit) == 40


def test_find_renames_reports_a_plain_rename(scratch_repo: Path) -> None:
    first_sha = head_commit(scratch_repo)
    assert first_sha is not None
    _git(scratch_repo, "mv", "keep.txt", "renamed.txt")
    _git(scratch_repo, "commit", "-q", "-m", "rename")
    assert find_renames(scratch_repo, first_sha) == [("keep.txt", "renamed.txt")]


def test_find_renames_empty_when_nothing_renamed(scratch_repo: Path) -> None:
    first_sha = head_commit(scratch_repo)
    assert first_sha is not None
    (scratch_repo / "keep.txt").write_text("changed\n")
    _git(scratch_repo, "commit", "-a", "-q", "-m", "edit only")
    assert find_renames(scratch_repo, first_sha) == []


def test_find_renames_none_for_unknown_base(scratch_repo: Path) -> None:
    assert find_renames(scratch_repo, "0" * 40) is None


def test_find_renames_none_outside_a_repo(tmp_path: Path) -> None:
    assert find_renames(tmp_path, "0" * 40) is None


def test_find_renames_detects_rename_plus_edit(scratch_repo: Path) -> None:
    # Enough shared content that the post-rename edit stays above git's
    # default 50% similarity threshold — a one-line file rewritten wholesale
    # would land below it and show up as delete+add, not a rename.
    (scratch_repo / "keep.txt").write_text("line one\nline two\nline three\nline four\n")
    _git(scratch_repo, "commit", "-a", "-q", "-m", "expand keep.txt")
    first_sha = head_commit(scratch_repo)
    assert first_sha is not None
    _git(scratch_repo, "mv", "keep.txt", "renamed.txt")
    (scratch_repo / "renamed.txt").write_text("line one\nline two\nline three\nchanged\n")
    _git(scratch_repo, "commit", "-a", "-q", "-m", "rename and edit")
    assert find_renames(scratch_repo, first_sha) == [("keep.txt", "renamed.txt")]


def test_find_renames_explicit_head_argument(scratch_repo: Path) -> None:
    first_sha = head_commit(scratch_repo)
    assert first_sha is not None
    _git(scratch_repo, "mv", "keep.txt", "renamed.txt")
    _git(scratch_repo, "commit", "-q", "-m", "rename")
    last_sha = head_commit(scratch_repo)
    assert last_sha is not None
    assert find_renames(scratch_repo, first_sha, head=last_sha) == [("keep.txt", "renamed.txt")]


def test_earliest_common_ancestor_of_one_sha_is_itself(scratch_repo: Path) -> None:
    sha = head_commit(scratch_repo)
    assert sha is not None
    assert earliest_common_ancestor(scratch_repo, [sha]) == sha


def test_earliest_common_ancestor_of_a_linear_pair_is_the_older_commit(scratch_repo: Path) -> None:
    first_sha = head_commit(scratch_repo)
    assert first_sha is not None
    (scratch_repo / "keep.txt").write_text("changed\n")
    _git(scratch_repo, "commit", "-a", "-q", "-m", "second")
    second_sha = head_commit(scratch_repo)
    assert second_sha is not None

    # Regardless of which SHA sorts lower as a hex string, the ancestor of a
    # linear pair is always the earlier commit -- the property a naive
    # `min()` over the strings cannot guarantee.
    assert earliest_common_ancestor(scratch_repo, [first_sha, second_sha]) == first_sha
    assert earliest_common_ancestor(scratch_repo, [second_sha, first_sha]) == first_sha


def test_earliest_common_ancestor_ignores_duplicates(scratch_repo: Path) -> None:
    sha = head_commit(scratch_repo)
    assert sha is not None
    assert earliest_common_ancestor(scratch_repo, [sha, sha, sha]) == sha


def test_earliest_common_ancestor_none_for_empty_shas(scratch_repo: Path) -> None:
    assert earliest_common_ancestor(scratch_repo, []) is None


def test_earliest_common_ancestor_none_for_unknown_sha(scratch_repo: Path) -> None:
    assert earliest_common_ancestor(scratch_repo, ["0" * 40]) is None


def test_earliest_common_ancestor_none_outside_a_repo(tmp_path: Path) -> None:
    assert earliest_common_ancestor(tmp_path, ["0" * 40]) is None
