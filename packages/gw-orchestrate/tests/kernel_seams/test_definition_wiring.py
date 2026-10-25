"""gw work next / explain / queue route with the workspace's own path (design §3.5, epic D-010)."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
from gw_kernel import apply_init, plan_init
from gw_kernel.testing.transactions import init_git as _init_git
from gw_kernel.workspace.errors import WorkspaceError
from gw_orchestrate.commands import run_orchestrate
from gw_orchestrate.stage_advance import run_stage_advance
from gw_work import commands as work
from ruamel.yaml import YAML

TODAY = date(2026, 8, 23)


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Next")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write(layout, path: str, *, type: str = "Feature", phase: str = "design") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: open\n"
        f"phase: {phase}\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\naffects:\n"
        f"- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n| Action | Done when | Rationale |\n"
        f"| --- | --- | --- |\n",
        encoding="utf-8",
    )


def _spec(layout, path: str) -> Path:
    artifact = layout.bundle_dir / f"{path}/references/01-design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Design\n", encoding="utf-8")
    return artifact


FEATURE = "work/feature-a"


SKIP_PLAN = [{"name": "features-skip-plan", "match": {"type": "Feature"}, "stages": ["design", "execute", "finish"]}]


ENTER_AT_PLAN = [
    {"name": "features-enter-at-plan", "match": {"type": "Feature"}, "stages": ["plan", "execute", "finish"]}
]


def _set_shared_path(layout, rules) -> None:
    shared = layout.root / "workflow.yaml"
    yaml = YAML(typ="safe")
    data = yaml.load(shared.read_text(encoding="utf-8"))
    data.setdefault("pipeline", {})["path"] = rules
    buffer = io.StringIO()
    yaml.dump(data, buffer)
    shared.write_text(buffer.getvalue(), encoding="utf-8", newline="")


def _break_local(layout) -> None:
    (layout.root / "workflow.local.yaml").write_text(
        "pipeline:\n  path:\n    - match: {stage: design}\n      stages: [finish]\n", encoding="utf-8", newline=""
    )


def _unphased(layout, path: str) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(page.read_text(encoding="utf-8").replace("phase: design\n", ""), encoding="utf-8", newline="")


def test_advance_agrees_with_next_on_a_workspace_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    _set_shared_path(layout, SKIP_PLAN)
    repo = _init_git(layout.root.parent)
    advanced = run_stage_advance(layout, FEATURE, today=TODAY, expected_phase="design", cwd=repo, dry_run=True)
    phase = next(change.after for change in advanced.outcome.plan.changes if change.key == "phase")
    assert phase == work.run_next(layout, FEATURE).route.on_complete.phase == "execute"


def test_malformed_path_blocks_advance_and_writes_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    repo = _init_git(layout.root.parent)
    _break_local(layout)
    before = {p: p.read_bytes() for p in layout.bundle_dir.rglob("*") if p.is_file()}
    with pytest.raises(WorkspaceError, match=r"workflow\.local\.yaml"):
        run_stage_advance(layout, FEATURE, today=TODAY, expected_phase="design", cwd=repo, dry_run=False)
    assert {p: p.read_bytes() for p in layout.bundle_dir.rglob("*") if p.is_file()} == before


def test_advance_without_dispatch_rules_names_the_manifest(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    repo = _init_git(layout.root.parent)
    yaml = YAML(typ="safe")
    data = yaml.load(layout.manifest_path.read_text(encoding="utf-8"))
    del data["workflow_config"]
    buffer = io.StringIO()
    yaml.dump(data, buffer)
    layout.manifest_path.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    with pytest.raises(WorkspaceError, match=r"workflow_config") as raised:
        run_stage_advance(layout, FEATURE, today=TODAY, expected_phase="design", cwd=repo, dry_run=True)
    assert str(layout.manifest_path) in str(raised.value)


def test_advance_without_the_shared_dispatch_file_refuses(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, FEATURE)
    _spec(layout, FEATURE)
    repo = _init_git(layout.root.parent)
    (layout.root / "workflow.yaml").unlink()
    with pytest.raises(WorkspaceError, match=r"workflow\.yaml"):
        run_stage_advance(layout, FEATURE, today=TODAY, expected_phase="design", cwd=repo, dry_run=True)


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
    with pytest.raises(WorkspaceError, match=r"workflow\.local\.yaml"):
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
    advanced = run_stage_advance(layout, FEATURE, today=TODAY, expected_phase="design", cwd=repo, dry_run=dry_run)
    assert advanced.outcome.plan.refusal is None
    phase = next(change.after for change in advanced.outcome.plan.changes if change.key == "phase")
    assert phase == preview.route.on_complete.phase == "execute"
    if dry_run:
        assert page.read_bytes() == before
    else:
        assert "phase: execute" in page.read_text(encoding="utf-8")


def test_unstamped_design_has_spec_plan_matches_planned_dispatch(tmp_path: Path) -> None:
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
    assert {p: p.read_bytes() for p in layout.bundle_dir.rglob("*") if p.is_file()} == before
