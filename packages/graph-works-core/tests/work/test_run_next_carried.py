"""`run_next` carries a `CarriedContext`: usable dispatch only, selected leaf, no routing effect."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.work import carried
from graph_works_core.work import commands as work
from graph_works_core.work.carried import CarriedContext, Slot, SlotFill, SlotInput
from graph_works_core.work.commands import GuidanceRequest
from graph_works_wire.work import next_payload

TODAY = date(2026, 9, 29)
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/feature-a"


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Next")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write(layout, path: str, *, type: str = "Feature", phase: str = "plan") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: open\n"
        f"phase: {phase}\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
        "affects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="\n",
    )


seen: list[SlotInput] = []


def _echo(inp: SlotInput) -> SlotFill:
    seen.append(inp)
    return SlotFill(lines=(f"- {inp.item.path} at {inp.stage}",), data={"n": 1})


def _boom(inp: SlotInput) -> SlotFill:
    raise OSError("git unavailable")


def _fake(monkeypatch: pytest.MonkeyPatch, produce: object) -> None:
    seen.clear()
    monkeypatch.setattr(carried, "SLOTS", (Slot("probe", "Probe", frozenset({"plan"}), produce),))  # type: ignore[arg-type]


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_plan_dispatch_fills_the_registry_for_the_selected_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    _fake(monkeypatch, _echo)
    result = work.run_next(layout, CHILD, dry_run=dry_run)  # no guidance request
    (slot,) = result.carried.slots
    assert slot.name == "probe" and slot.fill.lines == (f"- {CHILD} at plan",)
    assert seen[0].item.path == CHILD and any(i.path == CHILD for i in seen[0].items)
    assert seen[0].layout == layout and seen[0].bundle.root == layout.bundle_dir


def test_fake_producer_lines_reach_the_payload_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    _fake(monkeypatch, _echo)
    payload = next_payload(work.run_next(layout, CHILD), bundle_root=layout.bundle_dir)
    assert payload["carried_context"] == {
        "slots": {"probe": {"title": "Probe", "lines": [f"- {CHILD} at plan"], "data": {"n": 1}, "warnings": []}},
        "warnings": [],
    }


def test_shipped_registry_at_plan_reports_no_spec_baseline(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    result = work.run_next(layout, CHILD)
    assert [(s.name, s.fill) for s in result.carried.slots] == [
        ("epic_brief", SlotFill()),
        (
            "landed_since",
            SlotFill(
                lines=("No spec baseline recorded; landed-since unavailable.",),
                data={"code_baseline": None, "workspace_baseline": None},
            ),
        ),
    ]


def test_design_stage_carries_only_an_empty_epic_brief_without_an_epic(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD, phase="design")
    assert [(s.name, s.fill) for s in work.run_next(layout, CHILD).carried.slots] == [("epic_brief", SlotFill())]


@pytest.mark.parametrize("phase", ["design", "plan", "execute"])
def test_a_child_of_an_epic_carries_the_brief(tmp_path: Path, phase: str) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD, phase=phase)
    result = work.run_next(layout, CHILD)
    brief = next(s for s in result.carried.slots if s.name == "epic_brief")
    assert brief.title == "Epic brief"
    assert brief.fill.lines[0].startswith(f"Epic: [{EPIC}](/{EPIC}.md)")
    assert brief.fill.lines[-1].startswith("Read the full epic design")  # fixture epic has no index table
    payload = next_payload(result, bundle_root=layout.bundle_dir)
    assert payload["carried_context"]["slots"]["epic_brief"]["data"]["epic"] == EPIC


def test_a_top_level_item_carries_an_empty_brief(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-top")
    brief = next(s for s in work.run_next(layout, "work/feature-top").carried.slots if s.name == "epic_brief")
    assert brief.fill == SlotFill()


def test_descend_assembles_for_the_selected_leaf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    _fake(monkeypatch, _echo)
    result = work.run_next(layout, EPIC, descend=True)
    assert result.selected_path == CHILD and seen[0].item.path == CHILD


def test_a_held_item_carries_the_empty_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    ledger = layout.bundle_dir / CHILD / "references" / "00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        f"# Decisions\n\n## D-001 — q\nstatus: open\naffects: [{CHILD}]\n", encoding="utf-8", newline="\n"
    )
    _fake(monkeypatch, _echo)
    result = work.run_next(layout, CHILD)
    assert result.route.dispatch is None and result.carried == CarriedContext() and seen == []


def test_an_epic_waiting_on_children_carries_the_empty_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    _fake(monkeypatch, _echo)
    assert work.run_next(layout, EPIC).carried == CarriedContext() and seen == []


def test_a_dispatch_preflight_carries_the_empty_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-a")
    (layout.root / "dispatch.yaml").write_text(
        "version: 1\npipeline:\n  rules:\n  - match: {}\n    colour: red\n", encoding="utf-8", newline="\n"
    )
    _fake(monkeypatch, _echo)
    result = work.run_next(layout, "work/feature-a")
    assert result.dispatch_preflight is not None and result.carried == CarriedContext() and seen == []


def test_a_raising_producer_is_a_slot_warning_and_leaves_routing_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    baseline = work.run_next(layout, CHILD, guidance=GuidanceRequest(None))
    _fake(monkeypatch, _boom)
    result = work.run_next(layout, CHILD, guidance=GuidanceRequest(None))
    (slot,) = result.carried.slots
    assert slot.fill == SlotFill(warnings=("probe: unavailable: git unavailable",))
    assert result.route == baseline.route
    assert result.dispatch_resolution == baseline.dispatch_resolution
    assert result.dispatch_preflight == baseline.dispatch_preflight
    before = next_payload(baseline, bundle_root=layout.bundle_dir)
    after = next_payload(result, bundle_root=layout.bundle_dir)
    before.pop("carried_context")
    after.pop("carried_context")
    assert before == after


def test_a_selected_path_absent_from_items_carries_nothing(tmp_path: Path) -> None:
    # Unreachable through `run_next`; pins `_with_carried`'s own guard.
    from dataclasses import replace

    from okf_io import load_bundle

    layout = _layout(tmp_path)
    _write(layout, CHILD)
    bare = replace(work.run_next(layout, CHILD), carried=CarriedContext())
    assert bare.route.dispatch is not None and bare.dispatch_resolution is not None
    assert work._with_carried(layout, bare, load_bundle(layout.bundle_dir), ()) is bare
