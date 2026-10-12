"""Public work transaction re-export seam under both durability tiers."""

from __future__ import annotations

import sys

import pytest
from gw_kernel.workspace import anchors
from gw_pipeline import transactions
from gw_work import MutationApplication, apply_mutation
from gw_work import transactions as public_transactions


@pytest.fixture(params=["posix", "windows"], autouse=True)
def tier(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Keep the public re-export identity case under both original durability tiers.

    The weak-tier anchor runs as path logic on POSIX. The strong tier needs
    POSIX directory descriptors and is skipped on Windows as before.
    """
    if request.param == "posix" and sys.platform == "win32":
        pytest.skip("the strong tier needs openat/flock/os.O_DIRECTORY, none of which exist on Windows")
    platform_name = "win32" if request.param == "windows" else "linux"
    monkeypatch.setattr(
        transactions,
        "_open_root",
        lambda root: anchors.open_anchor(root, platform_name=platform_name),
    )
    monkeypatch.setattr(
        transactions,
        "_open_absolute_directory",
        lambda path: anchors.open_absolute_anchor(path, platform_name=platform_name),
    )
    return request.param


def test_public_work_transaction_surface_reexports_the_workspace_executor() -> None:
    assert public_transactions.apply_mutation is apply_mutation
    assert public_transactions.MutationApplication is MutationApplication
