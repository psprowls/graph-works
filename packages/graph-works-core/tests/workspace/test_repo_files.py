"""`repo_files`: a declared repository's tracked-file set, and path confinement within it."""

from __future__ import annotations

import sys
from datetime import date

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.repo_files import confine, declared_repos, repo_file_set, repo_file_sets

TODAY = date(2026, 9, 19)


def _layout(tmp_path):
    host = tmp_path / "host"
    (host / ".git").mkdir(parents=True)
    return apply_init(plan_init(host / ".works", today=TODAY, topic="Code")).layout


def test_tracked_files_are_in_the_set_and_untracked_are_not(tmp_path, git_repo, declare_repos):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"src/a.py": "a\n", "README.md": "r\n"}, untracked={"scratch.py": "x\n"})
    declare_repos(layout, {"code": (code, [])})

    (files,) = repo_file_sets(layout)

    assert files.name == "code"
    assert files.root == code.resolve()
    assert files.files == frozenset({"src/a.py", "README.md"})


def test_global_and_per_repo_globs_each_exclude(tmp_path, git_repo, declare_repos):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"AGENTS.md": "g\n", "t/fixtures/f.py": "f\n", "keep.py": "k\n"})
    declare_repos(layout, {"code": (code, ["**/fixtures/**"])}, ignore=["AGENTS.md"])

    (files,) = repo_file_sets(layout)

    assert files.files == frozenset({"keep.py"})


def test_manifest_order_is_kept(tmp_path, git_repo, declare_repos):
    layout = _layout(tmp_path)
    one = git_repo(tmp_path / "one", {"a.py": ""})
    two = git_repo(tmp_path / "two", {"b.py": ""})
    declare_repos(layout, {"zeta": (two, []), "alpha": (one, [])})

    assert [repo.name for repo in repo_file_sets(layout)] == ["zeta", "alpha"]


def test_a_non_git_directory_and_a_missing_one_are_empty(tmp_path, declare_repos):
    layout = _layout(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("a\n", encoding="utf-8", newline="\n")
    declare_repos(layout, {"plain": (plain, []), "gone": (tmp_path / "gone", [])})

    assert [repo.files for repo in repo_file_sets(layout)] == [frozenset(), frozenset()]


def test_a_plain_subdirectory_of_a_work_tree_is_not_that_work_tree(tmp_path, git_repo, declare_repos):
    layout = _layout(tmp_path)
    outer = git_repo(tmp_path / "outer", {"inner/a.py": "a\n"})
    declare_repos(layout, {"inner": (outer / "inner", [])})

    assert repo_file_sets(layout)[0].files == frozenset()


def test_no_manifest_is_no_repositories(tmp_path):
    layout = _layout(tmp_path)
    layout.manifest_path.unlink()
    assert repo_file_sets(layout) == ()
    assert declared_repos(layout) == ()


def test_a_malformed_manifest_raises(tmp_path):
    layout = _layout(tmp_path)
    layout.manifest_path.write_text("version: 1\nrepositories: [nope]\n", encoding="utf-8", newline="\n")
    with pytest.raises(WorkspaceError):
        repo_file_sets(layout)


def test_confine_returns_the_resolved_file(tmp_path, git_repo, declare_repos):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"src/a.py": "a\n"})
    declare_repos(layout, {"code": (code, [])})
    (repo,) = declared_repos(layout)

    assert confine(repo_file_set(repo), "src/a.py") == (code / "src" / "a.py").resolve()


@pytest.mark.parametrize("path", ["../x.py", "src/../../x.py", "/etc/passwd", "src\\a.py", "C:/x.py"])
def test_escapes_are_outside_the_repository(tmp_path, git_repo, declare_repos, path):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"src/a.py": "a\n"})
    declare_repos(layout, {"code": (code, [])})

    assert confine(repo_file_set(declared_repos(layout)[0]), path) == "outside-repository"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_tracked_symlink_out_of_the_repository_is_outside(tmp_path, git_repo, declare_repos):
    layout = _layout(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("s\n", encoding="utf-8", newline="\n")
    code = tmp_path / "code"
    code.mkdir()
    (code / "link.py").symlink_to(secret)
    git_repo(code, {"a.py": "a\n"})
    declare_repos(layout, {"code": (code, [])})
    files = repo_file_set(declared_repos(layout)[0])

    assert "link.py" in files.files
    assert confine(files, "link.py") == "outside-repository"


def test_untracked_ignored_and_absent_files_are_unknown(tmp_path, git_repo, declare_repos):
    layout = _layout(tmp_path)
    code = git_repo(tmp_path / "code", {"a.py": "a\n", "skip.py": "s\n", "gone.py": "g\n"}, untracked={"u.py": "u\n"})
    (code / "gone.py").unlink()
    declare_repos(layout, {"code": (code, ["skip.py"])})
    files = repo_file_set(declared_repos(layout)[0])

    for path in ("u.py", "skip.py", "gone.py", "nope.py", ""):
        assert confine(files, path) == "unknown-file", path


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
@pytest.mark.parametrize("parent_alias", [False, True], ids=["file-alias", "parent-alias"])
@pytest.mark.parametrize("policy", ["allowed", "untracked", "ignored", "ignored-alias"])
def test_alias_requires_both_requested_and_resolved_membership(tmp_path, git_repo, declare_repos, parent_alias, policy):
    """Checking only the alias or only its target must not expose excluded contents."""
    layout = _layout(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    target = "target/value.py"
    alias = "alias/value.py" if parent_alias else "alias.py"
    tracked = {target: "target contents\n"} if policy != "untracked" else {}
    if parent_alias:
        tracked[alias] = "original\n"
    else:
        (code / alias).symlink_to(target)
    git_repo(code, tracked)
    if policy == "untracked":
        (code / "target").mkdir(exist_ok=True)
        (code / target).write_text("target contents\n", encoding="utf-8", newline="\n")
    if parent_alias:
        (code / alias).unlink()
        (code / "alias").rmdir()
        (code / "alias").symlink_to("target", target_is_directory=True)
    ignore = [target] if policy == "ignored" else [alias] if policy == "ignored-alias" else []
    declare_repos(layout, {"code": (code, ignore)})
    files = repo_file_sets(layout)[0]

    assert (alias in files.files) == (policy != "ignored-alias")
    assert (target in files.files) == (policy in {"allowed", "ignored-alias"})
    expected = (code / target).resolve() if policy == "allowed" else "unknown-file"
    assert confine(files, alias) == expected
