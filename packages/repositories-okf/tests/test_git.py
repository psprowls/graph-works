from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from gitrepo import GIT, Upstream, git, make_upstream, missing_objects
from repositories_okf import git as g
from repositories_okf.pin import is_sha

RUNNER = g.Git(executable=GIT, environ=dict(os.environ))


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    up = make_upstream(tmp_path)
    up.commit(
        {"README.md": "# demo\n", "src/a.py": "a = 1\n" * 50, "src/b.py": "b = 1\n" * 50, "docs/x.md": "x\n"},
        "c1",
        tag="v1.0.0",
    )
    return up


def _clone(tmp_path: Path, upstream: Upstream, commit: str) -> Path:
    clone = tmp_path / "bundle" / "repositories" / "demo" / "references" / "git"
    assert g.materialize(RUNNER, upstream.url, clone, commit, incoming=tmp_path / "cache" / "incoming-1") is None
    return clone


def test_is_sha() -> None:
    assert is_sha("0123456789abcdef0123456789abcdef01234567")
    assert not is_sha("0123456")
    assert not is_sha("0123456789ABCDEF0123456789ABCDEF01234567")
    assert not is_sha(None)


def test_default_branch_reads_the_remote_head(tmp_path: Path) -> None:
    up = make_upstream(tmp_path, default_branch="trunk")
    up.commit({"f": "1\n"}, "c1", branch="trunk")
    assert g.default_branch(RUNNER, up.url, cwd=tmp_path) == "trunk"


def test_default_branch_of_an_unreachable_remote_is_a_failure(tmp_path: Path) -> None:
    result = g.default_branch(RUNNER, (tmp_path / "nope.git").as_uri(), cwd=tmp_path)
    assert isinstance(result, g.GitFailure)
    assert result.cause == "nonzero"


def test_resolve_ref_peels_an_annotated_tag_and_resolves_branches(upstream: Upstream, tmp_path: Path) -> None:
    head = git(upstream.work, "rev-parse", "HEAD")
    assert g.resolve_ref(RUNNER, upstream.url, "v1.0.0", cwd=tmp_path) == head
    assert g.resolve_ref(RUNNER, upstream.url, "refs/tags/v1.0.0", cwd=tmp_path) == head
    assert g.resolve_ref(RUNNER, upstream.url, "main", cwd=tmp_path) == head
    assert g.resolve_ref(RUNNER, upstream.url, head, cwd=tmp_path) == head
    missing = g.resolve_ref(RUNNER, upstream.url, "no-such-ref", cwd=tmp_path)
    assert isinstance(missing, g.GitFailure) and missing.cause == "not-found"


def test_materialize_leaves_a_detached_blobless_clone_and_no_incoming(upstream: Upstream, tmp_path: Path) -> None:
    head = git(upstream.work, "rev-parse", "HEAD")
    clone = _clone(tmp_path, upstream, head)
    assert g.head_state(RUNNER, clone) == g.HeadState(commit=head, detached=True)
    assert g.origin_url(RUNNER, clone) == upstream.url
    assert g.is_clean(RUNNER, clone) is True
    assert git(clone, "config", "remote.origin.partialclonefilter") == "blob:none"
    assert not (tmp_path / "cache" / "incoming-1").exists()


def test_materialize_fetches_a_pin_the_default_branch_does_not_contain(upstream: Upstream, tmp_path: Path) -> None:
    side = upstream.commit({"side.txt": "s\n"}, "side", branch="side")
    clone = _clone(tmp_path, upstream, side)
    assert g.head_state(RUNNER, clone) == g.HeadState(commit=side, detached=True)


def test_materialize_of_an_unknown_commit_cleans_up_and_says_not_found(upstream: Upstream, tmp_path: Path) -> None:
    clone = tmp_path / "bundle" / "repositories" / "demo" / "references" / "git"
    incoming = tmp_path / "cache" / "incoming-2"
    failure = g.materialize(RUNNER, upstream.url, clone, "0123456789abcdef0123456789abcdef01234567", incoming=incoming)
    assert isinstance(failure, g.GitFailure) and failure.cause == "not-found"
    assert not clone.exists()
    assert not incoming.exists()


