"""The one user-facing error exit for every `gw` sub-app.

`wiki_cli/errors.py` held this first, and `config_cli` and `workspace_resolution` each
spelled `Error: {exc}` by hand alongside it. C6 promotes the helper here (§4.6) so
`util_cli` reuses it instead of adding a fourth copy. Widening the normalization to the
two hand-spelled sites is deliberately *not* done here — that is
`2026-08-19-tech-debt-normalize-the-user-facing` (D-026).

`fail` is the explicit-mode sibling of `work_cli.rendering.fail`, for sub-apps that have
no parse-time `--json` callback.
"""

from __future__ import annotations

from typing import Never

import typer
from graph_works_wire.errors import REASONS, error_envelope

from graph_works_cli import exit_codes
from graph_works_cli.json_output import encode


def exit_error(
    message: str,
    *,
    code: int = exit_codes.GENERIC,
    cause: BaseException | None = None,
) -> Never:
    """Write one user-facing error and stop the current Typer command."""
    typer.echo(f"Error: {message}", err=True)
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
