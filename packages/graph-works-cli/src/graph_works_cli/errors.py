"""The one user-facing error exit for every `gw` sub-app.

`wiki_cli/errors.py` held this first, and `config_cli` and `workspace_resolution` each
spelled `Error: {exc}` by hand alongside it. C6 promotes the helper here (§4.6) so
`util_cli` reuses it instead of adding a fourth copy. Widening the normalization to the
two hand-spelled sites is deliberately *not* done here — that is
`2026-08-19-tech-debt-normalize-the-user-facing` (D-026).
"""

from __future__ import annotations

from typing import Never

import typer

from graph_works_cli import exit_codes


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
