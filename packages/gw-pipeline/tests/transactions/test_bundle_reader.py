"""The engine reader uses kernel clone pruning."""

from pathlib import Path


def test_transaction_reader_prunes_clone_contents(tmp_path: Path) -> None:
    from gw_kernel.workspace.anchors import open_absolute_anchor
    from gw_pipeline.transactions import _load_bundle_through

    root = _bundle(tmp_path).resolve()
    anchor = open_absolute_anchor(root)
    try:
        bundle = _load_bundle_through(anchor, root, ignore=())
    finally:
        anchor.close()
    assert bundle.pruned == frozenset({"repositories/demo/references/git"})
    assert not any("references/git/" in path for path in bundle.ignored)
    assert bundle.has_member("repositories/demo/references/git/README.md")


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


_PAGE = (
    "---\ntype: ReferenceRepository\ntitle: Demo\ndescription: Demo.\n"
    "url: https://example.com/demo.git\n---\n\n## Summary\n\nx\n"
)
