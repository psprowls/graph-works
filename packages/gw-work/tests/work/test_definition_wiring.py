"""gw work next / explain / queue route with the workspace's own path (design §3.5, epic D-010)."""

from __future__ import annotations

import ast
import importlib
import inspect
import io
from pathlib import Path

import gw_kernel
import pytest
from gw_kernel.workspace.errors import WorkspaceError
from gw_work import commands as work
from ruamel.yaml import YAML
from test_run_next import _layout, _spec, _write

FEATURE = "work/feature-a"


CORE = Path(gw_kernel.__file__).parent


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
