"""`resolve_repo`: what code this workspace catalogs, and why sometimes nothing.

Moved down out of `orchestrate` with the function. The assertions are unchanged
on purpose — the point of this file is that the move is a move. `resolve_repos`,
its every-declared-root sibling, is tested at the bottom.
"""

from __future__ import annotations

import pytest
from graph_works_core.workspace import repos
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for


def _workspace(tmp_path, manifest_text="version: 1\n"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace.yaml").write_text(manifest_text, encoding="utf-8")
    layout = layout_for(tmp_path)
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _repositories(layout, body: str) -> None:
    """Overwrite `workspace.yaml` with `body`'s `repositories`/etc. blocks."""
    layout.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    layout.manifest_path.write_text(f"version: 1\n{body}", encoding="utf-8")


def test_resolve_repo_returns_the_one_declared_repository(tmp_path):
    layout = _workspace(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')
    resolved, note = repos.resolve_repo(layout)
    assert resolved == code.resolve()
    assert note is None


def test_resolve_repo_selects_by_name_when_several_are_declared(tmp_path):
    layout = _workspace(tmp_path)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    _repositories(layout, f'repositories:\n  one:\n    path: "{one}"\n  two:\n    path: "{two}"\n')
    resolved, note = repos.resolve_repo(layout, repo_name="two")
    assert resolved == two.resolve()
    assert note is None


def test_resolve_repo_refuses_a_repo_name_nothing_declares(tmp_path):
    layout = _workspace(tmp_path)
    one = tmp_path / "one"
    one.mkdir()
    _repositories(layout, f'repositories:\n  one:\n    path: "{one}"\n')
    with pytest.raises(WorkspaceError, match="one") as excinfo:
        repos.resolve_repo(layout, repo_name="nope")
    assert "nope" in str(excinfo.value)


def test_resolve_repo_refuses_to_guess_between_several(tmp_path):
    layout = _workspace(tmp_path)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    _repositories(layout, f'repositories:\n  one:\n    path: "{one}"\n  two:\n    path: "{two}"\n')
    with pytest.raises(WorkspaceError) as excinfo:
        repos.resolve_repo(layout)
    message = str(excinfo.value)
    assert "repo_name" in message and "one" in message and "two" in message


def test_resolve_repo_degrades_when_none_are_declared(tmp_path):
    layout = _workspace(tmp_path)
    _repositories(layout, "repositories: {}\n")
    resolved, note = repos.resolve_repo(layout)
    assert resolved is None
    assert note  # a sentence, not silence


def test_resolve_repo_degrades_when_the_file_is_missing(tmp_path):
    # `load_config` propagates OSError for a missing `workspace.yaml`; a
    # workspace that has not been initialized is not an error here, it is a
    # degrade.
    tmp_path.mkdir(parents=True, exist_ok=True)
    layout = layout_for(tmp_path)
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    resolved, note = repos.resolve_repo(layout)
    assert resolved is None
    assert note


def test_resolve_repo_refuses_a_malformed_declarations_file(tmp_path):
    layout = _workspace(tmp_path)
    layout.manifest_path.write_text("version: 1\nrepositories: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match=r"workspace\.yaml"):
        repos.resolve_repo(layout)


def test_resolve_repo_is_exported():
    assert "resolve_repo" in repos.__all__


def test_resolve_repos_returns_every_declared_repository(tmp_path):
    layout = _workspace(tmp_path)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    _repositories(layout, f'repositories:\n  one:\n    path: "{one}"\n  two:\n    path: "{two}"\n')
    assert repos.resolve_repos(layout) == (one.resolve(), two.resolve())


def test_resolve_repos_returns_the_one_declared_repository(tmp_path):
    layout = _workspace(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')
    assert repos.resolve_repos(layout) == (code.resolve(),)


def test_resolve_repos_is_empty_when_none_are_declared(tmp_path):
    layout = _workspace(tmp_path)
    _repositories(layout, "repositories: {}\n")
    assert repos.resolve_repos(layout) == ()


def test_resolve_repos_is_empty_when_the_file_is_missing(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    layout = layout_for(tmp_path)
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    assert repos.resolve_repos(layout) == ()


def test_resolve_repos_refuses_a_malformed_declarations_file(tmp_path):
    layout = _workspace(tmp_path)
    layout.manifest_path.write_text("version: 1\nrepositories: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match=r"workspace\.yaml"):
        repos.resolve_repos(layout)


def test_resolve_repos_is_exported():
    assert "resolve_repos" in repos.__all__
