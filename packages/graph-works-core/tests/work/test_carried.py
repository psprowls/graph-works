"""`carried.py`: the closed slot registry and its phase-filtered, fail-soft assembly."""

from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast, get_args

import pytest
from _transaction_helpers import _git, _init_git
from graph_works_core.work import carried
from graph_works_core.work import commands as work
from graph_works_core.work.carried import (
    CarriedContext,
    FilledSlot,
    Slot,
    SlotFill,
    SlotInput,
    _finish_obligations,
    assemble_carried,
)
from graph_works_core.workspace import provenance
from graph_works_core.workspace.landed import stale_spec_for
from okf_io import load, load_bundle
from test_dispatch_reporting import _init_git_repo
from test_run_next_carried import CHILD, _layout, _write
from work_tracker_okf.items import SpecBaseline, load_items
from work_tracker_okf.obligations import Obligation
from work_tracker_okf.workflow import Stage


def _inp(stage: str) -> SlotInput:
    # Producers under test ignore the workspace; a stub keeps these unit tests off disk.
    stub = cast("object", SimpleNamespace())
    return SlotInput(layout=stub, bundle=stub, items=(), item=stub, stage=stage)  # type: ignore[arg-type]


def _fill(*lines: str) -> object:
    return lambda inp: SlotFill(lines=lines, data={"stage": inp.stage})


def _raise(exc: BaseException) -> object:
    def produce(inp: SlotInput) -> SlotFill:
        raise exc

    return produce


def test_only_slots_for_the_stage_are_included_in_registry_order() -> None:
    slots = (
        Slot("a", "A", frozenset({"plan"}), _fill("- a")),  # type: ignore[arg-type]
        Slot("b", "B", frozenset({"finish"}), _fill("- b")),  # type: ignore[arg-type]
        Slot("c", "C", frozenset({"plan", "finish"}), _fill("- c")),  # type: ignore[arg-type]
    )
    frame = assemble_carried(_inp("plan"), slots=slots)
    assert [s.name for s in frame.slots] == ["a", "c"]
    assert frame.slots[0] == FilledSlot("a", "A", SlotFill(lines=("- a",), data={"stage": "plan"}))
    assert frame.warnings == ()


def test_no_applicable_slot_is_the_empty_frame() -> None:
    other = (Slot("x", "X", frozenset({"plan"}), _fill("- x")),)  # type: ignore[arg-type]
    assert assemble_carried(_inp("design"), slots=other) == CarriedContext()
    assert assemble_carried(_inp("execute"), slots=other) == CarriedContext()


@pytest.mark.parametrize("exc", [OSError("disk gone"), ValueError("bad sha")])
def test_os_and_value_errors_degrade_to_a_slot_warning(exc: Exception) -> None:
    slots = (
        Slot("broken", "Broken", frozenset({"plan"}), _raise(exc)),  # type: ignore[arg-type]
        Slot("fine", "Fine", frozenset({"plan"}), _fill("- ok")),  # type: ignore[arg-type]
    )
    frame = assemble_carried(_inp("plan"), slots=slots)
    broken, fine = frame.slots
    assert broken == FilledSlot("broken", "Broken", SlotFill(warnings=(f"broken: unavailable: {exc}",)))
    assert broken.fill.lines == () and dict(broken.fill.data) == {}
    assert fine.fill.lines == ("- ok",)


def test_any_other_exception_propagates() -> None:
    slots = (Slot("bug", "Bug", frozenset({"plan"}), _raise(KeyError("x"))),)  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        assemble_carried(_inp("plan"), slots=slots)


def test_registry_invariants() -> None:
    names = [slot.name for slot in carried.SLOTS]
    assert names == ["epic_brief", "execute_return", "landed_since", "finish_obligations"]
    assert len(set(names)) == len(names)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in names)
    stages = set(get_args(Stage))
    assert all(slot.phases and slot.phases <= stages for slot in carried.SLOTS)
    assert {slot.name: slot.phases for slot in carried.SLOTS} == {
        "epic_brief": frozenset({"design", "plan", "execute"}),
        "execute_return": frozenset({"execute"}),
        "landed_since": frozenset({"plan"}),
        "finish_obligations": frozenset({"finish"}),
    }
    assert [slot.title for slot in carried.SLOTS] == [
        "Epic brief",
        "Returned scope",
        "Landed since your design",
        "Finish obligations",
    ]


