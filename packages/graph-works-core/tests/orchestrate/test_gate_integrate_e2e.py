"""End to end: children gate their own closure; the epic anchor and main re-run only stale units.

Design: work/epic-pipeline-wait-gate-efficiency/children/feature-integrate-scope §3. No integrate
scope exists (D-001): every post-merge gate is the ordinary `full` gate, and per-unit reuse across
items is what keeps it to the stale units plus repo-wide.
"""

from __future__ import annotations

import json
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

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="units are sh commands")

BASE, EPIC, CHILD_A, CHILD_B = "work/base", "work/epic", "work/child-a", "work/child-b"
COUNT = 'echo {name} >> "$(git rev-parse --path-format=absolute --git-common-dir)/gw-count"'
GATE = "    gate:\n      full: 'true'\n      units: 'cat units.json'\n"
SHAPES = {"independent": {}, "a-depends-on-c": {"a": ["c"]}}


def manifest(depends: dict[str, list[str]]) -> str:
    units = []
    for name in "abc":
        unit: dict[str, object] = {"name": name, "inputs": [f"packages/{name}/**"], "command": COUNT.format(name=name)}
        if name in depends:
            unit["depends_on"] = depends[name]
        units.append(unit)
    return json.dumps({"version": 1, "jobs": 2, "repo_wide": {"command": COUNT.format(name="rw")}, "units": units})


def item(env: GateEnv, path: str, worktree: Path, *, affects: list[str], start_sha: str | None = None) -> None:
    lines = ["type: Feature", f"title: {path}", "work_status: in-progress", "phase: execute", "owner: someone"]
    lines += ["repo: code", f"worktree: {worktree}"]
    if start_sha is not None:
        lines.append(f"start_sha: {start_sha}")
    lines += ["affects:", *(f"  - {entry}" for entry in affects)]
    page = env.layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\n" + "\n".join(lines) + "\n---\n\nBody.\n", encoding="utf-8", newline="\n")


def commit(root: Path, relative: str, text: str) -> None:
    (root / relative).write_text(text, encoding="utf-8", newline="\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", f"touch {relative}")


def counted(env: GateEnv) -> list[str]:
    path = env.repo / ".git" / "gw-count"
    return path.read_text(encoding="utf-8").split() if path.exists() else []


def gate(env: GateEnv, path: str, worktree: Path | None = None) -> list[str]:
    """Run one gate to a recorded green result; return the sorted names it actually executed."""
    before = len(counted(env))
    token = f"{time.monotonic_ns() % 10**8:08x}"
    started = run_gate_run(env.layout, path, worktree=worktree, now=NOW, token=token, spawn=spawn_runner)
    assert started.status in {"started", "running"}, started
    clock = WaitClock(wall=lambda: NOW, monotonic=time.monotonic)
    waited = run_gate_wait(
        env.layout, path, run_id=started.run_id, timeout=60.0, clock=clock, sleep=time.sleep, today=TODAY
    )
    assert waited.status == "finished" and waited.exit == 0 and waited.recorded, waited
    return sorted(counted(env)[before:])


def advance(env: GateEnv, path: str) -> None:
    result = stage.run_stage_advance(env.layout, path, today=TODAY, repo=env.repo, dry_run=False)
    assert result.outcome.written, result.outcome.plan


def owners(env: GateEnv, path: str, worktree: Path) -> dict[str, str]:
    check = run_gate_check(env.layout, path, worktree=worktree)
    assert check.status == "satisfied", check
    assert check.match is not None
    return {unit.name: unit.owner for unit in check.match.units}


@dataclass
class Anchor:
    env: GateEnv
    anchor: Path
    depends: dict[str, list[str]]


