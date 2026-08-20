"""High-level decision commands resolving every item to its epic-owned ledger."""

from __future__ import annotations

import operator
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf.config import Config, StateGateConfig
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from graph_works_core.work.commands import OverturnApplication
from graph_works_core.workspace.layout import WorkspaceLayout
from work_tracker_okf.compose import FilingApplication, FilingApplyError
from work_tracker_okf.filing import compose_slug

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


def _workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write_item(layout, slug, *, type="Feature", phase="design", extra="", archived=False):
    directory = layout.bundle_dir / "work" / "_archive" if archived else layout.bundle_dir / "work"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{slug}.md").write_text(
        _ITEM.format(type=type, slug=slug, phase=phase, extra=extra), encoding="utf-8"
    )


def _seeded(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, EPIC, type="Epic", phase="execute")
    _write_item(layout, CHILD, extra=f"parent: {EPIC}\n")
    return layout


def snapshot_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def seeded_decisions(tmp_path):
    layout = _seeded(tmp_path)
    work.run_decision_add(
        layout,
        CHILD,
        question="q1",
        status="open",
        affects=(CHILD,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    work.run_decision_add(
        layout,
        CHILD,
        question="q2",
        status="assumed",
        answer="guess",
        if_wrong="re-plan",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    return layout


def overturn_workspace(tmp_path: Path) -> tuple[WorkspaceLayout, Config, Path]:
    layout = _seeded(tmp_path)
    config = _config(layout)
    work.run_decision_add(
        layout,
        EPIC,
        question="Which layout?",
        status="answered",
        answer="Original",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    ledger = layout.bundle_dir / "work" / EPIC / "references/00-decisions.md"
    return layout, config, ledger


def _config(layout: WorkspaceLayout) -> Config:
    return Config(
        graph_dir=layout.cache_dir / "graph",
        declarations_dir=layout.config_dir,
        repos=(),
        state_gate=StateGateConfig(enabled=False, branches=("main",)),
    )


def create_follow_up_collision(layout: WorkspaceLayout, *, title: str, on: date) -> None:
    slug, _warnings = compose_slug("TechDebt", title, on=on)
    page = layout.bundle_dir / "work" / f"{slug}.md"
    page.write_text("authored collision\n", encoding="utf-8")


def test_add_dry_run_returns_the_same_plan_real_apply_uses(tmp_path):
    layout = _seeded(tmp_path)
    before = snapshot_bytes(layout.bundle_dir)
    dry = work.run_decision_add(
        layout,
        CHILD,
        question="Which store?",
        status="assumed",
        answer="SQLite",
        rationale="single writer",
        if_wrong="re-plan storage",
        affects=(CHILD,),
        on=TODAY,
        decided_by="pat",
    )
    assert dry.owner.epic_slug == EPIC
    assert dry.owner.redirected_from == CHILD
    assert dry.application.written is False
    with pytest.raises(FrozenInstanceError):
        dry.owner.epic_slug = "different"
    with pytest.raises(TypeError):
        operator.setitem(dry.counts, "total", 99)
    assert snapshot_bytes(layout.bundle_dir) == before

    real = work.run_decision_add(
        layout,
        CHILD,
        question="Which store?",
        status="assumed",
        answer="SQLite",
        rationale="single writer",
        if_wrong="re-plan storage",
        affects=(CHILD,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert real.plan == dry.plan
    assert real.application.written is True


def test_add_with_no_epic_ancestor_raises(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "2026-08-01-feature-lone")
    with pytest.raises(ValueError, match="no epic ancestor") as excinfo:
        work.run_decision_add(
            layout,
            "2026-08-01-feature-lone",
            question="q",
            status="open",
            on=TODAY,
            decided_by="pat",
        )
    assert "2026-08-01-feature-lone" in str(excinfo.value)


def test_unknown_target_is_distinct_from_a_known_item_with_no_epic(tmp_path):
    layout = _seeded(tmp_path)
    with pytest.raises(ValueError, match="unknown work item") as excinfo:
        work.run_decision_list(layout, "2026-08-09-feature-missing")
    assert "2026-08-09-feature-missing" in str(excinfo.value)


@pytest.mark.parametrize("epic_archived", [False, True])
def test_archived_child_resolves_its_epics_active_or_archived_ledger(tmp_path, epic_archived):
    layout = _workspace(tmp_path)
    _write_item(layout, EPIC, type="Epic", phase="execute", archived=epic_archived)
    _write_item(layout, CHILD, extra=f"parent: {EPIC}\n", archived=True)

    result = work.run_decision_list(layout, CHILD)

    prefix = layout.bundle_dir / "work" / ("_archive" if epic_archived else "")
    assert result.owner.epic_slug == EPIC
    assert result.owner.redirected_from == CHILD
    assert result.owner.ledger == prefix / EPIC / "references" / "00-decisions.md"


def test_active_child_wins_over_same_slug_archived_twin_with_a_historical_parent(tmp_path):
    historical_epic = "2026-07-01-epic-historical"
    layout = _workspace(tmp_path)
    _write_item(layout, EPIC, type="Epic", phase="execute")
    _write_item(layout, historical_epic, type="Epic", phase="execute", archived=True)
    _write_item(layout, CHILD, extra=f"parent: {EPIC}\n")
    _write_item(layout, CHILD, extra=f"parent: {historical_epic}\n", archived=True)

    result = work.run_decision_add(
        layout,
        CHILD,
        question="Which owner?",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )

    expected = layout.bundle_dir / "work" / EPIC / "references" / "00-decisions.md"
    historical = layout.bundle_dir / "work" / "_archive" / historical_epic / "references" / "00-decisions.md"
    assert result.owner.epic_slug == EPIC
    assert result.owner.ledger == expected
    assert expected.is_file()
    assert not historical.exists()


def test_epic_target_is_not_reported_as_redirected(tmp_path):
    layout = _seeded(tmp_path)
    result = work.run_decision_list(layout, EPIC)
    assert result.owner.redirected_from is None


def test_answer_dry_run_previews_the_exact_update_real_apply_uses(tmp_path):
    layout = _seeded(tmp_path)
    work.run_decision_add(
        layout, CHILD, question="Which store?", status="open", on=TODAY, decided_by="pat", dry_run=False
    )
    before = snapshot_bytes(layout.bundle_dir)
    dry = work.run_decision_answer(
        layout,
        CHILD,
        "D-001",
        answer="SQLite",
        rationale="confirmed",
        on=TODAY,
        decided_by="pat",
    )
    assert dry.application.written is False
    assert dry.plan is not None
    assert dry.plan.primary is not None
    assert dry.plan.primary.question == "Which store?"
    assert dry.plan.primary.status == "answered"
    assert dry.plan.primary.decided == "2026-08-17 by pat"
    assert snapshot_bytes(layout.bundle_dir) == before

    real = work.run_decision_answer(
        layout,
        CHILD,
        "D-001",
        answer="SQLite",
        rationale="confirmed",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert real.plan == dry.plan
    assert real.application.written is True
    assert [entry.status for entry in real.entries] == ["answered"]


def test_list_filters_entries_but_counts_the_whole_ledger(tmp_path):
    layout = seeded_decisions(tmp_path)
    before = snapshot_bytes(layout.bundle_dir)
    result = work.run_decision_list(layout, CHILD, status="open", affects=CHILD, cites=None)
    assert [entry.id for entry in result.entries] == ["D-001"]
    assert result.counts["total"] == 2
    assert result.plan is None
    assert result.application.written is False
    assert snapshot_bytes(layout.bundle_dir) == before


def test_list_forwards_cites_and_combines_it_with_other_filters(tmp_path):
    layout = seeded_decisions(tmp_path)
    work.run_decision_supersede(
        layout,
        CHILD,
        "D-001",
        question="q1 revised",
        answer="settled",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )

    result = work.run_decision_list(layout, CHILD, status="answered", cites="D-001")

    assert [entry.id for entry in result.entries] == ["D-003"]
    assert result.counts["total"] == 3


def test_refused_mutation_reports_the_plan_without_writing(tmp_path):
    layout = _seeded(tmp_path)
    before = snapshot_bytes(layout.bundle_dir)

    result = work.run_decision_add(
        layout,
        CHILD,
        question="q",
        status="assumed",
        if_wrong="re-plan",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )

    assert result.plan is not None
    assert result.plan.refusal == "answer-required"
    assert result.entries == ()
    assert result.application.written is False
    assert snapshot_bytes(layout.bundle_dir) == before


def test_stale_application_is_reported_without_claiming_entries_landed(tmp_path, monkeypatch):
    from work_tracker_okf import decisions

    layout = _seeded(tmp_path)
    apply_plan = decisions.apply_plan

    def apply_after_external_edit(plan):
        plan.ledger.parent.mkdir(parents=True, exist_ok=True)
        plan.ledger.write_text("# external edit\n", encoding="utf-8")
        return apply_plan(plan)

    monkeypatch.setattr(decisions, "apply_plan", apply_after_external_edit)

    result = work.run_decision_add(
        layout,
        CHILD,
        question="q",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )

    assert result.application.stale is True
    assert result.application.written is False
    assert result.application.entries == ()
    assert result.owner.ledger.read_text(encoding="utf-8") == "# external edit\n"


def test_supersede_dry_run_previews_both_entries_real_apply_uses(tmp_path):
    layout = _seeded(tmp_path)
    work.run_decision_add(
        layout,
        CHILD,
        question="Which store?",
        status="open",
        affects=(CHILD,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    before = snapshot_bytes(layout.bundle_dir)
    dry = work.run_decision_supersede(
        layout,
        CHILD,
        "D-001",
        question="Which store, revised?",
        answer="SQLite",
        rationale="confirmed",
        on=TODAY,
        decided_by="pat",
    )
    assert dry.plan is not None
    assert dry.plan.superseded is not None
    assert dry.plan.primary is not None
    assert dry.plan.superseded.status == "superseded"
    assert dry.plan.primary.supersedes == "D-001"
    assert dry.plan.primary.status == "answered"
    assert dry.plan.primary.affects == (CHILD,)
    assert dry.application.written is False
    assert snapshot_bytes(layout.bundle_dir) == before

    real = work.run_decision_supersede(
        layout,
        CHILD,
        "D-001",
        question="Which store, revised?",
        answer="SQLite",
        rationale="confirmed",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert real.plan == dry.plan
    assert real.application.written is True
    assert [entry.status for entry in real.entries] == ["superseded", "answered"]


def test_overturn_filing_refusal_leaves_ledger_byte_identical(tmp_path) -> None:
    layout, config, ledger = overturn_workspace(tmp_path)
    create_follow_up_collision(layout, title="Rework layout", on=TODAY)
    before = ledger.read_bytes()
    result = work.run_decision_overturn(
        layout,
        config,
        EPIC,
        "D-001",
        answer="Use the other layout",
        rationale="production evidence",
        follow_up_title="Rework layout",
        follow_up_type="TechDebt",
        follow_up_affects=("packages/work-tracker-okf",),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert result.plan.refusal == "follow-up-refused"
    assert result.application == OverturnApplication()
    assert ledger.read_bytes() == before


def test_overturn_supersedes_then_files_a_peer_follow_up(tmp_path) -> None:
    layout, config, _ledger = overturn_workspace(tmp_path)
    result = work.run_decision_overturn(
        layout,
        config,
        CHILD,
        "D-001",
        answer="Use the other layout",
        rationale=None,
        follow_up_title="Rework layout",
        follow_up_type="Bug",
        follow_up_affects=("packages/work-tracker-okf",),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert result.application.decision.written is True
    assert result.application.filing.written is True
    assert result.plan.filing.filing.seed.parent is None
    assert result.plan.filing.filing.seed.depends_on == ()
    assert "D-002" in result.plan.filing.filing.seed.description


def test_overturn_dry_run_previews_both_resources_byte_identically(tmp_path) -> None:
    layout, config, ledger = overturn_workspace(tmp_path)
    before = snapshot_bytes(layout.bundle_dir)
    result = work.run_decision_overturn(
        layout,
        config,
        EPIC,
        "D-001",
        answer="Other layout",
        rationale=None,
        follow_up_title="Rework layout",
        on=TODAY,
        decided_by="pat",
    )
    assert result.plan.decision.refusal is None
    assert result.plan.filing.refusal is None
    assert result.application == OverturnApplication()
    assert ledger.read_bytes() == before[f"work/{EPIC}/references/00-decisions.md"]
    assert snapshot_bytes(layout.bundle_dir) == before


def test_overturn_stale_decision_application_never_files_follow_up(tmp_path, monkeypatch) -> None:
    from work_tracker_okf import decisions

    layout, config, ledger = overturn_workspace(tmp_path)
    follow_up_slug, _warnings = compose_slug("TechDebt", "Rework stale layout", on=TODAY)
    follow_up = layout.bundle_dir / "work" / f"{follow_up_slug}.md"
    apply_plan = decisions.apply_plan

    def apply_after_external_edit(plan):
        ledger.write_text("# external edit\n", encoding="utf-8")
        return apply_plan(plan)

    monkeypatch.setattr(decisions, "apply_plan", apply_after_external_edit)
    result = work.run_decision_overturn(
        layout,
        config,
        EPIC,
        "D-001",
        answer="Other layout",
        rationale=None,
        follow_up_title="Rework stale layout",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )

    assert result.application.decision.stale is True
    assert result.application.filing == FilingApplication()
    assert result.warnings == ("stale-decision-plan: ledger changed after preflight; follow-up was not filed",)
    assert not follow_up.exists()


def test_overturn_partial_filing_failure_preserves_application_and_cause(tmp_path, monkeypatch) -> None:
    layout, config, _ledger = overturn_workspace(tmp_path)
    partial = FilingApplication(page=layout.bundle_dir / "work/partial.md")
    filing_error = FilingApplyError("disk full", partial)

    def fail_filing(_plan):
        raise filing_error

    monkeypatch.setattr(work, "apply_file_and_reconcile", fail_filing)
    with pytest.raises(work.OverturnApplyError) as raised:
        work.run_decision_overturn(
            layout,
            config,
            EPIC,
            "D-001",
            answer="Other layout",
            rationale=None,
            follow_up_title="Rework partial layout",
            on=TODAY,
            decided_by="pat",
            dry_run=False,
        )

    assert raised.value.application.decision.written is True
    assert raised.value.application.filing == partial
    assert raised.value.__cause__ is filing_error
