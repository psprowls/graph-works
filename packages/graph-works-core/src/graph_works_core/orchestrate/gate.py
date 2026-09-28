"""`gw work gate run | wait | check`: gw runs the repository gate and records it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from okf_ext.locking import locked
from okf_io import load_bundle
from work_tracker_okf.affects import code_affects
from work_tracker_okf.items import IGNORE, WorkItem, load_items

from graph_works_core.orchestrate import gate_git
from graph_works_core.orchestrate.gate_receipts import (
    GateMatch,
    GateRun,
    GateScope,
    any_for_tree,
    find_satisfying,
    record_gate_run,
)
from graph_works_core.orchestrate.wait import WaitClock
from graph_works_core.workspace import provenance
from graph_works_core.workspace.commits import item_stem
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.gate_config import ScopedGate, repo_gate
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_item_repo

GateRefusal = Literal[
    "unknown-item",
    "no-worktree",
    "foreign-worktree",
    "git-unavailable",
    "dirty-tree",
    "no-gate-configured",
    "no-scoped-gate",
    "scope-uncovered",
    "runner-failed",
    "no-run",
]


@dataclass(frozen=True, slots=True)
class ScopedCommand:
    command: str | None
    names: tuple[str, ...]
    uncovered: tuple[str, ...]


def expand_scoped(scoped: ScopedGate, code_paths: Sequence[str]) -> ScopedCommand:
    """One `&&`-joined command over the distinct roots the paths fall under.

    A scoped run never covers less than the item touches: any unmatched path,
    or no path at all, refuses.
    """
    pattern = PurePosixPath(scoped.roots).parts
    names: set[str] = set()
    uncovered: list[str] = []
    for path in code_paths:
        parts = PurePosixPath(path).parts
        if len(parts) >= len(pattern) and all(fnmatchcase(p, g) for p, g in zip(parts, pattern, strict=False)):
            names.add(parts[len(pattern) - 1])
        else:
            uncovered.append(path)
    if uncovered or not names:
        return ScopedCommand(None, tuple(sorted(names)), tuple(uncovered))
    ordered = tuple(sorted(names))
    return ScopedCommand(" && ".join(scoped.command.replace("{name}", n) for n in ordered), ordered, ())


@dataclass(frozen=True, slots=True)
class GateTarget:
    """What one request resolved to."""

    path: str
    repo: str
    repo_path: Path
    worktree: Path
    head: str
    tree: str


@dataclass(frozen=True, slots=True)
class GateRunResult:
    status: Literal["satisfied", "running", "started"] | None
    refusal: GateRefusal | None
    detail: str
    run_id: str | None
    match: GateMatch | None
    command: str | None
    names: tuple[str, ...]
    log_path: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GateCheckResult:
    status: Literal["satisfied", "unsatisfied"] | None
    refusal: GateRefusal | None
    detail: str
    match: GateMatch | None
    tree: str | None
    warnings: tuple[str, ...]


Spawn = Callable[[Path], None]

WINDOWS_DETACHED_FLAGS = 0x00000200 | 0x00000008  # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
RUNNER_START_TIMEOUT = 10.0
REPLACE_ATTEMPTS = 5
REPLACE_PAUSE = 0.05
RUN_ID_PATTERN = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{8}")
START_GRACE = timedelta(seconds=30)


def runs_dir(layout: WorkspaceLayout, path: str) -> Path:
    return layout.cache_dir / "gate-runs" / hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def new_run_id(now: datetime, token: str) -> str:
    if re.fullmatch(r"[0-9a-f]{8}", token) is None:
        raise ValueError("token must be 8 lowercase hex characters")
    return f"{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{token}"


def _alive(record_path: Path) -> bool:
    """Whether the runner still holds its lock; the only liveness probe (no pid signals)."""
    try:
        with locked(record_path.with_suffix(".lock"), blocking=False):
            return False
    except OSError:
        return True


def _parse_started(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _read_live(record_path: Path, now: datetime) -> tuple[dict[str, Any] | None, bool]:
    """The record and whether its runner is alive, probed in the one safe order.

    Liveness is probed BEFORE the record is read. The runner writes `result` before it
    releases its lock, so a free lock means the record read afterwards is final; the
    reverse order can see "no result" and then "no lock" for a run that just finished.
    Alive means the runner holds its lock, or it was spawned moments ago and has not
    taken it yet (the grace; a spawn-failed record carries a `result`, so never qualifies).
    """
    held = _alive(record_path)
    data = _read_record(record_path)
    if held:
        return data, True
    if data is None or data.get("runner_started") or data.get("result") is not None:
        return data, False
    started = _parse_started(data.get("started"))
    return data, started is not None and timedelta(0) <= now - started <= START_GRACE


def _check_refusal(reason: GateRefusal, detail: str, *, tree: str | None = None) -> GateCheckResult:
    status: Literal["unsatisfied"] | None = "unsatisfied" if reason == "dirty-tree" else None
    return GateCheckResult(status, reason, detail, None, tree, ())


def _run_refusal(reason: GateRefusal, detail: str, warnings: tuple[str, ...] = ()) -> GateRunResult:
    return GateRunResult(None, reason, detail, None, None, None, (), None, warnings)


def _git_detail(failure: provenance.GitFailure) -> str:
    return f"git unavailable ({failure.cause}): {failure.detail}"


def _resolve(
    layout: WorkspaceLayout, path: str, worktree: Path | None
) -> tuple[GateTarget, WorkItem] | GateCheckResult:
    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    by_path = {item.path: item for item in items}
    item = by_path.get(path)
    if item is None:
        return _check_refusal("unknown-item", f"{path}: no such work item")
    try:
        chosen = resolve_item_repo(layout, item, by_path)
    except WorkspaceError as exc:
        return _check_refusal("no-gate-configured", str(exc))
    if chosen.name is None or chosen.path is None:
        return _check_refusal("no-gate-configured", chosen.note or f"{path}: no code repository resolved")
    raw = worktree if worktree is not None else (Path(item.worktree) if item.worktree else None)
    if raw is None:
        return _check_refusal("no-worktree", f"{path} has no `worktree:` stamp; pass --worktree")
    executable = provenance.gate_git(layout)
    if isinstance(executable, provenance.GitFailure):
        return _check_refusal("git-unavailable", _git_detail(executable))
    try:
        if not raw.is_dir() or not gate_git.same_repository(raw, chosen.path, git=executable):
            return _check_refusal("foreign-worktree", f"{raw} is not a checkout of {chosen.name} ({chosen.path})")
        snap = gate_git.snapshot(raw, git=executable)
    except gate_git.GitUnavailable as exc:
        return _check_refusal("git-unavailable", str(exc))
    if snap.dirty:
        return _check_refusal("dirty-tree", "uncommitted changes: " + ", ".join(snap.dirty), tree=snap.tree)
    return GateTarget(path, chosen.name, chosen.path, raw.resolve(), snap.head, snap.tree), item


def resolve_target(layout: WorkspaceLayout, path: str, *, worktree: Path | None) -> GateTarget | GateCheckResult:
    resolved = _resolve(layout, path, worktree)
    return resolved if isinstance(resolved, GateCheckResult) else resolved[0]


def _existing_run(directory: Path, request: dict[str, object], now: datetime) -> str | None:
    for record_path in sorted(directory.glob("*.json")):
        data, live = _read_live(record_path, now)
        if data is None or data.get("result") is not None or not live:
            continue
        if all(data.get(key) == value for key, value in request.items()):
            run_id = data.get("run_id")
            if isinstance(run_id, str):
                return run_id
    return None


def run_gate_run(
    layout: WorkspaceLayout,
    path: str,
    *,
    scope: GateScope = "full",
    worktree: Path | None = None,
    now: datetime,
    token: str,
    spawn: Spawn,
) -> GateRunResult:
    """Start (or join, or short-circuit) one gate run for the item's worktree tree."""
    resolved = _resolve(layout, path, worktree)
    if isinstance(resolved, GateCheckResult):
        return _run_refusal(resolved.refusal or "no-run", resolved.detail)
    target, item = resolved
    try:
        gate = repo_gate(layout, target.repo)
    except WorkspaceError as exc:
        return _run_refusal("no-gate-configured", str(exc))
    if gate.full is None:
        return _run_refusal("no-gate-configured", f"set repositories.{target.repo}.gate.full in {layout.manifest_path}")
    lookup = find_satisfying(layout.bundle_dir, repo=target.repo, tree=target.tree, command=gate.full)
    if lookup.match is not None:
        return GateRunResult("satisfied", None, "", None, lookup.match, gate.full, (), None, lookup.warnings)
    command = gate.full
    names: tuple[str, ...] = ()
    if scope == "scoped":
        if gate.scoped is None:
            return _run_refusal(
                "no-scoped-gate",
                f"set repositories.{target.repo}.gate.scoped in {layout.manifest_path}",
                lookup.warnings,
            )
        paths = code_affects(item.affects)
        expanded = expand_scoped(gate.scoped, paths)
        if expanded.command is None:
            if expanded.uncovered:
                detail = f"no root in {gate.scoped.roots} matches: {', '.join(expanded.uncovered)}; run the full gate"
            else:
                detail = "no code `affects` to scope; run the full gate"
            return _run_refusal("scope-uncovered", detail, lookup.warnings)
        command, names = expanded.command, expanded.names
    run_id = new_run_id(now, token)
    executable = provenance.gate_git(layout)
    if isinstance(executable, provenance.GitFailure):
        return _run_refusal("git-unavailable", _git_detail(executable), lookup.warnings)
    try:
        log_dir = gate_git.gate_log_dir(target.worktree, item_stem(path), git=executable)
    except gate_git.GitUnavailable as exc:
        return _run_refusal("git-unavailable", str(exc), lookup.warnings)
    log_path = str(log_dir / f"{run_id}.log")
    directory = runs_dir(layout, path)
    directory.mkdir(parents=True, exist_ok=True)
    request: dict[str, object] = {"repo": target.repo, "tree": target.tree, "scope": scope, "command": command}
    record = {
        "schema": "gw-gate-run",
        "version": 1,
        "workspace": str(layout.root),
        "owner": path,
        "run_id": run_id,
        "repo": target.repo,
        "worktree": str(target.worktree),
        "head": target.head,
        "tree": target.tree,
        "scope": scope,
        "command": command,
        "names": list(names),
        "log_path": log_path,
        "started": f"{now.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}",
        "runner_started": False,
        "result": None,
        "recorded": False,
    }
    record_path = directory / f"{run_id}.json"
    with locked(directory / ".dir.lock"):
        joined = _existing_run(directory, request, now)
        if joined is not None:
            return GateRunResult("running", None, "", joined, None, command, names, log_path, lookup.warnings)
        write_record(record_path, record, exclusive=True)
    try:
        spawn(record_path)
    except OSError as exc:
        held = _alive(record_path)
        current = _read_record(record_path) or record
        if held or current.get("result") is not None:
            # slow to start, not failed: the runner owns the record now
            return GateRunResult("started", None, "", run_id, None, command, names, log_path, lookup.warnings)
        current["result"] = {"exit": None, "error": str(exc)}
        write_record(record_path, current)
        return GateRunResult(None, "runner-failed", str(exc), run_id, None, command, names, log_path, lookup.warnings)
    return GateRunResult("started", None, "", run_id, None, command, names, log_path, lookup.warnings)


