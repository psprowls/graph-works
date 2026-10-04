"""The detached gate runner: `python -m graph_works_core.orchestrate.gate_runner <record>`.

The process's own entry point is the one place in the gate that reads the
clock (`main`); everything below takes it as arguments. It holds
`<record>.lock` for its whole life -- the only liveness signal `wait` reads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from okf_ext.locking import locked

from graph_works_core.orchestrate import gate_git, gate_wake
from graph_works_core.orchestrate.gate import (
    SLOT_POLL,
    is_current,
    limit_or_none,
    pending_waiters,
    queue_head,
    record_requesters,
    settle_waiter,
    slots_dir,
    update_record,
)  # fmt: skip
from graph_works_core.orchestrate.gate_receipts import evaluate, sanitize_tail
from graph_works_core.orchestrate.gate_wake import Wake, WakeOutcome, resume_line
from graph_works_core.workspace import provenance
from graph_works_core.workspace.discovery import resolve
from graph_works_core.workspace.layout import WorkspaceLayout

RETRY_DELAYS = (1.0, 2.0, 4.0)
LOCK_ATTEMPTS = 20
LOCK_PAUSE = 0.05


Run = Callable[[str, Path, Path, Mapping[str, str]], int]


def run_command(command: str, cwd: Path, log_path: Path, env: Mapping[str, str]) -> int:
    """Run *command* through the host shell, appending its output to *log_path*, with *env* over ours."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", command] if sys.platform == "win32" else ["sh", "-c", command]
    with log_path.open("ab") as log:
        return subprocess.run(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
            env={**os.environ, **env},
        ).returncode


def _exit_status(returncode: int) -> int:
    if type(returncode) is not int:
        raise ValueError("command exit is not an integer")
    return 128 - returncode if returncode < 0 else returncode


