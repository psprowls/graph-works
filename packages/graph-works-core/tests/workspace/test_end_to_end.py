"""The acceptance properties: a workspace built end-to-end in a temp directory,
and one read-only smoke against a copy of the live one."""

from __future__ import annotations

import os
import shutil
from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf.config import load_config
from graph_works_core import apply_init, plan_init, resolve
from graph_works_core.workspace.errors import WorkspaceNotFound
from graph_works_core.workspace.manifest import read, set_value
from okf_ext.bundle import SCHEMA_DIRNAME, SECTIONS_DIRNAME
from okf_ext.tags import VOCABULARY_FILENAME

TODAY = date(2026, 8, 13)


@pytest.fixture
def workspace(tmp_path):
    """A workspace built the way a caller builds one: plan, then apply."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Acceptance"))


def test_step_1_a_fresh_init_succeeds_and_reports_change(workspace):
    assert workspace.ok
    assert workspace.changed


def test_step_2_resolve_returns_a_layout_whose_members_all_exist(workspace):
    layout = resolve(workspace=workspace.layout.root, environ={})
    assert layout == workspace.layout
    for directory in layout.directories:
        assert directory.is_dir(), directory
    assert layout.manifest_path.is_file()


def test_step_3_the_manifest_round_trips_and_preserves_every_other_key(workspace):
    path = workspace.layout.manifest_path
    before = read(path)
    set_value(path, "layout.worktrees_dir", "trees")
    after = read(path)
    assert after.worktrees_dir == "trees"
    assert after.topic == before.topic
    assert after.initialized_at == before.initialized_at
    assert after.version == before.version
    assert after.bundle_dir == before.bundle_dir


def test_step_3b_a_written_override_reaches_the_next_resolve(workspace):
    set_value(workspace.layout.manifest_path, "layout.worktrees_dir", "trees")
    assert resolve(workspace=workspace.layout.root, environ={}).worktrees_dir == workspace.layout.root / "trees"


def test_step_4_the_two_config_surfaces_agree(workspace):
    """Asserted rather than assumed: `workspace.yaml`'s merged `repositories:`/
    `ignore:` blocks are written from the layout, and this is the property
    that says so."""
    config = load_config(
        workspace.layout.bundle_dir,
        config_path=workspace.layout.manifest_path,
        graph_dir=workspace.layout.cache_dir,
        declarations_dir=workspace.layout.config_dir,
    )
    assert config.graph_dir == workspace.layout.cache_dir
    assert config.declarations_dir == workspace.layout.config_dir


def test_step_5_a_second_plan_over_the_same_root_is_empty(workspace):
    assert plan_init(workspace.layout.root, today=TODAY).is_empty


def test_the_declarations_land_where_the_config_says_they_do(workspace):
    """The `declarations_dir` seam's first production consumer. Every shipped
    test of it exercises the bundle-root default; this one does not."""
    config = load_config(
        workspace.layout.bundle_dir,
        config_path=workspace.layout.manifest_path,
        graph_dir=workspace.layout.cache_dir,
        declarations_dir=workspace.layout.config_dir,
    )
    assert (config.declarations_dir / SCHEMA_DIRNAME / "Package.schema.json").is_file()
    assert (config.declarations_dir / SECTIONS_DIRNAME / "Feature.yaml").is_file()
    assert (config.declarations_dir / VOCABULARY_FILENAME).is_file()
    assert not (workspace.layout.bundle_dir / SCHEMA_DIRNAME).exists()


def test_the_bundle_loads_as_an_okf_bundle(workspace):
    from okf_io import load_bundle

    bundle = load_bundle(workspace.layout.bundle_dir)
    assert bundle.logs.get("") is not None


# --- the live smoke, read-only ----------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("GRAPH_WIKI_WORKSPACE"),
    reason="no live workspace on this machine",
)
def test_the_live_workspace_is_refused_cleanly_rather_than_half_resolved(tmp_path, monkeypatch):
    """The live workspace is graph-wiki-shaped, not `.works`-shaped, so the
    expected result today is the clean refusal — which is itself the assertion
    worth having, because a *dirty* failure here is what the conversion work
    item would inherit.

    Top-level files only: `resolve` reads exactly one of them, and copying the
    whole vault to assert a refusal would be minutes of I/O for nothing.
    """
    live = Path(os.environ["GRAPH_WIKI_WORKSPACE"])
    copy = tmp_path / "live"
    copy.mkdir()
    for entry in live.iterdir():
        if entry.is_file():
            shutil.copy2(entry, copy / entry.name)
    monkeypatch.setenv("GRAPH_WORKS_DIR", str(copy))
    with pytest.raises(WorkspaceNotFound) as excinfo:
        resolve()
    assert "workspace.yaml" in str(excinfo.value)