def test_is_clean_sees_untracked_and_modified_files(upstream: Upstream, tmp_path: Path) -> None:
    clone = _clone(tmp_path, upstream, git(upstream.work, "rev-parse", "HEAD"))
    (clone / "new.txt").write_text("x\n", encoding="utf-8", newline="")
    assert g.is_clean(RUNNER, clone) is False
    (clone / "new.txt").unlink()
    (clone / "README.md").write_text("changed\n", encoding="utf-8", newline="")
    assert g.is_clean(RUNNER, clone) is False


def test_fetch_detach_describe_and_commit_facts(upstream: Upstream, tmp_path: Path) -> None:
    old = git(upstream.work, "rev-parse", "HEAD")
    clone = _clone(tmp_path, upstream, old)
    new = upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    assert g.has_commit(RUNNER, clone, new) is False
    assert g.fetch(RUNNER, clone, "main") == new
    assert g.has_commit(RUNNER, clone, new) is True
    assert g.head_state(RUNNER, clone).commit == old  # fetch never moves HEAD
    assert g.detach(RUNNER, clone, new) is None
    assert g.describe(RUNNER, clone, new) == f"v1.0.0-1-g{new[:7]}"
    facts = g.commit_facts(RUNNER, clone, new)
    assert isinstance(facts, g.CommitFacts)
    assert facts.commit == new and is_sha(facts.tree) and facts.commit_date[:4].isdigit()


def test_describe_without_tags_is_none(tmp_path: Path) -> None:
    up = make_upstream(tmp_path)
    head = up.commit({"f": "1\n"}, "c1")
    clone = _clone(tmp_path, up, head)
    assert g.describe(RUNNER, clone, head) is None


def test_fetch_of_a_missing_ref_is_not_found(upstream: Upstream, tmp_path: Path) -> None:
    clone = _clone(tmp_path, upstream, git(upstream.work, "rev-parse", "HEAD"))
    failure = g.fetch(RUNNER, clone, "no-such-branch")
    assert isinstance(failure, g.GitFailure) and failure.cause == "not-found"


def test_range_facts_fast_forward_with_modify_delete_rename_tag_and_merge(upstream: Upstream, tmp_path: Path) -> None:
    old = git(upstream.work, "rev-parse", "HEAD")
    clone = _clone(tmp_path, upstream, old)
    upstream.commit({"src/a.py": "a = 2\n", "docs/x.md": None}, "c2", tag="v1.1.0")
    upstream.rename("src/b.py", "src/c.py", "c3")
    new = upstream.merge("feature", {"src/d.py": "d\n"}, "Merge feature")
    g.fetch(RUNNER, clone, "main")
    before = missing_objects(clone)
    facts = g.range_facts(RUNNER, clone, old, new)
    assert isinstance(facts, g.RangeFacts)
    assert missing_objects(clone) == before  # the diff fetched no blobs
    assert (facts.base, facts.rewritten) == (old, False)
    assert facts.commits == 4  # c2, c3, the feature-branch commit, the merge
    assert set(facts.changes) == {
        g.FileChange("M", "src/a.py"),
        g.FileChange("D", "docs/x.md"),
        g.FileChange("R", "src/b.py", "src/c.py"),
        g.FileChange("A", "src/d.py"),
    }
    assert facts.files_changed == 4
    assert facts.merges == ("Merge feature",)
    assert facts.tags == ("v1.1.0",)


def test_range_facts_after_a_rewrite_runs_from_the_merge_base(upstream: Upstream, tmp_path: Path) -> None:
    base = git(upstream.work, "rev-parse", "HEAD")
    old = upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    clone = _clone(tmp_path, upstream, old)
    new = upstream.rewrite({"src/a.py": "a = 3\n", "src/e.py": "e\n"}, "c2-rewritten")
    g.fetch(RUNNER, clone, "main")
    facts = g.range_facts(RUNNER, clone, old, new)
    assert isinstance(facts, g.RangeFacts)
    assert (facts.base, facts.rewritten, facts.commits) == (base, True, 1)
    assert {change.path for change in facts.changes} == {"src/a.py", "src/e.py"}


def test_remove_tree_removes_read_only_git_objects(upstream: Upstream, tmp_path: Path) -> None:
    clone = _clone(tmp_path, upstream, git(upstream.work, "rev-parse", "HEAD"))
    g.remove_tree(clone.parent.parent)
    assert not clone.parent.parent.exists()
    g.remove_tree(clone.parent.parent)  # absent is a no-op