def run_gate_check(layout: WorkspaceLayout, path: str, *, worktree: Path | None = None) -> GateCheckResult:
    """Whether the worktree's current tree already has a satisfying receipt. Writes nothing."""
    resolved = _resolve(layout, path, worktree)
    if isinstance(resolved, GateCheckResult):
        if resolved.refusal == "dirty-tree":
            return GateCheckResult("unsatisfied", None, "dirty", None, resolved.tree, ())
        return resolved
    target = resolved[0]
    try:
        gate = repo_gate(layout, target.repo)
    except WorkspaceError as exc:
        return _check_refusal("no-gate-configured", str(exc))
    if gate.full is None:
        return _check_refusal(
            "no-gate-configured", f"set repositories.{target.repo}.gate.full in {layout.manifest_path}"
        )
    lookup = find_satisfying(layout.bundle_dir, repo=target.repo, tree=target.tree, command=gate.full)
    if lookup.match is not None:
        return GateCheckResult("satisfied", None, "", lookup.match, target.tree, lookup.warnings)
    seen = any_for_tree(layout.bundle_dir, repo=target.repo, tree=target.tree)
    detail = "only red/scoped/stale receipts" if seen else "no receipt"
    return GateCheckResult("unsatisfied", None, detail, None, target.tree, lookup.warnings)