RETURN_ID = "ret-20261005-3f9a1c2b"
RETURN_COVERAGE = f"/{CHILD}/references/03-execute-coverage.md"
RETURN_PLAN = f"/{CHILD}/references/02-plan.md"
RETURN_DESIGN = f"/{CHILD}/references/01-design.md"
RETURN_SHA = "a" * 64


def _execute_return_input(tmp_path: Path, *, record: object = "active", design: bool = True) -> SlotInput:
    layout = _layout(tmp_path)
    _write(layout, CHILD, phase="execute")
    doc = load(layout.bundle_dir / f"{CHILD}.md")
    if record in ("active", "completed"):
        record = {
            "id": RETURN_ID,
            "recorded": "2026-10-05",
            "state": record,
            "coverage": RETURN_COVERAGE,
            "plan": RETURN_PLAN,
            "plan_sha256": RETURN_SHA,
            "scope": [
                {"id": "R1", "text": "Implement Task 4 in the canonical plan"},
                {"id": "R2", "text": "Verify the new acceptance"},
            ],
        }
    if record is not None:
        doc.set("execute_return", record)
    if design:
        doc.set("sources", [{"id": "design", "resource": RETURN_DESIGN}])
    doc.save()
    bundle = load_bundle(layout.bundle_dir)
    (item,) = load_items(bundle)
    return SlotInput(layout, bundle, (item,), item, "execute")


def _execute_return_fill(inp: SlotInput) -> SlotFill:
    frame = assemble_carried(inp)
    slot = next(slot for slot in frame.slots if slot.name == "execute_return")
    assert slot.title == "Returned scope"
    return slot.fill


def test_execute_return_carries_scope_canonical_plan_and_destination(tmp_path: Path) -> None:
    inp = _execute_return_input(tmp_path)
    fill = _execute_return_fill(inp)
    plan_path = inp.layout.bundle_dir / RETURN_PLAN.lstrip("/")
    coverage_path = inp.layout.bundle_dir / RETURN_COVERAGE.lstrip("/")
    assert fill.lines == (
        f"Return `{RETURN_ID}` sent this item back from finish. Execute is not done until every row below is reported.",
        "- [ ] R1: Implement Task 4 in the canonical plan",
        "- [ ] R2: Verify the new acceptance",
        f"Canonical plan: {RETURN_PLAN} (sha256 `aaaaaaaaaaaa`), read it in the main workspace "
        f"({plan_path}) even if your content root's copy is older.",
        f"Design: {RETURN_DESIGN}",
        f"Report in: {coverage_path} under `## Returned scope {RETURN_ID}`; "
        "keep the row ids and set `Report state: reported`.",
    )
    assert fill.data == {
        "return_id": RETURN_ID,
        "scope": [
            {"id": "R1", "text": "Implement Task 4 in the canonical plan"},
            {"id": "R2", "text": "Verify the new acceptance"},
        ],
        "plan": RETURN_PLAN,
        "plan_sha256": RETURN_SHA,
        "design": RETURN_DESIGN,
        "coverage": {"resource": RETURN_COVERAGE, "path": str(coverage_path), "worktree": None, "branch": None},
    }
    assert fill.warnings == ()


@pytest.fixture
def execute_return_checkout(tmp_path: Path) -> tuple[SlotInput, Path]:
    inp = _execute_return_input(tmp_path)
    plan_path = inp.layout.bundle_dir / RETURN_PLAN.lstrip("/")
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Older plan\n", encoding="utf-8", newline="\n")
    _init_git(inp.layout.root)
    content = tmp_path / "content"
    _git(inp.layout.root, "worktree", "add", "-b", "returned-execute", str(content))
    plan_path.write_text("# New canonical plan\n", encoding="utf-8", newline="\n")
    doc = load(inp.layout.bundle_dir / f"{CHILD}.md")
    doc.set("repo_stamps", {"_workspace": {"worktree": str(content), "branch": "returned-execute"}})
    doc.save()
    _git(inp.layout.root, "add", ".")
    _git(inp.layout.root, "commit", "-m", "canonical plan and placement")
    bundle = load_bundle(inp.layout.bundle_dir)
    (item,) = load_items(bundle)
    return replace(inp, bundle=bundle, items=(item,), item=item), content


