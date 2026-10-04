"""Real detached gates reuse unchanged units; advance consumes mixed-run evidence."""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import pytest
from _gate_helpers import NOW, TODAY, receipt_text
from conftest import GateEnv, git
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.orchestrate.gate import run_gate_check, run_gate_run, run_gate_wait, spawn_runner
from graph_works_core.orchestrate.gate_receipts import GateRun, Reuse, parse_gate_receipt
from graph_works_core.orchestrate.wait import WaitClock

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unit commands use sh")

COUNT = 'echo {name} >> "$(git rev-parse --path-format=absolute --git-common-dir)/gw-count"'
GATE = "    gate:\n      full: 'true'\n      units: 'cat units.json'\n"


def _setup(env: GateEnv, tmp_path: Path) -> Path:
    env.set_manifest(gate=GATE)
    env.set_affects(["packages"])
    units = []
    for name in "abc":
        package = env.repo / "packages" / name
        package.mkdir(parents=True, exist_ok=True)
        (package / "m.py").write_text(name, encoding="utf-8", newline="\n")
        units.append(
            {
                "name": name,
                "inputs": [f"packages/{name}/**"],
                "command": COUNT.format(name=name),
                "depends_on": ["a"] if name == "b" else [],
            }
        )
    manifest = {"version": 1, "jobs": 2, "repo_wide": {"command": COUNT.format(name="rw")}, "units": units}
    (env.repo / "units.json").write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-qm", "units")
    worktree = tmp_path / "feature-wt"
    git(env.repo, "worktree", "add", "-q", "-b", "feature", str(worktree))
    env.state["start_sha"] = git(env.repo, "rev-parse", "HEAD")
    env.set_worktree(worktree)
    env.page_text = env.page_text.replace("owner: someone", f"owner: someone\nstart_sha: {env.state['start_sha']}")
    (env.layout.bundle_dir / f"{env.path}.md").write_text(env.page_text, encoding="utf-8", newline="\n")
    return worktree


def _gate(env: GateEnv, *, ran: set[str]) -> GateRun:
    started = run_gate_run(
        env.layout, env.path, now=NOW, token=f"{time.monotonic_ns() % 10**8:08x}", spawn=spawn_runner
    )
    assert started.status == "started", started
    assert {unit.name for unit in started.units if unit.planned} == ran
    waited = run_gate_wait(
        env.layout,
        env.path,
        run_id=started.run_id,
        timeout=60.0,
        clock=WaitClock(wall=lambda: NOW, monotonic=time.monotonic),
        sleep=time.sleep,
        today=TODAY,
    )
    assert waited.status == "finished" and waited.exit == 0 and waited.recorded, waited
    assert dict(waited.units) == dict.fromkeys(ran, 0)
    assert waited.repo_wide_exit == 0
    owner, runs = parse_gate_receipt(receipt_text(env))
    assert owner == env.path
    recorded = next(run for run in runs if run.run_id == started.run_id)
    assert recorded.scope == "full" and recorded.clean and not recorded.tree_changed
    assert recorded.repo_wide is not None and recorded.repo_wide.ran and recorded.repo_wide.exit == 0
    assert {unit.name for unit in recorded.units if unit.ran} == ran
    for unit in recorded.units:
        if unit.ran:
            assert unit.exit == 0 and unit.reused_from is None
            assert unit.log_path is not None and Path(unit.log_path).is_file()
    return recorded


def _counted(env: GateEnv) -> Counter[str]:
    return Counter((env.repo / ".git" / "gw-count").read_text(encoding="utf-8").splitlines())


def _touch(worktree: Path, name: str) -> None:
    (worktree / "packages" / name / "m.py").write_text(f"{name}2", encoding="utf-8", newline="\n")
    git(worktree, "commit", "-qam", f"touch {name}")


def test_only_the_changed_unit_reruns_and_advance_accepts_mixed_evidence(env: GateEnv, tmp_path: Path) -> None:
    worktree = _setup(env, tmp_path)
    first = _gate(env, ran={"a", "b", "c"})
    assert _counted(env) == {"a": 1, "b": 1, "c": 1, "rw": 1}
    _touch(worktree, "c")
    second = _gate(env, ran={"c"})
    assert _counted(env) == {"a": 1, "b": 1, "c": 2, "rw": 2}
    assert first.tree != second.tree and first.run_id != second.run_id
    reused = {unit.name: unit.reused_from for unit in second.units if not unit.ran}
    assert reused == dict.fromkeys(("a", "b"), Reuse(env.path, first.run_id))
    assert run_gate_check(env.layout, env.path).status == "satisfied"
    result = stage.run_stage_advance(env.layout, env.path, today=TODAY, repo=env.repo, dry_run=False)
    assert result.outcome.written, result.outcome.plan.detail
    assert result.gate_receipt is not None
    assert {unit.name: (unit.owner, unit.run_id) for unit in result.gate_receipt.units} == {
        "a": (env.path, first.run_id),
        "b": (env.path, first.run_id),
        "c": (env.path, second.run_id),
    }
    assert "phase: finish" in (env.layout.bundle_dir / f"{env.path}.md").read_text(encoding="utf-8")
    assert _counted(env) == {"a": 1, "b": 1, "c": 2, "rw": 2}


def test_touching_a_dependency_reruns_its_reverse_closure(env: GateEnv, tmp_path: Path) -> None:
    worktree = _setup(env, tmp_path)
    first = _gate(env, ran={"a", "b", "c"})
    assert _counted(env) == {"a": 1, "b": 1, "c": 1, "rw": 1}
    _touch(worktree, "a")
    second = _gate(env, ran={"a", "b"})
    assert _counted(env) == {"a": 2, "b": 2, "c": 1, "rw": 2}
    assert {unit.name: unit.reused_from for unit in second.units if not unit.ran} == {
        "c": Reuse(env.path, first.run_id)
    }
    assert run_gate_check(env.layout, env.path).status == "satisfied"
