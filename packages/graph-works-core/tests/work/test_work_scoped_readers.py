"""Adopted readers preserve typed results while loading only the work lane."""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import asks, gate, integrate, merge_workspace, placement, workspace_prepare
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.work import commands as work
from graph_works_core.work import reconcile
from graph_works_core.workspace import bundle as bundle_mod
from graph_works_core.workspace import finish
from work_tracker_okf import asks as pure_asks
from work_tracker_okf.items import IGNORE, load_items, unreadable_detail
from work_tracker_okf.placement import ReaderObservation

TODAY = date(2026, 10, 2)
NOW = datetime(2026, 10, 2, tzinfo=UTC)
ITEM = "work/feature-a"

_ITEM = """---
type: Feature
title: {slug}
description: d
status: stable
work_status: accepted
phase: plan
effort: medium
opened: 2026-10-01
updated: 2026-10-01
affects: []
---

## Summary
See [doc](/docs/page.md).

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _init_git(repo):
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-b", "main")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", str(repo.parent / "no-hooks")),
    ):
        _git(repo, "config", key, value)
    _git(repo, "add", ".")
    _git(repo, "commit", "--allow-empty", "-m", "fixture")


@pytest.fixture
def layout(tmp_path):
    code = tmp_path / "code"
    _init_git(code)
    source = tmp_path / "source"
    _git(code, "worktree", "add", "-b", "feature", str(source))
    _write(source / "feature.txt", "feature\n")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "feature")
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Scope")).layout
    _write(
        layout.manifest_path,
        layout.manifest_path.read_text(encoding="utf-8").replace(
            "repositories: {}", f"repositories:\n  code:\n    path: {code.as_posix()}"
        ),
    )
    root = layout.bundle_dir
    _write(root / "docs/page.md", "---\ntype: Explanation\ntitle: P\n---\n\nB.\n")
    _write(root / "code-graph/r/x.md", "---\ntype: Package\ntitle: X\n---\n\nB.\n")
    _write(root / "sources/s.md", "---\ntype: Source\ntitle: S\n---\n\nB.\n")
    for slug in ("feature-a", "feature-b"):
        _write(root / "work" / f"{slug}.md", _ITEM.format(slug=slug))
    (root / "work/feature-bad.md").write_bytes(b"\xff\xfe")
    _write(root / ITEM / "references/01-design.md", "# Design\n\nReader equivalence.\n")
    _write(
        root / ITEM / "references/00-decisions.md",
        "# Decisions\n\n## D-001 - Keep it?\nstatus: open\naffects: []\n",
    )
    _init_git(layout.root)
    return layout


def _legacy(layout):
    return bundle_mod.load_workspace_bundle(layout, ignore=IGNORE)


def _equivalent(layout, monkeypatch, module, call, *, loads=1):
    """Exercise both real loaders; require every adopted read to use the seam."""
    seen = []

    def scoped(lay):
        seen.append(lay)
        return bundle_mod.load_work_bundle(lay)

    # raising=False lets the RED run prove absent imports and unconverted calls
    # by behavior, rather than failing while setting up the monkeypatch.
    with monkeypatch.context() as patch:
        patch.setattr(module, "load_work_bundle", scoped, raising=False)
        result = call(layout)
        assert len(seen) == loads, "reader did not use the scoped loader"
    with monkeypatch.context() as patch:
        patch.setattr(module, "load_work_bundle", _legacy, raising=False)
        assert call(layout) == result
    return result


def _phase(layout, phase, *, stamped=False):
    page = layout.bundle_dir / f"{ITEM}.md"
    text = page.read_text(encoding="utf-8").replace("phase: plan", f"phase: {phase}")
    if stamped:
        text = text.replace("affects: []", f"worktree: {layout.root.parent / 'source'}\nbranch: feature\naffects: []")
    _write(page, text)


def test_the_projection_every_adopted_reader_consumes_is_identical(layout):
    scoped, legacy = bundle_mod.load_work_bundle(layout), _legacy(layout)
    assert tuple(load_items(scoped)) == tuple(load_items(legacy))
    work_ids = {cid for cid in legacy.concepts if cid.startswith("work/")}
    assert set(scoped.concepts) == work_ids
    for cid in work_ids:
        assert scoped.concepts[cid].serialize() == legacy.concepts[cid].serialize()
    assert unreadable_detail(scoped, "work/feature-bad") == unreadable_detail(legacy, "work/feature-bad")
    assert unreadable_detail(scoped, "work/feature-bad") is not None
    assert scoped.root == legacy.root


@pytest.mark.parametrize(
    "call",
    [
        work.run_status,
        work.run_work_list,
        lambda lay: work.run_item_read(lay, ITEM),
        lambda lay: work.run_item_read(lay, "work/feature-bad"),
        lambda lay: work.run_item_read(lay, "work/missing"),
        work.run_work_queue,
        work.run_open_decisions,
    ],
    ids=["status", "list", "item", "unreadable", "missing", "queue", "open-decisions"],
)
def test_work_display_results_match_legacy(layout, monkeypatch, call):
    result = _equivalent(layout, monkeypatch, work, call)
    if call is work.run_open_decisions:
        assert len(result) == 1 and result[0].decision.id == "D-001"


@pytest.mark.parametrize("path", [ITEM, "work/feature-bad", "work/missing"])
def test_active_work_typed_result_matches_legacy(layout, monkeypatch, path):
    # The only effect is the provenance pointer; stub that filesystem write.
    monkeypatch.setattr(work.provenance, "write_active_work", lambda *a, **k: layout.config_dir / "active-work.json")
    result = _equivalent(layout, monkeypatch, work, lambda lay: work.run_touch_active_work(lay, path, today=TODAY))
    assert result.refusal == {ITEM: None, "work/feature-bad": "unreadable", "work/missing": "unknown-item"}[path]


@pytest.mark.parametrize(
    "call", [finish.inspect_finish, lambda lay, path: finish.plan_finish_cleanup(lay, path, runner_cwd=None)]
)
def test_finish_results_match_legacy(layout, monkeypatch, call):
    _phase(layout, "finish", stamped=True)
    _equivalent(layout, monkeypatch, finish, lambda lay: call(lay, ITEM))


def test_reconcile_context_matches_legacy(layout, monkeypatch):
    result = _equivalent(layout, monkeypatch, reconcile, lambda lay: reconcile.run_reconcile_context(lay, ITEM))
    assert result.path == ITEM and result.spec_path.endswith("references/01-design.md")


def test_orchestrate_plan_matches_legacy(layout, monkeypatch):
    _equivalent(layout, monkeypatch, orchestrate, lambda lay: orchestrate.run_orchestrate(lay, ITEM))


def test_preparation_guard_token_is_load_independent(layout):
    assert placement.preparation_guard(layout, ITEM, bundle=bundle_mod.load_work_bundle(layout)) == (
        placement.preparation_guard(layout, ITEM, bundle=_legacy(layout))
    )


def test_workspace_preparation_matches_legacy_at_both_reads(layout, monkeypatch):
    _phase(layout, "execute")
    result = _equivalent(
        layout,
        monkeypatch,
        workspace_prepare,
        lambda lay: workspace_prepare.run_prepare_workspace(lay, ITEM, today=TODAY),
        loads=2,
    )
    assert result.refusal is None and len(result.steps) == 1 and not result.applied


def test_gate_resolution_matches_legacy(layout, monkeypatch):
    _phase(layout, "execute", stamped=True)
    result = _equivalent(layout, monkeypatch, gate, lambda lay: gate._resolve(lay, ITEM, None))
    assert isinstance(result, tuple) and result[0].repo == "code"


def _ask(layout):
    return asks.run_ask(
        layout,
        ITEM,
        kind="choice",
        summary="Pick",
        question="Keep it?",
        spec=None,
        options=(("keep", "Keep"), ("hold", "Hold")),
        created=NOW,
    )


def test_ask_preview_matches_legacy(layout, monkeypatch):
    result = _equivalent(layout, monkeypatch, asks, _ask)
    assert result.ok and not result.applied


def test_ask_answer_preview_matches_legacy(layout, monkeypatch):
    built = pure_asks.build_ask(
        item=ITEM,
        phase="plan",
        kind="choice",
        created=NOW,
        summary="Pick",
        question="Keep it?",
        spec=None,
        current_effort="medium",
        options=(pure_asks.AskOption("keep", "Keep"), pure_asks.AskOption("hold", "Hold")),
    )
    assert built.payload is not None
    payload = layout.bundle_dir / ITEM / "references/asks/plan-001-choice.json"
    _write(payload, built.payload.to_json())
    result = _equivalent(
        layout,
        monkeypatch,
        asks,
        lambda lay: asks.run_ask_answer(
            lay,
            str(payload),
            choice="keep",
            effort=None,
            notes=None,
            at=NOW,
            by="test",
        ),
    )
    assert result.changed and not result.applied and not result.refusals


def test_integrate_preview_matches_legacy(layout, monkeypatch):
    _phase(layout, "finish", stamped=True)
    result = _equivalent(
        layout,
        monkeypatch,
        integrate,
        lambda lay: integrate.run_integrate(lay, ITEM, repo_name="code", strategy="ff", today=TODAY),
    )
    assert result.refusal is None and result.outcome == "planned" and not result.applied


@pytest.mark.parametrize("stamped", [False, True], ids=["missing-target", "planned"])
def test_merge_workspace_precheck_matches_legacy(layout, monkeypatch, stamped):
    _phase(layout, "finish")
    if stamped:
        source = layout.root.parent / "workspace-source"
        _git(layout.root, "worktree", "add", "-b", "workspace-feature", str(source))
        page = layout.bundle_dir / f"{ITEM}.md"
        _write(
            page,
            page.read_text(encoding="utf-8").replace(
                "affects: []",
                f"repo_stamps:\n  _workspace:\n    worktree: {source}\n    branch: workspace-feature\naffects: []",
            ),
        )
    result = _equivalent(
        layout,
        monkeypatch,
        merge_workspace,
        lambda lay: merge_workspace.run_merge_workspace(lay, ITEM, today=TODAY),
    )
    assert result.refusal == (None if stamped else "no-workspace-target") and not result.applied
    if stamped:
        assert (result.source_branch, result.target_branch) == ("workspace-feature", "main")


def test_reader_receipt_preview_matches_legacy(layout, monkeypatch):
    observation = ReaderObservation(
        "task_1", "ctx_1", "dispatch-key", "code", str(layout.root.parent / "source"), "a" * 40
    )
    result = _equivalent(
        layout,
        monkeypatch,
        placement,
        lambda lay: placement.run_record_reader(lay, ITEM, root=ITEM, phase="plan", observation=observation),
    )
    assert result.plan.refusal is None and not result.written


def test_execute_baseline_preview_matches_legacy(layout, monkeypatch):
    _phase(layout, "execute")
    result = _equivalent(
        layout,
        monkeypatch,
        placement,
        lambda lay: placement.run_record_baseline(lay, ITEM, cwd=lay.root.parent / "source", today=TODAY),
    )
    assert result.plan.refusal is None and result.plan.changed and not result.written
