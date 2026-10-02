from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from graph_works_core.workspace.bundle import (
    CLONE_IGNORE,
    CLONE_PRUNE,
    ignored_by,
    load_bundle_at,
    load_workspace_bundle,
    with_clone_ignore,
)
from repositories_okf import CLONE_GLOB, PRUNE_GLOB
from work_tracker_okf.items import IGNORE

_PAGE = (
    "---\ntype: ReferenceRepository\ntitle: Demo\ndescription: Demo.\n"
    "url: https://example.com/demo.git\n---\n\n## Summary\n\nx\n"
)


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "okf"
    clone = root / "repositories" / "demo" / "references" / "git"
    clone.mkdir(parents=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# b\n", encoding="utf-8", newline="")
    (root / "repositories" / "demo.md").write_text(_PAGE, encoding="utf-8", newline="")
    (clone / "README.md").write_text("# Upstream readme, no frontmatter\n", encoding="utf-8", newline="")
    (clone / "index.md").write_text("# upstream index\n", encoding="utf-8", newline="")
    (clone / "log.md").write_text("# upstream log\n", encoding="utf-8", newline="")
    (clone / "docs").mkdir()
    (clone / "docs" / "page.md").write_text(_PAGE, encoding="utf-8", newline="")
    return root


def test_clone_ignore_is_the_lane_glob() -> None:
    assert CLONE_IGNORE == (CLONE_GLOB,)
    assert CLONE_PRUNE == (PRUNE_GLOB,)


def test_with_clone_ignore_appends_once_and_keeps_order() -> None:
    assert with_clone_ignore() == (CLONE_GLOB,)
    assert with_clone_ignore(("a/*", "b")) == ("a/*", "b", CLONE_GLOB)
    assert with_clone_ignore(("a/*", CLONE_GLOB)) == ("a/*", CLONE_GLOB)


def test_load_bundle_at_hides_every_clone_member(tmp_path: Path) -> None:
    bundle = load_bundle_at(_bundle(tmp_path))
    assert "repositories/demo" in bundle.concepts
    keys = (*bundle.concepts, *bundle.indexes, *bundle.logs, *bundle.assets)
    assert not [key for key in keys if "references/git" in key]
    assert bundle.pruned == frozenset({"repositories/demo/references/git"})
    assert bundle.has_member("repositories/demo/references/git/README.md")


def test_load_bundle_at_keeps_the_callers_own_ignores(tmp_path: Path) -> None:
    bundle = load_bundle_at(_bundle(tmp_path), ignore=("repositories/*",))
    assert "repositories/demo" not in bundle.concepts


def test_load_workspace_bundle_reads_the_layouts_bundle_dir(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    bundle = load_workspace_bundle(SimpleNamespace(bundle_dir=root))  # type: ignore[arg-type]
    assert bundle.root == root
    assert "repositories/demo/references/git" in bundle.pruned


def test_transaction_reader_prunes_clone_contents(tmp_path: Path) -> None:
    from graph_works_core.workspace.anchors import open_absolute_anchor
    from graph_works_core.workspace.transactions import _load_bundle_through

    root = _bundle(tmp_path).resolve()
    anchor = open_absolute_anchor(root)
    try:
        bundle = _load_bundle_through(anchor, root, ignore=())
    finally:
        anchor.close()
    assert bundle.pruned == frozenset({"repositories/demo/references/git"})
    assert not any("references/git/" in path for path in bundle.ignored)
    assert bundle.has_member("repositories/demo/references/git/README.md")


def test_ignored_by_matches_okf_io_classification(tmp_path: Path) -> None:
    root = tmp_path / "b"
    for rel in ("work/a.md", "work/a/references/01-design.md", "x/.DS_Store", "docs/p.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("---\ntitle: x\n---\n", encoding="utf-8", newline="\n")
    narrow = load_bundle_at(root, ignore=IGNORE)
    wide = load_bundle_at(root)
    members = {f"{c}.md" for c in wide.concepts} | set(wide.assets) | set(wide.ignored)
    assert {m for m in members if ignored_by(m, IGNORE)} == set(narrow.ignored) - set(wide.ignored)


def test_ignored_by_is_case_sensitive_and_matches_nested_paths() -> None:
    assert ignored_by("work/a/references/design.md", ("work/*/references/*",))
    assert not ignored_by("Work/a/references/design.md", ("work/*/references/*",))
    assert not ignored_by("docs/p.md", ())
