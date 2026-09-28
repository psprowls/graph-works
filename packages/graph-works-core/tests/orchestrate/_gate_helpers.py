"""Shared fixtures-as-functions for the `gw work gate` verb tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from graph_works_core.orchestrate.gate import run_gate_run, runs_dir
from graph_works_core.orchestrate.gate_receipts import GateRun, parse_gate_receipt, render_receipt
from graph_works_core.orchestrate.wait import WaitClock

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
TODAY = NOW.date()


def raise_(exc):
    def _raise(*args, **kwargs):
        raise exc

    return _raise


def no_sleep(_seconds: float) -> None:
    return None


def ticks(*values: float):
    it = iter(values)
    return lambda: next(it)


def fake_clock(*, step: float) -> WaitClock:
    state = {"t": 0.0}

    def monotonic() -> float:
        state["t"] += step
        return state["t"]

    return WaitClock(wall=lambda: NOW, monotonic=monotonic)


def fake_clock_at(wall: datetime, *, step: float = 0.0) -> WaitClock:
    """A clock pinned at *wall*; `monotonic` advances *step* per read."""
    state = {"t": 0.0}

    def monotonic() -> float:
        state["t"] += step
        return state["t"]

    return WaitClock(wall=lambda: wall, monotonic=monotonic)


CLOCK = fake_clock(step=1.0)


def mint_receipt(env, *, tree, owner=None, exit=0, scope="full", command="true") -> None:
    owner = owner or env.path
    target = env.layout.bundle_dir / owner / "references" / "03-gate-receipts.md"
    runs = list(parse_gate_receipt(target.read_text(encoding="utf-8"))[1]) if target.exists() else []
    runs.append(
        GateRun(
            run_id=f"20260928T12000{len(runs)}Z-0000000{len(runs)}",
            repo="code",
            worktree=str(env.repo),
            head="b" * 40,
            tree=tree,
            clean=True,
            tree_changed=False,
            scope=scope,
            command=command,
            names=(),
            exit=exit,
            log_path="/l",
            log_tail="",
            started=f"2026-09-28T12:00:0{len(runs)}Z",
            duration_s=1.0,
        )
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_receipt(owner, runs, created="2026-09-28"), encoding="utf-8", newline="\n")


def start(env) -> Path:
    started = run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=lambda record: None)
    return runs_dir(env.layout, env.path) / f"{started.run_id}.json"


def finish_unrecorded(record: Path, *, exit: int) -> None:
    data = json.loads(record.read_text(encoding="utf-8"))
    data.update(
        runner_started=True,
        recorded=False,
        result={"exit": exit, "tree_changed": False, "duration_s": 1.0, "log_tail": ""},
    )
    record.write_text(json.dumps(data), encoding="utf-8", newline="\n")


def receipt_text(env) -> str:
    return (env.layout.bundle_dir / env.path / "references" / "03-gate-receipts.md").read_text(encoding="utf-8")


def snapshot_dirs(env) -> list[str]:
    roots = [env.layout.root, runs_dir(env.layout, env.path)]
    return sorted(str(p) for root in roots if root.exists() for p in root.rglob("*"))