def gate_run_from_record(record: dict[str, Any], result: dict[str, Any]) -> GateRun:
    """The receipt entry a finished pending record stands for."""
    return GateRun(
        run_id=record["run_id"],
        repo=record["repo"],
        worktree=record["worktree"],
        head=record["head"],
        tree=record["tree"],
        clean=True,
        tree_changed=bool(result["tree_changed"]),
        scope=record["scope"],
        command=record["command"],
        names=tuple(record.get("names", ())),
        exit=int(result["exit"]),
        log_path=record["log_path"],
        log_tail=str(result["log_tail"]),
        started=record["started"],
        duration_s=float(result["duration_s"]),
    )


def spawn_runner(record_path: Path) -> None:
    """Start the detached runner, then wait for it to confirm it holds its lock."""
    record = json.loads(record_path.read_text(encoding="utf-8"))
    log_path = Path(record["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-m", "graph_works_core.orchestrate.gate_runner", str(record_path)]
    with log_path.open("ab") as log:
        options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": subprocess.STDOUT,
            "cwd": record["worktree"],
            "close_fds": True,
        }
        if sys.platform == "win32":
            options["creationflags"] = WINDOWS_DETACHED_FLAGS
        else:
            options["start_new_session"] = True
        subprocess.Popen(argv, **options)
    _await_runner_started(record_path, RUNNER_START_TIMEOUT)


