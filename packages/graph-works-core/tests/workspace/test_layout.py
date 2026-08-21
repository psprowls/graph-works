"""The layout object — the deliverable, not a set of path helpers."""

from __future__ import annotations

import dataclasses
import inspect

import pytest
from graph_works_core.workspace import layout as layout_module
from graph_works_core.workspace.layout import (
    DEFAULT_BUNDLE_DIR,
    DEFAULT_CACHE_DIR,
    DEFAULT_CONFIG_DIR,
    DEFAULT_REPOSITORIES_PATH,
    DEFAULT_WORKTREES_DIR,
    GW_DIRNAME,
    MANIFEST_FILENAME,
    layout_for,
)


def test_the_default_layout_puts_every_member_under_the_root(tmp_path):
    layout = layout_for(tmp_path)
    assert layout.root == tmp_path.resolve()
    assert layout.bundle_dir == tmp_path.resolve() / DEFAULT_BUNDLE_DIR
    assert layout.config_dir == tmp_path.resolve() / DEFAULT_CONFIG_DIR
    assert layout.cache_dir == tmp_path.resolve() / DEFAULT_CACHE_DIR
    assert layout.worktrees_dir == tmp_path.resolve() / DEFAULT_WORKTREES_DIR
    assert layout.repositories_path == tmp_path.resolve() / DEFAULT_REPOSITORIES_PATH
    assert layout.repo_root is None


def test_the_layout_is_frozen_and_slotted(tmp_path):
    layout = layout_for(tmp_path)
    assert dataclasses.is_dataclass(layout)
    with pytest.raises(dataclasses.FrozenInstanceError):
        layout.root = tmp_path  # type: ignore[misc]
    assert not hasattr(layout, "__dict__")


def test_the_manifest_name_is_fixed_at_the_root(tmp_path):
    assert layout_for(tmp_path).manifest_path == tmp_path.resolve() / MANIFEST_FILENAME
    assert MANIFEST_FILENAME == "workspace.yaml"


def test_the_three_relocated_defaults_share_the_gw_prefix(tmp_path):
    assert GW_DIRNAME == "_gw"
    assert DEFAULT_CONFIG_DIR == "_gw/_config"
    assert DEFAULT_CACHE_DIR == "_gw/_cache"
    assert DEFAULT_WORKTREES_DIR == "_gw/worktrees"
    assert DEFAULT_REPOSITORIES_PATH == "_gw/_repositories.yaml"
    assert DEFAULT_BUNDLE_DIR == "okf"  # unchanged (D5) -- content, not machinery


def test_an_override_relocates_one_member_and_leaves_the_others(tmp_path):
    layout = layout_for(tmp_path, bundle_dir="wiki")
    assert layout.bundle_dir == tmp_path.resolve() / "wiki"
    assert layout.config_dir == tmp_path.resolve() / DEFAULT_CONFIG_DIR


def test_an_absolute_override_is_honored_as_given(tmp_path):
    elsewhere = tmp_path.parent / "elsewhere-cache"
    layout = layout_for(tmp_path, cache_dir=str(elsewhere))
    assert layout.cache_dir == elsewhere


def test_directories_lists_every_member_init_creates(tmp_path):
    layout = layout_for(tmp_path)
    assert layout.directories == (
        layout.root,
        layout.config_dir,
        layout.cache_dir,
        layout.bundle_dir,
        layout.worktrees_dir,
    )


def test_gitignore_entries_name_the_two_gitignored_members(tmp_path):
    assert layout_for(tmp_path).gitignore_entries == ("/_cache/", "/worktrees/")


def test_gitignore_entries_follow_an_override_still_under_gw(tmp_path):
    layout = layout_for(tmp_path, cache_dir="_gw/var-cache")
    assert layout.gitignore_entries == ("/var-cache/", "/worktrees/")


def test_an_override_that_escapes_gw_is_not_gitignored_from_inside_it(tmp_path):
    """The spec's explicit edge case: relocating a member to a root-relative
    path outside `_gw/` (not merely outside the workspace entirely) still
    produces a correct, minimal `_gw/.gitignore` — the escaped member simply
    contributes no entry, since `_gw/.gitignore` cannot ignore what is not
    under it."""
    layout = layout_for(tmp_path, cache_dir="var/cache")
    assert layout.gitignore_entries == ("/worktrees/",)


def test_a_member_outside_the_workspace_cannot_be_gitignored_from_inside_it(tmp_path):
    layout = layout_for(tmp_path, cache_dir=str(tmp_path.parent / "outside-cache"))
    assert layout.gitignore_entries == ("/worktrees/",)


def test_scanner_excludes_name_the_workspace_relative_to_its_repo(tmp_path):
    repo = tmp_path / "repo"
    layout = layout_for(repo / ".works", repo_root=repo)
    assert layout.scanner_excludes == (".works/**",)


def test_scanner_excludes_are_empty_outside_any_repo(tmp_path):
    assert layout_for(tmp_path).scanner_excludes == ()


def test_scanner_excludes_are_empty_when_the_workspace_sits_outside_its_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    layout = layout_for(tmp_path / "workspace", repo_root=repo)
    assert layout.scanner_excludes == ()


def test_the_module_ships_no_path_lookup_function(tmp_path):
    """`graph_dir(workspace)` anywhere below band 3 is the structural defect
    this epic exists to remove; the layout module must not model one either."""
    public = [
        name
        for name, value in vars(layout_module).items()
        if not name.startswith("_") and inspect.isfunction(value) and value.__module__ == layout_module.__name__
    ]
    assert public == ["layout_for"]


def test_repositories_path_relocates_independently_of_the_other_three(tmp_path):
    layout = layout_for(tmp_path, repositories_path="elsewhere/_repositories.yaml")
    assert layout.repositories_path == tmp_path.resolve() / "elsewhere/_repositories.yaml"
    assert layout.config_dir == tmp_path.resolve() / DEFAULT_CONFIG_DIR
    assert layout.cache_dir == tmp_path.resolve() / DEFAULT_CACHE_DIR
    assert layout.worktrees_dir == tmp_path.resolve() / DEFAULT_WORKTREES_DIR


def test_repositories_path_does_not_join_directories(tmp_path):
    """It is a file, not a directory: init must not `mkdir` it directly."""
    layout = layout_for(tmp_path)
    assert layout.repositories_path not in layout.directories
