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
from okf_io import load_bundle
from work_tracker_okf.items import IGNORE, load_items


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


EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/feature-b"


def _page(layout, path: str, extra: str = "") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    kind = "Epic" if path == EPIC else "Feature"
    page.write_text(
        f"---\ntype: {kind}\ntitle: t\ndescription: d\nwork_status: open\n"
        f"opened: 2026-09-01\nupdated: 2026-09-01\n{extra}---\n",
        encoding="utf-8",
    )


def _items(layout, *, epic: str = "", child: str = ""):
    _page(layout, EPIC, epic)
    _page(layout, CHILD, child)
    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    return {item.path: item for item in items}


def _two(tmp_path):
    layout = _workspace(tmp_path)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    _repositories(layout, f'repositories:\n  one:\n    path: "{one}"\n  two:\n    path: "{two}"\n')
    return layout, one.resolve(), two.resolve()


def test_item_repo_inherits_from_an_ancestor(tmp_path):
    layout, _one, two = _two(tmp_path)
    index = _items(layout, epic="repo: two\n")
    assert repos.resolve_item_repo(layout, index[CHILD], index) == repos.ItemRepo("two", two, "frontmatter")


def test_item_repo_own_value_overrides_the_ancestor(tmp_path):
    layout, one, _two_path = _two(tmp_path)
    index = _items(layout, epic="repo: two\n", child="repo: one\n")
    assert repos.resolve_item_repo(layout, index[CHILD], index).path == one


def test_item_repo_absent_in_a_single_repo_workspace_is_the_sole_repo(tmp_path):
    layout = _workspace(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')
    index = _items(layout)
    assert repos.resolve_item_repo(layout, index[CHILD], index) == repos.ItemRepo("code", code.resolve(), "sole")


def test_item_repo_naming_the_sole_repo_is_accepted(tmp_path):
    layout = _workspace(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')
    index = _items(layout, child="repo: code\n")
    assert repos.resolve_item_repo(layout, index[CHILD], index).source == "frontmatter"


def test_item_repo_absent_in_a_multi_repo_workspace_refuses_naming_the_item(tmp_path):
    layout, _one, _two_path = _two(tmp_path)
    index = _items(layout)
    with pytest.raises(WorkspaceError) as excinfo:
        repos.resolve_item_repo(layout, index[CHILD], index)
    message = str(excinfo.value)
    assert CHILD in message and "repo:" in message and "repo_name" in message


def test_item_repo_naming_an_undeclared_repo_refuses_naming_the_setter(tmp_path):
    layout, _one, _two_path = _two(tmp_path)
    index = _items(layout, epic="repo: nope\n")
    with pytest.raises(WorkspaceError) as excinfo:
        repos.resolve_item_repo(layout, index[CHILD], index)
    message = str(excinfo.value)
    assert CHILD in message and EPIC in message and "'nope'" in message and "one" in message


def test_item_repo_flag_conflict_refuses_naming_both(tmp_path):
    layout, _one, _two_path = _two(tmp_path)
    index = _items(layout, epic="repo: two\n")
    with pytest.raises(WorkspaceError) as excinfo:
        repos.resolve_item_repo(layout, index[CHILD], index, repo_name="one")
    message = str(excinfo.value)
    assert "'one'" in message and "'two'" in message and EPIC in message


def test_item_repo_flag_agreeing_with_repo_is_accepted(tmp_path):
    layout, _one, two = _two(tmp_path)
    index = _items(layout, epic="repo: two\n")
    assert repos.resolve_item_repo(layout, index[CHILD], index, repo_name="two").path == two


def test_item_repo_flag_selects_for_an_untagged_item(tmp_path):
    layout, one, _two_path = _two(tmp_path)
    index = _items(layout)
    assert repos.resolve_item_repo(layout, index[CHILD], index, repo_name="one") == repos.ItemRepo("one", one, "flag")


def test_item_repo_uses_the_caller_fallback_before_strict(tmp_path):
    layout, one, _two_path = _two(tmp_path)
    index = _items(layout)
    chosen = repos.ItemRepo("one", one, "cwd")
    assert repos.resolve_item_repo(layout, index[CHILD], index, fallback=lambda: chosen) == chosen


def test_item_repo_with_zero_repositories_degrades_with_a_note(tmp_path):
    layout = _workspace(tmp_path)
    _repositories(layout, "repositories: {}\n")
    index = _items(layout)
    resolved = repos.resolve_item_repo(layout, index[CHILD], index)
    assert (resolved.name, resolved.path, resolved.source) == (None, None, "sole")
    assert resolved.note


def test_item_repo_malformed_repo_is_absent_with_a_note(tmp_path):
    layout = _workspace(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')
    index = _items(layout, child="repo: 3\n")
    resolved = repos.resolve_item_repo(layout, index[CHILD], index)
    assert resolved.path == code.resolve() and resolved.source == "sole"
    assert resolved.note is not None and CHILD in resolved.note and "malformed" in resolved.note


def test_declared_repositories_is_empty_when_the_manifest_is_absent(tmp_path):
    layout = _workspace(tmp_path)
    layout.manifest_path.unlink()
    assert repos.declared_repositories(layout) == {}
