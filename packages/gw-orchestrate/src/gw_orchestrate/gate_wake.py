"""Wake a parked gate waiter: type the resume line into its Orca terminal.

Core does not import workflow-orca (band rules), so this is one `subprocess.run`
with a timeout. `<orca>` is `ORCA_CLI_COMMAND` when set, else `orca` on PATH: the
subset of `plugins/gw/hooks/dispatch-prompt-guard.py`'s resolution that applies
to a runner which inherited a worker's environment. A waiter is delivered on
`accepted: true`; `turn_started` is detail only, because a busy worker's send
reports `input_accepted` alone and still runs the line (D-004).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

RESUME_LINE = (
    "Gate run {run_id} for {path} finished (exit {exit}). "
    "Run gw work gate wait {path} --run {run_id}, then continue the stage."
)
SEND_TIMEOUT_S = 30.0
WAIT_SUBMIT_S = 5
_DETAIL_LIMIT = 300


@dataclass(frozen=True, slots=True)
class WakeOutcome:
    status: Literal["delivered", "failed"]
    detail: str


Wake = Callable[[str, str], WakeOutcome]


def resume_line(run_id: str, path: str, exit_code: object) -> str:
    shown = str(exit_code) if isinstance(exit_code, int) and not isinstance(exit_code, bool) else "none"
    return RESUME_LINE.format(run_id=run_id, path=path, exit=shown)


def orca_command(environ: Mapping[str, str]) -> str:
    return environ.get("ORCA_CLI_COMMAND") or "orca"


def send_argv(orca: str, terminal: str, line: str) -> list[str]:
    return [
        orca, "terminal", "send", "--terminal", terminal, "--text", line,
        "--enter", "--wait-submit", str(WAIT_SUBMIT_S), "--json",
    ]  # fmt: skip


def _send_block(stdout: str) -> dict[str, Any] | None:
    try:
        body = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("ok") is not True:
        return None
    result = body.get("result")
    send = result.get("send") if isinstance(result, dict) else None
    return send if isinstance(send, dict) else None


def _stages(send: Mapping[str, Any]) -> str:
    prompt = send.get("prompt")
    stages = prompt.get("stages") if isinstance(prompt, dict) else None
    if not isinstance(stages, list):
        return "accepted"
    return ",".join(stage for stage in stages if isinstance(stage, str)) or "accepted"


def wake(
    terminal: str, line: str, *, environ: Mapping[str, str] | None = None, timeout: float = SEND_TIMEOUT_S
) -> WakeOutcome:
    """One `orca terminal send`. Never raises: every failure is a `failed` outcome."""
    argv = send_argv(orca_command(os.environ if environ is None else environ), terminal, line)
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return WakeOutcome("failed", str(exc)[-_DETAIL_LIMIT:])
    send = _send_block(done.stdout or "")
    if done.returncode == 0 and send is not None and send.get("accepted") is True:
        return WakeOutcome("delivered", _stages(send))
    text = (done.stderr or done.stdout or "").strip()
    return WakeOutcome("failed", f"exit {done.returncode}: {text}"[-_DETAIL_LIMIT:])
