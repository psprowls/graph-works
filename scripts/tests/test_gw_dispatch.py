"""Path-native contract tests for the background work dispatcher."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gw_dispatch  # noqa: E402


def _dispatcher(workspace: Path) -> gw_dispatch.Dispatcher:
    dispatcher = object.__new__(gw_dispatch.Dispatcher)
    dispatcher.workspace = workspace
    dispatcher.repo = workspace
    dispatcher.gw = ["gw"]
    dispatcher.bundle_root = workspace / "okf"
    return dispatcher


def test_work_item_discovery_returns_nested_canonical_paths(tmp_path: Path) -> None:
    work = tmp_path / "okf" / "work"
    nested = work / "release-r1" / "children" / "epic-e1" / "children"
    nested.mkdir(parents=True)
    (work / "release-r1.md").write_text("---\ntype: Release\n---\n", encoding="utf-8")
    (nested / "feature-f1.md").write_text("---\ntype: Feature\n---\n", encoding="utf-8")
    (nested / "index.md").write_text("# Index\n", encoding="utf-8")
    references = nested / "feature-f1" / "references"
    references.mkdir(parents=True)
    (references / "01-design.md").write_text("# Design\n", encoding="utf-8")

    assert _dispatcher(tmp_path).work_items() == [
        {"path": "work/release-r1"},
        {"path": "work/release-r1/children/epic-e1/children/feature-f1"},
    ]


def test_next_reads_the_named_workspace_and_canonical_path(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], cwd: Path | None = None, timeout: int = 120) -> tuple[int, str, str]:
        calls.append(argv)
        return 0, '{"selected_path":"work/release-r1"}', ""

    monkeypatch.setattr(gw_dispatch, "run", fake_run)

    assert _dispatcher(tmp_path).gw_next("work/release-r1") == {"selected_path": "work/release-r1"}
    assert calls == [["gw", "work", "next", "work/release-r1", "--workspace", str(tmp_path), "--json"]]


def test_bundle_discovery_honors_the_projected_bundle_directory(tmp_path: Path) -> None:
    projection = tmp_path / ".gw" / "cache"
    projection.mkdir(parents=True)
    (projection / "config.json").write_text('{"layout":{"bundle_dir":"custom"}}\n', encoding="utf-8")
    work = tmp_path / "custom" / "work"
    work.mkdir(parents=True)
    (work / "bug-custom.md").write_text("---\ntype: Bug\n---\n", encoding="utf-8")

    dispatcher = _dispatcher(tmp_path)
    dispatcher.bundle_root = dispatcher._bundle_root()

    assert dispatcher.bundle_root == tmp_path / "custom"
    assert dispatcher.work_items() == [{"path": "work/bug-custom"}]
