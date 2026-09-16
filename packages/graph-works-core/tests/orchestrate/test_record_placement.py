"""`run_record_placement`: the observed pair, and nothing else, under the owner lock."""

from __future__ import annotations

import difflib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import TypedDict

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import placement
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.work import commands as work
from graph_works_core.workspace import decision_owner as owners
from graph_works_core.workspace import transactions
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import Bundle, load
from work_tracker_okf.decisions import ledger_ref
from work_tracker_okf.mutation import WorkMutationPlan
from work_tracker_okf.placement import PlacementPlan
from work_tracker_okf.sources import upsert

TODAY = date(2026, 9, 14)
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/feature-a"
NESTED = f"{EPIC}/children/feature-b"
GRANDCHILD = f"{NESTED}/children/bug-c"
SOLO = "work/feature-solo"
BR = "psprowls/feature-a-1a2b3c4d"


class _RecordOverrides(TypedDict, total=False):
    path: str
    root: str
    phase: str
    branch: str


def _abs(*parts: str) -> str:
    """Host-absolute on every platform: `/wt/fork` is not absolute on win32."""
    return str(Path(Path.cwd().anchor, *parts))


WT = _abs("wt", "fork")


def _write(
    layout: WorkspaceLayout, path: str, *, type: str, phase: str | None, work_status: str, owner: str | None = None
) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    phase_line = f"phase: {phase}\n" if phase is not None else ""
    owner_line = f"owner: {owner}\n" if owner is not None else ""
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\n{owner_line}{phase_line}effort: medium\nopened: 2026-08-01\n"
        "updated: 2026-08-01\naffects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="",
    )


def _vault(tmp_path: Path) -> WorkspaceLayout:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Placement")).layout
    _write(layout, EPIC, type="Epic", phase="execute", work_status="in-progress")
    _write(layout, CHILD, type="Feature", phase="execute", work_status="in-progress", owner="pat")
    _write(layout, NESTED, type="Feature", phase="plan", work_status="open")
    _write(layout, GRANDCHILD, type="Bug", phase="execute", work_status="in-progress", owner="pat")
    _write(layout, SOLO, type="Feature", phase="plan", work_status="open")
    regenerated = work.run_regen_indexes(layout, dry_run=False)
    assert regenerated.application is not None and regenerated.application.ok
    return layout


def _record(
    layout: WorkspaceLayout,
    path: str = CHILD,
    *,
    root: str = EPIC,
    phase: str = "execute",
    worktree: str = WT,
    branch: str = BR,
    dry_run: bool = False,
) -> placement.PlacementRecord:
    return placement.run_record_placement(
        layout, path, root=root, phase=phase, worktree=worktree, branch=branch, today=TODAY, dry_run=dry_run
    )


def _snapshot(layout: WorkspaceLayout) -> dict[str, bytes]:
    return {
        p.relative_to(layout.bundle_dir).as_posix(): p.read_bytes() for p in sorted(layout.bundle_dir.rglob("*.md"))
    }


