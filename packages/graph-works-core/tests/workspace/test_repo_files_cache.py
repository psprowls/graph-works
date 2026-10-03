"""The inventory cache: equal to a fresh listing in every state, and git-free when warm."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace import provenance, repo_files
from graph_works_core.workspace.display_cache import open_display_cache
from graph_works_core.workspace.repo_files import confine, declared_repos, repo_file_set, repo_file_sets

TODAY = date(2026, 9, 19)


@pytest.fixture(autouse=True)
def _fresh_memo():
    getattr(repo_files, "_MEMO", {}).clear()
    yield
    getattr(repo_files, "_MEMO", {}).clear()


@pytest.fixture
def git_calls(monkeypatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    real = provenance.probe_git
    monkeypatch.setattr(provenance, "probe_git", lambda cwd, *args, **kw: calls.append(args) or real(cwd, *args, **kw))
    return calls


@pytest.fixture
def not_racy(monkeypatch):
    monkeypatch.setattr(repo_files, "RACY_NS", 0, raising=False)


def _layout(tmp_path):
    host = tmp_path / "host"
    (host / ".git").mkdir(parents=True)
    return apply_init(plan_init(host / ".works", today=TODAY, topic="Code")).layout


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _fresh(layout):
    """The uncached answer: no cache handle at all."""
    return tuple(repo_file_set(r) for r in declared_repos(layout))


def test_cached_equals_fresh_on_first_and_second_call(tmp_path, git_repo, declare_repos, not_racy):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"src/a.py": "a\n", "AGENTS.md": "g\n"}, untracked={"x.py": "x\n"})
    declare_repos(layout, {"code": (code, [])}, ignore=["AGENTS.md"])
    assert repo_file_sets(layout) == _fresh(layout)
    assert repo_file_sets(layout) == _fresh(layout)


def test_warm_call_runs_no_git(tmp_path, git_repo, declare_repos, git_calls, not_racy):
    layout = _layout(tmp_path)
    one = git_repo(tmp_path / "one", {"a.py": ""})
    two = git_repo(tmp_path / "two", {"b.py": ""})
    declare_repos(layout, {"one": (one, []), "two": (two, [])})
    repo_file_sets(layout)
    git_calls.clear()
    repo_files._MEMO.clear()  # the database alone must serve it
    repo_file_sets(layout)
    assert git_calls == []
    repo_file_sets(layout)  # and the memo
    assert git_calls == []
    assert repo_file_sets(layout) == _fresh(layout)


def test_git_add_relists_only_that_repository(tmp_path, git_repo, declare_repos, git_calls, not_racy):
    layout = _layout(tmp_path)
    one = git_repo(tmp_path / "one", {"a.py": ""})
    two = git_repo(tmp_path / "two", {"b.py": ""})
    declare_repos(layout, {"one": (one, []), "two": (two, [])})
    repo_file_sets(layout)
    (one / "new.py").write_text("n\n", encoding="utf-8", newline="\n")
    _git(one, "add", "new.py")
    git_calls.clear()
    sets = repo_file_sets(layout)
    assert "new.py" in sets[0].files
    listing_calls = [a for a in git_calls if a[0] == "ls-files"]
    assert sets == _fresh(layout)
    assert listing_calls == [("ls-files", "-z")]


@pytest.mark.parametrize("change", ["rm", "commit", "checkout", "reset-soft"])
def test_convergence_after_git_operations(tmp_path, git_repo, declare_repos, not_racy, change):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"a.py": "a\n", "b.py": "b\n"})
    _git(code, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
    declare_repos(layout, {"code": (code, [])})
    repo_file_sets(layout)
    if change == "rm":
        _git(code, "rm", "-q", "b.py")
    elif change == "commit":
        (code / "c.py").write_text("c\n", encoding="utf-8", newline="\n")
        _git(code, "add", "c.py")
        _git(code, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "two")
    elif change == "checkout":
        _git(code, "checkout", "-qb", "other")
        _git(code, "rm", "-q", "a.py")
        _git(code, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "drop a")
        _git(code, "checkout", "-q", "-")
    else:
        (code / "c.py").write_text("c\n", encoding="utf-8", newline="\n")
        _git(code, "add", "c.py")
        _git(code, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "two")
        repo_file_sets(layout)
        _git(code, "reset", "-q", "--soft", "HEAD~1")
    assert repo_file_sets(layout) == _fresh(layout)


def test_ignore_glob_change_and_path_change_converge(tmp_path, git_repo, declare_repos, not_racy):
    layout = _layout(tmp_path)
    one = git_repo(tmp_path / "one", {"a.py": "", "skip.py": ""})
    two = git_repo(tmp_path / "two", {"z.py": ""})
    declare_repos(layout, {"code": (one, [])})
    repo_file_sets(layout)
    declare_repos(layout, {"code": (one, ["skip.py"])})
    assert repo_file_sets(layout) == _fresh(layout)
    declare_repos(layout, {"code": (two, [])})
    assert repo_file_sets(layout) == _fresh(layout)


def test_removing_dot_git_converges_without_git(tmp_path, git_repo, declare_repos, git_calls, not_racy):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"a.py": ""})
    declare_repos(layout, {"code": (code, [])})
    repo_file_sets(layout)
    shutil.rmtree(code / ".git")
    git_calls.clear()
    assert repo_file_sets(layout)[0].files == frozenset()
    assert git_calls == []


def test_linked_worktree_add_relists_only_the_worktree(tmp_path, git_repo, declare_repos, git_calls, not_racy):
    layout = _layout(tmp_path)
    main = git_repo(tmp_path / "main", {"a.py": ""})
    _git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
    _git(main, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "wt")
    wt = tmp_path / "wt"
    declare_repos(layout, {"main": (main, []), "wt": (wt, [])})
    assert repo_file_sets(layout) == _fresh(layout)
    (wt / "w.py").write_text("w\n", encoding="utf-8", newline="\n")
    _git(wt, "add", "w.py")
    git_calls.clear()
    sets = repo_file_sets(layout)
    assert "w.py" in sets[1].files and "w.py" not in sets[0].files
    listing_calls = [a for a in git_calls if a[0] == "ls-files"]
    assert sets == _fresh(layout)
    assert len(listing_calls) == 1


def test_dot_git_turned_into_a_worktree_file_converges(tmp_path, git_repo, declare_repos, not_racy):
    layout = _layout(tmp_path)
    main = git_repo(tmp_path / "main", {"a.py": ""})
    _git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
    target = git_repo(tmp_path / "target", {"t.py": ""})
    declare_repos(layout, {"code": (target, [])})
    repo_file_sets(layout)
    shutil.rmtree(target)
    _git(main, "worktree", "add", "-q", str(target), "-b", "moved")
    assert repo_file_sets(layout) == _fresh(layout)


@pytest.mark.parametrize("future", [False, True])
def test_index_rewrite_inside_the_racy_window_is_relisted(
    tmp_path, git_repo, declare_repos, git_calls, monkeypatch, future
):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"a.py": ""}, untracked={"b.py": ""})
    index = code / ".git" / "index"
    original = index.read_bytes()
    _git(code, "rm", "--cached", "-q", "a.py")
    _git(code, "add", "b.py")
    replacement = index.read_bytes()
    assert len(original) == len(replacement)
    index.write_bytes(original)
    stamp = time.time_ns()
    os.utime(index, ns=(stamp, stamp))
    monkeypatch.setattr(repo_files.time, "time_ns", lambda: stamp + (-1 if future else 1_000_000_000))
    declare_repos(layout, {"code": (code, [])})
    key = repo_files.inventory_key(code.resolve(), [])
    assert repo_file_sets(layout)[0].files == frozenset({"a.py"})
    assert not repo_files._MEMO
    # Rewrite in place, restoring size, inode and mtime: the stat key stays equal.
    index.write_bytes(replacement)
    os.utime(index, ns=(stamp, stamp))
    assert repo_files.inventory_key(code.resolve(), []) == key
    git_calls.clear()
    actual = repo_file_sets(layout)
    listing_calls = [a for a in git_calls if a[0] == "ls-files"]
    assert actual[0].files == frozenset({"b.py"})
    assert actual == _fresh(layout)
    assert listing_calls == [("ls-files", "-z")]


def test_index_changed_mid_listing_is_returned_not_stored(tmp_path, git_repo, declare_repos, monkeypatch, not_racy):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"a.py": ""})
    declare_repos(layout, {"code": (code, [])})
    real = repo_files._list

    def listing_then_add(root, ignore):
        files = real(root, ignore)
        (code / "late.py").write_text("l\n", encoding="utf-8", newline="\n")
        _git(code, "add", "late.py")
        return files

    monkeypatch.setattr(repo_files, "_list", listing_then_add)
    (first,) = repo_file_sets(layout)
    assert "late.py" not in first.files
    monkeypatch.setattr(repo_files, "_list", real)
    with open_display_cache(layout) as cache:
        assert cache.inventory(code.resolve(), "anything") is None
        assert cache.inventory(code.resolve(), repo_files.inventory_key(code.resolve(), [])) is None
    assert "late.py" in repo_file_sets(layout)[0].files


@pytest.mark.parametrize("name", ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"])
@pytest.mark.parametrize("redirect", [False, True])
def test_git_environment_overrides_bypass_the_cache(
    tmp_path, git_repo, declare_repos, monkeypatch, git_calls, not_racy, name, redirect
):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"a.py": ""})
    other = git_repo(tmp_path / "other", {"b.py": ""})
    declare_repos(layout, {"code": (code, [])})
    assert repo_file_sets(layout)[0].files == frozenset({"a.py"})
    target = other if redirect else code
    value = target if name == "GIT_WORK_TREE" else target / ".git"
    if name == "GIT_INDEX_FILE":
        value /= "index"
    monkeypatch.setenv(name, str(value))
    git_calls.clear()
    actual = repo_file_sets(layout)
    probes = list(git_calls)
    assert actual == _fresh(layout)
    assert probes and probes[0] == ("rev-parse", "--show-toplevel")
    if actual[0].files:
        assert ("ls-files", "-z") in probes
    else:
        # Redirected work tree can fail root validation, but it must run git.
        assert redirect
    assert actual[0].files == (
        frozenset() if name == "GIT_WORK_TREE" and redirect else frozenset({"b.py" if redirect else "a.py"})
    )


def test_disabled_runs_git_every_call(tmp_path, git_repo, declare_repos, git_calls, not_racy):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"a.py": ""})
    declare_repos(layout, {"code": (code, [])})
    layout.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    repo_file_sets(layout)
    git_calls.clear()
    actual = repo_file_sets(layout)
    listing_calls = [a for a in git_calls if a[0] == "ls-files"]
    assert actual == _fresh(layout)
    assert listing_calls == [("ls-files", "-z")]


def test_confine_agrees_cached_and_fresh(tmp_path, git_repo, declare_repos, not_racy):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"src/a.py": "a\n", "skip.py": "s\n"}, untracked={"u.py": "u\n"})
    declare_repos(layout, {"code": (code, ["skip.py"])})
    (declared,) = declared_repos(layout)
    repo_file_sets(layout)
    with open_display_cache(layout) as cache:
        cached = repo_file_set(declared, cache=cache)
    fresh = repo_file_set(declared)
    for path in ("src/a.py", "skip.py", "u.py", "../x", "/etc/passwd", "src\\a.py", "missing.py"):
        assert confine(cached, path) == confine(fresh, path)


@pytest.mark.parametrize("failed_probe", ["rev-parse", "ls-files"])
def test_transient_git_failure_is_not_cached(tmp_path, git_repo, declare_repos, monkeypatch, not_racy, failed_probe):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"a.py": ""})
    declare_repos(layout, {"code": (code, [])})
    real = provenance.probe_git

    def fail(cwd, *args, **kwargs):
        if args[0] == failed_probe:
            return provenance.GitOutcome(1, "", "ok", "transient failure")
        return real(cwd, *args, **kwargs)

    key = repo_files.inventory_key(code.resolve(), [])
    monkeypatch.setattr(provenance, "probe_git", fail)
    assert repo_file_sets(layout)[0].files == frozenset()
    assert not repo_files._MEMO
    with open_display_cache(layout) as cache:
        assert cache.inventory(code.resolve(), key) is None
    monkeypatch.setattr(provenance, "probe_git", real)
    assert repo_files.inventory_key(code.resolve(), []) == key
    actual = repo_file_sets(layout)
    assert actual[0].files == frozenset({"a.py"})
    assert actual == _fresh(layout)


@pytest.mark.parametrize("operation", ["lstat", "read_text", "second-key"])
def test_key_io_failure_lists_fresh_without_storing(
    tmp_path, git_repo, declare_repos, monkeypatch, not_racy, operation
):
    layout = _layout(tmp_path)
    main = git_repo(tmp_path / "main", {"a.py": ""})
    _git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
    code = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(code), "-b", "wt")
    declare_repos(layout, {"code": (code, [])})
    key = repo_files.inventory_key(code.resolve(), [])
    with monkeypatch.context() as patch:
        if operation == "second-key":
            real = repo_files.inventory_key
            calls = 0

            def fail_second(root, ignore):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise PermissionError("key unavailable")
                return real(root, ignore)

            patch.setattr(repo_files, "inventory_key", fail_second)
        else:
            real = getattr(Path, operation)

            def fail(path, *args, **kwargs):
                if path == code / ".git":
                    raise PermissionError("key unavailable")
                return real(path, *args, **kwargs)

            patch.setattr(Path, operation, fail)
        assert repo_file_sets(layout)[0].files == frozenset({"a.py"})
        assert not repo_files._MEMO
    with open_display_cache(layout) as cache:
        assert cache.inventory(code.resolve(), key) is None
    assert repo_file_sets(layout) == _fresh(layout)


def test_repository_without_index_can_cache_empty_success(tmp_path, git_repo, declare_repos, git_calls):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {})
    declare_repos(layout, {"code": (code, [])})
    assert not (code / ".git" / "index").exists()
    assert repo_file_sets(layout)[0].files == frozenset()
    repo_files._MEMO.clear()
    git_calls.clear()
    assert repo_file_sets(layout)[0].files == frozenset()
    assert not git_calls
    assert repo_file_sets(layout) == _fresh(layout)


@pytest.mark.parametrize("persisted", [False, True], ids=["memo", "database"])
def test_index_stat_failure_after_git_add_cannot_reuse_absent_index_inventory(
    tmp_path, git_repo, declare_repos, monkeypatch, not_racy, persisted
):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {})
    declare_repos(layout, {"code": (code, [])})
    index = code / ".git" / "index"
    assert not index.exists()
    assert repo_file_sets(layout)[0].files == frozenset()
    if persisted:
        repo_files._MEMO.clear()
    (code / "new.py").write_text("new\n", encoding="utf-8", newline="\n")
    _git(code, "add", "new.py")
    key = repo_files.inventory_key(code.resolve(), [])
    fresh = _fresh(layout)
    real_stat = Path.stat

    def unavailable(path, *args, **kwargs):
        if path == index:
            raise PermissionError("index stat unavailable")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unavailable)
    actual = repo_file_sets(layout)
    assert actual[0].files == frozenset({"new.py"})
    assert actual == fresh
    assert (code.resolve(), key) not in repo_files._MEMO
    with open_display_cache(layout) as cache:
        assert cache.inventory(code.resolve(), key) is None


@pytest.mark.parametrize("failed_key", [1, 2], ids=["first-key", "second-key"])
def test_index_stat_failure_returns_fresh_without_storing(tmp_path, git_repo, declare_repos, monkeypatch, failed_key):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {})
    declare_repos(layout, {"code": (code, [])})
    index = code / ".git" / "index"
    key = repo_files.inventory_key(code.resolve(), [])
    fresh = _fresh(layout)
    real_stat = Path.stat
    calls = 0

    def unavailable(path, *args, **kwargs):
        nonlocal calls
        if path == index:
            calls += 1
            if calls == failed_key:
                raise OSError("index stat unavailable")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unavailable)
    actual = repo_file_sets(layout)
    assert actual == fresh
    assert actual[0].files == frozenset()
    assert not repo_files._MEMO
    with open_display_cache(layout) as cache:
        assert cache.inventory(code.resolve(), key) is None


def test_head_only_change_needs_no_git(tmp_path, git_repo, declare_repos, git_calls, not_racy):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"a.py": ""})
    for message in ("one", "two"):
        _git(code, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-qm", message)
    declare_repos(layout, {"code": (code, [])})
    repo_file_sets(layout)
    key = repo_files.inventory_key(code.resolve(), [])
    _git(code, "reset", "-q", "--soft", "HEAD~1")
    assert repo_files.inventory_key(code.resolve(), []) == key
    git_calls.clear()
    actual = repo_file_sets(layout)
    assert not git_calls
    assert actual == _fresh(layout)


def test_ignore_compiler_fixture_matches_on_cached_reads(tmp_path, git_repo, declare_repos, not_racy):
    """Pin compiler semantics; changing them requires its display-cache version bump."""
    layout = _layout(tmp_path)
    paths = {
        path: ""
        for path in ("top.py", "src/a.py", "src/deep/b.py", "fixtures/x.py", "pkg/fixtures/y.py", "skip.py", "keep.txt")
    }
    code = git_repo(tmp_path / "code", paths)
    declare_repos(layout, {"code": (code, ["*.py", "src/*.py", "**/fixtures/**"])})
    expected = frozenset({"src/deep/b.py", "keep.txt"})
    assert repo_file_sets(layout)[0].files == expected
    repo_files._MEMO.clear()
    assert repo_file_sets(layout)[0].files == expected
    assert _fresh(layout)[0].files == expected


def test_memo_is_bounded_and_database_serves_after_clear(tmp_path, git_repo, declare_repos, git_calls, not_racy):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"a.py": ""})
    declare_repos(layout, {"code": (code, [])})
    (repo,) = declared_repos(layout)
    with open_display_cache(layout) as cache:
        for number in range(65):
            changed = replace(repo, ignore=(f"missing-{number}",))
            assert repo_file_set(changed, cache=cache).files == frozenset({"a.py"})
            assert len(repo_files._MEMO) <= 64
        assert len(repo_files._MEMO) == 1
        repo_files._MEMO.clear()
        git_calls.clear()
        assert repo_file_set(changed, cache=cache).files == frozenset({"a.py"})
        assert not git_calls


def test_no_repositories_never_opens_display_cache(tmp_path, monkeypatch):
    layout = _layout(tmp_path)
    layout.manifest_path.unlink()

    def unexpected(layout):
        pytest.fail("no repositories must not open the display cache")

    monkeypatch.setattr(repo_files, "open_display_cache", unexpected)
    assert repo_file_sets(layout) == ()
