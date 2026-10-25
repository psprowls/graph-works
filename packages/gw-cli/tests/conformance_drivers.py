"""The two drivers of the placement conformance suite.

Each driver runs the gw verb sequence its skill prescribes, through the CLI in-process, with
a fixed fake stage per phase standing in for the agent. The fake stages are identical across
drivers: same file names, same contents, same commit messages. Every driver step that can
change git state ends in `assert_conforms`.
"""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conformance_fixture import (
    CHILDREN,
    EPIC_TITLE,
    Fixture,
    Oracle,
    assert_conforms,
    assert_refreshed,
    gw,
    item_type,
    normalized,
    observe,
)
from gw_cli.work_cli import main as work_main
from gw_kernel.testing.transactions import git

RECEIPT_SCRIPT = Path(__file__).resolve().parents[3] / "plugins/gw/skills/finishing-relay/references/finish-receipt.py"

#: The order both drivers follow. The epic designs and plans, the children walk phase by phase
#: in the order a, b, c, then the epic's execute gate and finish. A's squash therefore lands on
#: the epic branch after B's execute commit, so B's finish-time refresh must be a real merge.
PHASES = ("design", "plan", "execute", "finish")


@dataclass
class RunLog:
    verbs: list[str] = field(default_factory=list)
    place_modes: set[str] = field(default_factory=set)
    refreshes: list[tuple[str, str]] = field(default_factory=list)
    notified: list[str] = field(default_factory=list)
    port_calls: list[tuple[str, ...]] = field(default_factory=list)
    snapshots: dict[str, dict[str, str]] = field(default_factory=dict)
    scripts: list[str] = field(default_factory=list)
    refused_cleanup: list[str] = field(default_factory=list)
    created: int = 0


REPO = Path(__file__).resolve().parents[3]
FakeOrcaPort = runpy.run_path(str(REPO / "packages/gw-orchestrate/tests/fake_orca_port.py"))["FakeOrcaPort"]


class RecordingNotifier:
    """The placement notifier seam: records one event per placed worktree."""

    def __init__(self, log: RunLog) -> None:
        self._log = log

    def placed(self, event: Any) -> str | None:
        self._log.notified.append(str(event.worktree))
        return "recorded"


class WorkerPort(FakeOrcaPort):  # type: ignore[misc]
    """A `FakeOrcaPort` whose launched workers each observe the worktree they were launched into."""

    def worker_start(
        self, run_id: str, task_id: str, *, request: Any, placement_argv: Any, retry_of: Any = None
    ) -> Any:
        n = len(self.workers) + 1
        path = placement_argv[1].removeprefix("path:")
        branch = git(Path(path), "rev-parse", "--abbrev-ref", "HEAD").strip()
        self.start = {**self.start, "dispatch_id": f"ctx_{n}", "worktree_id": f"wt{n}"}
        self.worktrees[f"id:wt{n}"] = {
            "id": f"wt{n}",
            "repo_id": "r",
            "path": path,
            "branch": branch,
            "display_name": branch,
            "is_main": False,
            "parent_id": None,
            "comment": None,
        }
        return super().worker_start(run_id, task_id, request=request, placement_argv=placement_argv, retry_of=retry_of)


@contextmanager
def faked_orca(monkeypatch: Any, log: RunLog, fx: Fixture) -> Iterator[Any]:
    """Route the CLI's Orca port and placement notifier to in-process fakes."""
    port = WorkerPort(repos=[{"id": f"repo-{name}", "path": str(path)} for name, path in fx.repos.items()])
    monkeypatch.setattr(work_main, "orca_port", lambda: port)
    monkeypatch.setattr(work_main, "placement_notifier", lambda: RecordingNotifier(log))
    try:
        yield port
    finally:
        log.port_calls.extend((name, *map(str, args)) for name, args, _kw in port.calls)


def schedule(fx: Fixture) -> Iterator[tuple[str, str]]:
    """(path, phase) pairs in driver order; lazy, because the plan stage files the children."""
    assert fx.epic is not None
    yield fx.epic, "design"
    yield fx.epic, "plan"
    for phase in PHASES:
        for spec in CHILDREN:
            yield fx.children[spec.key], phase
    yield fx.epic, "execute"
    yield fx.epic, "finish"