def test_a_missing_executable_is_a_failure_value(tmp_path: Path) -> None:
    runner = g.Git(executable=str(tmp_path / "no-git"), environ={})
    result = g.head_state(runner, tmp_path)
    assert isinstance(result, g.GitFailure) and result.cause == "missing"


@pytest.mark.parametrize("step", range(7))
def test_range_failures_return_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, step: int) -> None:
    responses = [
        g._Ran(1, "", ""),
        g._Ran(0, "a" * 40, ""),
        g._Ran(0, "1", ""),
        g._Ran(0, "", ""),
        g._Ran(0, "", ""),
        g._Ran(0, "", ""),
    ]
    failure = g.GitFailure("timeout", "git", "timed out")
    if step == 6:
        responses[1] = g._Ran(2, "", "broken")
    else:
        responses[step] = failure
    monkeypatch.setattr(g, "_run", lambda *args, **kwargs: responses.pop(0))
    assert isinstance(g.range_facts(RUNNER, tmp_path, "a" * 40, "b" * 40), g.GitFailure)


def test_runner_timeout_and_origin_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import subprocess

    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(g.subprocess, "run", timeout)
    assert g.origin_url(RUNNER, tmp_path).cause == "timeout"
    assert isinstance(g.commit_facts(RUNNER, tmp_path, "a" * 40), g.GitFailure)
    for code, expected in [(1, None), (2, "nonzero")]:
        monkeypatch.setattr(
            g.subprocess, "run", lambda *args, code=code, **kwargs: subprocess.CompletedProcess([], code, "", "boom")
        )
        result = g.origin_url(RUNNER, tmp_path)
        assert result is None if expected is None else result.cause == expected


def test_missing_default_and_ref_and_symbolic_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(g, "_checked", lambda *args, **kwargs: "")
    assert isinstance(g.default_branch(RUNNER, "url", cwd=tmp_path), g.GitFailure)
    monkeypatch.setattr(g, "_checked", lambda *args, **kwargs: g.GitFailure("missing", "git", "missing"))
    assert isinstance(g.resolve_ref(RUNNER, "url", "main", cwd=tmp_path), g.GitFailure)
    results = [g._Ran(0, "a" * 40, ""), g.GitFailure("missing", "git", "missing")]
    monkeypatch.setattr(g, "_run", lambda *args, **kwargs: results.pop(0))
    assert isinstance(g.head_state(RUNNER, tmp_path), g.GitFailure)


def test_disconnected_range_and_status_parser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    results = [
        g._Ran(1, "", ""),
        g._Ran(1, "", ""),
        g._Ran(0, "1", ""),
        g._Ran(0, "A\0f\0", ""),
        g._Ran(0, "", ""),
        g._Ran(0, "tag\n", ""),
    ]
    monkeypatch.setattr(g, "_run", lambda *args, **kwargs: results.pop(0))
    facts = g.range_facts(RUNNER, tmp_path, "a" * 40, "b" * 40)
    assert isinstance(facts, g.RangeFacts) and facts.base is None and facts.rewritten
    assert g._parse_name_status("C100\0old\0new\0T\0typed") == (g.FileChange("A", "new"), g.FileChange("T", "typed"))
    monkeypatch.setattr(g, "_run", lambda *args, **kwargs: g._Ran(2, "", "failure"))
    assert isinstance(g.range_facts(RUNNER, tmp_path, "a" * 40, "b" * 40), g.GitFailure)


def test_clear_readonly(tmp_path: Path) -> None:
    path = tmp_path / "readonly"
    path.write_bytes(b"x")
    path.chmod(0o400)
    g._clear_readonly(lambda value: Path(value).unlink(), str(path), PermissionError())
    assert not path.exists()


def test_materialize_preserves_existing_destination(upstream: Upstream, tmp_path: Path) -> None:
    dest = tmp_path / "existing"
    dest.mkdir()
    (dest / "keep").write_bytes(b"owned elsewhere")
    incoming = tmp_path / "incoming"
    result = g.materialize(RUNNER, upstream.url, dest, git(upstream.work, "rev-parse", "HEAD"), incoming=incoming)
    assert isinstance(result, g.GitFailure)
    assert (dest / "keep").read_bytes() == b"owned elsewhere"
    assert not incoming.exists()


