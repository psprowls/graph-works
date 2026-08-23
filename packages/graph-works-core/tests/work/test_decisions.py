"""Path-owned decisions are planned and committed as one work mutation."""

from __future__ import annotations

import fcntl
import hashlib
import os
from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf.config import Config, StateGateConfig
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from okf_io import load

TODAY = date(2026, 8, 23)
OWNER = "work/release-v1/children/epic-a/children/feature-a"
LEAF = f"{OWNER}/children/bug-a"


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Decisions")).layout


def _write(layout, path: str, type: str) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: open\n"
        "phase: design\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\naffects:\n"
        "- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n",
        encoding="utf-8",
    )


def _workspace(tmp_path: Path):
    layout = _layout(tmp_path)
    _write(layout, "work/release-v1", "Release")
    _write(layout, "work/release-v1/children/epic-a", "Epic")
    _write(layout, OWNER, "Feature")
    _write(layout, LEAF, "Bug")
    return layout


def _config(layout) -> Config:
    return Config(
        graph_dir=layout.cache_dir / "graph",
        declarations_dir=layout.config_dir,
        repos=(),
        state_gate=StateGateConfig(enabled=False, branches=("main",)),
    )


def test_leaf_decision_redirects_to_nearest_feature_owner(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    result = work.run_decision_add(
        layout,
        LEAF,
        question="Ship it?",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
    )
    assert result.owner.owner_path == OWNER
    assert result.owner.redirected_from == LEAF
    assert result.owner.ledger == layout.bundle_dir / f"{OWNER}/references/00-decisions.md"
    assert result.application is None


def test_live_decision_is_journaled_and_registers_ledger(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    result = work.run_decision_add(
        layout,
        LEAF,
        question="Ship it?",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert result.application is not None and result.application.ok
    assert result.application.journal.is_file()
    assert result.owner.ledger.is_file()
    sources = load(layout.bundle_dir / f"{OWNER}.md").fm.sources
    assert [source.id for source in sources] == ["decisions"]
    lock = layout.cache_dir / "decisions" / f"{hashlib.sha256(OWNER.encode()).hexdigest()}.lock"
    assert lock.is_file()
    assert not result.owner.ledger.with_suffix(".lock").exists()


def test_answer_and_list_use_the_same_canonical_owner(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    work.run_decision_add(
        layout,
        LEAF,
        question="Ship it?",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    answered = work.run_decision_answer(
        layout,
        LEAF,
        "D-001",
        answer="yes",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    listed = work.run_decision_list(layout, OWNER, status="answered")
    assert answered.application is not None and answered.application.ok
    assert [entry.id for entry in listed.entries] == ["D-001"]


def test_list_filters_entries_but_counts_the_entire_owner_ledger(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    work.run_decision_add(
        layout,
        LEAF,
        question="Open question",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    work.run_decision_add(
        layout,
        LEAF,
        question="Settled question",
        status="answered",
        answer="yes",
        affects=(OWNER,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    result = work.run_decision_list(layout, LEAF, status="open", affects=LEAF)
    assert [entry.id for entry in result.entries] == ["D-001"]
    assert result.counts["total"] == 2
    assert result.plan is None and result.application is None


def test_list_combines_cites_with_status_filter(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    work.run_decision_add(
        layout,
        LEAF,
        question="Original",
        status="answered",
        answer="yes",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    work.run_decision_supersede(
        layout,
        LEAF,
        "D-001",
        question="Revised",
        answer="no",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    result = work.run_decision_list(layout, LEAF, status="answered", cites="D-001")
    assert [entry.id for entry in result.entries] == ["D-002"]
    assert result.counts["total"] == 2


def test_add_dry_run_matches_live_plan_and_refusal_writes_nothing(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    before = {
        path.relative_to(layout.bundle_dir): path.read_bytes()
        for path in layout.bundle_dir.rglob("*")
        if path.is_file()
    }
    dry = work.run_decision_add(
        layout,
        LEAF,
        question="Which store?",
        status="assumed",
        answer="SQLite",
        if_wrong="re-plan storage",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
    )
    assert dry.application is None
    assert {
        path.relative_to(layout.bundle_dir): path.read_bytes()
        for path in layout.bundle_dir.rglob("*")
        if path.is_file()
    } == before
    real = work.run_decision_add(
        layout,
        LEAF,
        question="Which store?",
        status="assumed",
        answer="SQLite",
        if_wrong="re-plan storage",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert real.plan == dry.plan
    assert real.application is not None and real.application.ok

    before_refusal = layout.bundle_dir.joinpath(f"{OWNER}/references/00-decisions.md").read_bytes()
    refused = work.run_decision_add(
        layout,
        LEAF,
        question="Missing answer",
        status="assumed",
        if_wrong="re-plan",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert refused.plan is not None and refused.plan.refusal == "answer-required"
    assert refused.application is None
    assert layout.bundle_dir.joinpath(f"{OWNER}/references/00-decisions.md").read_bytes() == before_refusal


def test_archived_parent_capable_item_owns_its_canonical_ledger(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    archived_owner = "work/_archive/epic-a"
    _write(layout, archived_owner, "Epic")
    result = work.run_decision_list(layout, archived_owner)
    assert result.owner.owner_path == archived_owner
    assert result.owner.redirected_from is None
    assert result.owner.ledger == layout.bundle_dir / f"{archived_owner}/references/00-decisions.md"


def test_concurrent_ledger_change_after_allocation_is_never_overwritten(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    original = work._decisions.plan_append
    concurrent = f"# Decisions\n\n## D-900 — Concurrent\nstatus: open\naffects: [{LEAF}]\n"

    def inject_after_planning(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan.ledger.parent.mkdir(parents=True, exist_ok=True)
        plan.ledger.write_text(concurrent, encoding="utf-8")
        return plan

    monkeypatch.setattr(work._decisions, "plan_append", inject_after_planning)
    result = work.run_decision_add(
        layout,
        LEAF,
        question="Ship it?",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert result.application is not None and not result.application.ok
    assert result.owner.ledger.read_text(encoding="utf-8") == concurrent
    assert [entry.id for entry in result.entries] == ["D-900"]
    assert result.counts["total"] == 1


def test_concurrent_empty_ledger_does_not_replace_planned_absence(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    original = work._decisions.plan_append

    def inject_after_planning(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan.ledger.parent.mkdir(parents=True, exist_ok=True)
        plan.ledger.write_bytes(b"")
        return plan

    monkeypatch.setattr(work._decisions, "plan_append", inject_after_planning)
    result = work.run_decision_add(
        layout,
        LEAF,
        question="Ship it?",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert result.application is not None and not result.application.ok
    assert result.owner.ledger.read_bytes() == b""
    assert result.entries == ()


def test_live_decision_allocation_occurs_while_owner_lock_is_held(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    lock = layout.cache_dir / "decisions" / f"{hashlib.sha256(OWNER.encode()).hexdigest()}.lock"
    original = work._decisions.plan_append
    observed = False

    def assert_lock_held(*args, **kwargs):
        nonlocal observed
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed = True
        finally:
            os.close(descriptor)
        return original(*args, **kwargs)

    monkeypatch.setattr(work._decisions, "plan_append", assert_lock_held)
    result = work.run_decision_add(
        layout,
        LEAF,
        question="Ship it?",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert observed is True
    assert result.application is not None and result.application.ok


def test_live_decision_revalidates_owner_selection_after_acquiring_the_lock(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    new_owner = "work/release-v1/children/epic-a/children/feature-b"
    new_leaf = f"{new_owner}/children/bug-a"
    _write(layout, new_owner, "Feature")
    original = work.fcntl.flock
    moved = False

    def move_before_lock(descriptor, operation):
        nonlocal moved
        if operation == fcntl.LOCK_EX and not moved:
            destination = layout.bundle_dir / f"{new_leaf}.md"
            destination.parent.mkdir(parents=True)
            (layout.bundle_dir / f"{LEAF}.md").rename(destination)
            moved = True
        return original(descriptor, operation)

    monkeypatch.setattr(work.fcntl, "flock", move_before_lock)
    with pytest.raises(ValueError, match="unknown work item"):
        work.run_decision_add(
            layout,
            LEAF,
            question="Ship it?",
            affects=(LEAF,),
            on=TODAY,
            decided_by="pat",
            dry_run=False,
        )
    assert not (layout.bundle_dir / f"{OWNER}/references/00-decisions.md").exists()
    assert not (layout.bundle_dir / f"{new_owner}/references/00-decisions.md").exists()


def test_decision_refuses_an_owner_page_changed_after_its_snapshot(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    owner_page = layout.bundle_dir / f"{OWNER}.md"
    external = owner_page.read_bytes() + b"\nExternal owner edit.\n"
    original = work.upsert
    injected = False

    def inject_after_snapshot(*args, **kwargs):
        nonlocal injected
        if not injected:
            owner_page.write_bytes(external)
            injected = True
        return original(*args, **kwargs)

    monkeypatch.setattr(work, "upsert", inject_after_snapshot)
    result = work.run_decision_add(
        layout,
        LEAF,
        question="Ship it?",
        affects=(LEAF,),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert result.application is not None and not result.application.ok
    assert owner_page.read_bytes() == external
    assert not result.owner.ledger.exists()


def test_unknown_path_is_a_caller_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown work item"):
        work.run_decision_list(_workspace(tmp_path), "work/missing")


def test_overturn_refusal_keeps_the_ledger_byte_identical(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    work.run_decision_add(
        layout,
        LEAF,
        question="Original?",
        status="answered",
        answer="yes",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    before = (layout.bundle_dir / f"{OWNER}/references/00-decisions.md").read_bytes()
    result = work.run_decision_overturn(
        layout,
        _config(layout),
        LEAF,
        "D-001",
        answer="no",
        rationale=None,
        follow_up_title="Invalid follow up",
        follow_up_type="NotAType",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert result.plan.refusal == "follow-up-refused"
    assert result.application.mutation is None
    assert (layout.bundle_dir / f"{OWNER}/references/00-decisions.md").read_bytes() == before


def test_overturn_applies_decision_and_follow_up_in_one_journal(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    work.run_decision_add(
        layout,
        LEAF,
        question="Original?",
        status="answered",
        answer="yes",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    result = work.run_decision_overturn(
        layout,
        _config(layout),
        LEAF,
        "D-001",
        answer="no",
        rationale="new evidence",
        follow_up_title="Repair original choice",
        follow_up_affects=("packages/a",),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert result.application.mutation is not None and result.application.mutation.ok
    assert result.application.mutation.journal.is_file()
    assert (layout.bundle_dir / "work/tech-debt-repair-original-choice.md").is_file()
    assert [entry.status for entry in work.run_decision_list(layout, OWNER).entries] == ["superseded", "answered"]


def test_overturn_refuses_a_follow_up_target_created_after_planning(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    work.run_decision_add(
        layout,
        LEAF,
        question="Original?",
        status="answered",
        answer="yes",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    ledger = layout.bundle_dir / f"{OWNER}/references/00-decisions.md"
    ledger_before = ledger.read_bytes()
    original = work.plan_file_and_reconcile
    external = b"external follow-up owner\n"

    def inject_after_planning(*args, **kwargs):
        outcome = original(*args, **kwargs)
        outcome.plan.filing.target.parent.mkdir(parents=True, exist_ok=True)
        outcome.plan.filing.target.write_bytes(external)
        return outcome

    monkeypatch.setattr(work, "plan_file_and_reconcile", inject_after_planning)
    result = work.run_decision_overturn(
        layout,
        _config(layout),
        LEAF,
        "D-001",
        answer="no",
        rationale=None,
        follow_up_title="Raced follow up",
        follow_up_affects=("packages/a",),
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    assert result.application.mutation is not None and not result.application.mutation.ok
    assert result.plan.filing.filing.target.read_bytes() == external
    assert ledger.read_bytes() == ledger_before


def test_overturn_revalidates_owner_selection_after_acquiring_the_lock(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    work.run_decision_add(
        layout,
        LEAF,
        question="Original?",
        status="answered",
        answer="yes",
        on=TODAY,
        decided_by="pat",
        dry_run=False,
    )
    ledger = layout.bundle_dir / f"{OWNER}/references/00-decisions.md"
    before = ledger.read_bytes()
    new_owner = "work/release-v1/children/epic-a/children/feature-b"
    new_leaf = f"{new_owner}/children/bug-a"
    _write(layout, new_owner, "Feature")
    original = work.fcntl.flock
    moved = False

    def move_before_lock(descriptor, operation):
        nonlocal moved
        if operation == fcntl.LOCK_EX and not moved:
            destination = layout.bundle_dir / f"{new_leaf}.md"
            destination.parent.mkdir(parents=True)
            (layout.bundle_dir / f"{LEAF}.md").rename(destination)
            moved = True
        return original(descriptor, operation)

    monkeypatch.setattr(work.fcntl, "flock", move_before_lock)
    with pytest.raises(ValueError, match="unknown work item"):
        work.run_decision_overturn(
            layout,
            _config(layout),
            LEAF,
            "D-001",
            answer="no",
            rationale=None,
            follow_up_title="Must not file",
            on=TODAY,
            decided_by="pat",
            dry_run=False,
        )
    assert ledger.read_bytes() == before
    assert not (layout.bundle_dir / "work/tech-debt-must-not-file.md").exists()
