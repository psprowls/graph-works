"""Shared test helpers for the `gw` CLI suite.

Lives in `helpers.py` rather than `conftest.py`, following `okf-io`'s precedent: these are
plain functions imported by name, not pytest fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

from graph_works_cli.cli import app
from typer.testing import CliRunner

_runner = CliRunner()


def initialized_workspace(root: Path) -> Path:
    """Bootstrap the smallest real initialized workspace at *root*, for CLI boundary tests.

    Shared by every suite that needs a real `.gw`/`okf` layout on disk rather than a
    monkeypatched bundle — do not re-invent this scaffolding per test module.
    """
    result = _runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0
    return root


def console_script(name: str) -> Path:
    """This environment's installed entry point for `name`.

    Windows installs `<name>.exe` beside the interpreter in `Scripts/`; POSIX installs an
    extensionless `<name>` in `bin/`. `Path(sys.executable).parent` is that directory on both.
    """
    directory = Path(sys.executable).parent
    entry_point = directory / (f"{name}.exe" if sys.platform == "win32" else name)
    assert entry_point.is_file(), f"console script not installed: {entry_point}"
    return entry_point