def test_recording_changes_only_the_pair_and_updated(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{CHILD}.md"
    before = page.read_text(encoding="utf-8")
    others = {k: v for k, v in _snapshot(layout).items() if k != f"{CHILD}.md"}

    record = _record(layout)

    assert record.plan.refusal is None
    assert record.written
    after = page.read_text(encoding="utf-8")
    changed = [line for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line[:2] in ("- ", "+ ")]
    assert {line[2:].split(":", 1)[0] for line in changed} == {"worktree", "branch", "updated"}
    assert [line for line in changed if line.startswith("- ")] == ["- updated: 2026-08-01"]
    written = load(page).fm_data()
    assert (written["worktree"], written["branch"]) == (WT, BR)
    assert (written["phase"], written["work_status"], written["owner"]) == ("execute", "in-progress", "pat")
    assert {k: v for k, v in _snapshot(layout).items() if k != f"{CHILD}.md"} == others
    assert not (layout.cache_dir / "active-work.json").exists()


def test_an_identical_replay_writes_nothing(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    assert _record(layout).written
    before = _snapshot(layout)

    replay = _record(layout)

    assert replay.plan.refusal is None and not replay.plan.changed
    assert replay.application is None and not replay.written
    assert _snapshot(layout) == before


def test_a_dry_run_plans_the_change_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _vault(tmp_path)
    before = _snapshot(layout)
    monkeypatch.setattr(placement, "locked_decision_owner", lambda *a, **k: pytest.fail("dry run must not lock"))

    record = _record(layout, dry_run=True)

    assert record.plan.changed and record.application is None
    assert _snapshot(layout) == before


@pytest.mark.parametrize(
    ("kwargs", "refusal"),
    [
        ({"path": NESTED, "root": EPIC, "phase": "plan"}, "read-only-descendant"),
        ({"phase": "finish"}, "phase-mismatch"),
        ({"root": SOLO}, "outside-root"),
        ({"root": "work/nope"}, "unknown-root"),
        ({"path": "work/nope"}, "unknown-path"),
        ({"branch": "refs/heads/" + BR}, "invalid-pair"),
        ({"phase": "done"}, "invalid-phase"),
    ],
)
def test_every_refusal_writes_nothing(tmp_path: Path, kwargs: _RecordOverrides, refusal: str) -> None:
    layout = _vault(tmp_path)
    before = _snapshot(layout)

    record = _record(layout, **kwargs)

    assert record.plan.refusal == refusal
    assert record.application is None
    assert _snapshot(layout) == before


def test_a_terminal_item_refuses(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    _write(layout, CHILD, type="Feature", phase="done", work_status="resolved")
    assert _record(layout, phase="finish").plan.refusal == "terminal"


def test_a_nested_root_and_a_lone_root_record(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    assert _record(layout, NESTED, root=NESTED, phase="plan", worktree=_abs("wt", "nested"), branch="b/nested").written
    assert _record(layout, GRANDCHILD, root=NESTED, phase="execute", worktree=_abs("wt", "gc"), branch="b/gc").written
    assert _record(layout, SOLO, root=SOLO, phase="plan", worktree=_abs("wt", "solo"), branch="b/solo").written


def test_an_open_hold_is_left_untouched_and_still_blocks_advance(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    ledger_ref_epic = ledger_ref(EPIC)
    ledger_path = ledger_ref_epic.path(layout.bundle_dir)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("", encoding="utf-8", newline="")
    epic_page = layout.bundle_dir / f"{EPIC}.md"
    document = load(epic_page)
    upsert(document, ledger_ref_epic, title="Decisions")
    epic_page.write_text(document.serialize(), encoding="utf-8", newline="")
    filed = work.run_decision_add(
        layout, CHILD, question="Stop?", hold="skip", phase="execute", on=TODAY, decided_by="coordinator", dry_run=False
    )
    assert filed.application is not None and filed.application.ok
    ledger = owners.decision_context(layout, CHILD).owner.ledger
    ledger_before = ledger.read_bytes()

    assert _record(layout).written

    assert ledger.read_bytes() == ledger_before
    blocked = stage.run_stage_advance(layout, CHILD, today=TODAY, infer_worktree=False, dry_run=True)
    assert blocked.outcome.plan.refusal == "blocked"


def test_a_stale_preimage_refuses_without_a_partial_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{CHILD}.md"
    before = _snapshot(layout)
    external_edit = page.read_bytes() + b"\nout-of-band\n"
    original = transactions.apply_mutation

    def edited_out_of_band(
        layout: WorkspaceLayout,
        plan: WorkMutationPlan,
        *,
        repo_root: Path | None = None,
        baseline_bundle: Bundle | None = None,
        allowed_new_findings: tuple[tuple[str, str], ...] = (),
    ) -> transactions.MutationApplication:
        page.write_bytes(external_edit)
        return original(
            layout,
            plan,
            repo_root=repo_root,
            baseline_bundle=baseline_bundle,
            allowed_new_findings=allowed_new_findings,
        )

    monkeypatch.setattr(placement, "apply_mutation", edited_out_of_band)
    record = _record(layout)

    assert record.application is not None and not record.application.ok
    assert "changed since planning" in record.application.failures[0]
    assert not record.application.rolled_back
    assert not record.application.written
    assert not record.written
    assert _snapshot(layout) == {**before, f"{CHILD}.md": external_edit}
    text = page.read_text(encoding="utf-8")
    assert "worktree:" not in text and "branch:" not in text


def test_a_failed_postcondition_rolls_back_the_written_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{CHILD}.md"
    before = _snapshot(layout)
    observed: list[tuple[str, str]] = []

    def fail_after_write(*args: object, **kwargs: object) -> tuple[str, ...]:
        written = load(page).fm_data()
        observed.append((written["worktree"], written["branch"]))
        return ("forced placement validation failure",)

    monkeypatch.setattr(transactions, "_validate_postconditions", fail_after_write)

    record = _record(layout)

    assert observed == [(WT, BR)]
    assert record.application is not None
    assert not record.application.ok
    assert record.application.rolled_back
    assert record.application.failures == ("validation failed: forced placement validation failure",)
    assert not record.written
    assert _snapshot(layout) == before
    text = page.read_text(encoding="utf-8")
    assert "worktree:" not in text and "branch:" not in text


def test_the_lock_held_baseline_stays_unmutated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{CHILD}.md"
    before = page.read_bytes()
    original = transactions.apply_mutation

    def check_baseline(
        layout: WorkspaceLayout,
        plan: WorkMutationPlan,
        *,
        repo_root: Path | None = None,
        baseline_bundle: Bundle | None = None,
        allowed_new_findings: tuple[tuple[str, str], ...] = (),
    ) -> transactions.MutationApplication:
        assert baseline_bundle is not None
        document = baseline_bundle.concepts[CHILD]
        assert document.serialize().encode("utf-8") == before
        return original(
            layout,
            plan,
            repo_root=repo_root,
            baseline_bundle=baseline_bundle,
            allowed_new_findings=allowed_new_findings,
        )

    monkeypatch.setattr(placement, "apply_mutation", check_baseline)

    assert _record(layout).written


def test_an_edit_after_the_locked_projection_cannot_become_the_preimage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{CHILD}.md"
    external_edit = page.read_bytes().replace(b"phase: execute\n", b"phase: finish\n")
    before = _snapshot(layout)
    original = placement._mutation

    def edit_before_building_mutation(bundle: Bundle, plan: PlacementPlan) -> WorkMutationPlan:
        page.write_bytes(external_edit)
        return original(bundle, plan)

    monkeypatch.setattr(placement, "_mutation", edit_before_building_mutation)

    record = _record(layout)

    assert not record.written
    assert record.application is not None and not record.application.ok
    assert "changed since planning" in record.application.failures[0]
    assert not record.application.rolled_back
    assert not record.application.written
    assert page.read_bytes() == external_edit
    assert _snapshot(layout) == {**before, f"{CHILD}.md": external_edit}
    text = page.read_text(encoding="utf-8")
    assert "worktree:" not in text and "branch:" not in text


@pytest.mark.parametrize("bad_path", [CHILD, EPIC])
@pytest.mark.parametrize("field", ["phase", "effort"])
@pytest.mark.parametrize("raw", ["17", "[execute]", "{}", "false", '""'])
@pytest.mark.parametrize("identical_pair", [False, True])
@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "live"])
def test_raw_malformed_item_or_root_never_applies_even_on_replay(
    tmp_path: Path, bad_path: str, field: str, raw: str, identical_pair: bool, dry_run: bool
) -> None:
    layout = _vault(tmp_path)
    _write(layout, CHILD, type="TestGap", phase="execute", work_status="open")
    if identical_pair:
        assert _record(layout).written
    page = layout.bundle_dir / f"{bad_path}.md"
    old = "execute" if field == "phase" else "medium"
    page.write_bytes(page.read_bytes().replace(f"{field}: {old}\n".encode(), f"{field}: {raw}\n".encode()))
    before = _snapshot(layout)

    record = _record(layout, dry_run=dry_run)

    assert record.plan.refusal == "invalid-item"
    assert bad_path in record.plan.detail and field in record.plan.detail
    assert record.plan.changes == () and not record.plan.changed
    assert record.application is None and not record.written
    assert _snapshot(layout) == before


@pytest.mark.parametrize(
    ("phase", "effort", "status", "requested"),
    [
        ("17", "medium", "open", "design"),
        ("[execute]", "small", "open", "design"),
        ("execute", "17", "in-progress", "execute"),
    ],
)
@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "live"])
def test_raw_bug_repros_refuse_without_entry_or_write(
    tmp_path: Path, phase: str, effort: str, status: str, requested: str, dry_run: bool
) -> None:
    layout = _vault(tmp_path)
    _write(layout, SOLO, type="Bug", phase=phase, work_status=status)
    page = layout.bundle_dir / f"{SOLO}.md"
    page.write_bytes(page.read_bytes().replace(b"effort: medium\n", f"effort: {effort}\n".encode()))
    before = _snapshot(layout)

    record = _record(layout, SOLO, root=SOLO, phase=requested, dry_run=dry_run)

    assert record.plan.refusal == "invalid-item"
    assert record.plan.current_phase == ("execute" if phase == "execute" else None)
    assert record.plan.changes == ()
    assert record.application is None and not record.written
    assert _snapshot(layout) == before


@pytest.mark.parametrize("bad_path", [CHILD, EPIC])
@pytest.mark.parametrize("field", ["phase", "effort"])
def test_live_record_rejects_raw_invalidity_from_the_refreshed_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_path: str, field: str
) -> None:
    layout = _vault(tmp_path)
    original = owners.locked_decision_owner
    page = layout.bundle_dir / f"{bad_path}.md"
    old = "execute" if field == "phase" else "medium"
    external_edit = page.read_bytes().replace(f"{field}: {old}\n".encode(), f"{field}: 17\n".encode())
    before = _snapshot(layout)

    @contextmanager
    def edit_before_lock(layout: WorkspaceLayout, path: str) -> Iterator[owners.DecisionContext]:
        page.write_bytes(external_edit)
        with original(layout, path) as context:
            yield context

    monkeypatch.setattr(placement, "locked_decision_owner", edit_before_lock)
    record = _record(layout)

    assert record.plan.refusal == "invalid-item"
    assert bad_path in record.plan.detail and field in record.plan.detail
    assert record.plan.changes == ()
    assert record.application is None and not record.written
    assert _snapshot(layout) == {**before, f"{bad_path}.md": external_edit}