def test_execute_return_uses_verified_checkout_for_report_and_main_bundle_for_plan(execute_return_checkout) -> None:
    inp, content = execute_return_checkout
    before = tuple(_git(root, "rev-parse", "HEAD") for root in (inp.layout.root, content))
    fill = _execute_return_fill(inp)
    coverage_path = content / "okf" / RETURN_COVERAGE.lstrip("/")
    assert fill.data["coverage"] == {
        "resource": RETURN_COVERAGE,
        "path": str(coverage_path),
        "worktree": str(content),
        "branch": "returned-execute",
    }
    assert fill.lines[3].find(str(inp.layout.bundle_dir / RETURN_PLAN.lstrip("/"))) > 0
    assert str(content) not in fill.lines[3]
    assert fill.lines[-1].startswith(f"Report in: {coverage_path} under `## Returned scope {RETURN_ID}`")
    assert fill.warnings == ()
    assert tuple(_git(root, "rev-parse", "HEAD") for root in (inp.layout.root, content)) == before
    assert all(not _git(root, "status", "--porcelain").strip() for root in (inp.layout.root, content))


@pytest.mark.parametrize("damage", ["dirty", "missing"])
def test_execute_return_keeps_scope_when_destination_is_unverified(execute_return_checkout, damage: str) -> None:
    inp, content = execute_return_checkout
    if damage == "dirty":
        (content / "untracked").write_text("dirty\n", encoding="utf-8", newline="\n")
    else:
        doc = load(inp.layout.bundle_dir / f"{CHILD}.md")
        doc.set("repo_stamps", {"_workspace": {"worktree": str(content / "missing"), "branch": "returned-execute"}})
        doc.save()
        bundle = load_bundle(inp.layout.bundle_dir)
        (item,) = load_items(bundle)
        inp = replace(inp, bundle=bundle, items=(item,), item=item)
    fill = _execute_return_fill(inp)
    assert fill.lines[1:3] == (
        "- [ ] R1: Implement Task 4 in the canonical plan",
        "- [ ] R2: Verify the new acceptance",
    )
    assert fill.data["return_id"] == RETURN_ID
    assert fill.data["coverage"] == {"resource": RETURN_COVERAGE, "path": None, "worktree": None, "branch": None}
    assert fill.warnings and "execute_return" in fill.warnings[0]
    assert not any(line.startswith("Report in:") for line in fill.lines)


@pytest.mark.parametrize("record", [None, "completed"])
def test_execute_return_absent_or_completed_is_empty(tmp_path: Path, record: object) -> None:
    assert _execute_return_fill(_execute_return_input(tmp_path, record=record)) == SlotFill()


def test_execute_return_malformed_metadata_warns_before_execution(tmp_path: Path) -> None:
    fill = _execute_return_fill(_execute_return_input(tmp_path, record={"state": "active"}))
    assert fill == SlotFill(warnings=("execute_return: malformed; repair before executing",))


@pytest.mark.parametrize("stage", ["design", "plan", "finish"])
def test_execute_return_slot_is_absent_outside_execute(tmp_path: Path, stage: str) -> None:
    frame = assemble_carried(replace(_execute_return_input(tmp_path), stage=stage))
    assert "execute_return" not in [slot.name for slot in frame.slots]


def test_execute_return_without_plan_or_design_still_carries_report_instructions(tmp_path: Path) -> None:
    inp = _execute_return_input(tmp_path, design=False)
    doc = load(inp.layout.bundle_dir / f"{CHILD}.md")
    record = doc.fm_data()["execute_return"]
    del record["plan"]
    del record["plan_sha256"]
    doc.set("execute_return", record)
    doc.save()
    bundle = load_bundle(inp.layout.bundle_dir)
    (item,) = load_items(bundle)
    fill = _execute_return_fill(replace(inp, bundle=bundle, items=(item,), item=item))
    assert fill.data["plan"] is None and fill.data["plan_sha256"] is None and fill.data["design"] is None
    assert not any(line.startswith(("Canonical plan:", "Design:")) for line in fill.lines)
    assert fill.lines[-1].startswith("Report in:")


