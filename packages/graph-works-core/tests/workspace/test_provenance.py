"""Provenance degrades. Every function answers None (or no-ops) rather than
failing an advance."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from graph_works_core.workspace import provenance
from graph_works_core.workspace.layout import layout_for


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-m", "first")
    return root


def test_a_non_repo_has_no_worktree_state(tmp_path):
    assert provenance.worktree_state(tmp_path, tmp_path) is None


def test_probe_git_reports_returncode_and_cause(tmp_path):
    from graph_works_core.workspace.provenance import probe_git

    outside = probe_git(tmp_path, "rev-parse", "--is-inside-work-tree")
    assert outside.cause == "ok" and outside.returncode not in (0, None)

    missing = probe_git(tmp_path, "status", executable="git-definitely-not-installed")
    assert (missing.returncode, missing.cause) == (None, "missing")


@pytest.mark.parametrize(
    ("failure", "cause"),
    [
        (subprocess.TimeoutExpired(["git"], 5), "timeout"),
        (OSError("git transport failed"), "error"),
    ],
)
def test_probe_git_degrades_timeout_and_runner_errors(tmp_path, monkeypatch, failure, cause):
    def _fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(provenance.subprocess, "run", _fail)
    outcome = provenance.probe_git(tmp_path, "status")
    assert (outcome.returncode, outcome.stdout, outcome.cause) == (None, "", cause)


@pytest.mark.parametrize("raw", ["", "   "])
def test_a_blank_git_dir_answer_resolves_to_none(raw, tmp_path):
    # `Path("")` normalizes to `Path(".")`, whose str form is not empty --
    # this guards against that surprise resolving a blank answer to `cwd`.
    assert provenance._absolute(raw, tmp_path) is None


def test_the_main_checkout_is_not_a_linked_worktree(repo):
    assert provenance.worktree_state(repo, repo) is None


def test_a_linked_worktree_reports_its_toplevel_and_branch(repo, tmp_path):
    linked = tmp_path / "wt"
    _git(repo, "worktree", "add", "-b", "feature/x", str(linked))
    state = provenance.worktree_state(linked, repo)
    assert state is not None
    top, branch = state
    assert Path(top).resolve() == linked.resolve()
    assert branch == "feature/x"


def test_a_detached_head_has_no_branch_worth_stamping(repo, tmp_path):
    linked = tmp_path / "wt"
    _git(repo, "worktree", "add", "--detach", str(linked))
    assert provenance.worktree_state(linked, repo) is None


def test_a_linked_worktree_of_another_repo_is_refused(repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    _git(other, "config", "user.email", "t@example.com")
    _git(other, "config", "user.name", "T")
    (other / "b.txt").write_text("two\n", encoding="utf-8")
    _git(other, "add", "b.txt")
    _git(other, "commit", "-m", "first")
    linked = tmp_path / "wt"
    _git(other, "worktree", "add", "-b", "feature/y", str(linked))
    assert provenance.worktree_state(linked, repo) is None


def test_results_facts_gathers_the_range(repo):
    start = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / "b.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "second")
    facts = provenance.results_facts(repo, phase="execute", start_sha=start, paths=["b.txt"], opened="2026-01-01")
    assert facts is not None
    assert facts.phase == "execute"
    assert facts.start_sha == start
    assert facts.files == ("b.txt",)
    assert len(facts.commits) == 1
    assert facts.scope == ("b.txt",)


def test_results_facts_degrades_outside_a_repo(tmp_path):
    assert (
        provenance.results_facts(tmp_path, phase="execute", start_sha="deadbee", paths=["a.txt"], opened="2026-01-01")
        is None
    )


def test_results_facts_degrades_on_an_unknown_start_sha(repo):
    assert (
        provenance.results_facts(repo, phase="execute", start_sha="0" * 40, paths=["a.txt"], opened="2026-01-01")
        is None
    )


def test_results_facts_declines_an_unscoped_range(repo):
    # Empty `affects:` means we cannot attribute the item to anything -- an
    # unscoped page is worse than an absent one. Matches `commits_touching`'s
    # own contract in this module.
    facts = provenance.results_facts(repo, phase="execute", start_sha="HEAD", paths=[], opened="2026-01-01")
    assert facts is None


def test_results_facts_scopes_out_commits_outside_affects(repo):
    base = provenance.head_sha(repo)
    (repo / "in.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "in.txt")
    _git(repo, "commit", "-m", "touches in")
    (repo / "out.txt").write_text("y\n", encoding="utf-8")
    _git(repo, "add", "out.txt")
    _git(repo, "commit", "-m", "touches out")
    facts = provenance.results_facts(repo, phase="execute", start_sha=base, paths=["in.txt"], opened="2026-01-01")
    assert facts is not None
    assert facts.files == ("in.txt",)
    assert len(facts.commits) == 1
    assert "touches out" not in facts.commits[0]


def test_results_facts_flags_a_start_that_predates_the_item(repo):
    start = provenance.head_sha(repo)
    (repo / "b.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "second")
    # The fixture's one commit is made "now" by the test's own git config; an
    # `opened` far in the future reads as "the start predates the item."
    facts = provenance.results_facts(repo, phase="execute", start_sha=start, paths=["b.txt"], opened="2099-01-01")
    assert facts is not None
    assert facts.start_predates_item is True


def test_results_facts_does_not_flag_a_start_at_or_after_the_item(repo):
    start = provenance.head_sha(repo)
    (repo / "b.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "second")
    facts = provenance.results_facts(repo, phase="execute", start_sha=start, paths=["b.txt"], opened="2000-01-01")
    assert facts is not None
    assert facts.start_predates_item is False


def test_the_active_work_pointer_lands_in_the_cache_dir(tmp_path):
    layout = layout_for(tmp_path)
    written = provenance.write_active_work(layout, "work/feature-x", "execute", updated="2026-08-14")
    assert written == layout.cache_dir / provenance.ACTIVE_WORK_FILENAME
    assert json.loads(written.read_text(encoding="utf-8")) == {
        "path": "work/feature-x",
        "phase": "execute",
        "updated": "2026-08-14",
    }


def test_a_done_pointer_is_refused(tmp_path):
    # `done` is not an ARTIFACT_PHASES member and `artifact_path` raises on it,
    # so a done pointer would crash the *next* hook rather than this one.
    with pytest.raises(ValueError):
        provenance.write_active_work(layout_for(tmp_path), "work/feature-x", "done", updated="2026-08-14")


def test_a_noncanonical_active_work_path_is_refused(tmp_path):
    with pytest.raises(ValueError, match="canonical work-item path"):
        provenance.write_active_work(layout_for(tmp_path), "feature-x", "execute", updated="2026-08-14")


def test_writing_the_pointer_degrades_on_oserror(tmp_path, monkeypatch):
    layout = layout_for(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr(Path, "write_text", _boom)
    assert provenance.write_active_work(layout, "work/feature-x", "execute", updated="2026-08-14") is None


def test_clearing_removes_a_pointer_at_an_archived_path(tmp_path):
    layout = layout_for(tmp_path)
    provenance.write_active_work(layout, "work/feature-x", "execute", updated="2026-08-14")
    assert provenance.clear_active_work(layout, {"work/feature-x"}) is True
    assert not (layout.cache_dir / provenance.ACTIVE_WORK_FILENAME).exists()


def test_clearing_leaves_a_pointer_at_a_different_path(tmp_path):
    layout = layout_for(tmp_path)
    provenance.write_active_work(layout, "work/feature-x", "execute", updated="2026-08-14")
    assert provenance.clear_active_work(layout, {"work/feature-y"}) is False
    assert (layout.cache_dir / provenance.ACTIVE_WORK_FILENAME).exists()


@pytest.mark.parametrize("body", ["", "not json", "[1, 2]"])
def test_clearing_fails_open_on_a_broken_pointer(tmp_path, body):
    layout = layout_for(tmp_path)
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    (layout.cache_dir / provenance.ACTIVE_WORK_FILENAME).write_text(body, encoding="utf-8")
    assert provenance.clear_active_work(layout, {"work/feature-x"}) is False


def test_clearing_a_missing_pointer_is_a_no_op(tmp_path):
    assert provenance.clear_active_work(layout_for(tmp_path), {"work/feature-x"}) is False


def test_old_slug_pointer_is_discarded_as_invalid_coordination_state(tmp_path):
    layout = layout_for(tmp_path)
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    target = layout.cache_dir / provenance.ACTIVE_WORK_FILENAME
    target.write_text('{"slug":"feature-x","phase":"execute"}\n', encoding="utf-8")
    assert provenance.clear_active_work(layout, {"work/feature-x"}) is True
    assert not target.exists()


def test_noncanonical_path_pointer_is_discarded_as_invalid_coordination_state(tmp_path):
    layout = layout_for(tmp_path)
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    target = layout.cache_dir / provenance.ACTIVE_WORK_FILENAME
    target.write_text('{"path":"feature-x","phase":"execute"}\n', encoding="utf-8")
    assert provenance.clear_active_work(layout, {"work/feature-x"}) is True
    assert not target.exists()


def test_a_sibling_worktree_stamps_when_repo_is_itself_a_worktree(repo, tmp_path):
    # The case that flips from `None`. The old check required the common dir's
    # *parent* to equal `repo`, which is only true when `repo` is the main
    # checkout -- so a coordinator standing in one worktree could never stamp a
    # sibling. Two paths are the same repository exactly when their common dirs
    # agree, which is the property the docstring already claimed.
    first = tmp_path / "wt-a"
    second = tmp_path / "wt-b"
    _git(repo, "worktree", "add", "-b", "feature/a", str(first))
    _git(repo, "worktree", "add", "-b", "feature/b", str(second))
    state = provenance.worktree_state(second, first)
    assert state is not None
    top, branch = state
    assert Path(top).resolve() == second.resolve()
    assert branch == "feature/b"


def test_a_repo_that_is_not_a_repo_stamps_nothing(repo, tmp_path):
    linked = tmp_path / "wt"
    _git(repo, "worktree", "add", "-b", "feature/x", str(linked))
    plain = tmp_path / "plain"
    plain.mkdir()
    assert provenance.worktree_state(linked, plain) is None


def test_a_separate_git_dir_checkout_still_matches(tmp_path):
    # `--separate-git-dir` puts the common dir somewhere the layout inference
    # never guessed. Comparing common dirs is indifferent to where it lives.
    # The old parent-based check would have failed; the new common-dir equality
    # check works regardless of where the git directory physically lives.

    # Create a working directory and a separate location for the git directory
    repo_wd = tmp_path / "repo-separate"
    repo_wd.mkdir()
    git_dir = tmp_path / "git-storage"

    # Initialize with separate git dir
    _git(repo_wd, "init", "-b", "main", f"--separate-git-dir={git_dir}")
    _git(repo_wd, "config", "user.email", "t@example.com")
    _git(repo_wd, "config", "user.name", "T")
    (repo_wd / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo_wd, "add", "a.txt")
    _git(repo_wd, "commit", "-m", "first")

    # Add a linked worktree to the repo with separate git dir
    linked = tmp_path / "wt"
    _git(repo_wd, "worktree", "add", "-b", "feature/x", str(linked))

    # Verify worktree_state still correctly identifies the linked worktree
    state = provenance.worktree_state(linked, repo_wd)
    assert state is not None
    top, branch = state
    assert Path(top).resolve() == linked.resolve()
    assert branch == "feature/x"


# --- reconcile evidence: anchors, reachability, scoped log -------------------


def test_spec_anchor_commit_is_the_last_commit_touching_the_file(repo):
    spec = repo / "spec.md"
    spec.write_text("one\n", encoding="utf-8")
    _git(repo, "add", "spec.md")
    _git(repo, "commit", "-m", "add spec")
    expected = provenance.head_sha(repo)
    # A later commit that does NOT touch the spec must not become its anchor.
    (repo / "other.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-m", "unrelated")
    assert provenance.spec_anchor_commit(repo, spec) == expected


def test_an_untracked_spec_has_no_anchor(repo):
    spec = repo / "untracked.md"
    spec.write_text("one\n", encoding="utf-8")
    assert provenance.spec_anchor_commit(repo, spec) is None


def test_a_spec_outside_the_repo_has_no_anchor(repo, tmp_path):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("one\n", encoding="utf-8")
    assert provenance.spec_anchor_commit(repo, outside) is None


def test_a_non_repo_yields_no_anchor(tmp_path):
    assert provenance.spec_anchor_commit(tmp_path, tmp_path / "a.md") is None


def test_commit_exists_for_a_real_sha(repo):
    sha = provenance.head_sha(repo)
    assert sha is not None
    assert provenance.commit_exists(repo, sha) is True


@pytest.mark.parametrize("sha", ["", "0123456789abcdef0123456789abcdef01234567"])
def test_commit_exists_is_false_for_a_blank_or_fabricated_sha(repo, sha):
    assert provenance.commit_exists(repo, sha) is False


def test_commit_exists_is_false_outside_a_repo(tmp_path):
    assert provenance.commit_exists(tmp_path, "abc1234") is False


def test_commits_touching_is_newest_first_and_path_scoped(repo):
    base = provenance.head_sha(repo)
    for name, message in (("in.txt", "touches in"), ("out.txt", "touches out"), ("in.txt", "touches in again")):
        (repo / name).write_text(f"{message}\n", encoding="utf-8")
        _git(repo, "add", name)
        _git(repo, "commit", "-m", message)
    rows = provenance.commits_touching(repo, f"{base}..HEAD", ["in.txt"])
    assert [subject for _, subject in rows] == ["touches in again", "touches in"]
    assert all(len(sha) == 40 for sha, _ in rows)


def test_commits_touching_returns_a_tuple_of_pairs(repo):
    base = provenance.head_sha(repo)
    (repo / "in.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "in.txt")
    _git(repo, "commit", "-m", "one")
    rows = provenance.commits_touching(repo, f"{base}..HEAD", ["in.txt"])
    assert isinstance(rows, tuple)
    assert all(isinstance(row, tuple) and len(row) == 2 for row in rows)


def test_an_empty_paths_never_widens_to_the_whole_log(repo):
    base = provenance.head_sha(repo)
    (repo / "in.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "in.txt")
    _git(repo, "commit", "-m", "one")
    assert provenance.commits_touching(repo, f"{base}..HEAD", []) == ()


def test_a_bad_range_reads_as_nothing_landed(repo):
    assert provenance.commits_touching(repo, "nope..HEAD", ["a.txt"]) == ()


def test_a_subject_carrying_separators_survives_intact(repo):
    base = provenance.head_sha(repo)
    subject = "feat(core): add\tthing -- with: punctuation"
    (repo / "in.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "in.txt")
    _git(repo, "commit", "-m", subject)
    rows = provenance.commits_touching(repo, f"{base}..HEAD", ["in.txt"])
    assert [s for _, s in rows] == [subject]


def test_merge_base_finds_the_fork_point(repo, tmp_path):
    _git(repo, "checkout", "-b", "feature/x")
    (repo / "b.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "second")
    base = provenance.merge_base(repo, "main", "HEAD")
    assert base is not None
    assert (
        base
        == subprocess.run(
            ["git", "rev-parse", "main"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
    )


def test_merge_base_on_the_base_branch_is_head(repo):
    # The honest main-mode degrade: an empty range, which `results.render()`
    # already warns about, rather than a wrong one.
    assert provenance.merge_base(repo, "main", "HEAD") == provenance.head_sha(repo)


def test_merge_base_outside_a_repo_is_none(tmp_path):
    assert provenance.merge_base(tmp_path, "main", "HEAD") is None


def test_merge_base_with_an_unknown_ref_is_none(repo):
    assert provenance.merge_base(repo, "no-such-branch", "HEAD") is None


def test_default_base_falls_back_when_there_is_no_origin(repo):
    # A local repo with no remote has no `refs/remotes/origin/HEAD`.
    assert provenance.default_base(repo) == provenance.FALLBACK_BASE


def test_default_base_of_no_repo_is_the_fallback():
    assert provenance.default_base(None) == provenance.FALLBACK_BASE


def test_default_base_reads_the_origin_head_symref(repo, tmp_path):
    _git(repo, "remote", "add", "origin", str(tmp_path / "nowhere"))
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert provenance.default_base(repo) == "trunk"


def test_dirty_paths_reports_an_untracked_file_in_scope(repo):
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    assert provenance.dirty_paths(repo, ["b.txt"]) == ("b.txt",)


def test_dirty_paths_reports_a_modified_tracked_file_in_scope(repo):
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    assert provenance.dirty_paths(repo, ["a.txt"]) == ("a.txt",)


def test_dirty_paths_ignores_dirt_outside_the_declared_scope(repo):
    (repo / "elsewhere").mkdir()
    (repo / "elsewhere/x.txt").write_text("new\n", encoding="utf-8")
    assert provenance.dirty_paths(repo, ["a.txt"]) == ()


def test_dirty_paths_declines_an_unscoped_read(repo):
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    assert provenance.dirty_paths(repo, []) is None


def test_dirty_paths_degrades_outside_a_repo(tmp_path):
    assert provenance.dirty_paths(tmp_path, ["a.txt"]) is None


def test_dirty_paths_unquotes_a_path_git_would_escape(repo):
    (repo / "spaced name.txt").write_text("new\n", encoding="utf-8")
    assert provenance.dirty_paths(repo, ["spaced name.txt"]) == ("spaced name.txt",)