def _await_runner_started(record_path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = _read_record(record_path)
        if data is not None and data.get("runner_started"):
            return
        time.sleep(0.05)
    raise OSError(f"gate runner did not start within {timeout:.0f}s ({record_path})")


@dataclass(frozen=True, slots=True)
class GateWaitResult:
    status: Literal["finished", "running", "orphaned"] | None
    refusal: GateRefusal | None
    detail: str
    run_id: str | None
    exit: int | None
    recorded: bool
    log_path: str | None
    log_tail: str | None
    receipt_path: str | None


def _wait_refusal(detail: str) -> GateWaitResult:
    return GateWaitResult(None, "no-run", detail, None, None, False, None, None, None)


def _read_record(record_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _choose_record(directory: Path, run_id: str | None) -> Path | None:
    if run_id is not None:
        if RUN_ID_PATTERN.fullmatch(run_id) is None:
            return None
        chosen = directory / f"{run_id}.json"
        return chosen if chosen.is_file() else None
    ranked: list[tuple[str, str, Path]] = []
    for candidate in directory.glob("*.json"):
        data = _read_record(candidate)
        if data is not None:
            ranked.append((str(data.get("started", "")), str(data.get("run_id", "")), candidate))
    return max(ranked)[2] if ranked else None


def write_record(
    record_path: Path,
    data: dict[str, Any],
    *,
    exclusive: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Write a pending record atomically (temp + replace); a Windows reader may briefly block the replace."""
    if exclusive and record_path.exists():
        raise FileExistsError(record_path)
    temp = record_path.with_name(f".{record_path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                temp.replace(record_path)
                return
            except PermissionError:
                if attempt + 1 == REPLACE_ATTEMPTS:
                    raise
                sleep(REPLACE_PAUSE * (attempt + 1))
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _mark_recorded(record_path: Path) -> None:
    data = _read_record(record_path)
    if data is not None:
        data["recorded"] = True
        write_record(record_path, data)


def run_gate_wait(
    layout: WorkspaceLayout,
    path: str,
    *,
    run_id: str | None,
    timeout: float,
    clock: WaitClock,
    sleep: Callable[[float], None],
    today: date,
) -> GateWaitResult:
    """Report a gate run's outcome, waiting up to *timeout* seconds. Never starts a run."""
    record_path = _choose_record(runs_dir(layout, path), run_id)
    if record_path is None:
        return _wait_refusal(f"{path}: no gate run to wait for")
    deadline = clock.monotonic() + timeout
    while True:
        data, alive = _read_live(record_path, clock.wall())
        if data is None:
            return _wait_refusal(f"{record_path}: unreadable pending record")
        rid = str(data.get("run_id"))
        log_path = data.get("log_path")
        log_path = log_path if isinstance(log_path, str) else None
        result = data.get("result")
        if isinstance(result, dict):
            code = result.get("exit")
            if not isinstance(code, int):
                return GateWaitResult(
                    "orphaned", None, str(result.get("error", "runner failed")), rid, None, False, log_path, None, None
                )
            recorded = bool(data.get("recorded"))
            receipt_path = f"{path}/references/03-gate-receipts.md" if recorded else None
            detail = ""
            if not recorded and not alive:
                outcome = record_gate_run(layout, path, gate_run_from_record(data, result), today=today)
                if outcome.refusal is None:
                    _mark_recorded(record_path)
                    recorded, receipt_path = True, outcome.receipt_path
                else:
                    detail = outcome.detail or outcome.refusal
            if recorded or not alive:
                tail = result.get("log_tail")
                return GateWaitResult(
                    "finished", None, detail, rid, code, recorded, log_path,
                    tail if isinstance(tail, str) else None, receipt_path,
                )  # fmt: skip
        elif not alive:
            return GateWaitResult("orphaned", None, "runner is not alive and left no result", rid, None, False,
                                  log_path, None, None)  # fmt: skip
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            return GateWaitResult("running", None, "", rid, None, False, log_path, None, None)
        sleep(min(2.0, remaining))
