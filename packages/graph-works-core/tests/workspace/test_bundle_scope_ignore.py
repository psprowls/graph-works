"""The lint fallback preserves the shared partition's literal names."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from graph_works_core.workspace.bundle import load_workspace_bundle, work_scope


def test_ignore_fallback_preserves_escaped_names_without_enumerating_again(tmp_path, monkeypatch):
    root = tmp_path / "okf"
    for member in ("docs[old]/page.md", "a*b/page.md", "index[old].md", "work/bug-a.md"):
        path = root / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\ntype: Bug\ntitle: Page\n---\n\nBody.\n", encoding="utf-8", newline="")
    layout = SimpleNamespace(bundle_dir=root)
    scope = work_scope(layout)
    original_iterdir = Path.iterdir

    def no_reenumeration(path):
        if path == root:
            pytest.fail("scope fallback enumerated the partition again")
        return original_iterdir(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "iterdir", no_reenumeration)
        ignore = scope.as_ignore()
    bundle = load_workspace_bundle(layout, ignore=ignore)
    assert set(bundle.concepts) == {"work/bug-a"}
    assert {"docs[old]/page.md", "a*b/page.md", "index[old].md"} <= bundle.ignored
    assert bundle.member_id("docs[old]/page.md") == "docs[old]/page.md"
    assert bundle.pruned == frozenset()
