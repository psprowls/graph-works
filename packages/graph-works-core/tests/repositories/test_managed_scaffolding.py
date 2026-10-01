from __future__ import annotations

from pathlib import Path

from graph_works_core.repositories.commands import _managed_checkout, _relative_to_root
from workspace_fixture import make_workspace


def test_managed_checkout_follows_the_manifest_entry_for_the_clone(tmp_path: Path) -> None:
    layout = make_workspace(tmp_path)
    assert _managed_checkout(layout, "demo") is None
    before = layout.manifest_path.read_text(encoding="utf-8")
    prefix, _, rest = before.partition("repositories:")
    _, _, suffix = rest.partition("ignore:")
    layout.manifest_path.write_text(
        prefix
        + "repositories:\n"
        + "  demo:\n    path: okf/repositories/demo/references/git\n"
        + "    checkout: .gw/worktrees/demo/main\n"
        + "  plain:\n    path: code\n    checkout: x\n"
        + "ignore:"
        + suffix,
        encoding="utf-8",
        newline="",
    )
    assert _managed_checkout(layout, "demo") == (layout.root / ".gw/worktrees/demo/main").resolve()
    assert _managed_checkout(layout, "plain") is None


def test_relative_to_root(tmp_path: Path) -> None:
    layout = make_workspace(tmp_path)
    assert _relative_to_root(layout, layout.root / ".gw" / "worktrees" / "demo" / "main") == ".gw/worktrees/demo/main"
    outside = tmp_path / "elsewhere"
    assert _relative_to_root(layout, outside) == outside.resolve().as_posix()
