"""gw work next / explain / queue route with the workspace's own path (design §3.5, epic D-010)."""

from __future__ import annotations

import ast
import importlib
import inspect
import io
from pathlib import Path

import graph_works_core
import pytest
from _transaction_helpers import _init_git
from graph_works_core.orchestrate import placement
from graph_works_core.orchestrate.commands import run_orchestrate
from graph_works_core.orchestrate.stage_advance import run_stage_advance
from graph_works_core.work import commands as work
from graph_works_core.workspace.errors import WorkspaceError
from ruamel.yaml import YAML
from test_run_next import TODAY, _layout, _spec, _write
from work_tracker_okf.placement import ReaderObservation

FEATURE = "work/feature-a"
CORE = Path(graph_works_core.__file__).parent
SKIP_PLAN = [{"name": "features-skip-plan", "match": {"type": "Feature"}, "stages": ["design", "execute", "finish"]}]
ENTER_AT_PLAN = [
    {"name": "features-enter-at-plan", "match": {"type": "Feature"}, "stages": ["plan", "execute", "finish"]}
]


def _takes_definition(target: object) -> bool:
    try:
        return "definition" in inspect.signature(target).parameters  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _bindings(tree: ast.Module) -> tuple[dict[str, object], dict[str, object]]:
    names: dict[str, object] = {}
    modules: dict[str, object] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("work_tracker_okf"):
            module = importlib.import_module(node.module)
            for alias in node.names:
                bound = getattr(module, alias.name, None)
                if inspect.ismodule(bound):
                    modules[alias.asname or alias.name] = bound
                elif bound is not None:
                    names[alias.asname or alias.name] = bound
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("work_tracker_okf") and alias.asname:
                    modules[alias.asname] = importlib.import_module(alias.name)
    return names, modules