@pytest.mark.parametrize("field", ["coverage", "plan"])
def test_execute_return_keeps_scope_when_resource_path_escapes_bundle(tmp_path: Path, field: str) -> None:
    inp = _execute_return_input(tmp_path)
    doc = load(inp.layout.bundle_dir / f"{CHILD}.md")
    record = doc.fm_data()["execute_return"]
    record[field] = "/../outside.md"
    doc.set("execute_return", record)
    doc.save()
    bundle = load_bundle(inp.layout.bundle_dir)
    (item,) = load_items(bundle)
    fill = _execute_return_fill(replace(inp, bundle=bundle, items=(item,), item=item))
    assert "- [ ] R1: Implement Task 4 in the canonical plan" in fill.lines
    assert fill.warnings and "member outside bundle" in fill.warnings[0]
    unsafe_line = "Report in:" if field == "coverage" else "Canonical plan:"
    assert not any(line.startswith(unsafe_line) for line in fill.lines)


def _finish_input(tmp_path: Path) -> SlotInput:
    layout = _layout(tmp_path)
    _write(layout, CHILD, phase="finish")
    bundle = load_bundle(layout.bundle_dir)
    (item,) = load_items(bundle)
    return SlotInput(layout=layout, bundle=bundle, items=(item,), item=item, stage="finish")


def test_finish_obligations_render_one_line_each_in_stored_order(tmp_path: Path) -> None:
    inp = _finish_input(tmp_path)
    item = replace(
        inp.item,
        finish_obligations=(
            Obligation("Tag the release", "deferred", "2026-09-20"),
            Obligation("Live Orca test not run", "coverage", "2026-09-21"),
        ),
    )
    fill = _finish_obligations(replace(inp, item=item))
    assert fill.lines == (
        "- [deferred] Tag the release (recorded 2026-09-20)",
        "- [coverage] Live Orca test not run (recorded 2026-09-21)",
    )
    assert fill.data == {
        "obligations": [
            {"text": "Tag the release", "origin": "deferred", "recorded": "2026-09-20"},
            {"text": "Live Orca test not run", "origin": "coverage", "recorded": "2026-09-21"},
        ]
    }
    assert fill.warnings == ()


def test_finish_obligations_empty_item_is_an_empty_fill(tmp_path: Path) -> None:
    assert _finish_obligations(_finish_input(tmp_path)) == SlotFill()


def test_finish_obligations_malformed_entries_add_a_slot_warning(tmp_path: Path) -> None:
    inp = _finish_input(tmp_path)
    item = replace(inp.item, invalid_optional_fields=("finish_obligations",))
    fill = _finish_obligations(replace(inp, item=item))
    assert fill.warnings == ("finish_obligations: malformed entries ignored",)


def test_run_next_finish_obligations_slot_contains_recorded_entry(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD, phase="finish")
    page = layout.bundle_dir / f"{CHILD}.md"
    original = page.read_text(encoding="utf-8")
    page.write_text(
        original.replace(
            "affects:\n- packages/a\n",
            "affects:\n- packages/a\nfinish_obligations:\n"
            "- text: Tag the release\n  origin: deferred\n  recorded: 2026-09-20\n",
        ),
        encoding="utf-8",
        newline="\n",
    )
    _init_git_repo(layout)
    result = work.run_next(layout, CHILD)
    assert result.route.dispatch is not None, result
    assert result.dispatch_resolution is not None, result
    assert result.dispatch_preflight is None, result
    assert result.carried.slots == (
        FilledSlot(
            "finish_obligations",
            "Finish obligations",
            SlotFill(
                lines=("- [deferred] Tag the release (recorded 2026-09-20)",),
                data={"obligations": [{"text": "Tag the release", "origin": "deferred", "recorded": "2026-09-20"}]},
            ),
        ),
    )


def test_shipped_producer_docstrings_name_their_owning_child() -> None:
    assert "feature-spec-baseline-and-sibling-context" in (carried._landed_since.__doc__ or "")
    assert "feature-finish-obligations-and-caveats" in (carried._finish_obligations.__doc__ or "")


def test_slot_fill_default_data_is_an_empty_read_only_mapping() -> None:
    fill = SlotFill()
    assert dict(fill.data) == {}
    with pytest.raises(TypeError):
        fill.data["x"] = 1  # type: ignore[index]


