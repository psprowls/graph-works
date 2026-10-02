from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import work_tracker_okf
from graph_works_core.workspace.bundle import (
    CLONE_IGNORE,
    CLONE_PRUNE,
    BundleScope,
    load_bundle_at,
    load_work_bundle,
    load_workspace_bundle,
    with_clone_ignore,
    with_clone_prune,
    work_scope,
)
from repositories_okf import CLONE_GLOB, PRUNE_GLOB

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


def _layout(root: Path) -> SimpleNamespace:
    return SimpleNamespace(bundle_dir=root)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


_ITEM = "---\ntype: Bug\ntitle: Item\n---\n\nSee [doc](/docs/page.md).\n"
_DOC = "---\ntype: Explanation\ntitle: Doc\n---\n\nBody.\n"


def _mixed_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "okf"
    _write(root / "index.md", "---\nokf_version: 0.2\n---\n\n# b\n")
    _write(root / "log.md", "# Log\n")
    _write(root / "work" / "bug-a.md", _ITEM)
    _write(root / "work" / "bug-a" / "references" / "01-design.md", "# d\n")
    _write(root / "work" / "_archive" / "bug-old.md", _ITEM)
    _write(root / "docs" / "page.md", _DOC)
    _write(root / "code-graph" / "repo" / "entities" / "packages" / "p.md", _DOC)
    _write(root / "sources" / "2026-10-s.md", _DOC)
    return root


def test_with_clone_prune_appends_once_and_keeps_order() -> None:
    assert with_clone_prune() == CLONE_PRUNE
    assert with_clone_prune(("docs",)) == ("docs", PRUNE_GLOB)
    assert with_clone_prune(("docs", PRUNE_GLOB)) == ("docs", PRUNE_GLOB)


def test_load_bundle_at_merges_a_callers_prune_with_the_clone_prune(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _write(root / "docs" / "page.md", _DOC)
    bundle = load_bundle_at(root, prune=("docs",))
    assert bundle.pruned == frozenset({"docs", "repositories/demo/references/git"})
    assert "docs/page" not in bundle.concepts
    assert bundle.has_member("docs/page.md")


def test_work_scope_prunes_every_other_directory_and_ignores_root_files(tmp_path: Path) -> None:
    scope = work_scope(_layout(_mixed_bundle(tmp_path)))
    assert scope == BundleScope(
        ignore=("index.md", "log.md", *work_tracker_okf.IGNORE),
        prune=("code-graph", "docs", "sources"),
    )


def test_work_scope_never_lists_beneath_a_pruned_directory(tmp_path: Path) -> None:
    root = _mixed_bundle(tmp_path)
    (root / "docs" / "bad.md").write_bytes(b"\xff\xfe not utf-8")
    bundle = load_work_bundle(_layout(root))
    assert bundle.pruned == frozenset({"code-graph", "docs", "sources"})
    assert set(bundle.concepts) == {"work/bug-a", "work/_archive/bug-old"}
    assert all(key.startswith("work/") or "/" not in key for key in bundle.ignored)
    assert not [key for key in bundle.unreadable if key.startswith("docs/")]
    assert bundle.has_member("docs/page.md")
    assert not bundle.has_member("docs/missing.md")


def test_work_scope_escapes_metacharacters(tmp_path: Path) -> None:
    root = _mixed_bundle(tmp_path)
    _write(root / "docs[old]" / "x.md", _DOC)
    _write(root / "a*b" / "y.md", _DOC)
    _write(root / "work" / "docso" / "z.md", _ITEM)
    _write(root / "work" / "ab" / "w.md", _ITEM)
    bundle = load_work_bundle(_layout(root))
    assert bundle.pruned == frozenset({"a*b", "code-graph", "docs", "docs[old]", "sources"})
    assert {"work/docso/z", "work/ab/w"} <= set(bundle.concepts)


def test_work_scope_without_a_work_directory(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    _write(root / "index.md", "---\nokf_version: 0.2\n---\n\n# b\n")
    _write(root / "docs" / "page.md", _DOC)
    bundle = load_work_bundle(_layout(root))
    assert bundle.concepts == {}
    assert bundle.pruned == frozenset({"docs"})


def test_work_scope_of_a_missing_bundle_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        work_scope(_layout(tmp_path / "absent"))


def test_work_bundle_never_lists_pruned_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _mixed_bundle(tmp_path)
    original_iterdir = Path.iterdir
    original_scandir = os.scandir
    listed: list[Path] = []

    def record(path: Path) -> None:
        if not path.is_relative_to(root):
            return
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] != work_tracker_okf.WORK_DIR:
            pytest.fail(f"Listed pruned directory: {relative}")
        listed.append(path)

    def guarded_iterdir(path: Path) -> Iterator[Path]:
        record(path)
        return original_iterdir(path)

    def guarded_scandir(path: str | os.PathLike[str]) -> os.scandir[str]:
        record(Path(path))
        return original_scandir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    monkeypatch.setattr(os, "scandir", guarded_scandir)
    bundle = load_work_bundle(_layout(root))
    assert root / "work" in listed
    assert bundle.pruned == frozenset({"code-graph", "docs", "sources"})
    assert bundle.has_member("docs/page.md")
