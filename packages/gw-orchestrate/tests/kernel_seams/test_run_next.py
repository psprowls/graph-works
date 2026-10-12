"""Path-native routing and transactional managed-source normalization."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from gw_kernel import apply_init, plan_init
from gw_kernel.testing.transactions import init_git as _init_git
from gw_work import commands as work
from okf_io import load

TODAY = date(2026, 8, 23)


EPIC = "work/epic-a"


CHILD = f"{EPIC}/children/feature-a"


def _write(layout, path: str, *, type: str = "Feature", phase: str = "design") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: open\n"
        f"phase: {phase}\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
        "affects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )


def _landed_plan(tmp_path: Path, *, overlap: bool = True):
    import re
    import subprocess

    def git(repo: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    repo = _init_git(tmp_path / "code")
    baseline = git(repo, "rev-parse", "HEAD")
    target = repo / "packages/a/new.py"
    target.parent.mkdir(parents=True)
    target.write_text("landed\n", encoding="utf-8", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "sibling")
    landed = git(repo, "rev-parse", "HEAD")
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Next")).layout
    manifest = re.sub(r"(?m)^repositories:.*\n(?:[ \t]+.*\n)*", "", layout.manifest_path.read_text(encoding="utf-8"))
    layout.manifest_path.write_text(
        manifest + f"repositories:\n  code:\n    path: {json.dumps(str(repo))}\n", encoding="utf-8", newline="\n"
    )
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD, phase="plan")
    sibling = f"{EPIC}/children/feature-sibling"
    _write(layout, sibling, phase="finish")
    for path, extra in (
        (EPIC, "repo: code\n"),
        (
            CHILD,
            f"spec_baseline:\n  code: {baseline}\ndepends_on:\n- path: {sibling}\n"
            "  blocks: execute\n  needs: resolved\n",
        ),
        (sibling, f"resolved_in: {landed}\n"),
    ):
        page = load(layout.bundle_dir / f"{path}.md")
        if path == sibling:
            page.set("work_status", "resolved")
            page.set("affects", ["packages/a" if overlap else "packages/b"])
        text = page.serialize().replace("\n---\n", "\n" + extra + "---\n", 1)
        page.path.write_text(text, encoding="utf-8", newline="\n")
    _init_git(layout.root)
    return layout, repo, sibling, landed


def test_a_plan_stage_reconcile_completion_ends_the_loop(tmp_path: Path) -> None:
    from gw_orchestrate.stage_advance import run_stage_advance
    from work_tracker_okf.workflow import Dispatch

    layout, repo, _, _ = _landed_plan(tmp_path)
    assert work.run_next(layout, CHILD).route.dispatch == Dispatch("plan")
    advanced = run_stage_advance(
        layout, CHILD, today=TODAY, expected_phase="plan", cwd=repo, infer_worktree=False, dry_run=False
    )
    assert advanced.outcome.written
    assert work.run_next(layout, CHILD).route.dispatch == Dispatch("plan")