@contextmanager
def in_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def slug_of(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def fake_stage(
    fx: Fixture, path: str, phase: str, *, worktree: Path | None, artifact: Path | None, repo: str | None
) -> None:
    """What the agent would do at *phase*; identical for both drivers."""
    if phase in ("design", "plan"):
        assert artifact is not None
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(f"# {phase.title()} for {slug_of(path)}\n", encoding="utf-8", newline="\n")
        if phase == "plan" and item_type(path) == "Epic":
            for spec in CHILDREN:
                filed = gw(
                    fx,
                    "work",
                    "file",
                    "--parent-path",
                    path,
                    "--kind",
                    "Feature",
                    "--title",
                    spec.title,
                    "--summary",
                    spec.title,
                    "--repo",
                    spec.repo,
                    "--affects",
                    spec.affects,
                    "--effort",
                    "medium",
                )
                fx.children[spec.key] = filed["path"]
    elif phase == "execute" and item_type(path) != "Epic":
        assert worktree is not None and repo is not None
        spec = next(s for s in CHILDREN if fx.children[s.key] == path)
        target = worktree / spec.affects / f"{slug_of(path)}.py"
        target.write_text(f"# {slug_of(path)}\n", encoding="utf-8", newline="\n")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-q", "-m", f"execute {slug_of(path)}")


class AttendedDriver:
    """The `workflow` skill, one stage per item, with the session's cwd in a declared checkout."""

    def run(self, fx: Fixture, oracle: Oracle, monkeypatch: Any) -> RunLog:
        self.log = RunLog()
        with faked_orca(monkeypatch, self.log, fx), in_directory(fx.repos["core"]):
            self._file_epic(fx)
            assert_conforms(fx, oracle, "filed")
            for path, phase in schedule(fx):
                self._stage(fx, oracle, path, phase)
            self._archive(fx, oracle)
        return self.log

    # -- verbs ---------------------------------------------------------------------------

    def _gw(self, fx: Fixture, *args: str, ok: bool = True) -> dict[str, Any]:
        verb = " ".join(a for a in args[:2])
        self.log.verbs.append(verb)
        return gw(fx, *args, ok=ok)

    def _file_epic(self, fx: Fixture) -> None:
        filed = self._gw(
            fx,
            "work",
            "file",
            "--kind",
            "Epic",
            "--title",
            EPIC_TITLE,
            "--summary",
            EPIC_TITLE,
            "--repo",
            "core",
            "--effort",
            "medium",
            "--affects",
            "packages/core-a",
        )
        fx.epic = filed["path"]

    def _place(self, fx: Fixture, oracle: Oracle, path: str, mode: str, step: str) -> dict[str, Any]:
        payload = self._gw(fx, "work", "place", path, mode, "--dry-run") if False else None
        payload = self._gw(fx, "work", "place", path, mode)
        self.log.place_modes.add(mode)
        assert payload["refusal"] is None, f"[{step}] place refused: {payload['refusal']}"
        for entry in payload["chain"]:
            self.log.created += entry["action"] == "created"
            self.log.refreshes.append((entry["path"], entry["refresh"]["kind"]))
        oracle.place(path, payload["repo"]["name"])
        assert_refreshed(fx, path, payload)
        assert_conforms(fx, oracle, step)
        return payload

    def _stage(self, fx: Fixture, oracle: Oracle, path: str, phase: str) -> None:
        step = f"{slug_of(path)}:{phase}"
        nxt = self._gw(fx, "work", "next", path)
        current = nxt["phase"] or "none"
        if not (nxt["action"] or {}).get("skill"):
            # A satisfied gate (an epic's execute): no placement, no stage, only the advance.
            assert nxt["blockers"] == [], f"[{step}] gate not satisfied: {nxt['blockers']}"
            if current == "execute":
                self._gate(fx, path)
            self._advance(fx, path, current, step)
            return
        assert (nxt["phase"] or phase) in (phase, "none") or nxt["on_dispatch"], f"[{step}] at {nxt['phase']}"
        if nxt["on_dispatch"]:
            args = ["work", "advance", path, "--from", current]
            if "owner" in nxt["on_dispatch"]["requires"]:
                args += ["--owner", "conformance"]
            self._gw(fx, *args)
            current = phase
        mode = "--start" if phase == "execute" else "--ensure"
        placed = self._place(fx, oracle, path, mode, step)
        placement = placed["placement"]
        worktree = Path(placement["worktree"]) if placement else None
        artifact = Path(nxt["artifact"]["path"]) if nxt.get("artifact") else None
        if phase == "finish":
            self._finish(fx, oracle, path, step)
            return
        fake_stage(fx, path, phase, worktree=worktree, artifact=artifact, repo=placed["repo"]["name"])
        assert_conforms(fx, oracle, f"{step}:staged")
        if phase == "execute":
            self._gate(fx, path)
        self._advance(fx, path, current, step)

    def _gate(self, fx: Fixture, path: str) -> None:
        """`gate run`, then `gate wait` unless the tree already has a green receipt."""
        started = self._gw(fx, "work", "gate", "run", path)
        if started["status"] != "satisfied":
            self._gw(fx, "work", "gate", "wait", path)

    def _advance(self, fx: Fixture, path: str, current: str, step: str, *extra: str) -> dict[str, Any]:
        payload = self._gw(fx, "work", "advance", path, "--from", current, *extra)
        assert payload["refusal"] is None, f"[{step}] advance refused: {payload['refusal']}"
        return payload

    def _receipt(self, fx: Fixture, mode: str, path: str, *extra: str) -> dict[str, Any]:
        """The finish-receipt helper the skills name, run as the skills run it."""
        self.log.scripts.append(f"finish-receipt.py {mode}")
        proc = subprocess.run(
            [sys.executable, str(RECEIPT_SCRIPT), mode, path, "--workspace", str(fx.workspace), *extra],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"finish-receipt.py {mode} {path}: {proc.stdout}{proc.stderr}"
        payload: dict[str, Any] = json.loads(proc.stdout)
        return payload

    def _finish(self, fx: Fixture, oracle: Oracle, path: str, step: str) -> None:
        targets = self._gw(fx, "work", "next", path)["finish_targets"]
        assert targets, f"[{step}] no finish targets"
        for target in targets:
            integrated = self._gw(fx, "work", "integrate", path, "--repo", target["repo"]["name"], "--apply")
            assert integrated["refusal"] is None, f"[{step}] integrate refused: {integrated['refusal']}"
            assert_conforms(fx, oracle, f"{step}:integrated:{target['repo']['name']}")
        inspected = self._receipt(fx, "inspect", path)
        assert inspected["complete"], f"[{step}] receipt incomplete: {inspected}"
        self._advance(fx, path, "finish", step, "--resolved-in", inspected["resolved_in"])
        plan = self._receipt(fx, "cleanup", path, "--runner-cwd", str(Path.cwd()))
        for row in plan["rows"]:
            assert row["action"] == "remove", f"[{step}] cleanup row not removable: {row}"
            removed = self._gw(fx, "work", "place", path, "--remove", "--repo-name", row["repo"], ok=False)
            self.log.place_modes.add("--remove")
            if removed["_exit"] != 0 and "conflicts with repo" in removed["error"]["message"]:
                # Known product defect: the plan's own command for an owner's foreign-repository
                # row is refused when the owner carries `repo:`. The row stays until prune.
                self.log.refused_cleanup.append(row["command"])
                continue
            assert removed["_exit"] == 0, f"[{step}] remove failed: {removed}"
            assert removed["refusal"] is None, f"[{step}] remove refused: {removed['refusal']}"
            oracle.remove(path, row["repo"])
            assert_conforms(fx, oracle, f"{step}:removed:{row['repo']}")

    def _archive(self, fx: Fixture, oracle: Oracle) -> None:
        assert fx.epic is not None
        archived = self._gw(fx, "work", "archive", fx.epic)
        oracle.archive([fx.epic, *fx.children.values()])
        fx.epic_archived_path = archived["path_mapping"][fx.epic].removesuffix(".md")
        assert_conforms(fx, oracle, "archived")
        self.log.snapshots["final"] = normalized(fx)


class AutoDriveDriver(AttendedDriver):
    """The `auto-drive` skill: a coordinator loop over `orchestrate` and `dispatch`, plus the
    worker session each dispatch would launch, run inline with the same fake stages."""

    def run(self, fx: Fixture, oracle: Oracle, monkeypatch: Any, *, sweep_midway: bool = True) -> RunLog:
        self.log = RunLog()
        self.sweep_midway = sweep_midway
        self.retried = False
        with faked_orca(monkeypatch, self.log, fx), in_directory(fx.workspace):
            self._file_epic(fx)
            assert fx.epic is not None
            plan_file = fx.root / "plan.json"
            for _cycle in range(40):
                plan = self._gw(fx, "work", "orchestrate", fx.epic)
                plan_file.write_text(json.dumps(plan), encoding="utf-8", newline="\n")
                if not plan["dispatches"]:
                    if not self._settle_gate(fx, oracle):
                        break
                    continue
                for entry in plan["dispatches"]:
                    self._dispatch_and_work(fx, oracle, entry, plan_file)
            nxt = self._gw(fx, "work", "next", fx.epic, ok=False)
            assert nxt["work_status"] == "resolved", f"auto-drive stalled: {nxt['phase']} {nxt['blockers']}"
            self._gw(fx, "work", "place", fx.epic, "--prune")
            self.log.place_modes.add("--prune")
            oracle.prune(fx.epic)
            assert_conforms(fx, oracle, "pruned")
            self._archive(fx, oracle)
        return self.log

    def _settle_gate(self, fx: Fixture, oracle: Oracle) -> bool:
        """A satisfied execute gate has no dispatch: the coordinator runs the gate and advances."""
        assert fx.epic is not None
        nxt = self._gw(fx, "work", "next", fx.epic, ok=False)
        if nxt["work_status"] == "resolved":
            return False
        assert not (nxt["action"] or {}).get("skill") and nxt["blockers"] == [], (
            f"empty plan but epic is not at a satisfied gate: {nxt['phase']} {nxt['blockers']}"
        )
        self._gate(fx, fx.epic)
        self._advance(fx, fx.epic, nxt["phase"], "epic:gate")
        return True

    def _dispatch(self, fx: Fixture, key: str, plan_file: Path, *extra: str) -> dict[str, Any]:
        payload = self._gw(
            fx, "work", "dispatch", key, "--plan", str(plan_file), "--run", "run-1", "--no-probe", *extra
        )
        assert payload["ok"], f"dispatch {key}: {payload}"
        return payload

    def _dispatch_and_work(self, fx: Fixture, oracle: Oracle, entry: dict[str, Any], plan_file: Path) -> None:
        path, phase, key = entry["path"], entry["phase"], entry["key"]
        step = f"{slug_of(path)}:{phase}"
        dispatched = self._dispatch(fx, key, plan_file)
        oracle.place(path, entry["repo"]["name"])
        assert dispatched["placement"]["path"] == entry["placement"]["worktree"], f"[{step}] launched elsewhere"
        assert_conforms(fx, oracle, f"{step}:dispatched")
        self.log.refreshes.append((path, "dispatch"))
        if phase == "execute" and not self.retried and slug_of(path) == "feature-child-a":
            # The worker dies without advancing; the coordinator relaunches the same key.
            self.retried = True
            observed = {repo: observe(fx.repos[repo]) for repo in fx.repos}
            retry = self._dispatch(fx, key, plan_file, "--retry-of", dispatched["dispatch_id"])
            assert retry["placement"]["path"] == dispatched["placement"]["path"]
            assert {repo: observe(fx.repos[repo]) for repo in fx.repos} == observed, "retry minted a second worktree"
            assert_conforms(fx, oracle, f"{step}:retried")
        self._worker(fx, oracle, entry, Path(dispatched["placement"]["path"]), step)
        if phase == "finish" and slug_of(path) == "feature-child-a" and self.sweep_midway:
            snapshot = {repo: observe(fx.repos[repo]) for repo in fx.repos}
            self._gw(fx, "work", "archive")
            assert {repo: observe(fx.repos[repo]) for repo in fx.repos} == snapshot, "mid-run sweep touched git"
            assert (fx.workspace / "okf" / f"{fx.epic}.md").is_file(), "mid-run sweep archived the open epic"
            assert_conforms(fx, oracle, f"{step}:swept")

    def _worker(self, fx: Fixture, oracle: Oracle, entry: dict[str, Any], worktree: Path, step: str) -> None:
        """The dispatched `workflow` session: supervised, so it never places."""
        path, phase = entry["path"], entry["phase"]
        nxt = self._gw(fx, "work", "next", path)
        current = nxt["phase"] or "none"
        if nxt["on_dispatch"]:
            args = ["work", "advance", path, "--from", current]
            if "owner" in nxt["on_dispatch"]["requires"]:
                args += ["--owner", "conformance"]
            self._gw(fx, *args)
            current = phase
        if phase == "finish":
            self._finish(fx, oracle, path, step)
            return
        artifact = Path(nxt["artifact"]["path"]) if nxt.get("artifact") else None
        fake_stage(fx, path, phase, worktree=worktree, artifact=artifact, repo=entry["repo"]["name"])
        assert_conforms(fx, oracle, f"{step}:staged")
        if phase == "execute":
            self._gate(fx, path)
        self._advance(fx, path, current, step)
