"""The transport. The only module in this package that runs a process.

Two streams, never merged: `check --wait` writes a JSON keepalive line to
stderr every 15 seconds while the real response goes to stdout, so a runner
that merged them would hand this module output it cannot parse. `OrcaResult`
carries both fields for exactly that reason.

Every Orca response is an `{"id", "ok", "result"}` envelope. `unwrap` is the
single place that opens it, so a failure — a non-zero exit, an `ok: false`, or
stdout that is not JSON at all — becomes one error class rather than three
shapes a caller has to know apart.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from subagents_io.backend import BackendError


@dataclass(frozen=True)
class OrcaResult:
    """One completed `orca` invocation, with its streams kept apart."""

    returncode: int
    stdout: str
    stderr: str


class OrcaCliError(BackendError):
    """An `orca` invocation that did not produce a usable result.

    One vendor error class beneath the protocol's base, so a coordinator
    catching `BackendError` catches this too without importing anything from
    this package.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        returncode: int,
        stderr: str,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        self.argv: tuple[str, ...] = tuple(argv)
        self.returncode = returncode
        self.stderr = stderr
        self.code = code
        detail = message or stderr.strip() or "(no detail)"
        prefix = f"{code}: " if code else ""
        super().__init__(f"{' '.join(self.argv)} failed (exit {returncode}): {prefix}{detail}")


def _subprocess_run(argv: Sequence[str]) -> OrcaResult:
    """The default runner. `check=False`: a non-zero exit is `unwrap`'s to report."""
    completed = subprocess.run(list(argv), capture_output=True, text=True, check=False)
    return OrcaResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def unwrap(argv: Sequence[str], result: OrcaResult) -> dict[str, Any]:
    """Open the envelope, or raise `OrcaCliError`."""
    if result.returncode != 0 and not result.stdout.strip():
        raise OrcaCliError(argv, returncode=result.returncode, stderr=result.stderr)
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise OrcaCliError(
            argv,
            returncode=result.returncode,
            stderr=result.stderr,
            message=f"stdout was not JSON: {exc}",
        ) from exc
    if not isinstance(body, dict):
        raise OrcaCliError(
            argv,
            returncode=result.returncode,
            stderr=result.stderr,
            message="envelope was not an object",
        )
    if not body.get("ok"):
        error = body.get("error") or {}
        raise OrcaCliError(
            argv,
            returncode=result.returncode,
            stderr=result.stderr,
            code=error.get("code"),
            message=error.get("message"),
        )
    payload = body.get("result")
    if not isinstance(payload, dict):
        raise OrcaCliError(
            argv,
            returncode=result.returncode,
            stderr=result.stderr,
            message="envelope carried no result object",
        )
    return payload
