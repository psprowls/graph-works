"""Adopted readers preserve typed results while loading only the work lane."""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime

import pytest
from gw_kernel import apply_init, plan_init
from gw_kernel.workspace import bundle as bundle_mod
from gw_orchestrate import asks, gate, integrate
from gw_orchestrate import commands as orchestrate
from work_tracker_okf import asks as pure_asks
from work_tracker_okf.items import IGNORE

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
        stamp = f"worktree: {layout.root.parent / 'source'}\nbranch: feature\naffects: [feature.txt]"
        text = text.replace("affects: []", stamp)
    _write(page, text)


def test_orchestrate_plan_matches_legacy(layout, monkeypatch):
    _equivalent(layout, monkeypatch, orchestrate, lambda lay: orchestrate.run_orchestrate(lay, ITEM))


def test_gate_resolution_matches_legacy(layout, monkeypatch):
    _phase(layout, "execute", stamped=True)
    result = _equivalent(layout, monkeypatch, gate, lambda lay: gate._resolve(lay, ITEM))
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