def through_anchor(env: GateEnv, tmp_path: Path, shape: str) -> Anchor:
    """Design §3 steps 1-5: base gate, two children, both landed on the epic anchor."""
    depends = SHAPES[shape]
    env.set_manifest(gate=GATE)
    for name in "abc":
        (env.repo / "packages" / name).mkdir(parents=True, exist_ok=True)
        (env.repo / "packages" / name / "m.py").write_text(name, encoding="utf-8", newline="\n")
    (env.repo / "units.json").write_text(manifest(depends), encoding="utf-8", newline="\n")
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-qm", "units")
    item(env, BASE, env.repo, affects=["packages"])
    item(env, EPIC, env.repo, affects=["packages"])

    # 1. Base: a full gate on main records every unit plus repo-wide.
    assert gate(env, BASE) == ["a", "b", "c", "rw"]

    # 2. Epic anchor, and two children branched from it.
    anchor = tmp_path / "epic-wt"
    git(env.repo, "worktree", "add", "-q", "-b", "epic", str(anchor))
    start = git(anchor, "rev-parse", "HEAD")
    child_a, child_b = tmp_path / "child-a", tmp_path / "child-b"
    git(env.repo, "worktree", "add", "-q", "-b", "child-a", str(child_a), "epic")
    git(env.repo, "worktree", "add", "-q", "-b", "child-b", str(child_b), "epic")
    item(env, CHILD_A, child_a, affects=["packages/a"], start_sha=start)
    item(env, CHILD_B, child_b, affects=["packages/c"], start_sha=start)

    # 3. Child B executes (c plus its reverse closure plus repo-wide) and fast-forwards onto the anchor.
    commit(child_b, "packages/c/m.py", "c2")
    assert gate(env, CHILD_B) == (["a", "c", "rw"] if depends else ["c", "rw"])
    advance(env, CHILD_B)
    git(anchor, "merge", "--ff-only", "child-b")
    before = counted(env)
    assert owners(env, CHILD_B, anchor)["c"] == CHILD_B
    assert counted(env) == before  # a check runs nothing

    # 4. Child A executes on a tree without B: a plus its reverse closure plus repo-wide.
    commit(child_a, "packages/a/m.py", "a2")
    assert gate(env, CHILD_A) == ["a", "rw"]
    advance(env, CHILD_A)

    # 5. A merges onto the anchor (a new tree): only what both children together made stale re-runs.
    git(anchor, "merge", "--no-ff", "child-a", "-m", "merge child-a")
    assert run_gate_check(env.layout, CHILD_A, worktree=anchor).status == "unsatisfied"
    assert gate(env, CHILD_A, anchor) == (["a", "rw"] if depends else ["rw"])
    assert owners(env, CHILD_A, anchor) == {"a": CHILD_A, "b": BASE, "c": CHILD_B}
    return Anchor(env, anchor, depends)


@pytest.mark.parametrize("shape", SHAPES)
def test_children_and_the_anchor_merge_run_only_stale_units(env: GateEnv, tmp_path: Path, shape: str) -> None:
    through_anchor(env, tmp_path, shape)


@pytest.mark.parametrize("shape", SHAPES)
def test_epic_finish_onto_unmoved_main_runs_nothing(env: GateEnv, tmp_path: Path, shape: str) -> None:
    through_anchor(env, tmp_path, shape)
    before = counted(env)
    git(env.repo, "merge", "--ff-only", "epic")  # 6. main unmoved: a fast-forward to the anchor's tree
    check = run_gate_check(env.layout, EPIC, worktree=env.repo)
    assert check.status == "satisfied" and check.match is not None, check
    assert {unit.name: unit.owner for unit in check.match.units} == {"a": CHILD_A, "b": BASE, "c": CHILD_B}
    assert check.match.owner == CHILD_A  # repo-wide from the anchor merge's run
    assert counted(env) == before


@pytest.mark.parametrize("moved", ["b", "c"])
@pytest.mark.parametrize("shape", SHAPES)
def test_epic_finish_onto_moved_main_reruns_only_what_main_staled(
    env: GateEnv, tmp_path: Path, shape: str, moved: str
) -> None:
    scenario = through_anchor(env, tmp_path, shape)
    commit(env.repo, f"packages/{moved}/main.py", "moved")  # 7. main moves under one unit
    git(env.repo, "merge", "--no-ff", "epic", "-m", "merge epic")
    assert run_gate_check(env.layout, EPIC, worktree=env.repo).status == "unsatisfied"
    dependents = [name for name, deps in scenario.depends.items() if moved in deps]
    assert gate(env, EPIC, env.repo) == sorted([moved, *dependents, "rw"])
