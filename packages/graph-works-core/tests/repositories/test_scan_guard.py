from __future__ import annotations

from pathlib import Path

from gitrepo import git
from graph_works_core.repositories.scan_guard import BundleChange, bundle_changes, discard_bundle_changes
from graph_works_core.workspace.layout import layout_for
from workspace_fixture import make_workspace


def test_a_clean_bundle_has_no_changes(tmp_path: Path) -> None:
    assert bundle_changes(make_workspace(tmp_path)) == ()


def test_changes_are_listed_bundle_relative_and_discarded(tmp_path: Path) -> None:
    layout = make_workspace(tmp_path)
    log = layout.bundle_dir / "log.md"
    original = log.read_bytes()
    log.write_text("edited\n", encoding="utf-8", newline="")
    new = layout.bundle_dir / "code-graph" / "demo" / "x.md"
    new.parent.mkdir(parents=True)
    new.write_text("new\n", encoding="utf-8", newline="")
    (layout.root / "outside-bundle.txt").write_text("x\n", encoding="utf-8", newline="")
    changes = bundle_changes(layout)
    assert changes == (BundleChange("code-graph/demo/x.md", True), BundleChange("log.md", False))
    discard_bundle_changes(layout, changes)
    assert log.read_bytes() == original and not new.exists()
    assert (layout.root / "outside-bundle.txt").exists()
    assert bundle_changes(layout) == ()


def test_a_bundle_outside_git_is_none(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text("version: 1\n", encoding="utf-8", newline="")
    layout = layout_for(tmp_path)
    layout.bundle_dir.mkdir(parents=True)
    assert bundle_changes(layout) is None


def test_a_renamed_path_reports_its_new_name(tmp_path: Path) -> None:
    layout = make_workspace(tmp_path)
    git(layout.bundle_dir, "mv", "log.md", "renamed.md")
    changes = bundle_changes(layout)
    assert changes is not None and [change.path for change in changes] == ["log.md", "renamed.md"]
    discard_bundle_changes(layout, changes)
    assert (layout.bundle_dir / "log.md").exists() and not (layout.bundle_dir / "renamed.md").exists()