def test_module_is_read_only_and_imports_no_interface() -> None:
    source = Path(carried.__file__).read_text(encoding="utf-8")
    for forbidden in ("write_text", "write_bytes", "graph_works_cli", "graph_works_wire", "graph_works_serve"):
        assert forbidden not in source


SIBLING = "work/epic-a/children/feature-sibling"


def _landed_git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _landed_commit(repo: Path, subject: str) -> str:
    page = repo / "packages/a" / f"{subject}.py"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(subject + "\n", encoding="utf-8", newline="\n")
    _landed_git(repo, "add", "-A")
    _landed_git(repo, "commit", "-qm", subject)
    return _landed_git(repo, "rev-parse", "HEAD")


@pytest.fixture
def landed_input(tmp_path: Path):
    layout = _layout(tmp_path)
    _write(layout, CHILD, phase="plan")
    _init_git_repo(layout)
    code = _landed_git(layout.root, "rev-parse", "HEAD")
    bundle = load_bundle(layout.bundle_dir)
    (item,) = load_items(bundle)
    item = replace(item, spec_baseline=SpecBaseline(code=code, workspace=code))
    sibling = replace(item, path=SIBLING, work_status="resolved", resolved_in=code)
    return SlotInput(layout, bundle, (item, sibling), item, "plan"), code


def test_no_baseline_is_one_line(landed_input, monkeypatch) -> None:
    inp, _ = landed_input

    def boom(*args, **kwargs):
        raise AssertionError("unexpected Git call")

    monkeypatch.setattr(provenance, "run_git", boom)
    monkeypatch.setattr(provenance, "probe_git", boom)
    fill = carried._landed_since(replace(inp, item=replace(inp.item, spec_baseline=None)))
    assert fill.lines == ("No spec baseline recorded; landed-since unavailable.",)
    assert fill.data["code_baseline"] is None


def test_no_entries(landed_input) -> None:
    inp, code = landed_input
    fill = carried._landed_since(inp)
    assert fill.lines[0] == f"Spec baseline: code `{code[:12]}`, workspace `{code[:12]}`."
    assert "No sibling has landed since the baseline." in fill.lines


def test_entries_commits_and_diff_command(landed_input) -> None:
    inp, code = landed_input
    c1 = _landed_commit(inp.layout.root.parent, "second")
    c2 = _landed_commit(inp.layout.root.parent, "third")
    sibling = replace(inp.items[1], resolved_in=c1)
    fill = carried._landed_since(replace(inp, items=(inp.item, sibling)))
    assert f"- `{SIBLING}` resolved in `{c1[:12]}` — affects overlap: yes" in fill.lines
    assert "Commits since baseline touching affects: 2" in fill.lines
    assert fill.lines[-2] == f"Diff: `git diff {code}..HEAD -- packages/a`"
    assert fill.data["diff_command"] == f"git diff {code}..HEAD -- packages/a"
    assert fill.data["siblings"] == [{"path": SIBLING, "resolved_in": c1, "overlaps": True}]
    assert fill.data["commits"] == [{"sha": c2, "subject": "third"}, {"sha": c1, "subject": "second"}]


def test_commit_list_caps_at_twenty(landed_input) -> None:
    inp, _ = landed_input
    for n in range(23):
        sha = _landed_commit(inp.layout.root.parent, f"change{n}")
    fill = carried._landed_since(replace(inp, items=(inp.item, replace(inp.items[1], resolved_in=sha))))
    assert sum(line.startswith("- `") and "resolved in" not in line for line in fill.lines) == 20
    assert "- … and 3 more" in fill.lines
    assert len(fill.data["commits"]) == 23


def test_workspace_drift_is_reported_and_never_stale(landed_input) -> None:
    inp, _ = landed_input
    _landed_commit(inp.layout.root.parent, "workspace1")
    _landed_commit(inp.layout.root.parent, "workspace2")
    fill = carried._landed_since(inp)
    assert any(line.startswith("Workspace: 2 commits since") for line in fill.lines)
    assert stale_spec_for(inp.layout, inp.items, inp.item) == ()


def test_landed_since_warnings_become_slot_warnings(landed_input) -> None:
    inp, _ = landed_input
    sibling = replace(inp.items[1], resolved_in="https://github.com/o/r/pull/1")
    fill = carried._landed_since(replace(inp, items=(inp.item, sibling)))
    assert any("pull/1" in w for w in fill.warnings)
