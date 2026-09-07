"""Shared test helpers for the `work-tracker-okf` CLI suite.

Lives in `helpers.py` rather than `conftest.py`, following `okf-io`'s precedent: these are
plain functions imported by name, not pytest fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path


def console_script(name: str) -> Path:
    """This environment's installed entry point for `name`.

    Windows installs `<name>.exe` beside the interpreter in `Scripts/`; POSIX installs an
    extensionless `<name>` in `bin/`. `Path(sys.executable).parent` is that directory on both.
    """
    directory = Path(sys.executable).parent
    entry_point = directory / (f"{name}.exe" if sys.platform == "win32" else name)
    assert entry_point.is_file(), f"console script not installed: {entry_point}"
    return entry_point
