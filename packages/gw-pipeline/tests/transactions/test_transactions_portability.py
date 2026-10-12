"""Unit tests for the directory-anchor seam.

Every selector test injects `platform_name` explicitly rather than
monkeypatching `sys.platform`, which is the whole point of the argument: a
POSIX box asserts the Windows arm without a Windows box, and neither arm is
dead lines against the 95% floor.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from gw_kernel.workspace import anchors


def test_raw_descriptor_refuses_a_windows_anchor(tmp_path: Path) -> None:
    """`UnsupportedAnchorPlatform` still fires -- now only from the one call
    site (`transactions._raw_descriptor`) that genuinely cannot take a
    `_WindowsAnchor`, not from the selectors themselves."""
    from gw_pipeline import transactions

    anchor = anchors.open_anchor(tmp_path, platform_name="win32")
    try:
        with pytest.raises(anchors.UnsupportedAnchorPlatform, match="no descriptor to lend"):
            transactions._raw_descriptor(anchor)
    finally:
        anchor.close()


def test_workspace_engine_imports_with_fcntl_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The native-Windows import path, simulated: hide `fcntl`, drop the
    modules from `sys.modules`, and import again.  A structural `ast` check
    cannot catch a lazily-added top-level import in a helper module; this can.
    """
    for name in [n for n in sys.modules if n.startswith(("gw_kernel.workspace", "gw_pipeline.transactions"))]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "fcntl", None)  # a None entry makes `import fcntl` raise
    importlib.import_module("gw_pipeline.transactions")