def test_materialize_cleans_owned_paths_after_move_failure(
    upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "clone"
    incoming = tmp_path / "incoming"

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("cannot move")

    monkeypatch.setattr(g.shutil, "move", fail)
    result = g.materialize(RUNNER, upstream.url, dest, git(upstream.work, "rev-parse", "HEAD"), incoming=incoming)
    assert isinstance(result, g.GitFailure) and "cannot move" in result.detail
    assert not dest.exists() and not incoming.exists()


def test_materialize_preserves_existing_incoming(upstream: Upstream, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "keep").write_bytes(b"owned elsewhere")
    result = g.materialize(RUNNER, upstream.url, tmp_path / "clone", "a" * 40, incoming=incoming)
    assert isinstance(result, g.GitFailure)
    assert (incoming / "keep").read_bytes() == b"owned elsewhere"


def _detached_clone(tmp_path: Path, upstream: Upstream) -> Path:
    clone = tmp_path / "bundle" / "repositories" / "demo" / "references" / "git"
    clone.parent.mkdir(parents=True)
    assert g.clone_partial(RUNNER, upstream.url, clone) is None
    assert g.detach(RUNNER, clone, git(upstream.work, "rev-parse", "HEAD")) is None
    return clone


def test_worktree_add_links_the_existing_local_branch(tmp_path: Path) -> None:
    up = make_upstream(tmp_path)
    head = up.commit({"a.py": "a = 1\n"}, "c1")
    clone = _detached_clone(tmp_path, up)
    checkout = tmp_path / ".gw" / "worktrees" / "demo" / "main"
    assert g.worktree_add(RUNNER, clone, checkout, "main") is None
    assert (checkout / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert g.current_branch(RUNNER, checkout) == "main"
    assert g.current_branch(RUNNER, clone) is None
    listed = g.worktree_list(RUNNER, clone)
    assert not isinstance(listed, g.GitFailure)
    assert [Path(w.path).resolve() for w in listed] == [clone.resolve(), checkout.resolve()]
    assert listed[0].branch is None and listed[1] == g.Worktree(listed[1].path, head, "main")


def test_worktree_add_creates_a_missing_local_branch_from_origin(tmp_path: Path) -> None:
    up = make_upstream(tmp_path)
    up.commit({"a.py": "a = 1\n"}, "c1")
    develop = up.commit({"a.py": "a = 2\n"}, "c2", branch="develop")
    clone = _detached_clone(tmp_path, up)
    assert isinstance(g.branch_tip(RUNNER, clone, "develop"), g.GitFailure)
    checkout = tmp_path / "co"
    assert g.worktree_add(RUNNER, clone, checkout, "develop") is None
    assert g.current_branch(RUNNER, checkout) == "develop"
    assert g.branch_tip(RUNNER, clone, "develop") == develop
    assert git(checkout, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") == "origin/develop"


def test_worktree_add_honors_a_non_default_track(tmp_path: Path) -> None:
    up = make_upstream(tmp_path, default_branch="trunk")
    trunk = up.commit({"a.py": "a = 1\n"}, "c1", branch="trunk")
    feature = up.commit({"a.py": "a = 2\n"}, "c2", branch="feature")
    clone = _detached_clone(tmp_path, up)
    assert g.branch_tip(RUNNER, clone, "trunk") == trunk
    assert isinstance(g.branch_tip(RUNNER, clone, "feature"), g.GitFailure)
    checkout = tmp_path / "feature-checkout"
    assert g.worktree_add(RUNNER, clone, checkout, "feature") is None
    assert g.branch_tip(RUNNER, clone, "feature") == feature
    assert g.current_branch(RUNNER, checkout) == "feature"
    assert git(checkout, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") == "origin/feature"


def test_commits_in_the_checkout_are_visible_from_the_clone(tmp_path: Path) -> None:
    up = make_upstream(tmp_path)
    base = up.commit({"a.py": "a = 1\n"}, "c1")
    clone = _detached_clone(tmp_path, up)
    checkout = tmp_path / "co"
    assert g.worktree_add(RUNNER, clone, checkout, "main") is None
    (checkout / "b.py").write_text("b = 1\n", encoding="utf-8", newline="")
    git(checkout, "add", "b.py")
    git(checkout, "commit", "-q", "-m", "c2")
    git(checkout, "commit", "-q", "--allow-empty", "-m", "c3")
    tip = g.branch_tip(RUNNER, clone, "main")
    assert tip == git(checkout, "rev-parse", "HEAD")
    assert g.rev_count(RUNNER, clone, base, str(tip)) == 2
    assert g.rev_count(RUNNER, clone, str(tip), str(tip)) == 0


def test_worktree_remove_unlinks_and_prunes(tmp_path: Path) -> None:
    up = make_upstream(tmp_path)
    up.commit({"a.py": "a = 1\n"}, "c1")
    clone = _detached_clone(tmp_path, up)
    checkout = tmp_path / "co"
    assert g.worktree_add(RUNNER, clone, checkout, "main") is None
    (checkout / "scratch.txt").write_text("x\n", encoding="utf-8", newline="")
    assert g.worktree_remove(RUNNER, clone, checkout) is None
    assert not checkout.exists()
    listed = g.worktree_list(RUNNER, clone)
    assert not isinstance(listed, g.GitFailure) and len(listed) == 1


def test_worktree_add_onto_an_existing_path_fails(tmp_path: Path) -> None:
    up = make_upstream(tmp_path)
    up.commit({"a.py": "a = 1\n"}, "c1")
    clone = _detached_clone(tmp_path, up)
    occupied = tmp_path / "co"
    occupied.mkdir()
    (occupied / "x").write_text("x\n", encoding="utf-8", newline="")
    assert isinstance(g.worktree_add(RUNNER, clone, occupied, "main"), g.GitFailure)
    assert (occupied / "x").read_text(encoding="utf-8") == "x\n"


def test_worktree_list_preserves_a_path_with_newlines(tmp_path: Path) -> None:
    up = make_upstream(tmp_path)
    up.commit({"a.py": "a = 1\n"}, "c1")
    clone = _detached_clone(tmp_path, up)
    checkout = tmp_path / "checkout\n\nwith-newlines"
    assert g.worktree_add(RUNNER, clone, checkout, "main") is None
    listed = g.worktree_list(RUNNER, clone)
    assert not isinstance(listed, g.GitFailure)
    assert [Path(worktree.path).resolve() for worktree in listed] == [clone.resolve(), checkout.resolve()]


def test_worktree_add_returns_a_failure_for_parent_creation_error(tmp_path: Path) -> None:
    up = make_upstream(tmp_path)
    up.commit({"a.py": "a = 1\n"}, "c1")
    clone = _detached_clone(tmp_path, up)
    blocked = tmp_path / "blocked"
    blocked.write_text("owned elsewhere", encoding="utf-8", newline="")
    result = g.worktree_add(RUNNER, clone, blocked / "child" / "co", "main")
    assert isinstance(result, g.GitFailure) and result.cause == "nonzero"
    assert blocked.read_text(encoding="utf-8") == "owned elsewhere"


def test_worktree_add_cleans_only_its_empty_parents_after_git_failure(tmp_path: Path) -> None:
    up = make_upstream(tmp_path)
    up.commit({"a.py": "a = 1\n"}, "c1")
    clone = _detached_clone(tmp_path, up)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_text("owned elsewhere", encoding="utf-8", newline="")
    dest = existing / "new" / "parents" / "co"
    assert isinstance(g.worktree_add(RUNNER, clone, dest, "missing"), g.GitFailure)
    assert not (existing / "new").exists()
    assert (existing / "keep").read_text(encoding="utf-8") == "owned elsewhere"


def test_current_branch_and_rev_count_fail_as_values(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert isinstance(g.current_branch(RUNNER, plain), g.GitFailure)
    assert isinstance(g.rev_count(RUNNER, plain, "a" * 40, "b" * 40), g.GitFailure)


def _checkout(tmp_path: Path) -> Path:
    (tmp_path / "up").mkdir()
    up = make_upstream(tmp_path / "up")
    up.commit({"README.md": "# demo\n"}, "c1")
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", up.url, str(work))
    return work


def test_toplevel_and_linked_worktree(tmp_path: Path) -> None:
    work = _checkout(tmp_path)
    (work / "sub").mkdir()
    linked = tmp_path / "linked"
    git(work, "worktree", "add", "-q", "--detach", str(linked))
    assert g.toplevel(RUNNER, work / "sub") == work.resolve()
    assert g.is_linked_worktree(RUNNER, work) is False
    assert g.is_linked_worktree(RUNNER, linked) is True
    assert isinstance(g.toplevel(RUNNER, tmp_path), g.GitFailure)


def test_is_pristine_counts_untracked_but_not_ignored(tmp_path: Path) -> None:
    work = _checkout(tmp_path)
    assert g.is_pristine(RUNNER, work) is True
    (work / ".gitignore").write_text("node_modules/\n", encoding="utf-8", newline="")
    git(work, "add", ".gitignore")
    git(work, "commit", "-qm", "ignore")
    (work / "node_modules").mkdir()
    (work / "node_modules" / "x.js").write_text("x\n", encoding="utf-8", newline="")
    assert g.is_pristine(RUNNER, work) is True
    (work / "new.txt").write_text("x\n", encoding="utf-8", newline="")
    assert g.is_pristine(RUNNER, work) is False
    (work / "new.txt").unlink()
    (work / "README.md").write_text("changed\n", encoding="utf-8", newline="")
    assert g.is_pristine(RUNNER, work) is False


def test_set_config_and_attach(tmp_path: Path) -> None:
    work = _checkout(tmp_path)
    assert g.set_config(RUNNER, work, "worktree.useRelativePaths", "true") is None
    assert git(work, "config", "--local", "worktree.useRelativePaths") == "true"
    git(work, "switch", "-q", "--detach")
    assert g.attach(RUNNER, work, "main") is None
    assert git(work, "symbolic-ref", "--short", "HEAD") == "main"
    assert isinstance(g.attach(RUNNER, work, "no-such-branch"), g.GitFailure)


def test_worktree_repair_rewrites_links_after_a_move(tmp_path: Path) -> None:
    work = _checkout(tmp_path)
    g.set_config(RUNNER, work, "worktree.useRelativePaths", "true")
    linked = tmp_path / "linked"
    git(work, "worktree", "add", "-q", "--detach", str(linked))
    moved = tmp_path / "moved"
    work.replace(moved)
    assert g.worktree_repair(RUNNER, moved, [linked]) is None
    assert Path(git(linked, "rev-parse", "--git-common-dir")).resolve() == (moved / ".git").resolve()
    link = (linked / ".git").read_text(encoding="utf-8").removeprefix("gitdir: ").strip()
    assert not Path(link).is_absolute()
    assert g.worktree_repair(RUNNER, moved, []) is None


@pytest.mark.parametrize(
    "operation",
    [
        lambda path: g.toplevel(RUNNER, path),
        lambda path: g.is_linked_worktree(RUNNER, path),
        lambda path: g.is_pristine(RUNNER, path),
        lambda path: g.set_config(RUNNER, path, "worktree.useRelativePaths", "true"),
        lambda path: g.attach(RUNNER, path, "main"),
        lambda path: g.worktree_repair(RUNNER, path, [path / "linked"]),
    ],
    ids=["toplevel", "linked", "pristine", "config", "attach", "repair"],
)
def test_existing_checkout_operations_return_failures(tmp_path: Path, operation: Callable[[Path], object]) -> None:
    assert isinstance(operation(tmp_path), g.GitFailure)


def test_detach_preserves_ignored_files_when_target_starts_tracking_them(upstream: Upstream, tmp_path: Path) -> None:
    old = upstream.commit({".gitignore": "private.txt\n"}, "ignore local file")
    clone = _clone(tmp_path, upstream, old)
    local = clone / "private.txt"
    local.write_bytes(b"local data")
    (upstream.work / "private.txt").write_bytes(b"upstream data")
    git(upstream.work, "add", "-f", "private.txt")
    git(upstream.work, "commit", "-qm", "track file")
    git(upstream.work, "push", "-q", "origin", "HEAD:main")
    new = g.fetch(RUNNER, clone, "main")
    assert isinstance(new, str)
    assert g.is_clean(RUNNER, clone) is True
    failure = g.detach(RUNNER, clone, new)
    assert isinstance(failure, g.GitFailure)
    assert local.read_bytes() == b"local data"
    assert g.head_state(RUNNER, clone) == g.HeadState(commit=old, detached=True)
