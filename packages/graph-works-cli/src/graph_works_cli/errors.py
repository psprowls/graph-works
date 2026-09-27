"""The shared user-facing error writer and exit for every `gw` sub-app.

All human error paths delegate here; JSON refusal policy stays with its caller.
Core's lowercase prefix is restyled at this CLI boundary (D-026).
"""

from __future__ import annotations

from typing import Never

import typer
from graph_works_wire.errors import REASONS, error_envelope

from graph_works_cli import exit_codes
from graph_works_cli.json_output import encode

#: Core's own failure prefix, restyled onto the CLI's `Error:` (D-026).
_CORE_ERROR_PREFIX = "error: "


def echo_error(message: str) -> None:
    """Write one user-facing error line to stderr, restyling core's prefix."""
    if message.startswith(_CORE_ERROR_PREFIX):
        message = message[len(_CORE_ERROR_PREFIX) :]
    typer.echo(f"Error: {message}", err=True)


def exit_error(
    message: str,
    *,
    code: int = exit_codes.GENERIC,
    cause: BaseException | None = None,
) -> Never:
    """Write one user-facing error and stop the current Typer command."""
    echo_error(message)
    if cause is None:
        raise typer.Exit(code=code)
    raise typer.Exit(code=code) from cause


def fail(
    message: str,
    *,
    reason: str,
    json_mode: bool,
    command: str,
    payload: object = None,
    code: int = exit_codes.GENERIC,
    cause: BaseException | None = None,
) -> Never:
    """Emit the wire refusal envelope on stdout when *json_mode*, then exit via `exit_error`."""
    if reason not in REASONS:
        raise ValueError(f"{reason!r} is not in the closed reason vocabulary")
    if json_mode:
        typer.echo(
            encode(
                error_envelope(
                    command=command,
                    reason=reason,
                    message=message,
                    exit_code=code,
                    payload=payload,
                )
            )
        )
    exit_error(message, code=code, cause=cause)
