"""The reparent write path's index-backed candidate set (display-only exception, D-108)."""

from __future__ import annotations

import os

import okf_io
import pytest
from gw_kernel.read_session import move_selection, open_read_session
from gw_kernel.read_session.moves import MoveSelection
from okf_ext import readindex
from okf_ext.readindex import sync

ROOT = "work/epic-a/children/bug-b"
FILES = {
    f"{ROOT}.md": "---\ntype: Bug\ntitle: B\nwork_status: open\n---\nbody\n",
    f"{ROOT}/references/00-decisions.md": "---\ntitle: D\n---\nd\n",
    "docs/explanations/linker.md": f"---\ntitle: L\n---\n[b](/{ROOT}.md)\n",
    "docs/explanations/sourced.md": f"---\ntitle: S\nsources:\n  - resource: /{ROOT}.md\n---\nx\n",
    "docs/explanations/broken.md": "---\ntitle: [unclosed\n---\n",
    "docs/explanations/unrelated.md": "---\ntitle: U\n---\nnothing\n",
}


def _write(workspace) -> None:
    for rel, text in FILES.items():
        path = workspace.bundle_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        os.utime(path, ns=(1_600_000_000_000_000_000,) * 2)


def test_selection_is_subtree_referrers_mentions_parse_errors_and_unreadable(workspace) -> None:
    _write(workspace)
    with open_read_session(workspace) as session:
        selection = move_selection(session, [ROOT])
    assert isinstance(selection, MoveSelection)
    assert selection.candidates == {
        f"{ROOT}.md",
        f"{ROOT}/references/00-decisions.md",
        "docs/explanations/linker.md",
        "docs/explanations/sourced.md",
        "docs/explanations/broken.md",
        "docs/explanations/yaml.md",
        "docs/explanations/bad.md",
        "work/epic-a/references/undecodable.md",
    }
    assert "docs/explanations/unrelated.md" not in selection.candidates


def test_selection_inventory_is_the_reconcile_walk(workspace, monkeypatch) -> None:
    _write(workspace)
    calls: list[int] = []
    real = okf_io.walk

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(sync, "walk", counting)
    with open_read_session(workspace) as session:
        selection = move_selection(session, [ROOT])
    assert isinstance(selection, MoveSelection)
    assert len(calls) == 1
    assert {m.id for m in selection.inventory.members} >= {f"{ROOT}.md"}


def test_selection_documents_are_reconcile_parsed(workspace) -> None:
    _write(workspace)
    with open_read_session(workspace):
        pass
    target = workspace.bundle_dir / "docs/explanations/unrelated.md"
    target.write_text("---\ntitle: Edited\n---\nnew\n", encoding="utf-8", newline="\n")
    with open_read_session(workspace) as session:
        selection = move_selection(session, [ROOT])
    assert isinstance(selection, MoveSelection)
    assert set(selection.documents) == {"docs/explanations/unrelated.md"}
    assert selection.documents["docs/explanations/unrelated.md"].fm.title == "Edited"


def test_selection_reports_fallback_reasons(workspace, monkeypatch) -> None:
    _write(workspace)
    with open_read_session(workspace):
        pass
    with open_read_session(workspace, reconcile=False) as session:
        assert session.backend == "index"
        assert move_selection(session, [ROOT]) == "unavailable"

    def busy(*args, **kwargs):
        raise readindex.IndexBusy("locked")

    monkeypatch.setattr(readindex, "open_index", busy)
    with open_read_session(workspace) as session:
        assert move_selection(session, [ROOT]) == "busy"
    monkeypatch.undo()

    workspace.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    with open_read_session(workspace) as session:
        assert move_selection(session, [ROOT]) == "disabled"
    workspace.local_manifest_path.unlink()

    locked = workspace.bundle_dir / "docs/locked"
    locked.mkdir()
    (locked / "x.md").write_text("---\ntitle: X\n---\n", encoding="utf-8", newline="\n")
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("directory permissions are not enforced")
        with open_read_session(workspace) as session:
            assert move_selection(session, [ROOT]) == "unreadable directories"
    finally:
        locked.chmod(0o755)
