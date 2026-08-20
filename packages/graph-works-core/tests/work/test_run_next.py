"""`run_next`: routes *slug*, resolving `has_open_decision` per-slug -- the
case `work-tracker-okf/cli.py`'s `next_stage` could never report."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import Document, load

TODAY = date(2026, 8, 17)
EPIC = "2026-08-01-epic-x"
CHILD = "2026-08-02-feature-a"

_ITEM = """---
type: {type}
title: {slug}
description: d
status: stable
workflow_status: open
phase: {phase}
effort: medium
opened: 2026-08-01
updated: 2026-08-01
affects:
- packages/a
{extra}---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""


def test_qualified_work_command_surface_is_complete() -> None:
    assert work.__all__ == [
        "AdoptChildSpecsResult",
        "ChildRollup",
        "Decision",
        "DecisionCommandResult",
        "DecisionOwner",
        "DependencyEdge",
        "DependencyIssue",
        "DependencyParse",
        "FilingOutcome",
        "NextApplication",
        "NextResult",
        "OverturnApplication",
        "OverturnApplyError",
        "OverturnPlan",
        "OverturnResult",
        "SourceNormalization",
        "StatusReport",
        "Transition",
        "parse_dependencies",
        "run_adopt_child_specs",
        "run_decision_add",
        "run_decision_answer",
        "run_decision_list",
        "run_decision_overturn",
        "run_decision_supersede",
        "run_file",
        "run_lint",
        "run_next",
        "run_regen_index",
        "run_status",
    ]


def _workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write_item(layout, slug, *, type="Feature", phase="plan", extra=""):
    (layout.bundle_dir / "work" / f"{slug}.md").write_text(
        _ITEM.format(type=type, slug=slug, phase=phase, extra=extra), encoding="utf-8"
    )


def _ledger(layout, epic_slug):
    path = layout.bundle_dir / "work" / epic_slug / "references" / "00-decisions.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def item_bytes(layout: WorkspaceLayout, slug: str) -> bytes:
    return (layout.bundle_dir / "work" / f"{slug}.md").read_bytes()


def _write_canonical_spec(layout: WorkspaceLayout, slug: str) -> None:
    target = layout.bundle_dir / "work" / slug / "references/01-design-spec.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# Design spec — {slug}\n", encoding="utf-8")


def workspace_with_unstamped_canonical_spec(tmp_path: Path, slug: str) -> WorkspaceLayout:
    layout = _workspace(tmp_path)
    _write_item(layout, slug, phase="design")
    _write_canonical_spec(layout, slug)
    return layout


def workspace_with_source(tmp_path: Path, slug: str, *, resource: str) -> WorkspaceLayout:
    layout = _workspace(tmp_path)
    _write_item(
        layout,
        slug,
        phase="design",
        extra=f"sources:\n  - id: design-spec\n    resource: {resource}\n",
    )
    return layout


def gated_epic_with_leaf_specs(tmp_path: Path) -> WorkspaceLayout:
    layout = _workspace(tmp_path)
    _write_item(layout, EPIC, type="Epic", phase="execute")
    _write_item(layout, CHILD, phase="design", extra=f"parent: {EPIC}\n")
    _write_canonical_spec(layout, EPIC)
    _write_canonical_spec(layout, CHILD)
    return layout


def test_unknown_requested_slug_is_a_narrow_caller_error(tmp_path):
    layout = _workspace(tmp_path)
    with pytest.raises(ValueError, match="unknown work item 'missing'"):
        work.run_next(layout, "missing")


def test_next_plans_canonical_source_and_routes_against_planned_state(tmp_path) -> None:
    layout = workspace_with_unstamped_canonical_spec(tmp_path, CHILD)
    before = item_bytes(layout, CHILD)
    result = work.run_next(layout, CHILD)
    assert result.requested_slug == CHILD
    assert result.selected_slug == CHILD
    assert [change.slug for change in result.normalizations] == [CHILD]
    assert result.state.has_spec_doc is True
    assert result.route.dispatch.variant == "reconcile"
    assert item_bytes(layout, CHILD) == before