@pytest.mark.parametrize("optional", ["", "phase: null\n", "phase:\n"])
@pytest.mark.parametrize(("kind", "requested"), [("Bug", "design"), ("TestGap", "execute")])
@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "live"])
def test_raw_absent_or_null_phase_still_records_routing_entry(
    tmp_path: Path, optional: str, kind: str, requested: str, dry_run: bool
) -> None:
    layout = _vault(tmp_path)
    _write(layout, SOLO, type=kind, phase=None, work_status="open")
    page = layout.bundle_dir / f"{SOLO}.md"
    page.write_bytes(page.read_bytes().replace(b"effort: medium\n", f"effort: small\n{optional}".encode()))
    before = _snapshot(layout)

    record = _record(layout, SOLO, root=SOLO, phase=requested, dry_run=dry_run)

    assert record.plan.refusal is None and record.plan.current_phase == requested
    assert record.plan.changed and record.written is (not dry_run)
    if dry_run:
        assert record.application is None and _snapshot(layout) == before
    else:
        assert load(page).fm_data().get("phase") is None


@pytest.mark.parametrize("optional", ["", "effort: null\n", "effort:\n"])
@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "live"])
def test_raw_absent_or_null_effort_still_records_existing_phase(tmp_path: Path, optional: str, dry_run: bool) -> None:
    layout = _vault(tmp_path)
    for path in (CHILD, EPIC):
        page = layout.bundle_dir / f"{path}.md"
        page.write_bytes(page.read_bytes().replace(b"effort: medium\n", optional.encode()))
    before = _snapshot(layout)

    record = _record(layout, dry_run=dry_run)

    assert record.plan.refusal is None and record.plan.current_phase == "execute"
    assert record.plan.changed and record.written is (not dry_run)
    if dry_run:
        assert record.application is None and _snapshot(layout) == before
    else:
        assert load(layout.bundle_dir / f"{CHILD}.md").fm_data().get("effort") is None
