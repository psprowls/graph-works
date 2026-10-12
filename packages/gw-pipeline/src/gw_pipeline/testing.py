"""Installed test support for engine-global durability-tier injection."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from gw_kernel.workspace import anchors

from gw_pipeline import transactions


@contextmanager
def forced_tier(platform_name: str) -> Iterator[None]:
    """Run the engine on a named tier.

    Patches the two module-level selector delegators in `transactions` rather
    than `anchors`' own functions, because `transactions._open_root` and
    `_open_absolute_directory` resolve those names through `transactions`'
    globals -- the same reason the `:290-305` delegator layer exists.

    Skips rather than failing when a POSIX tier is forced on Windows: see the
    `tier` fixture in `test_transactions.py` for why that direction is
    impossible rather than merely untested.
    """
    if platform_name != "win32" and sys.platform == "win32":
        pytest.skip("the strong tier needs openat/flock/os.O_DIRECTORY, none of which exist on Windows")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            transactions,
            "_open_root",
            lambda root: anchors.open_anchor(root, platform_name=platform_name),
        )
        patch.setattr(
            transactions,
            "_open_absolute_directory",
            lambda path: anchors.open_absolute_anchor(path, platform_name=platform_name),
        )
        yield
