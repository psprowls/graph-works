"""Shared fixtures-as-functions for the `gw work gate` verb tests."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from graph_works_core.orchestrate.gate import run_gate_run, runs_dir
from graph_works_core.orchestrate.gate_receipts import GateRun, parse_gate_receipt, render_receipt
from graph_works_core.orchestrate.wait import WaitClock

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
TODAY = NOW.date()


def set_max_concurrent(env, value: str) -> None:
    """Write `workflow.gate.max_concurrent: <value>` into the env's committed manifest."""
    text = env.layout.manifest_path.read_text(encoding="utf-8")
    text = re.sub(
        r"^workflow: \{.*\}$",
        "workflow: {dispatch_rules: dispatch.yaml, gate: {max_concurrent: " + value + "}}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    env.layout.manifest_path.write_text(text, encoding="utf-8", newline="\n")


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


def write_receipt(bundle_dir: Path, owner: str, *, worktree, tree, scope="full", command="true", exit=0) -> None:
    target = bundle_dir / owner / "references" / "03-gate-receipts.md"
    runs = list(parse_gate_receipt(target.read_text(encoding="utf-8"))[1]) if target.exists() else []
    runs.append(
        GateRun(
            run_id=f"20260928T12000{len(runs)}Z-0000000{len(runs)}",
            repo="code",
            worktree=str(worktree),
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


def mint_receipt(env, *, tree, owner=None, exit=0, scope="full", command="true") -> None:
    write_receipt(
        env.layout.bundle_dir, owner or env.path, worktree=env.repo, tree=tree, scope=scope, command=command, exit=exit
    )


def gate_ready(layout, declared: Path, item: str, *, tree_of: Path | None = None) -> None:
    """Declare *declared* as repository `code` with a full gate, and mint a green receipt for
    the current tree of *tree_of* (default: *declared*) -- for tests that advance a clean item
    past execute -> finish and are not about the gate."""
    import subprocess

    text = layout.manifest_path.read_text(encoding="utf-8")
    block = f"repositories:\n  code:\n    path: {json.dumps(str(declared))}\n    gate:\n      full: 'true'\n"
    text = re.sub(r"^repositories:.*(?:\n[ ].*)*\n", block, text, count=1, flags=re.MULTILINE)
    layout.manifest_path.write_text(text, encoding="utf-8", newline="\n")
    root = tree_of or declared
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    write_receipt(layout.bundle_dir, item, worktree=root, tree=tree)


def start(env) -> Path:
    started = run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=lambda record: None)
    return runs_dir(env.layout) / f"{started.run_id}.json"


def finish_unrecorded(record: Path, *, exit: int) -> None:
    data = json.loads(record.read_text(encoding="utf-8"))
    data.update(
        runner_started=True,
        recorded=False,
        result={"exit": exit, "tree_changed": False, "duration_s": 1.0, "log_tail": ""},
    )
    record.write_text(json.dumps(data), encoding="utf-8", newline="\n")


def add_item(env, path: str = "work/feature-b", *, status: str = "in-progress") -> str:
    """A second execute-phase item on the same worktree as *env*'s item."""
    page = (
        f"---\ntype: Feature\ntitle: B\nwork_status: {status}\nphase: execute\nowner: someone\nrepo: code\n"
        f"worktree: {env.repo}\naffects:\n  - packages/a\n---\n\nBody.\n"
    )
    (env.layout.bundle_dir / f"{path}.md").write_text(page, encoding="utf-8", newline="\n")
    return path


def record_of(env, run_id: str) -> dict:
    return json.loads((runs_dir(env.layout) / f"{run_id}.json").read_text(encoding="utf-8"))


def receipt_text(env) -> str:
    return (env.layout.bundle_dir / env.path / "references" / "03-gate-receipts.md").read_text(encoding="utf-8")


def snapshot_dirs(env) -> list[str]:
    roots = [env.layout.root, runs_dir(env.layout)]
    return sorted(str(p) for root in roots if root.exists() for p in root.rglob("*"))


def mint_unit_receipt(env, *, owner: str, hashes, repo_wide=None, exit=0) -> None:
    from graph_works_core.orchestrate.gate_receipts import UnitEntry

    target = env.layout.bundle_dir / owner / "references" / "03-gate-receipts.md"
    runs = list(parse_gate_receipt(target.read_text(encoding="utf-8"))[1]) if target.exists() else []
    runs.append(
        GateRun(
            run_id=f"20260928T13000{len(runs)}Z-1000000{len(runs)}",
            repo="code",
            worktree=str(env.repo),
            head="b" * 40,
            tree=env.tree,
            clean=True,
            tree_changed=False,
            scope="full",
            command="units",
            names=(),
            exit=exit,
            log_path="/l",
            log_tail="",
            started=f"2026-09-28T13:00:0{len(runs)}Z",
            duration_s=1.0,
            manifest_hash="d" * 64,
            repo_wide=repo_wide,
            units=tuple(UnitEntry(n, h, ran=True, exit=exit, log_path="/l", duration_s=1.0) for n, h in hashes.items()),
        )
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_receipt(owner, runs, created="2026-09-28"), encoding="utf-8", newline="\n")
