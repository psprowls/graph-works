"""Acceptance tests for `scripts/check_subtree_base.py`.

Outside the repo's coverage `source` list for the same reason as
`test_audit_delta.py`: `scripts/` is repo tooling, not a package, so these do not
move the 95% gate.

The git-backed cases build real repositories rather than stubbing `git`. The
thing under test is a claim about git's own reachability rules — that a
squash-merge orphans the subtree note and a merge commit keeping it as a second
parent does not — and a stub cannot be wrong about that in the same way git can.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_subtree_base import (  # noqa: E402
    PREFIX,
    BaseError,
    Report,
    check,
    check_prefix_rooted,
    find_latest_squash,
    ledger_sha,
)

import pytest  # noqa: E402

SPLIT = "b36e0829c6d0140e93cfef2ca599b1b07d4a7797"
OLDER = "7cb90bfc909442f5bb0d7aa6e39c12f28c925ced"


def run(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / PREFIX).mkdir(parents=True)
    run("init", "-q", "-b", "main", str(repo), cwd=tmp_path)
    run("config", "user.email", "t@example.com", cwd=repo)
    run("config", "user.name", "T", cwd=repo)
    (repo / "justfile").write_text("default:\n")
    (repo / PREFIX / "SKILL.md").write_text("vendored\n")
    run("add", "-A", cwd=repo)
    run("commit", "-qm", "root", cwd=repo)
    return repo


def add_squash(repo: Path, split: str, subject: str) -> str:
    """A commit carrying the subtree trailers, as `git subtree` writes them."""
    message = f"{subject}\n\ngit-subtree-dir: {PREFIX}\ngit-subtree-split: {split}\n"
    (repo / PREFIX / "SKILL.md").write_text(f"vendored {split[:7]}\n")
    run("add", "-A", cwd=repo)
    run("commit", "-qm", message, cwd=repo)
    return run("rev-parse", "HEAD", cwd=repo)


def write_sync(repo: Path, sha: str) -> None:
    (repo / "plugins").mkdir(exist_ok=True)
    (repo / "plugins" / "SYNC.md").write_text(
        "# Sync\n\n## Merge ledger\n\n| Date | Tag | Split | Merge |\n"
        "|---|---|---|---|\n"
        f"| 2026-08-17 | v6.3.0 | `{sha}` | `deadbeef` |\n"
    )


def test_ledger_sha_takes_the_last_row():
    sync = f"| a | `{OLDER}` |\n| b | `{SPLIT}` |\n"
    assert ledger_sha(sync) == SPLIT


def test_ledger_sha_ignores_prose_shas():
    """The Recovery section discusses split SHAs; only table rows are claims."""
    sync = f"Recovery talks about `{OLDER}` in prose.\n\n| a | `{SPLIT}` |\n"
    assert ledger_sha(sync) == SPLIT


def test_ledger_sha_missing_is_an_error():
    with pytest.raises(BaseError, match="no subtree-split SHA"):
        ledger_sha("no rows here\n")


def test_find_latest_squash_takes_the_newest(tmp_path):
    """Two imports: the older one is dead lineage and must not be returned."""
    repo = make_repo(tmp_path)
    add_squash(repo, OLDER, "Squashed 'plugins/graph-works/' content")
    newest = add_squash(repo, SPLIT, "Squashed 'plugins/graph-works/' changes")
    commit, split = find_latest_squash(repo)
    assert (commit, split) == (newest, SPLIT)


def test_find_latest_squash_reaches_through_a_second_parent(tmp_path):
    """The whole point of the merge-commit shape: reachability, not first-parent."""
    repo = make_repo(tmp_path)
    run("checkout", "-q", "-b", "vendor", cwd=repo)
    imported = add_squash(repo, SPLIT, "Squashed 'plugins/graph-works/' content")
    run("checkout", "-q", "main", cwd=repo)
    run("merge", "-q", "--no-ff", "--no-edit", "-m", "epic", "vendor", cwd=repo)
    assert find_latest_squash(repo) == (imported, SPLIT)


def test_find_latest_squash_is_an_error_when_orphaned(tmp_path):
    """A squash-merge leaves the note unreachable — this is the failure to catch."""
    repo = make_repo(tmp_path)
    run("checkout", "-q", "-b", "vendor", cwd=repo)
    add_squash(repo, SPLIT, "Squashed 'plugins/graph-works/' content")
    run("checkout", "-q", "main", cwd=repo)
    run("merge", "--squash", "vendor", cwd=repo)
    run("commit", "-qm", "epic, squashed", cwd=repo)
    with pytest.raises(BaseError, match="subtree merge base is gone"):
        find_latest_squash(repo)


def test_check_flags_ledger_disagreement(tmp_path):
    repo = make_repo(tmp_path)
    add_squash(repo, SPLIT, "Squashed 'plugins/graph-works/' content")
    write_sync(repo, OLDER)
    report = check(repo)
    assert not report.ok
    assert "ledger row" in report.failures[0]


def test_check_is_ok_when_the_ledger_agrees(tmp_path):
    repo = make_repo(tmp_path)
    add_squash(repo, SPLIT, "Squashed 'plugins/graph-works/' content")
    write_sync(repo, SPLIT)
    assert check(repo).ok


def test_prefix_rooted_falls_back_when_upstream_is_absent(tmp_path):
    """A clone that never fetched `upstream` gets the structural check, not a failure."""
    repo = make_repo(tmp_path)
    squash = add_squash(repo, SPLIT, "Squashed 'plugins/graph-works/' content")
    report = Report()
    check_prefix_rooted(squash, SPLIT, repo, report)
    assert not report.ok  # this synthetic commit IS repo-rooted, and is caught as such
    assert "repo-rooted" in report.failures[0]
    assert any("structural check only" in note for note in report.notes)
