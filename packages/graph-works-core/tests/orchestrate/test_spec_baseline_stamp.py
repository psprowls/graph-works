"""Spec baselines are resolved before the advance's workspace commit."""

import subprocess
from pathlib import Path

import pytest
from graph_works_core.orchestrate.stage_advance import run_stage_advance
from graph_works_core.work.commands import run_next
from okf_io import load
from test_orchestrate_shell import TODAY, _ready, _write
from test_record_baseline import PATH, _head
from test_record_baseline import _setup as _repos


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _setup(tmp_path: Path, *, phase: str = "design", baseline: str = "", spec: str = ""):
    layout, repo, older = _repos(tmp_path)
    _ready(layout, PATH, phase=phase, extra="repo: code\n" + baseline)
    target = layout.bundle_dir / PATH / "references/01-design.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Design\n" + spec, encoding="utf-8", newline="\n")
    _git(layout.root, "init", "-b", "main")
    _git(layout.root, "config", "user.email", "t@example.com")
    _git(layout.root, "config", "user.name", "T")
    _git(layout.root, "add", ".")
    _git(layout.root, "commit", "-m", "workspace")
    return layout, repo, older


def _advance(layout, cwd: Path, *, phase: str = "design", dry_run: bool = False):
    return run_stage_advance(
        layout, PATH, today=TODAY, expected_phase=phase, cwd=cwd, infer_worktree=False, dry_run=dry_run
    )


def _page(layout):
    return load(layout.bundle_dir / f"{PATH}.md")


def test_stamp_prefers_the_spec_baseline_commit_line(tmp_path: Path) -> None:
    layout, repo, older = _setup(tmp_path)
    target = layout.bundle_dir / PATH / "references/01-design.md"
    target.write_text(f"**Baseline commit:** `{older}`\n", encoding="utf-8", newline="\n")
    result = _advance(layout, tmp_path)
    assert result.outcome.written
    assert _page(layout).fm_raw["spec_baseline"]["code"] == older != _head(repo)


def test_stamp_prefers_the_last_reconciled_head_over_the_baseline_line(tmp_path: Path) -> None:
    layout, repo, older = _setup(tmp_path)
    target = layout.bundle_dir / PATH / "references/01-design.md"
    target.write_text(
        f"**Baseline commit:** `{older}`\n## Reconciled 2026-08-23 ({older}..{_head(repo)})\n",
        encoding="utf-8",
        newline="\n",
    )
    _advance(layout, tmp_path)
    assert _page(layout).fm_raw["spec_baseline"]["code"] == _head(repo)


def test_stamp_falls_back_to_the_cwd_checkout_of_the_items_repo(tmp_path: Path) -> None:
    layout, repo, _ = _setup(tmp_path)
    _advance(layout, repo)
    assert _page(layout).fm_raw["spec_baseline"]["code"] == _head(repo)


def test_stamp_omits_code_and_warns_when_nothing_resolves(tmp_path: Path) -> None:
    layout, _, _ = _setup(tmp_path)
    before = _head(layout.root)
    result = _advance(layout, tmp_path)
    assert _page(layout).fm_raw["spec_baseline"] == {"workspace": before}
    assert "spec baseline: no code sha resolvable; landed-since will be unavailable" in result.warnings
    assert result.outcome.written


def test_workspace_sha_is_the_head_before_the_advance_commit(tmp_path: Path) -> None:
    layout, repo, _ = _setup(tmp_path)
    before = _head(layout.root)
    _advance(layout, repo)
    assert _page(layout).fm_raw["spec_baseline"]["workspace"] == before != _head(layout.root)


def _planning(tmp_path: Path, *, stale: bool):
    layout, repo, older = _setup(tmp_path, phase="plan")
    baseline = older if stale else _head(repo)
    page = _page(layout)
    page.set("spec_baseline", {"code": baseline, "workspace": _head(layout.root)})
    page.path.write_bytes(page.serialize().encode("utf-8"))
    _write(
        layout,
        "work/feature-sibling",
        phase=None,
        work_status="resolved",
        extra=f"repo: code\nresolved_in: {_head(repo) if stale else older}\n",
    )
    _git(layout.root, "add", ".")
    _git(layout.root, "commit", "-m", "planning")
    return layout, repo


def test_a_plan_stage_reconcile_completion_keeps_plan_and_overwrites_both_keys(tmp_path: Path) -> None:
    layout, repo = _planning(tmp_path, stale=True)
    previous = dict(_page(layout).fm_raw["spec_baseline"])
    result = _advance(layout, repo, phase="plan")
    assert result.outcome.written
    assert _page(layout).fm_raw["phase"] == "plan"
    stamped = _page(layout).fm_raw["spec_baseline"]
    assert stamped["code"] == _head(repo) != previous["code"]
    assert stamped["workspace"] != previous["workspace"]


def test_a_plan_stage_completion_without_staleness_goes_to_execute(tmp_path: Path) -> None:
    layout, repo = _planning(tmp_path, stale=False)
    previous = dict(_page(layout).fm_raw["spec_baseline"])
    _advance(layout, repo, phase="plan")
    assert _page(layout).fm_raw["phase"] == "execute"
    assert _page(layout).fm_raw["spec_baseline"] == previous


def test_a_dry_run_stamps_nothing(tmp_path: Path) -> None:
    layout, repo, _ = _setup(tmp_path)
    before = _head(layout.root)
    result = _advance(layout, repo, dry_run=True)
    assert not result.outcome.written
    assert "spec_baseline" not in _page(layout).fm_raw
    assert _head(layout.root) == before


def test_a_dry_run_reports_the_missing_code_warning(tmp_path: Path) -> None:
    layout, _, _ = _setup(tmp_path)
    result = _advance(layout, tmp_path, dry_run=True)
    assert "spec baseline: no code sha resolvable; landed-since will be unavailable" in result.warnings
    assert "spec_baseline" not in _page(layout).fm_raw


def test_a_refused_advance_preserves_the_existing_stamp(tmp_path: Path) -> None:
    layout, repo = _planning(tmp_path, stale=True)
    previous = dict(_page(layout).fm_raw["spec_baseline"])
    result = _advance(layout, repo, phase="design")
    assert result.outcome.plan.refusal is not None
    assert not result.outcome.written
    assert not result.outcome.plan.stamp_baseline
    assert _page(layout).fm_raw["spec_baseline"] == previous


@pytest.mark.parametrize("anchor_kind", ["baseline", "reconciled"])
def test_short_spec_anchor_restamps_full_oid_and_exits_reconciliation(tmp_path: Path, anchor_kind: str) -> None:
    layout, repo = _planning(tmp_path, stale=True)
    head = _head(repo)
    target = layout.bundle_dir / PATH / "references/01-design.md"
    text = (
        f"**Baseline commit:** `{head[:12]}`\n"
        if anchor_kind == "baseline"
        else f"## Reconciled 2026-08-23 ({head[:12]}..{head[:12]})\n"
    )
    target.write_text(text, encoding="utf-8", newline="\n")
    result = _advance(layout, repo, phase="plan")
    assert result.outcome.written
    assert _page(layout).fm_raw["spec_baseline"]["code"] == head
    next_result = run_next(layout, PATH)
    assert next_result.route.dispatch.stage == "plan"
