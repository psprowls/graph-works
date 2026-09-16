"""Hold resolution through the shared decision-owner module."""

from __future__ import annotations

import hashlib
import itertools
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from graph_works_core.workspace import decision_owner as owners
from okf_io import load_bundle
from work_tracker_okf.items import IGNORE, load_items

TODAY = date(2026, 9, 13)
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/bug-a"
LONE = "work/bug-lone"


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Holds")).layout


def _page(layout, path: str, type: str, phase: str = "execute") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: in-progress\n"
        f"owner: pat\nphase: {phase}\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
        "affects:\n- packages/a\n---\n\n## Summary\nd\n",
        encoding="utf-8",
        newline="",
    )


def _ledger(layout, owner: str, text: str) -> None:
    ledger = layout.bundle_dir / f"{owner}/references/00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(text, encoding="utf-8", newline="")


def test_holds_resolve_through_nearest_and_self_owners(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout, EPIC, "Epic")
    _page(layout, CHILD, "Bug")
    _page(layout, LONE, "Bug")
    _ledger(
        layout,
        EPIC,
        f"## D-001 — a\nstatus: answered\naffects: [{CHILD}]\n\n"
        f"## D-002 — b\nstatus: open\naffects: [{CHILD}]\nhold: skip\nphase: execute\n\n"
        f"## D-003 — c\nstatus: open\naffects: [{CHILD}]\n",
    )
    _ledger(layout, LONE, f"## D-001 — d\nstatus: open\naffects: [{LONE}]\n")
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    child = owners.hold_for(items, bundle.root, CHILD)
    assert child is not None and (child.decision_id, child.shape, child.phase) == ("D-002", "skip", "execute")
    assert owners.hold_for(items, bundle.root, EPIC) is None
    assert owners.hold_for(items, bundle.root, "work/unknown") is None
    by_path = owners.holds_by_path(items, bundle.root)
    assert set(by_path) == {CHILD, LONE}
    assert by_path[LONE].shape == "question"


def test_locked_decision_owner_yields_a_fresh_in_lock_projection(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout, EPIC, "Epic")
    _page(layout, CHILD, "Bug")
    with owners.locked_decision_owner(layout, CHILD) as context:
        assert context.owner.owner_path == EPIC
        assert context.owner.redirected_from == CHILD
        assert context.owner.ledger == layout.bundle_dir / f"{EPIC}/references/00-decisions.md"
        assert CHILD in {item.path for item in context.items}
        assert owners.hold_in(context, CHILD) is None
    expected = layout.cache_dir / "decisions" / f"{hashlib.sha256(EPIC.encode()).hexdigest()}.lock"
    assert owners.decision_lock_path(layout, EPIC) == expected


def test_owner_changes_are_retried_at_most_three_times_then_refused(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _page(layout, EPIC, "Epic")
    _page(layout, CHILD, "Bug")
    calls = itertools.cycle([EPIC, CHILD])
    seen: list[str] = []

    def flapping(items, path):
        owner = next(calls)
        seen.append(owner)
        return owner

    monkeypatch.setattr(owners, "decision_owner", flapping)
    with (
        pytest.raises(ValueError, match=f"decision owner of {CHILD} changed during locking; re-run"),
        owners.locked_decision_owner(layout, CHILD),
    ):
        raise AssertionError("must never yield")
    assert len(seen) == 2 * owners.MAX_OWNER_ATTEMPTS == 6
    with pytest.raises(ValueError, match="changed during locking"):
        work.run_decision_add(
            layout,
            CHILD,
            question="q",
            affects=(CHILD,),
            on=TODAY,
            decided_by="pat",
            dry_run=False,
        )
    assert not (layout.bundle_dir / f"{EPIC}/references/00-decisions.md").exists()
    assert not (layout.bundle_dir / f"{CHILD}/references/00-decisions.md").exists()


def test_a_single_reparent_settles_within_one_retry(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _page(layout, EPIC, "Epic")
    _page(layout, CHILD, "Bug")
    answers = iter([CHILD, EPIC, EPIC, EPIC])
    monkeypatch.setattr(owners, "decision_owner", lambda items, path: next(answers))
    with owners.locked_decision_owner(layout, CHILD) as context:
        assert context.owner.owner_path == EPIC


def test_unknown_paths_are_caller_errors(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with pytest.raises(ValueError, match="unknown work item"):
        owners.decision_context(layout, "work/nope")
