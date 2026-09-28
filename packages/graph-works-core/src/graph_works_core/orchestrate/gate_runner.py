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
import tempfile
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from okf_ext.locking import locked

from graph_works_core.orchestrate import gate_git
from graph_works_core.orchestrate.gate import gate_run_from_record
from graph_works_core.orchestrate.gate_receipts import record_gate_run, sanitize_tail
from graph_works_core.workspace import provenance
from graph_works_core.workspace.discovery import resolve

RETRY_DELAYS = (1.0, 2.0, 4.0)
LOCK_ATTEMPTS = 20
LOCK_PAUSE = 0.05


def run_command(command: str, cwd: Path, log_path: Path) -> int:
    """Run *command* through the host shell, appending its output to *log_path*."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", command] if sys.platform == "win32" else ["sh", "-c", command]
    with log_path.open("ab") as log:
        return subprocess.run(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, check=False
        ).returncode


def _exit_status(returncode: int) -> int:
    return 128 - returncode if returncode < 0 else returncode


def _read(record_path: Path) -> dict[str, Any]:
    data = json.loads(record_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{record_path}: pending record is not a mapping")
    return data


def _update(record_path: Path, **fields: object) -> None:
    data = _read(record_path)
    data.update(fields)
    handle, temp = tempfile.mkstemp(dir=record_path.parent, prefix=".record-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(data, indent=2) + "\n")
        Path(temp).replace(record_path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


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


def execute(
    record_path: Path,
    *,
    wall: Callable[[], datetime],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    run: Callable[[str, Path, Path], int] = run_command,
) -> int:
    """Run the recorded gate command, write the result, and record the receipt (retrying)."""
    with ExitStack() as stack:
        if not _acquire(stack, record_path, sleep):
            return 1  # another runner already owns this record
        record = _read(record_path)
        _update(record_path, runner_started=True)
        worktree = Path(record["worktree"])
        log_path = Path(record["log_path"])
        layout = resolve(workspace=record["workspace"])
        began = monotonic()
        returncode = run(record["command"], worktree, log_path)
        duration = round(monotonic() - began, 3)
        try:
            executable = provenance.gate_git(layout)
            if isinstance(executable, provenance.GitFailure):
                raise gate_git.GitUnavailable(executable.detail)
            after = gate_git.snapshot(worktree, git=executable)
            changed = after.tree != record["tree"] or bool(after.dirty)
        except gate_git.GitUnavailable:
            changed = True
        tail = sanitize_tail(_read_log(log_path))
        result = {"exit": _exit_status(returncode), "tree_changed": changed, "duration_s": duration, "log_tail": tail}
        _update(record_path, result=result)
        entry = gate_run_from_record(record, result)
        for delay in RETRY_DELAYS:
            outcome = record_gate_run(layout, record["owner"], entry, today=wall().date())
            if outcome.refusal is None:
                _update(record_path, recorded=True)
                break
            if outcome.refusal == "owner-terminal":
                break
            sleep(delay)
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m graph_works_core.orchestrate.gate_runner <pending-record>", file=sys.stderr)
        return 2
    return execute(Path(args[0]), wall=lambda: datetime.now(UTC), monotonic=time.monotonic, sleep=time.sleep)


if __name__ == "__main__":
    raise SystemExit(main())
