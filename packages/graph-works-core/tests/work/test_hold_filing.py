"""`gw work decision add --hold`: refusals, atomic parks, never-overwrite."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from okf_io import load

TODAY = date(2026, 9, 13)
FEATURE = "work/feature-held"
BUG = "work/bug-held"


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Holds")).layout


def _page(
    layout,
    path: str = FEATURE,
    *,
    type: str = "Feature",
    phase: str | None = "execute",
    work_status: str = "in-progress",
):
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    phase_line = f"phase: {phase}\n" if phase else ""
    page.write_text(
        f"---\ntype: {type}\ntitle: Held\ndescription: d\nstatus: stable\nwork_status: {work_status}\n"
        f"owner: pat\n{phase_line}effort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
        "affects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n",
        encoding="utf-8",
        newline="",
    )
    (layout.bundle_dir / path / "references").mkdir(parents=True, exist_ok=True)


def _draft(tmp_path: Path, *, phase: str = "execute", decision: str = "pending", item: str = FEATURE) -> Path:
    draft = tmp_path / f"draft-{phase}-{decision}.md"
    draft.write_text(
        f"---\ntitle: 'Checkpoint: Held ({phase})'\nitem: {item}\ndecision: {decision}\nphase: {phase}\n"
        "dispatch_key: gw-execute-held-00000000\nbranch: feature/held\nworktree: /tmp/wt\nbase: main\n"
        "head: none\ncreated: 2026-09-13T10:00:00Z\n---\n\n## Completed work\n\nhalf\n\n"
        "## Remaining actions\n\nrest\n\n## Question\n\nwhich?\n\n- a\n- b\n\n## Placement\n\nclean\n\n"
        "## Validation evidence\n\nnone run: parked before tests\n",
        encoding="utf-8",
        newline="",
    )
    return draft


def _snapshot(layout) -> dict[str, bytes]:
    refs = layout.bundle_dir / FEATURE / "references"
    files = {p.relative_to(layout.bundle_dir).as_posix(): p.read_bytes() for p in refs.rglob("*") if p.is_file()}
    files["page"] = (layout.bundle_dir / f"{FEATURE}.md").read_bytes()
    return files


def _add(layout, **kwargs):
    base = {"question": "Stop here?", "on": TODAY, "decided_by": "coordinator", "dry_run": False}
    return work.run_decision_add(layout, FEATURE, **{**base, **kwargs})


def test_a_skip_is_filed_open_with_its_phase_and_default_affects(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout)
    result = _add(layout, hold="skip", phase="execute")
    assert result.application is not None and result.application.ok
    entry = result.plan.primary
    assert (entry.status, entry.hold, entry.phase, entry.checkpoint, entry.affects) == (
        "open",
        "skip",
        "execute",
        None,
        (FEATURE,),
    )
    assert work.run_next(layout, FEATURE).route.blockers[0].startswith(f"open decision {entry.id} (skip)")


def test_a_park_files_ledger_and_stamped_checkpoint_together(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout)
    draft = _draft(tmp_path)
    draft_bytes = draft.read_bytes()
    result = _add(layout, hold="park", phase="execute", checkpoint=draft)
    assert result.application is not None and result.application.ok
    entry = result.plan.primary
    target = layout.bundle_dir / FEATURE / "references" / f"03-execute-checkpoint-{entry.id}.md"
    assert entry.checkpoint == f"/{FEATURE}/references/03-execute-checkpoint-{entry.id}.md"
    assert target.read_text(encoding="utf-8") == draft_bytes.decode("utf-8").replace(
        "decision: pending", f"decision: {entry.id}"
    )
    assert draft.read_bytes() == draft_bytes  # copied, never moved or edited
    assert not any(s.resource == entry.checkpoint for s in load(layout.bundle_dir / f"{FEATURE}.md").fm.sources)


def test_a_standalone_bug_park_owns_its_ledger_and_preserves_checkpoint(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout, BUG, type="Bug")
    draft = _draft(tmp_path, item=BUG)
    draft_bytes = draft.read_bytes()
    result = work.run_decision_add(
        layout,
        BUG,
        question="Stop here?",
        on=TODAY,
        decided_by="coordinator",
        hold="park",
        phase="execute",
        checkpoint=draft,
        dry_run=False,
    )
    assert result.application is not None and result.application.ok
    assert result.owner.owner_path == BUG and result.owner.redirected_from is None
    assert result.owner.ledger == layout.bundle_dir / BUG / "references" / "00-decisions.md"
    assert result.owner.ledger.is_file()
    entry = result.plan.primary
    target = layout.bundle_dir / BUG / "references" / f"03-execute-checkpoint-{entry.id}.md"
    assert target.read_text(encoding="utf-8") == draft_bytes.decode("utf-8").replace(
        "decision: pending", f"decision: {entry.id}"
    )
    assert draft.read_bytes() == draft_bytes


def test_two_parks_at_one_phase_never_overwrite(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout)
    first = _add(layout, hold="park", phase="execute", checkpoint=_draft(tmp_path))
    second = _add(layout, hold="park", phase="execute", checkpoint=_draft(tmp_path))
    refs = layout.bundle_dir / FEATURE / "references"
    assert first.plan.primary.id != second.plan.primary.id
    assert sorted(p.name for p in refs.glob("03-execute-checkpoint-*.md")) == sorted(
        f"03-execute-checkpoint-{r.plan.primary.id}.md" for r in (first, second)
    )


def test_checkpoint_exists_refuses_without_touching_anything(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout)
    squatter = layout.bundle_dir / FEATURE / "references" / "03-execute-checkpoint-D-001.md"
    squatter.write_text("someone else's\n", encoding="utf-8", newline="")
    before = _snapshot(layout)
    result = _add(layout, hold="park", phase="execute", checkpoint=_draft(tmp_path))
    assert result.plan.refusal == "checkpoint-exists" and result.application is None
    assert _snapshot(layout) == before


@pytest.mark.parametrize(
    ("page", "kwargs", "refusal"),
    [
        (
            {},
            {"hold": "skip", "phase": "execute", "status": "assumed", "answer": "a", "if_wrong": "w"},
            "hold-status",
        ),
        ({}, {"hold": "skip", "phase": "execute", "affects": ("work/other",)}, "hold-affects"),
        ({}, {"hold": "skip", "phase": "plan"}, "hold-phase-mismatch"),
        ({"phase": "done", "work_status": "resolved"}, {"hold": "skip", "phase": "done"}, "hold-terminal"),
        ({}, {"hold": "park", "phase": "execute"}, "hold-checkpoint"),
        (
            {"phase": None, "work_status": "open"},
            {"hold": "park", "phase": "entry", "draft": True},
            "hold-phase-mismatch",
        ),
        ({}, {"hold": "park", "phase": "execute", "draft": {"phase": "plan"}}, "checkpoint-invalid"),
        ({}, {"hold": "park", "phase": "execute", "draft": {"decision": "D-099"}}, "checkpoint-invalid"),
    ],
)
def test_every_refusal_leaves_ledger_and_references_byte_identical(tmp_path, page, kwargs, refusal) -> None:
    layout = _layout(tmp_path)
    _page(layout, **page)
    draft = kwargs.pop("draft", None)
    if draft is not None:
        kwargs["checkpoint"] = _draft(tmp_path, **(draft if isinstance(draft, dict) else {}))
    before = _snapshot(layout)
    result = _add(layout, **kwargs)
    assert result.plan.refusal == refusal, result.plan.detail
    assert result.application is None
    assert _snapshot(layout) == before


def test_a_skip_with_a_checkpoint_refuses(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout)
    result = _add(layout, hold="skip", phase="execute", checkpoint=_draft(tmp_path))
    assert result.plan.refusal == "hold-checkpoint"


@pytest.mark.parametrize("kwargs", [{"hold": "pause", "phase": "execute"}, {"phase": "execute"}])
def test_caller_errors_raise(tmp_path: Path, kwargs) -> None:
    layout = _layout(tmp_path)
    _page(layout)
    with pytest.raises(ValueError):
        _add(layout, **kwargs)


def test_dry_run_plans_the_same_refusals_and_writes_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout)
    before = _snapshot(layout)
    assert _add(layout, hold="skip", phase="plan", dry_run=True).plan.refusal == "hold-phase-mismatch"
    planned = _add(layout, hold="park", phase="execute", checkpoint=_draft(tmp_path), dry_run=True)
    assert planned.plan.refusal is None and planned.application is None
    assert _snapshot(layout) == before


def test_a_park_writes_neither_file_when_the_journal_refuses(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _page(layout)
    ledger = layout.bundle_dir / FEATURE / "references" / "00-decisions.md"
    original = work.apply_mutation

    def squat_then_apply(layout_, mutation, **kwargs):
        target = next(w for w in mutation.writes if "-checkpoint-" in w.member)
        (layout.bundle_dir / target.member).write_text("raced\n", encoding="utf-8", newline="")
        return original(layout_, mutation, **kwargs)

    monkeypatch.setattr(work, "apply_mutation", squat_then_apply)
    result = _add(layout, hold="park", phase="execute", checkpoint=_draft(tmp_path))
    assert result.application is not None and not result.application.ok
    assert not ledger.exists()
    assert (
        next((layout.bundle_dir / FEATURE / "references").glob("03-execute-checkpoint-*.md")).read_text(
            encoding="utf-8"
        )
        == "raced\n"
    )