def test_descend_preserves_ancestor_and_leaf_normalizations(tmp_path) -> None:
    layout = gated_epic_with_leaf_specs(tmp_path)
    result = work.run_next(layout, EPIC, descend=True)
    assert result.descent is not None
    assert result.descent.path == (EPIC, CHILD)
    assert result.selected_slug == CHILD
    assert [change.slug for change in result.normalizations] == [EPIC, CHILD]


def test_existing_noncanonical_design_source_is_preserved(tmp_path) -> None:
    layout = workspace_with_source(tmp_path, CHILD, resource="/authored/spec.md")
    result = work.run_next(layout, CHILD)
    assert result.normalizations == ()
    assert result.state.has_spec_doc is True


def raising_save(message: str):
    def fail(_document: Document) -> None:
        raise OSError(message)

    return fail


def test_apply_saves_each_normalization_and_is_idempotent(tmp_path) -> None:
    layout = workspace_with_unstamped_canonical_spec(tmp_path, CHILD)
    first = work.run_next(layout, CHILD, dry_run=False)
    second = work.run_next(layout, CHILD, dry_run=False)
    assert first.application.normalized == (CHILD,)
    assert second.normalizations == ()
    assert second.application.normalized == ()


def test_apply_preserves_authored_override_introduced_after_planning(tmp_path, monkeypatch) -> None:
    layout = gated_epic_with_leaf_specs(tmp_path)
    child_page = layout.bundle_dir / "work" / f"{CHILD}.md"
    authored_extra = (
        f"parent: {EPIC}\nsources:\n  - id: design-spec\n    resource: /authored/spec.md\n    title: Authored design\n"
    )
    authored_bytes = _ITEM.format(
        type="Feature",
        slug=CHILD,
        phase="design",
        extra=authored_extra,
    ).encode()
    original_load = work.load
    override_introduced = False

    def load_after_author_override(page: Path) -> Document:
        nonlocal override_introduced
        if page == child_page and not override_introduced:
            child_page.write_bytes(authored_bytes)
            override_introduced = True
        return original_load(page)

    monkeypatch.setattr(work, "load", load_after_author_override)
    result = work.run_next(layout, EPIC, descend=True, dry_run=False)

    assert [change.slug for change in result.normalizations] == [EPIC, CHILD]
    assert result.application.normalized == (EPIC,)
    assert result.warnings == ()
    assert item_bytes(layout, CHILD) == authored_bytes
    assert load(child_page).fm.sources[0].resource == "/authored/spec.md"
    assert result.state.has_spec_doc is True
    assert result.route.dispatch.variant == "reconcile"


def test_apply_continues_after_skipping_an_authored_override(tmp_path, monkeypatch) -> None:
    layout = gated_epic_with_leaf_specs(tmp_path)
    epic_page = layout.bundle_dir / "work" / f"{EPIC}.md"
    authored_extra = (
        "sources:\n  - id: design-spec\n    resource: /authored/epic-spec.md\n    title: Authored epic design\n"
    )
    authored_bytes = _ITEM.format(
        type="Epic",
        slug=EPIC,
        phase="execute",
        extra=authored_extra,
    ).encode()
    original_load = work.load
    override_introduced = False

    def load_after_author_override(page: Path) -> Document:
        nonlocal override_introduced
        if page == epic_page and not override_introduced:
            epic_page.write_bytes(authored_bytes)
            override_introduced = True
        return original_load(page)

    monkeypatch.setattr(work, "load", load_after_author_override)
    result = work.run_next(layout, EPIC, descend=True, dry_run=False)

    assert [change.slug for change in result.normalizations] == [EPIC, CHILD]
    assert result.application.normalized == (CHILD,)
    assert result.warnings == ()
    assert item_bytes(layout, EPIC) == authored_bytes
    assert load(epic_page).fm.sources[0].resource == "/authored/epic-spec.md"
    assert load(layout.bundle_dir / "work" / f"{CHILD}.md").fm.sources[0].resource == (
        f"/work/{CHILD}/references/01-design-spec.md"
    )


