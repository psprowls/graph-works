"""Path-native routing and transactional managed-source normalization."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from okf_io import load

TODAY = date(2026, 8, 23)
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/feature-a"


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Next")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write(layout, path: str, *, type: str = "Feature", phase: str = "design") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: open\n"
        f"phase: {phase}\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
        "affects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )


def _spec(layout, path: str) -> Path:
    artifact = layout.bundle_dir / f"{path}/references/01-design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Design\n", encoding="utf-8")
    return artifact


def test_unknown_path_is_a_caller_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="work/missing"):
        work.run_next(_layout(tmp_path), "work/missing")


@pytest.mark.skipif(sys.platform != "win32", reason="exercises a Windows exclusive file handle (C4)")
def test_a_locked_member_is_named_instead_of_reported_unknown(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/locked")
    page = layout.bundle_dir / "work/locked.md"

    import ctypes

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    handle = ctypes.windll.kernel32.CreateFileW(
        str(page), GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None
    )
    assert handle != INVALID_HANDLE_VALUE
    try:
        with pytest.raises(ValueError, match=r"work/locked\.md") as excinfo:
            work.run_next(layout, "work/locked")
        assert "unknown work item" not in str(excinfo.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def test_next_reports_requested_and_selected_canonical_paths(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    result = work.run_next(layout, CHILD)
    assert result.requested_path == CHILD
    assert result.selected_path == CHILD


def test_descend_uses_structural_child_paths(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    result = work.run_next(layout, EPIC, descend=True)
    assert result.descent is not None and result.descent.path == (EPIC, CHILD)
    assert result.selected_path == CHILD


def test_canonical_design_artifact_plans_source_normalization(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    _spec(layout, CHILD)
    result = work.run_next(layout, CHILD)
    assert [change.path for change in result.normalizations] == [CHILD]
    assert result.state.has_spec_doc is True
    assert result.artifact is not None and result.artifact.rel.endswith("references/01-design.md")


def test_live_normalization_is_one_journaled_write_and_idempotent(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    _spec(layout, CHILD)
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    first = work.run_next(layout, CHILD, dry_run=False)
    second = work.run_next(layout, CHILD, dry_run=False)
    assert first.warnings == ()
    assert first.application.normalized == (CHILD,)
    assert second.application.normalized == ()
    assert [source.id for source in load(layout.bundle_dir / f"{CHILD}.md").fm.sources] == ["design"]


def test_live_normalization_preserves_an_authored_source_raced_after_planning(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    _spec(layout, CHILD)
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    page = layout.bundle_dir / f"{CHILD}.md"
    authored = load(page)
    authored.set(
        "sources",
        [{"id": "design", "resource": "/authored/design.md", "title": "Authored design"}],
    )
    authored_bytes = authored.serialize().encode("utf-8")
    original = work.parse
    raced = False

    def inject_after_snapshot(*args, **kwargs):
        nonlocal raced
        document = original(*args, **kwargs)
        if kwargs.get("path") == page and not raced:
            page.write_bytes(authored_bytes)
            raced = True
        return document

    monkeypatch.setattr(work, "parse", inject_after_snapshot)
    result = work.run_next(layout, CHILD, dry_run=False)
    assert result.application.normalized == ()
    assert any("normalization failed" in warning for warning in result.warnings)
    assert page.read_bytes() == authored_bytes
    assert load(page).fm.sources[0].resource == "/authored/design.md"


def test_failed_ancestor_normalization_does_not_block_leaf_repair(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    _spec(layout, EPIC)
    _spec(layout, CHILD)
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    epic_page = layout.bundle_dir / f"{EPIC}.md"
    authored = load(epic_page)
    authored.set("sources", [{"id": "design", "resource": "/authored/epic.md"}])
    authored_bytes = authored.serialize().encode("utf-8")
    original = work.parse
    raced = False

    def inject_after_snapshot(*args, **kwargs):
        nonlocal raced
        document = original(*args, **kwargs)
        if kwargs.get("path") == epic_page and not raced:
            epic_page.write_bytes(authored_bytes)
            raced = True
        return document

    monkeypatch.setattr(work, "parse", inject_after_snapshot)
    result = work.run_next(layout, EPIC, descend=True, dry_run=False)
    assert result.application.normalized == (CHILD,)
    assert epic_page.read_bytes() == authored_bytes
    assert load(layout.bundle_dir / f"{CHILD}.md").fm.sources[0].resource.endswith(f"/{CHILD}/references/01-design.md")


def test_open_owner_decision_blocks_exact_affected_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    ledger = layout.bundle_dir / f"{CHILD}/references/00-decisions.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        f"# Decisions\n\n## D-001 — question\nstatus: open\naffects: [{CHILD}]\n",
        encoding="utf-8",
    )
    result = work.run_next(layout, CHILD)
    assert result.state.has_open_decision is True
    assert result.route.dispatch is None


def _split_layout(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / ".git").mkdir(parents=True)
    code = tmp_path / "code"
    (code / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(vault / ".works", today=TODAY, topic="Split")).layout
    layout.manifest_path.write_text(
        f'version: 1\nrepositories:\n  "code":\n    path: {json.dumps(str(code))}\n',
        encoding="utf-8",
    )
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def test_split_topology_normalization_validates_against_the_declared_code_repo(tmp_path: Path) -> None:
    layout = _split_layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    _spec(layout, CHILD)
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    result = work.run_next(layout, CHILD, dry_run=False)
    assert result.warnings == ()
    assert result.application.normalized == (CHILD,)


def test_dependency_parser_requires_complete_path_mapping() -> None:
    parsed = work.parse_dependencies([{"path": "work/feature-a", "blocks": "plan", "needs": "design"}])
    assert parsed.issues == ()
    assert parsed.edges == (work.DependencyEdge("work/feature-a", "plan", "design"),)
