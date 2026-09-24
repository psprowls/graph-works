"""`only_stale_inventory`: the one rollback cause a lane-index writer may retry."""

from __future__ import annotations

from pathlib import Path

from graph_works_core.workspace.transactions import (
    STALE_INVENTORY_DETAIL,
    MutationApplication,
    only_stale_inventory,
)


def _rolled_back(*failures: str, rolled_back: bool = True) -> MutationApplication:
    return MutationApplication(
        transaction_id="t",
        journal=Path("/journal"),
        moved=(),
        written=(),
        created_directories=(),
        warnings=(),
        failures=failures,
        rolled_back=rolled_back,
    )


def test_one_stale_lane_is_retryable() -> None:
    application = _rolled_back(f"validation failed: work/index.md: {STALE_INVENTORY_DETAIL}")
    assert only_stale_inventory(application)


def test_several_stale_lanes_are_retryable() -> None:
    application = _rolled_back(
        f"validation failed: work/index.md: {STALE_INVENTORY_DETAIL}; "
        f"work/epic-r/children/index.md: {STALE_INVENTORY_DETAIL}"
    )
    assert only_stale_inventory(application)


def test_staleness_mixed_with_another_validation_failure_is_not() -> None:
    application = _rolled_back(
        f"validation failed: work/index.md: {STALE_INVENTORY_DETAIL}; work/bug-x: final work item did not reload"
    )
    assert not only_stale_inventory(application)


def test_a_rollback_verification_failure_is_not() -> None:
    application = _rolled_back(
        f"validation failed: work/index.md: {STALE_INVENTORY_DETAIL}",
        "rollback verification failed: boom",
    )
    assert not only_stale_inventory(application)


def test_a_preflight_refusal_is_not() -> None:
    assert not only_stale_inventory(_rolled_back("preflight failed: work/index.md changed since planning"))


def test_an_application_that_did_not_roll_back_is_not() -> None:
    application = _rolled_back(f"validation failed: work/index.md: {STALE_INVENTORY_DETAIL}", rolled_back=False)
    assert not only_stale_inventory(application)


def test_a_clean_application_is_not() -> None:
    assert not only_stale_inventory(_rolled_back(rolled_back=False))