def test_save_failure_warns_and_reroutes_against_persisted_state(tmp_path, monkeypatch) -> None:
    layout = workspace_with_unstamped_canonical_spec(tmp_path, CHILD)
    monkeypatch.setattr(Document, "save", raising_save("read-only filesystem"))
    result = work.run_next(layout, CHILD, dry_run=False)
    assert result.application.normalized == ()
    assert any("read-only filesystem" in warning for warning in result.warnings)
    assert result.state.has_spec_doc is False
    assert result.route.dispatch.variant != "reconcile"


def test_a_lone_item_routes_with_no_open_decision(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "2026-08-01-feature-a", phase="plan")
    result = work.run_next(layout, "2026-08-01-feature-a")
    assert result is not None
    assert result.state.has_open_decision is False
    assert result.route.dispatch is not None


def test_an_open_decision_affecting_the_slug_blocks_it(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "2026-08-01-epic-x", type="Epic", phase="execute")
    _write_item(layout, "2026-08-02-feature-a", phase="design", extra="parent: 2026-08-01-epic-x\n")
    _ledger(layout, "2026-08-01-epic-x").write_text(
        "# Decisions\n\n## D-001 — question\nstatus: open\naffects: [2026-08-02-feature-a]\n\nprose\n",
        encoding="utf-8",
    )
    result = work.run_next(layout, "2026-08-02-feature-a")
    assert result is not None
    assert result.state.has_open_decision is True
    assert result.route.dispatch is None
    assert "open decision" in result.route.blockers[0]


def test_an_open_decision_not_naming_the_slug_does_not_block_it(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "2026-08-01-epic-x", type="Epic", phase="execute")
    _write_item(layout, "2026-08-02-feature-a", phase="design", extra="parent: 2026-08-01-epic-x\n")
    _ledger(layout, "2026-08-01-epic-x").write_text(
        "# Decisions\n\n## D-001 — question\nstatus: open\naffects: [some-other-slug]\n\nprose\n",
        encoding="utf-8",
    )
    result = work.run_next(layout, "2026-08-02-feature-a")
    assert result is not None
    assert result.state.has_open_decision is False
    assert result.route.dispatch is not None


def test_artifact_names_the_design_spec_the_dispatched_stage_will_write(tmp_path) -> None:
    layout = _workspace(tmp_path)
    _write_item(layout, CHILD, phase="design")
    result = work.run_next(layout, CHILD)
    assert result.artifact is not None
    assert result.artifact.rel == f"work/{CHILD}/references/01-design-spec.md"
    assert result.artifact.source_id == "design-spec"


def test_artifact_names_the_plan_at_the_plan_stage(tmp_path) -> None:
    layout = _workspace(tmp_path)
    _write_item(layout, CHILD, phase="plan")
    result = work.run_next(layout, CHILD)
    assert result.artifact is not None
    assert result.artifact.rel == f"work/{CHILD}/references/02-plan-plan.md"


def test_artifact_is_none_when_the_completion_stamps_nothing(tmp_path) -> None:
    layout = _workspace(tmp_path)
    _write_item(layout, CHILD, phase="execute")
    result = work.run_next(layout, CHILD)
    assert result.route.on_complete is not None
    assert result.route.on_complete.stamp_source is None
    assert result.artifact is None


def test_artifact_follows_the_descent_to_the_selected_leaf(tmp_path) -> None:
    layout = gated_epic_with_leaf_specs(tmp_path)
    result = work.run_next(layout, EPIC, descend=True)
    assert result.selected_slug == CHILD
    assert result.artifact is not None
    assert result.artifact.rel.startswith(f"work/{CHILD}/")


def test_dependency_vocabulary_is_reachable_through_the_command_module(tmp_path) -> None:
    parsed = work.parse_dependencies([{"slug": "s", "blocks": "nope"}])
    assert [issue.code for issue in parsed.issues] == ["invalid-blocks"]
    assert parsed.edges == (work.DependencyEdge("s", "nope", "resolved"),)
