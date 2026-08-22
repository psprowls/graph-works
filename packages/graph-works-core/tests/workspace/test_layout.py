"""The layout object — the deliverable, not a set of path helpers."""

from __future__ import annotations

import dataclasses
import inspect

import pytest
from graph_works_core.workspace import layout as layout_module
from graph_works_core.workspace.layout import (
    CACHE_DIRNAME,
    DEFAULT_BUNDLE_DIR,
    DEFAULT_CONFIG_DIR,
    GW_DIRNAME,
    MANIFEST_FILENAME,
    WORKTREES_DIRNAME,
    layout_for,
)


def test_the_default_layout_puts_every_member_under_the_root(tmp_path):
    layout = layout_for(tmp_path)
    assert layout.root == tmp_path.resolve()
    assert layout.bundle_dir == tmp_path.resolve() / DEFAULT_BUNDLE_DIR
    assert layout.config_dir == tmp_path.resolve() / DEFAULT_CONFIG_DIR
    assert layout.cache_dir == layout.config_dir / CACHE_DIRNAME
    assert layout.worktrees_dir == layout.config_dir / WORKTREES_DIRNAME
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


def test_the_control_plane_default_is_dot_gw(tmp_path):
    assert GW_DIRNAME == ".gw"
    assert DEFAULT_CONFIG_DIR == ".gw"
    assert DEFAULT_BUNDLE_DIR == "okf"  # unchanged (D5) -- content, not machinery


def test_an_override_relocates_one_member_and_leaves_the_others(tmp_path):
    layout = layout_for(tmp_path, bundle_dir="wiki")
    assert layout.bundle_dir == tmp_path.resolve() / "wiki"
    assert layout.config_dir == tmp_path.resolve() / DEFAULT_CONFIG_DIR


def test_cache_and_worktrees_derive_from_an_overridden_config_dir(tmp_path):
    """The consequence the anchor change exists to guarantee: relocating
    `config_dir` moves the whole control plane as a unit, not just itself."""
    layout = layout_for(tmp_path, config_dir="machinery")
    assert layout.config_dir == tmp_path.resolve() / "machinery"
    assert layout.cache_dir == tmp_path.resolve() / "machinery" / CACHE_DIRNAME
    assert layout.worktrees_dir == tmp_path.resolve() / "machinery" / WORKTREES_DIRNAME


def test_an_explicit_cache_dir_override_still_wins_over_derivation(tmp_path):
    layout = layout_for(tmp_path, config_dir="machinery", cache_dir="elsewhere-cache")
    assert layout.cache_dir == tmp_path.resolve() / "elsewhere-cache"


def test_an_absolute_override_is_honored_as_given(tmp_path):
    elsewhere = tmp_path.parent / "elsewhere-cache"
    layout = layout_for(tmp_path, cache_dir=str(elsewhere))
    assert layout.cache_dir == elsewhere


def test_an_absolute_cache_dir_override_contributes_no_gitignore_entry(tmp_path):
    elsewhere = tmp_path.parent / "elsewhere-cache"
    layout = layout_for(tmp_path, cache_dir=str(elsewhere))
    assert layout.gitignore_entries == ("/worktrees/",)


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
    assert layout_for(tmp_path).gitignore_entries == ("/cache/", "/worktrees/")


def test_gitignore_entries_follow_config_dir_when_it_relocates(tmp_path):
    """The anchor is `config_dir`, wherever it resolves — not a fixed literal
    prefix any override must stay under."""
    layout = layout_for(tmp_path, config_dir="machinery")
    assert layout.gitignore_entries == ("/cache/", "/worktrees/")


def test_an_override_that_escapes_config_dir_is_not_gitignored_from_inside_it(tmp_path):
    """The spec's explicit edge case: relocating a member to a root-relative
    path outside `config_dir` still produces a correct, minimal
    `<config_dir>/.gitignore` — the escaped member simply contributes no
    entry, since `<config_dir>/.gitignore` cannot ignore what is not under
    it."""
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
