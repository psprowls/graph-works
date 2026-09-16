"""The pure placement planner (D-006): who may record where a stage runs, and when."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TypedDict

import pytest
from okf_io import load, load_bundle
from work_helpers import load_written_items, make_item, write_item
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import WorkItem, load_items
from work_tracker_okf.placement import PLACEMENT_REFUSALS, PlacementPlan, apply_placement, plan_placement
from work_tracker_okf.workflow import route, state_for

TODAY = date(2026, 9, 14)
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/feature-a"
NESTED = f"{EPIC}/children/feature-b"
GRANDCHILD = f"{NESTED}/children/bug-c"
SOLO = "work/bug-solo"
# Host-absolute on every platform: `/wt/fork` is not absolute on win32.
WT = str(Path(Path.cwd().anchor, "wt", "fork"))
BR = "psprowls/feature-a-1a2b3c4d"


class _ItemOverrides(TypedDict, total=False):
    type: str
    phase: str
    work_status: str
    effort: str


def _vault(**child: object) -> tuple[WorkItem, ...]:
    epic = make_item(
        EPIC,
        type="Epic",
        phase="execute",
        work_status="in-progress",
        effort="medium",
        active_child_paths=(CHILD, NESTED),
    )
    child_defaults: dict[str, object] = {
        "type": "Feature",
        "phase": "execute",
        "work_status": "in-progress",
        "effort": "medium",
        "parent_path": EPIC,
        "ancestor_paths": (EPIC,),
    }
    feature = make_item(CHILD, **{**child_defaults, **child})
    nested = make_item(
        NESTED,
        type="Feature",
        phase="plan",
        work_status="open",
        effort="medium",
        parent_path=EPIC,
        ancestor_paths=(EPIC,),
        active_child_paths=(GRANDCHILD,),
    )
    grandchild = make_item(
        GRANDCHILD,
        type="Bug",
        phase="execute",
        work_status="in-progress",
        effort="medium",
        parent_path=NESTED,
        ancestor_paths=(NESTED, EPIC),
    )
    solo = make_item(SOLO, type="Bug", phase=None, work_status="open", effort="medium")
    return (epic, feature, nested, grandchild, solo)


def _plan(
    items: Sequence[WorkItem],
    path: str = CHILD,
    *,
    root: str = EPIC,
    phase: str = "execute",
    worktree: str = WT,
    branch: str = BR,
) -> PlacementPlan:
    return plan_placement(items, path, root=root, phase=phase, worktree=worktree, branch=branch, today=TODAY)


def test_the_refusal_vocabulary_is_closed() -> None:
    assert {
        "unknown-path",
        "unknown-root",
        "outside-root",
        "invalid-item",
        "invalid-phase",
        "invalid-pair",
        "read-only-descendant",
        "terminal",
        "entry-unprovable",
        "phase-mismatch",
    } == PLACEMENT_REFUSALS


@pytest.mark.parametrize("phase", ["design", "plan", "execute", "finish"])
def test_the_root_may_record_at_every_phase(phase: str) -> None:
    items = tuple(replace(item, phase=phase) if item.path == EPIC else item for item in _vault())
    plan = _plan(items, EPIC, root=EPIC, phase=phase)
    assert plan.refusal is None, plan.detail
    assert plan.changes == (("worktree", WT), ("branch", BR), ("updated", TODAY))


@pytest.mark.parametrize("phase", ["execute", "finish"])
def test_a_descendant_may_record_at_a_code_phase(phase: str) -> None:
    plan = _plan(_vault(phase=phase), phase=phase)
    assert plan.refusal is None, plan.detail
    assert plan.current_phase == phase


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_a_descendant_may_never_record_at_a_read_only_phase(phase: str) -> None:
    plan = _plan(_vault(phase=phase), phase=phase)
    assert plan.refusal == "read-only-descendant"
    assert plan.changes == ()


def test_a_nested_orchestration_root_records_itself_and_its_descendants() -> None:
    items = _vault()
    assert _plan(items, NESTED, root=NESTED, phase="plan").refusal is None
    assert _plan(items, GRANDCHILD, root=NESTED, phase="execute").refusal is None
    assert _plan(items, GRANDCHILD, root=EPIC, phase="execute").refusal is None


def test_a_lone_item_is_its_own_root_and_records_its_entry_phase() -> None:
    plan = _plan(_vault(), SOLO, root=SOLO, phase="design")
    assert plan.refusal is None, plan.detail
    assert plan.current_phase == "design"


@pytest.mark.parametrize(
    ("path", "root", "refusal"),
    [
        ("work/missing", EPIC, "unknown-path"),
        (CHILD, "work/missing-root", "unknown-root"),
        (CHILD, SOLO, "outside-root"),
        (CHILD, NESTED, "outside-root"),
        (EPIC, CHILD, "outside-root"),
    ],
)
def test_the_root_relationship_is_checked_by_physical_hierarchy(path: str, root: str, refusal: str) -> None:
    assert _plan(_vault(), path, root=root).refusal == refusal


def test_a_never_entered_item_must_match_its_routing_entry_phase() -> None:
    assert _plan(_vault(), SOLO, root=SOLO, phase="execute").refusal == "phase-mismatch"


def test_a_direct_entry_test_gap_descendant_records_at_execute() -> None:
    items = _vault(type="TestGap", phase=None, work_status="open", effort="small")
    plan = _plan(items, phase="execute")
    assert plan.refusal is None, plan.detail
    assert plan.current_phase == "execute"


def test_an_unsized_never_entered_test_gap_cannot_prove_its_entry() -> None:
    items = _vault(type="TestGap", phase=None, work_status="open", effort=None)
    assert _plan(items, phase="execute").refusal == "entry-unprovable"


@pytest.mark.parametrize(
    ("overrides", "refusal"),
    [
        ({"work_status": "resolved", "phase": "done"}, "terminal"),
        ({"work_status": "wontfix"}, "terminal"),
        ({"type": ""}, "invalid-item"),
        ({"phase": "review"}, "invalid-item"),
    ],
)
def test_terminal_and_malformed_items_refuse(overrides: dict[str, object], refusal: str) -> None:
    assert _plan(_vault(**overrides)).refusal == refusal


@pytest.mark.parametrize(
    ("path", "phase"),
    [(EPIC, "design"), (EPIC, "plan"), (EPIC, "execute"), (EPIC, "finish"), (CHILD, "execute"), (CHILD, "finish")],
)
@pytest.mark.parametrize("identical_pair", [False, True], ids=["changed-pair", "identical-pair"])
def test_mitigated_items_refuse_placement_without_mutation(
    path: str, phase: str, identical_pair: bool, tmp_path: Path
) -> None:
    previous_pair = (WT, BR) if identical_pair else (str(Path(WT).parent / "old"), "old/branch")
    items = tuple(
        replace(
            item,
            phase=phase,
            work_status="mitigated",
            worktree=previous_pair[0],
            branch=previous_pair[1],
            updated="2026-08-01",
        )
        if item.path == path
        else item
        for item in _vault()
    )
    page = tmp_path / "item.md"
    page.write_text(
        f"---\ntype: {'Epic' if path == EPIC else 'Feature'}\nphase: {phase}\nwork_status: mitigated\n"
        f"worktree: {previous_pair[0]}\nbranch: {previous_pair[1]}\nupdated: 2026-08-01\n---\nBody\n",
        encoding="utf-8",
        newline="",
    )
    document = load(page)
    before = document.serialize()

    plan = _plan(items, path, phase=phase)

    assert plan.refusal == "terminal"
    assert plan.current_phase == phase
    assert path in plan.detail and phase in plan.detail and "mitigated" in plan.detail
    assert plan.before == previous_pair
    assert plan.changes == ()
    assert not plan.changed
    assert next(item for item in items if item.path == path).updated == "2026-08-01"
    with pytest.raises(AssertionError, match="refused placement \\(terminal\\)"):
        apply_placement(document, plan)
    assert document.serialize() == before
    assert page.read_text(encoding="utf-8") == before


def test_a_stage_that_moved_on_refuses_the_stale_phase() -> None:
    plan = _plan(_vault(phase="finish"), phase="execute")
    assert plan.refusal == "phase-mismatch"
    assert plan.current_phase == "finish"
    assert "finish" in plan.detail


@pytest.mark.parametrize("phase", ["done", "review", ""])
def test_an_unrecordable_phase_refuses(phase: str) -> None:
    assert _plan(_vault(), phase=phase).refusal == "invalid-phase"


@pytest.mark.parametrize(
    ("worktree", "branch"),
    [
        ("", BR),
        (WT, ""),
        ("relative/wt", BR),
        (WT, "refs/heads/" + BR),
        (WT, "HEAD"),
        (WT + "\n", BR),
        (" " + WT, BR),
    ],
)
def test_an_invalid_observation_refuses(worktree: str, branch: str) -> None:
    assert _plan(_vault(), worktree=worktree, branch=branch).refusal == "invalid-pair"


def test_the_identical_pair_plans_no_change() -> None:
    plan = _plan(_vault(worktree=WT, branch=BR))
    assert plan.refusal is None
    assert plan.changes == ()
    assert not plan.changed


def test_a_new_verified_pair_replaces_an_existing_one() -> None:
    plan = _plan(_vault(worktree="/wt/old", branch="old/branch"))
    assert plan.before == ("/wt/old", "old/branch")
    assert plan.changes[:2] == (("worktree", WT), ("branch", BR))


def test_updated_is_not_touched_when_it_is_already_today() -> None:
    plan = _plan(_vault(updated=TODAY.isoformat()))
    assert [key for key, _ in plan.changes] == ["worktree", "branch"]


def test_apply_sets_only_the_planned_keys(tmp_path: Path) -> None:
    page = tmp_path / "feature-a.md"
    page.write_text(
        "---\ntype: Feature\ntitle: A\nphase: execute\nwork_status: in-progress\nupdated: 2026-08-01\n---\n\nBody\n",
        encoding="utf-8",
        newline="",
    )
    document = load(page)
    apply_placement(document, _plan(_vault()))
    rendered = document.serialize()
    assert load(page).fm_data().get("worktree") is None  # nothing saved; `document` is in memory
    assert f"worktree: {WT}\n" in rendered
    assert f"branch: {BR}\n" in rendered
    assert "updated: 2026-09-14\n" in rendered
    assert "phase: execute\n" in rendered and "work_status: in-progress\n" in rendered
    assert rendered.endswith("\nBody\n")


def test_apply_refuses_a_refused_plan(tmp_path: Path) -> None:
    page = tmp_path / "p.md"
    page.write_text("---\ntype: Feature\n---\n", encoding="utf-8", newline="")
    with pytest.raises(AssertionError):
        apply_placement(load(page), _plan(_vault(phase="plan"), phase="plan"))


@pytest.mark.parametrize("path", [CHILD, EPIC])
@pytest.mark.parametrize(
    "overrides",
    [
        {"type": ""},
        {"phase": "review"},
        {"work_status": ""},
        {"work_status": "broken"},
        {"effort": ""},
        {"effort": "huge"},
    ],
)
def test_invalid_item_or_root_refuses_before_recording(path: str, overrides: _ItemOverrides) -> None:
    items = tuple(replace(item, **overrides) if item.path == path else item for item in _vault())
    plan = _plan(items)
    assert plan.refusal == "invalid-item"
    assert path in plan.detail
    assert plan.changes == ()


def test_invalid_never_entered_item_is_not_merely_an_unprovable_entry() -> None:
    assert _plan(_vault(phase=None, effort="huge")).refusal == "invalid-item"


def test_missing_effort_does_not_invalidate_a_recorded_phase_or_root() -> None:
    items = tuple(replace(item, effort=None) for item in _vault())
    assert _plan(items).refusal is None


def test_non_open_never_entered_item_cannot_prove_entry() -> None:
    assert _plan(_vault(phase=None, work_status="accepted")).refusal == "entry-unprovable"


@pytest.mark.parametrize(("phase", "refusal"), [(None, "entry-unprovable"), ("execute", None)])
def test_dependencies_gate_entry_proof_but_not_recorded_phase(phase: str | None, refusal: str | None) -> None:
    items = _vault(
        type="TestGap",
        phase=phase,
        work_status="open",
        effort="small",
        dependency_edges=(DependencyEdge(SOLO, "execute", "resolved"),),
    )
    plan = _plan(items)
    assert plan.refusal == refusal
    assert plan.changed is (refusal is None)


def test_existing_hold_still_blocks_routing_but_not_same_phase_placement() -> None:
    items = _vault()
    hold = HoldFact(CHILD, "D-001", "park", "execute")
    state = state_for(items, CHILD, hold=hold)
    assert state is not None
    assert route(state).dispatch is None
    assert _plan(items).refusal is None
    assert route(state).dispatch is None


@pytest.mark.parametrize(("worktree", "branch", "key"), [(WT, "old", "branch"), ("/wt/old", BR, "worktree")])
def test_only_the_changed_half_of_the_pair_is_planned(worktree: str, branch: str, key: str) -> None:
    plan = _plan(_vault(worktree=worktree, branch=branch))
    assert [name for name, _ in plan.changes] == [key, "updated"]


def test_applying_an_identical_pair_preserves_every_byte(tmp_path: Path) -> None:
    page = tmp_path / "p.md"
    page.write_text("---\ntype: Feature\nupdated: 2026-08-01\n---\nBody\n", encoding="utf-8", newline="")
    document = load(page)
    before = document.serialize()
    apply_placement(document, _plan(_vault(worktree=WT, branch=BR)))
    assert document.serialize() == before


@pytest.mark.parametrize("bad_path", [CHILD, EPIC])
@pytest.mark.parametrize("field", ["phase", "effort"])
@pytest.mark.parametrize("raw", ["17", "[execute]", "{}", "false", '""'])
@pytest.mark.parametrize("identical_pair", [False, True])
def test_raw_malformed_item_or_root_refuses_before_replay(
    tmp_path: Path, bad_path: str, field: str, raw: str, identical_pair: bool
) -> None:
    for path, kind in ((EPIC, "Epic"), (CHILD, "TestGap")):
        fields = {"phase": "execute", "effort": "small"}
        if path == bad_path:
            fields[field] = raw
        pair = f"worktree: {WT}\nbranch: {BR}\n" if identical_pair else ""
        write_item(
            tmp_path,
            path,
            f"type: {kind}\nwork_status: open\nphase: {fields['phase']}\neffort: {fields['effort']}\n{pair}",
        )
    bundle = load_bundle(tmp_path)
    before = {path: doc.serialize() for path, doc in bundle.concepts.items()}

    plan = _plan(load_items(bundle))

    assert plan.refusal == "invalid-item"
    assert bad_path in plan.detail and field in plan.detail
    assert plan.changes == () and not plan.changed
    with pytest.raises(AssertionError, match="refused placement"):
        apply_placement(bundle.concepts[CHILD], plan)
    assert {path: doc.serialize() for path, doc in bundle.concepts.items()} == before
    assert {path: (tmp_path / f"{path}.md").read_bytes() for path in before} == {
        path: text.encode("utf-8") for path, text in before.items()
    }


@pytest.mark.parametrize(
    ("phase", "effort", "status", "requested"),
    [
        ("17", "medium", "open", "design"),
        ("[execute]", "small", "open", "design"),
        ("execute", "17", "in-progress", "execute"),
    ],
)
def test_raw_bug_repros_never_gain_placement_eligibility(
    tmp_path: Path, phase: str, effort: str, status: str, requested: str
) -> None:
    page = write_item(tmp_path, SOLO, f"type: Bug\nwork_status: {status}\nphase: {phase}\neffort: {effort}\n")
    before = page.read_bytes()
    plan = _plan(load_written_items(tmp_path), SOLO, root=SOLO, phase=requested)
    assert plan.refusal == "invalid-item"
    assert plan.current_phase == ("execute" if phase == "execute" else None)
    assert plan.changes == ()
    assert page.read_bytes() == before


@pytest.mark.parametrize("optional", ["", "phase: null\n", "phase:\n"])
@pytest.mark.parametrize(("kind", "requested"), [("Bug", "design"), ("TestGap", "execute")])
def test_raw_absent_or_null_phase_keeps_routing_entry(tmp_path: Path, optional: str, kind: str, requested: str) -> None:
    write_item(tmp_path, SOLO, f"type: {kind}\nwork_status: open\neffort: small\n{optional}")
    plan = _plan(load_written_items(tmp_path), SOLO, root=SOLO, phase=requested)
    assert plan.refusal is None and plan.current_phase == requested
    assert plan.changed


@pytest.mark.parametrize("optional", ["", "effort: null\n", "effort:\n"])
def test_raw_absent_or_null_effort_keeps_recorded_phase_on_item_and_root(tmp_path: Path, optional: str) -> None:
    for path, kind in ((EPIC, "Epic"), (CHILD, "Feature")):
        write_item(tmp_path, path, f"type: {kind}\nwork_status: in-progress\nphase: execute\n{optional}")
    plan = _plan(load_written_items(tmp_path))
    assert plan.refusal is None and plan.current_phase == "execute"
    assert plan.changed