def test_every_core_call_to_a_definition_taking_callable_passes_it() -> None:
    missing = []
    for source in sorted(CORE.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names, modules = _bindings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = None
            if isinstance(node.func, ast.Name):
                target = names.get(node.func.id)
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                module = modules.get(node.func.value.id)
                target = getattr(module, node.func.attr, None) if module is not None else None
            if target is None or not _takes_definition(target):
                continue
            if not any(keyword.arg == "definition" for keyword in node.keywords):
                missing.append(f"{source.relative_to(CORE)}:{node.lineno}")
    assert missing == []


def test_the_guard_sees_the_known_entry_points() -> None:
    from work_tracker_okf.compose import advance_and_stamp
    from work_tracker_okf.placement import plan_placement, plan_reader_receipt
    from work_tracker_okf.workflow import route

    assert all(_takes_definition(f) for f in (route, advance_and_stamp, plan_placement, plan_reader_receipt))


def _set_shared_path(layout, rules) -> None:
    shared = layout.root / "dispatch.yaml"
    yaml = YAML(typ="safe")
    data = yaml.load(shared.read_text(encoding="utf-8"))
    data.setdefault("pipeline", {})["path"] = rules
    buffer = io.StringIO()
    yaml.dump(data, buffer)
    shared.write_text(buffer.getvalue(), encoding="utf-8", newline="")


def _break_local(layout) -> None:
    (layout.root / "dispatch.local.yaml").write_text(
        "pipeline:\n  path:\n    - match: {stage: design}\n      stages: [finish]\n", encoding="utf-8", newline=""
    )


def _unphased(layout, path: str) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(page.read_text(encoding="utf-8").replace("phase: design\n", ""), encoding="utf-8", newline="")


def test_next_routes_a_feature_design_completion_to_execute(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    assert work.run_next(layout, FEATURE).route.on_complete.phase == "plan"
    _set_shared_path(layout, SKIP_PLAN)
    assert work.run_next(layout, FEATURE).route.on_complete.phase == "execute"


def test_explain_and_queue_use_the_same_definition(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _unphased(layout, FEATURE)
    _set_shared_path(layout, ENTER_AT_PLAN)
    assert work.run_next(layout, FEATURE).route.dispatch.stage == "plan"
    assert work.run_dispatch_explain(layout, FEATURE).next_result.route.dispatch.stage == "plan"
    (entry,) = [e for e in work.run_work_queue(layout) if e.item.path == FEATURE]
    assert entry.result.route.dispatch.stage == "plan"


def test_malformed_path_blocks_next_even_for_a_satisfied_gate(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    _break_local(layout)
    result = work.run_next(layout, FEATURE)
    assert result.dispatch_preflight is not None
    assert str(layout.root / "dispatch.local.yaml") in result.dispatch_preflight


def test_malformed_path_is_every_queue_entrys_preflight(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _write(layout, "work/bug-b", type="Bug")
    _break_local(layout)
    entries = work.run_work_queue(layout)
    assert {entry.item.path for entry in entries} == {FEATURE, "work/bug-b"}
    assert all(entry.result.dispatch_preflight for entry in entries)


def test_terminal_item_carries_no_preflight_under_a_malformed_file(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE, phase="done")
    _break_local(layout)
    assert work.run_next(layout, FEATURE).dispatch_preflight is None


def test_explain_raises_on_a_malformed_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _break_local(layout)
    with pytest.raises(WorkspaceError, match=r"dispatch\.local\.yaml"):
        work.run_dispatch_explain(layout, FEATURE)


def test_malformed_path_blocks_a_parent_completion_without_dispatch(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    parent = "work/epic-e"
    child = f"{parent}/children/feature-a"
    _write(layout, parent, type="Epic", phase="execute")
    _write(layout, child)
    page = layout.bundle_dir / f"{child}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("work_status: open", "work_status: resolved"),
        encoding="utf-8",
        newline="",
    )
    _break_local(layout)
    result = work.run_next(layout, parent)
    assert result.route.dispatch is None
    assert result.route.on_complete is not None
    assert str(layout.root / "dispatch.local.yaml") in result.dispatch_preflight
    (entry,) = work.run_work_queue(layout)
    assert entry.item.path == parent
    assert entry.result.dispatch_preflight == result.dispatch_preflight


@pytest.mark.parametrize("dry_run", [True, False])
def test_applied_next_routes_with_the_loaded_definition(tmp_path: Path, dry_run: bool) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    _set_shared_path(layout, SKIP_PLAN)
    result = work.run_next(layout, FEATURE, dry_run=dry_run)
    assert result.route.on_complete.phase == "execute"


def test_missing_manifest_dispatch_reference_has_a_recovery_hint(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    yaml = YAML(typ="safe")
    data = yaml.load(layout.manifest_path.read_text(encoding="utf-8"))
    del data["workflow"]["dispatch_rules"]
    buffer = io.StringIO()
    yaml.dump(data, buffer)
    layout.manifest_path.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    result = work.run_next(layout, FEATURE)
    assert "workflow.dispatch_rules" in result.dispatch_preflight
    assert "initialize or recreate dispatch rules" in result.dispatch_preflight
    assert str(layout.manifest_path) in result.dispatch_preflight
    with pytest.raises(WorkspaceError, match=r"workflow\.dispatch_rules"):
        work.run_dispatch_explain(layout, FEATURE)


@pytest.mark.parametrize("dry_run", [True, False])
def test_next_loads_dispatch_configuration_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    _set_shared_path(layout, SKIP_PLAN)
    original = work.load_dispatch_config
    calls = []

    def counted(layout):
        calls.append(layout.root)
        return original(layout)

    monkeypatch.setattr(work, "load_dispatch_config", counted)
    result = work.run_next(layout, FEATURE, dry_run=dry_run)
    assert result.route.on_complete.phase == "execute"
    assert calls == [layout.root]


def test_advance_agrees_with_next_on_a_workspace_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    _set_shared_path(layout, SKIP_PLAN)
    repo = _init_git(layout.root.parent)
    advanced = run_stage_advance(
        layout, FEATURE, today=TODAY, expected_phase="design", cwd=repo, infer_worktree=False, dry_run=True
    )
    phase = next(change.after for change in advanced.outcome.plan.changes if change.key == "phase")
    assert phase == work.run_next(layout, FEATURE).route.on_complete.phase == "execute"


def test_malformed_path_blocks_advance_and_writes_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    repo = _init_git(layout.root.parent)
    _break_local(layout)
    before = {p: p.read_bytes() for p in layout.bundle_dir.rglob("*") if p.is_file()}
    with pytest.raises(WorkspaceError, match=r"dispatch\.local\.yaml"):
        run_stage_advance(
            layout, FEATURE, today=TODAY, expected_phase="design", cwd=repo, infer_worktree=False, dry_run=False
        )
    assert {p: p.read_bytes() for p in layout.bundle_dir.rglob("*") if p.is_file()} == before


def test_advance_without_dispatch_rules_names_the_manifest(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    repo = _init_git(layout.root.parent)
    yaml = YAML(typ="safe")
    data = yaml.load(layout.manifest_path.read_text(encoding="utf-8"))
    del data["workflow"]["dispatch_rules"]
    buffer = io.StringIO()
    yaml.dump(data, buffer)
    layout.manifest_path.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    with pytest.raises(WorkspaceError, match=r"workflow\.dispatch_rules") as raised:
        run_stage_advance(
            layout, FEATURE, today=TODAY, expected_phase="design", cwd=repo, infer_worktree=False, dry_run=True
        )
    assert str(layout.manifest_path) in str(raised.value)


def test_advance_without_the_shared_dispatch_file_refuses(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    repo = _init_git(layout.root.parent)
    (layout.root / "dispatch.yaml").unlink()
    with pytest.raises(WorkspaceError, match=r"dispatch\.yaml"):
        run_stage_advance(
            layout, FEATURE, today=TODAY, expected_phase="design", cwd=repo, infer_worktree=False, dry_run=True
        )


@pytest.mark.parametrize("custom_path", [False, True])
def test_reader_receipt_uses_the_workspace_entry_phase(tmp_path: Path, custom_path: bool) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _unphased(layout, FEATURE)
    if custom_path:
        _set_shared_path(layout, ENTER_AT_PLAN)
    page = layout.bundle_dir / f"{FEATURE}.md"
    before = page.read_bytes()
    record = placement.run_record_reader(
        layout,
        FEATURE,
        root=FEATURE,
        phase="plan",
        observation=ReaderObservation("task_1", "ctx_1", "dispatch-key", "repo", "/observed", "a" * 40),
        dry_run=False,
    )
    assert record.plan.refusal == (None if custom_path else "phase-mismatch")
    assert record.written == custom_path
    assert (placement.read_reader_receipt(layout, FEATURE, "ctx_1") is not None) == custom_path
    assert page.read_bytes() == before


def test_placement_uses_the_workspace_entry_phase(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _unphased(layout, FEATURE)
    _set_shared_path(layout, [{"match": {"type": "Feature"}, "stages": ["execute", "finish"]}])
    record = placement.run_record_placement(
        layout, FEATURE, root=FEATURE, phase="execute", worktree="/observed", branch="feature-a", today=TODAY
    )
    assert record.plan.refusal is None
    assert record.plan.changes


@pytest.mark.parametrize("shell", ["reader", "placement"])
def test_malformed_path_blocks_placement_shells_without_writes(tmp_path: Path, shell: str) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE, phase="plan" if shell == "reader" else "execute")
    _break_local(layout)
    page = layout.bundle_dir / f"{FEATURE}.md"
    before = page.read_bytes()
    with pytest.raises(WorkspaceError, match=r"dispatch\.local\.yaml"):
        if shell == "reader":
            placement.run_record_reader(
                layout,
                FEATURE,
                root=FEATURE,
                phase="plan",
                observation=ReaderObservation("task_1", "ctx_1", "dispatch-key", "repo", "/observed", "a" * 40),
                dry_run=False,
            )
        else:
            placement.run_record_placement(
                layout,
                FEATURE,
                root=FEATURE,
                phase="execute",
                worktree="/observed",
                branch="feature-a",
                today=TODAY,
                dry_run=False,
            )
    assert page.read_bytes() == before
    assert not (layout.cache_dir / "reader-receipts").exists()


def test_orchestrate_plans_the_same_stage_next_dispatches(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _unphased(layout, FEATURE)
    _set_shared_path(layout, ENTER_AT_PLAN)
    _init_git(layout.root.parent)
    planned = run_orchestrate(layout, FEATURE)
    assert [d.phase for d in planned.dispatches if d.slug == FEATURE] == [
        work.run_next(layout, FEATURE).route.dispatch.stage
    ]
    assert work.run_next(layout, FEATURE).route.dispatch.stage == "plan"


def test_malformed_path_refuses_orchestrate(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _break_local(layout)
    with pytest.raises(WorkspaceError, match=r"dispatch\.local\.yaml"):
        run_orchestrate(layout, FEATURE)


@pytest.mark.parametrize("dry_run", [True, False])
def test_unstamped_design_has_spec_advance_matches_next(tmp_path: Path, dry_run: bool) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    _set_shared_path(
        layout, [{"match": {"type": "Feature", "has_spec": True}, "stages": ["design", "execute", "finish"]}]
    )
    repo = _init_git(layout.root.parent)
    page = layout.bundle_dir / f"{FEATURE}.md"
    before = page.read_bytes()
    preview = work.run_next(layout, FEATURE)
    advanced = run_stage_advance(
        layout, FEATURE, today=TODAY, expected_phase="design", cwd=repo, infer_worktree=False, dry_run=dry_run
    )
    assert advanced.outcome.plan.refusal is None
    phase = next(change.after for change in advanced.outcome.plan.changes if change.key == "phase")
    assert phase == preview.route.on_complete.phase == "execute"
    if dry_run:
        assert page.read_bytes() == before
    else:
        assert "phase: execute" in page.read_text(encoding="utf-8")


@pytest.mark.parametrize("dry_run", [True, False])
def test_unstamped_design_has_spec_reader_matches_planned_dispatch(tmp_path: Path, dry_run: bool) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _unphased(layout, FEATURE)
    _spec(layout, FEATURE)
    _set_shared_path(
        layout, [{"match": {"type": "Feature", "has_spec": True}, "stages": ["plan", "execute", "finish"]}]
    )
    _init_git(layout.root.parent)
    before = {p: p.read_bytes() for p in layout.bundle_dir.rglob("*") if p.is_file()}
    assert work.run_next(layout, FEATURE).route.dispatch.stage == "plan"
    assert [d.phase for d in run_orchestrate(layout, FEATURE).dispatches] == ["plan"]
    record = placement.run_record_reader(
        layout,
        FEATURE,
        root=FEATURE,
        phase="plan",
        observation=ReaderObservation("task_1", "ctx_1", "dispatch-key", "repo", "/observed", "a" * 40),
        dry_run=dry_run,
    )
    assert record.plan.refusal is None
    assert record.written == (not dry_run)
    assert {p: p.read_bytes() for p in layout.bundle_dir.rglob("*") if p.is_file()} == before
    if dry_run:
        assert not (layout.cache_dir / "reader-receipts").exists()


def test_unstamped_design_has_spec_placement_matches_next(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _unphased(layout, FEATURE)
    _spec(layout, FEATURE)
    _set_shared_path(layout, [{"match": {"type": "Feature", "has_spec": True}, "stages": ["execute", "finish"]}])
    page = layout.bundle_dir / f"{FEATURE}.md"
    before = page.read_bytes()
    assert work.run_next(layout, FEATURE).route.dispatch.stage == "execute"
    record = placement.run_record_placement(
        layout, FEATURE, root=FEATURE, phase="execute", worktree="/observed", branch="feature-a", today=TODAY
    )
    assert record.plan.refusal is None
    assert record.plan.changes
    assert page.read_bytes() == before
