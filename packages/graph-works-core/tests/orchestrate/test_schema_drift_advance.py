"""Stale installed work schemas: advance refuses early, refresh, then advance succeeds."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import stage_advance
from graph_works_core.orchestrate.stage_advance import run_stage_advance
from graph_works_core.workspace import work_schemas as ws
from graph_works_core.workspace.layout import WorkspaceLayout

TODAY = date(2026, 10, 5)
PATH = "work/feature-one"
BASE = "schema/_base.schema.json"


def _old_base() -> bytes:
    """The packaged base without `spec_baseline`: the pre-25f7d0f6 shape."""
    text = ws.packaged_schemas()[BASE].decode("utf-8")
    data = json.loads(text)
    data["properties"].pop("spec_baseline")
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


@pytest.fixture
def stale(tmp_path: Path) -> WorkspaceLayout:
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Stale")).layout
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    design = layout.bundle_dir / PATH / "references" / "01-design.md"
    design.parent.mkdir(parents=True)
    design.write_text("# Design\n", encoding="utf-8", newline="")
    (layout.bundle_dir / f"{PATH}.md").write_text(
        "---\ntype: Feature\ntitle: One\ndescription: d\nstatus: draft\nwork_status: open\nphase: design\n"
        "effort: medium\nopened: 2026-09-01\nupdated: 2026-09-01\naffects: []\n"
        f"sources:\n  - id: design\n    resource: /{PATH}/references/01-design.md\n    title: Design\n---\n",
        encoding="utf-8",
        newline="",
    )
    old = _old_base()
    (ws.declarations_dir_for(layout) / BASE).write_bytes(old)
    digests = {k: hashlib.sha256(v).hexdigest() for k, v in ws.packaged_schemas().items()}
    digests[BASE] = hashlib.sha256(old).hexdigest()
    (ws.declarations_dir_for(layout) / ws.PROVENANCE_RELATIVE).write_bytes(ws.render_provenance(digests))
    subprocess.run(["git", "init", "-q", str(layout.root)], check=True)
    subprocess.run(["git", "-C", str(layout.root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(layout.root), "config", "user.email", "test@example.test"], check=True)
    subprocess.run(["git", "-C", str(layout.root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(layout.root), "commit", "-qm", "seed workspace"], check=True)
    return layout


def _head(layout: WorkspaceLayout) -> str:
    return subprocess.run(
        ["git", "-C", str(layout.root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _tree(layout: WorkspaceLayout) -> dict[str, bytes]:
    # Decision-owner locks and transaction journals are operational state, not domain writes.
    return {
        p.relative_to(layout.root).as_posix(): p.read_bytes()
        for base in (layout.bundle_dir, layout.config_dir)
        for p in base.rglob("*")
        if p.is_file() and not p.is_relative_to(layout.cache_dir)
    }


@pytest.mark.parametrize("dry_run", [True, False])
def test_stale_base_refuses_before_any_write(stale: WorkspaceLayout, dry_run: bool) -> None:
    before = _tree(stale)
    head_before = _head(stale)
    called: list[object] = []
    result = run_stage_advance(
        stale, PATH, today=TODAY, infer_worktree=False, dry_run=dry_run, before_apply=called.append
    )
    assert result.outcome.plan.refusal == "schema-drift", result
    assert BASE in (result.outcome.plan.detail or "")
    assert "gw config sync --schemas --workspace" in (result.outcome.plan.detail or "")
    assert result.application is None
    assert called == []
    assert _tree(stale) == before
    assert _head(stale) == head_before
    assert not (stale.cache_dir / "gate").exists()
    assert not (stale.cache_dir / "active-work.json").exists()


def test_refresh_then_advance_succeeds(stale: WorkspaceLayout) -> None:
    ws.apply_schema_refresh(ws.plan_schema_refresh(stale))
    result = run_stage_advance(stale, PATH, today=TODAY, infer_worktree=False, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert result.application is not None and result.application.ok
    assert result.outcome.written
    assert result.outcome.plan.transition is not None and result.outcome.plan.transition.phase == "plan"
    text = (stale.bundle_dir / f"{PATH}.md").read_text(encoding="utf-8")
    assert "spec_baseline:" in text and "phase: plan" in text


def test_drift_between_preflight_and_apply_aborts_without_writes(stale: WorkspaceLayout) -> None:
    ws.apply_schema_refresh(ws.plan_schema_refresh(stale))
    before = _tree(stale)
    head_before = _head(stale)

    def edit_schema(_candidate: object) -> None:
        (ws.declarations_dir_for(stale) / BASE).write_bytes(_old_base())

    result = run_stage_advance(stale, PATH, today=TODAY, infer_worktree=False, dry_run=False, before_apply=edit_schema)
    assert result.application is not None and not result.application.ok
    assert any("schema-drift" in failure for failure in result.application.failures)
    assert not result.application.rolled_back
    schema_path = (ws.declarations_dir_for(stale) / BASE).relative_to(stale.root).as_posix()
    before[schema_path] = _old_base()
    assert _tree(stale) == before
    assert _head(stale) == head_before


@pytest.mark.parametrize("dry_run", [True, False])
def test_stale_schemas_refuse_before_execute_gates(
    stale: WorkspaceLayout, dry_run: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = stale.bundle_dir / f"{PATH}.md"
    page.write_text(
        page.read_text(encoding="utf-8")
        .replace("phase: design", "phase: execute\nowner: human:test")
        .replace("work_status: open", "work_status: in-progress"),
        encoding="utf-8",
        newline="",
    )
    before = _tree(stale)
    head_before = _head(stale)

    def unexpected_gate(*args: object, **kwargs: object) -> None:
        pytest.fail("schema drift must refuse before commit or receipt gates")

    monkeypatch.setattr(stage_advance, "_commit_gate", unexpected_gate)
    monkeypatch.setattr(stage_advance, "_receipt_gate", unexpected_gate)
    result = run_stage_advance(
        stale,
        PATH,
        today=TODAY,
        infer_worktree=False,
        dry_run=dry_run,
        skip_gate="no-start-sha",
        skip_reason="reviewed",
        actor="human:test",
    )
    assert result.outcome.plan.refusal == "schema-drift"
    assert result.gate_bypass is None and result.gate_receipt is None
    assert result.application is None
    assert _tree(stale) == before
    assert _head(stale) == head_before
    assert not (stale.cache_dir / "gate").exists()
    assert not (stale.cache_dir / "active-work.json").exists()


def test_schema_drift_is_not_a_bypassable_code(stale: WorkspaceLayout) -> None:
    ws.apply_schema_refresh(ws.plan_schema_refresh(stale))
    before = _tree(stale)
    head_before = _head(stale)
    result = run_stage_advance(
        stale,
        PATH,
        today=TODAY,
        infer_worktree=False,
        dry_run=False,
        skip_gate="schema-drift",
        skip_reason="reviewed",
        actor="human:test",
    )
    assert result.outcome.plan.refusal == "gate-bypass-invalid"
    assert result.application is None
    assert _tree(stale) == before
    assert _head(stale) == head_before
    assert not (stale.cache_dir / "gate").exists()
    assert not (stale.cache_dir / "active-work.json").exists()
