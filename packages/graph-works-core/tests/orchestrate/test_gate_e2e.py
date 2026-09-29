"""End to end: one real gate execution per distinct tree across execute and finish."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from _gate_helpers import NOW, TODAY
from conftest import GateEnv, git
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.orchestrate.gate import run_gate_check, run_gate_run, run_gate_wait, spawn_runner
from graph_works_core.orchestrate.wait import WaitClock

COUNTER_GATE = (
    "    gate:\n"
    '      full: \'sh -c "echo run >> \\"$(git rev-parse --path-format=absolute --git-common-dir)/gw-count\\""\'\n'
)


@dataclass
class E2E:
    env: GateEnv
    main: Path
    worktree: Path


@pytest.fixture
def e2e(env: GateEnv, tmp_path: Path) -> E2E:
    env.set_manifest(gate=COUNTER_GATE)
    main = env.repo
    worktree = tmp_path / "feature-wt"
    git(main, "worktree", "add", "-q", "-b", "feature", str(worktree))
    env.state["start_sha"] = git(main, "rev-parse", "HEAD")
    env.set_worktree(worktree)
    page = env.layout.bundle_dir / f"{env.path}.md"
    text = env.page_text.replace("owner: someone", f"owner: someone\nstart_sha: {env.state['start_sha']}")
    page.write_text(text, encoding="utf-8", newline="\n")
    env.page_text = text
    return E2E(env, main, worktree)


def commit_under_affects(root: Path) -> None:
    (root / "packages/a/y.py").write_text("y", encoding="utf-8", newline="\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "work")


def commit_elsewhere(root: Path) -> None:
    (root / "elsewhere.txt").write_text("e", encoding="utf-8", newline="\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "elsewhere")


def run_and_wait(e: E2E, *, worktree: Path | None = None):
    started = run_gate_run(
        e.env.layout,
        e.env.path,
        worktree=worktree,
        now=NOW,
        token=f"{time.monotonic_ns() % 10**8:08x}",
        spawn=spawn_runner,
    )
    assert started.status in {"started", "running"}, started
    real = WaitClock(wall=lambda: NOW, monotonic=time.monotonic)
    waited = run_gate_wait(
        e.env.layout, e.env.path, run_id=started.run_id, timeout=60.0, clock=real, sleep=time.sleep, today=TODAY
    )
    assert waited.status == "finished" and waited.exit == 0 and waited.recorded, waited
    return waited


def advance(e: E2E):
    return stage.run_stage_advance(e.env.layout, e.env.path, today=TODAY, repo=e.main, dry_run=False)


def gate_count(e: E2E) -> int:
    counter = e.main / ".git" / "gw-count"
    return len(counter.read_text(encoding="utf-8").splitlines()) if counter.exists() else 0


@pytest.mark.skipif(sys.platform == "win32", reason="sh gate command")
def test_one_gate_per_distinct_tree_across_execute_and_finish(e2e: E2E) -> None:
    e = e2e
    commit_under_affects(e.worktree)
    run_and_wait(e)  # execute gate: 1
    adv = advance(e)
    assert adv.outcome.written, adv.outcome.plan  # execute -> finish passes on the receipt
    assert run_gate_check(e.env.layout, e.env.path).status == "satisfied"  # relay R1: no rerun
    git(e.main, "merge", "--ff-only", "feature")  # fast-forward in the target worktree
    assert run_gate_check(e.env.layout, e.env.path, worktree=e.main).status == "satisfied"  # R4: no rerun
    assert git(e.worktree, "log", "main..feature") == ""  # zero-commit finish: R2 settles
    assert gate_count(e) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="sh gate command")
def test_a_true_merge_gates_the_new_tree_once(e2e: E2E) -> None:
    e = e2e
    commit_under_affects(e.worktree)
    run_and_wait(e)
    commit_elsewhere(e.main)  # target moved
    git(e.main, "merge", "--no-ff", "feature", "-m", "merge")
    assert run_gate_check(e.env.layout, e.env.path, worktree=e.main).status == "unsatisfied"
    run_and_wait(e, worktree=e.main)
    assert gate_count(e) == 2
    assert run_gate_check(e.env.layout, e.env.path, worktree=e.main).status == "satisfied"