def _read(record_path: Path) -> dict[str, Any]:
    data = json.loads(record_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{record_path}: pending record is not a mapping")
    return data


def _update(record_path: Path, *, sleep: Callable[[float], None] = time.sleep, **fields: object) -> None:
    update_record(record_path, lambda data: data.update(fields), sleep=sleep)


def _read_log(log_path: Path) -> str:
    try:
        return log_path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _acquire(stack: ExitStack, record_path: Path, sleep: Callable[[float], None]) -> bool:
    """Take the liveness lock, riding out a concurrent `_alive` probe's momentary hold."""
    for attempt in range(LOCK_ATTEMPTS):
        try:
            stack.enter_context(locked(record_path.with_suffix(".lock"), blocking=False))
        except OSError:
            if attempt + 1 < LOCK_ATTEMPTS:
                sleep(LOCK_PAUSE)
            continue
        return True
    return False


def _await_slot(
    stack: ExitStack, record_path: Path, slots: Path, limit: int, *,
    wall: Callable[[], datetime], sleep: Callable[[float], None],
) -> None:  # fmt: skip
    """Take a slot lock, trying only while this record heads the queue. The OS frees it on death."""
    while True:
        if queue_head(record_path.parent, wall()) == record_path:
            for index in range(limit):
                try:
                    stack.enter_context(locked(slots / f"slot-{index}.lock", blocking=False))
                except OSError:
                    continue
                return
        sleep(SLOT_POLL)


def _satisfied_by(layout: WorkspaceLayout, record: Mapping[str, Any]) -> str | None:
    """The run id that now satisfies this full-scope, non-fresh record's key, through `evaluate`."""
    if record.get("scope") != "full" or record.get("fresh"):
        return None
    hashes = {unit["name"]: unit["hash"] for unit in record.get("units") or ()}
    if not hashes:
        return None
    wide = record.get("repo_wide")
    evaluation = evaluate(
        layout,
        repo=record["repo"],
        tree=record["tree"],
        full_command=record.get("full_command") or record["command"],
        repo_wide_command=wide.get("command") if isinstance(wide, dict) else None,
        hashes=hashes,
    )
    return evaluation.evidence.run_id if evaluation.satisfied and evaluation.evidence is not None else None


def _recheck(layout: WorkspaceLayout, record: Mapping[str, Any]) -> dict[str, object] | None:
    """After a queue wait: settle without running when satisfied meanwhile or the tree moved."""
    satisfied_by = _satisfied_by(layout, record)
    if satisfied_by is not None:
        return {"result": {"exit": 0, "satisfied_by": satisfied_by}, "recorded": True}
    executable = provenance.gate_git(layout)
    try:
        if isinstance(executable, provenance.GitFailure):
            raise gate_git.GitUnavailable(executable.detail)
        snap = gate_git.snapshot(Path(record["worktree"]), git=executable)
    except gate_git.GitUnavailable:
        return None  # run anyway; the post-run snapshot marks it tree_changed
    if snap.tree != record["tree"] or snap.dirty:
        return {"result": {"exit": None, "error": "tree changed while queued"}}
    return None


def unit_log_path(log_path: Path, unit: str) -> Path:
    """The log for one unit, beside the run's aggregate log."""
    return log_path.with_suffix("") / f"{unit}.log"


def _note(log_path: Path, lock: threading.Lock, line: str) -> None:
    with lock, log_path.open("a", encoding="utf-8", newline="\n") as log:
        log.write(line + "\n")


def _run_v2(
    record: dict[str, Any], worktree: Path, log_path: Path, run: Run, monotonic: Callable[[], float]
) -> tuple[dict[str, Any], str | None]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    planned = [unit for unit in record["units"] if unit["planned"]]
    outcome: dict[str, Any] = {"setup": None, "repo_wide": None, "units": {}}
    failure_log: str | None = None
    wide = record.get("repo_wide")

    def timed(command: str, log: Path, env: Mapping[str, str]) -> tuple[int, float]:
        began = monotonic()
        code = _exit_status(run(command, worktree, log, env))
        return code, round(monotonic() - began, 3)

    if record.get("setup") and (planned or (wide is not None and wide["planned"])):
        code, duration = timed(record["setup"], log_path, {})
        outcome["setup"] = {"exit": code, "duration_s": duration}
        if code != 0:
            # Capture before later unit progress can evict this failure from the tail.
            failure_log = _read_log(log_path)
            if wide is not None and wide["planned"]:
                outcome["repo_wide"] = {"exit": code, "duration_s": 0.0}
            for unit in planned:
                outcome["units"][unit["name"]] = {"exit": code, "duration_s": 0.0, "log_path": str(log_path)}
            return outcome, failure_log
    if wide is not None and wide["planned"]:
        code, duration = timed(wide["command"], log_path, {})
        outcome["repo_wide"] = {"exit": code, "duration_s": duration}
        if code != 0:
            # Capture before later unit progress can evict this failure from the tail.
            failure_log = _read_log(log_path)

    def one(unit: dict[str, Any]) -> tuple[str, int, float, Path]:
        log = unit_log_path(log_path, unit["name"])
        _note(log_path, lock, f"[{unit['name']}] started")
        code, duration = timed(unit["command"], log, unit.get("env") or {})
        _note(log_path, lock, f"[{unit['name']}] finished exit={code} in {duration:.0f}s")
        return unit["name"], code, duration, log

    with ThreadPoolExecutor(max_workers=record["jobs"]) as pool:
        # map preserves manifest order, so the first failing unit's tail is deterministic.
        for name, code, duration, log in pool.map(one, planned):
            outcome["units"][name] = {"exit": code, "duration_s": duration, "log_path": str(log)}
            if code != 0 and failure_log is None:
                failure_log = _read_log(log)
    return outcome, failure_log


def wake_waiters(
    record_path: Path, *, wake: Wake, wall: Callable[[], datetime], sleep: Callable[[float], None]
) -> None:
    """Type the resume line into each unsettled waiter's terminal and record the outcome.

    Called with the liveness lock still held, after the result and every receipt.
    """
    data, waiting = pending_waiters(record_path)
    result = data.get("result") if data is not None else None
    if data is None or not isinstance(result, dict):
        return
    for entry in waiting:
        path, terminal = entry.get("path"), entry.get("terminal")
        if not isinstance(path, str) or not isinstance(terminal, str):
            continue
        try:
            _wake_one(record_path, data, path, terminal, result, wake=wake, wall=wall, sleep=sleep)
        except Exception:
            continue


def _wake_one(
    record_path: Path,
    data: dict[str, Any],
    path: str,
    terminal: str,
    result: dict[str, Any],
    *,
    wake: Wake,
    wall: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> None:
    line = resume_line(str(data.get("run_id")), path, result.get("exit"))
    outcome = WakeOutcome("failed", "not attempted")
    for delay in RETRY_DELAYS:
        outcome = wake(terminal, line)
        if outcome.status == "delivered":
            break
        sleep(delay)
    woken = {"status": outcome.status, "at": f"{wall().astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}", "detail": outcome.detail}
    settle_waiter(record_path, path, terminal, woken, sleep=sleep)


def _wake_safely(
    record_path: Path, *, wake: Wake, wall: Callable[[], datetime], sleep: Callable[[float], None]
) -> None:
    """A wake failure never touches the gate: the result, the receipts and the exit code stand."""
    try:
        wake_waiters(record_path, wake=wake, wall=wall, sleep=sleep)
    except Exception:
        return


def execute(
    record_path: Path,
    *,
    wall: Callable[[], datetime],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    run: Run = run_command,
    wake: Wake = gate_wake.wake,
) -> int:
    """Run the recorded gate command, write the result, record it on every requester (retrying), then wake waiters."""
    with ExitStack() as stack:
        if not _acquire(stack, record_path, sleep):
            return 1  # another runner already owns this record
        try:
            return _run_locked(stack, record_path, wall=wall, monotonic=monotonic, sleep=sleep, run=run)
        finally:
            _wake_safely(record_path, wake=wake, wall=wall, sleep=sleep)


def _run_locked(
    stack: ExitStack,
    record_path: Path,
    *,
    wall: Callable[[], datetime],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    run: Run,
) -> int:
    record = _read(record_path)
    if not is_current(record):
        return 1  # not a run-index record: every reader ignores it, so it is never run
    if record.get("result") is not None:
        return 0  # already finished (or marked failed): never run twice
    _update(record_path, sleep=sleep, runner_started=True)
    worktree = Path(record["worktree"])
    log_path = Path(record["log_path"])
    layout = resolve(workspace=record["workspace"])
    limit = limit_or_none(layout)
    if limit is not None:
        # The slot is held by `stack` until this returns: through the command and the recording.
        _await_slot(stack, record_path, slots_dir(layout), limit, wall=wall, sleep=sleep)
        record = _read(record_path)  # a fresh requester may have joined while this waited
        settled = _recheck(layout, record)
        if settled is not None:
            _update(record_path, sleep=sleep, **settled)
            return 0
    _update(record_path, sleep=sleep, status="running")
    began = monotonic()
    if record.get("version") == 2 and not record.get("implicit"):
        v2, failure_log = _run_v2(record, worktree, log_path, run, monotonic)
        overall = 1 if failure_log is not None else 0
    else:
        v2 = None
        overall = _exit_status(run(record["command"], worktree, log_path, {}))
        failure_log = None
    duration = round(monotonic() - began, 3)
    try:
        executable = provenance.gate_git(layout)
        if isinstance(executable, provenance.GitFailure):
            raise gate_git.GitUnavailable(executable.detail)
        after = gate_git.snapshot(worktree, git=executable)
        changed = after.tree != record["tree"] or bool(after.dirty)
    except gate_git.GitUnavailable:
        changed = True
    tail = sanitize_tail(failure_log if failure_log is not None else _read_log(log_path))
    result = {"exit": overall, "tree_changed": changed, "duration_s": duration, "log_tail": tail}
    if v2 is not None:
        result.update(v2)
    _update(record_path, sleep=sleep, result=result)
    record_requesters(layout, record_path, today=wall().date(), delays=RETRY_DELAYS, sleep=sleep)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m graph_works_core.orchestrate.gate_runner <pending-record>", file=sys.stderr)
        return 2
    return execute(Path(args[0]), wall=lambda: datetime.now(UTC), monotonic=time.monotonic, sleep=time.sleep)


if __name__ == "__main__":
    raise SystemExit(main())
