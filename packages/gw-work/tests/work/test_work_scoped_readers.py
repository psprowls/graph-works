"""Adopted readers preserve typed results while loading only the work lane."""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from datetime import date

import pytest
from gw_kernel import apply_init, plan_init
from gw_kernel.read_session import BundleSession
from gw_kernel.read_session import open as read_session_open
from gw_kernel.workspace import bundle as bundle_mod
from gw_pipeline import finish
from gw_work import commands as work
from gw_work import reconcile
from work_tracker_okf.items import IGNORE, load_items, unreadable_detail

TODAY = date(2026, 10, 2)


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
    seen = []
    real = read_session_open.open_read_session

    @contextmanager
    def indexed(lay):
        seen.append(lay)
        with real(lay) as session:
            assert session.backend == "index"
            yield session

    @contextmanager
    def legacy(lay):
        yield BundleSession(lay.bundle_dir, fallback="disabled")

    with monkeypatch.context() as patch:
        patch.setattr(read_session_open, "open_read_session", indexed)
        result = call(layout)
        assert seen == [layout]
    with monkeypatch.context() as patch:
        patch.setattr(read_session_open, "open_read_session", legacy)
        assert call(layout) == result
    if call is work.run_open_decisions:
        assert len(result) == 1 and result[0].decision.id == "D-001"


@pytest.mark.parametrize("path", [ITEM, "work/feature-bad", "work/missing"])
def test_active_work_typed_result_matches_legacy(layout, monkeypatch, path):
    # The only effect is the provenance pointer; stub that filesystem write.
    monkeypatch.setattr(work.provenance, "write_active_work", lambda *a, **k: layout.config_dir / "active-work.json")
    result = _equivalent(layout, monkeypatch, work, lambda lay: work.run_touch_active_work(lay, path, today=TODAY))
    assert result.refusal == {ITEM: None, "work/feature-bad": "unreadable", "work/missing": "unknown-item"}[path]


def test_finish_results_match_legacy(layout, monkeypatch):
    _phase(layout, "finish", stamped=True)
    _equivalent(layout, monkeypatch, finish, lambda lay: finish.inspect_finish(lay, ITEM))


def test_reconcile_context_matches_legacy(layout, monkeypatch):
    result = _equivalent(layout, monkeypatch, reconcile, lambda lay: reconcile.run_reconcile_context(lay, ITEM))
    assert result.path == ITEM and result.spec_path.endswith("references/01-design.md")
