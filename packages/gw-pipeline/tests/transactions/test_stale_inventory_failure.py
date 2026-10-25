"""`only_stale_inventory`: the one rollback cause a lane-index writer may retry."""

from __future__ import annotations

from pathlib import Path

from gw_pipeline.transactions import (
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


def _outdated_index_plan(tmp_path: Path, operation: str):
    import hashlib
    from dataclasses import replace

    from gw_kernel.testing.transactions import plan as _plan
    from gw_kernel.testing.transactions import workspace as _workspace
    from work_tracker_okf.mutation import PlannedWrite

    layout = _workspace(tmp_path)
    page = layout.bundle_dir / "work/bug-x.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Bug\ntitle: X\ndescription: D\nstatus: stable\nwork_status: open\nphase: design\n"
        "effort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n---\n\n## Summary\nD\n",
        encoding="utf-8",
        newline="",
    )
    index = layout.bundle_dir / "work/index.md"
    before = b"# Work\n"
    index.write_bytes(before)
    # The write carries an index that omits bug-x: outdated against the inventory.
    write = PlannedWrite("work/index.md", hashlib.sha256(before).hexdigest(), b"# Work\n\nprose\n")
    return layout, replace(_plan(layout, writes=(write,)), operation=operation)


def test_postcondition_only_checks_regen_plans(tmp_path: Path) -> None:
    from gw_pipeline.transactions import apply_mutation

    layout, archive_plan = _outdated_index_plan(tmp_path / "a", "archive")
    applied = apply_mutation(layout, archive_plan, commit=None)
    assert applied.ok, applied.failures
    assert not any(STALE_INVENTORY_DETAIL in failure for failure in applied.failures)

    layout, indexes_plan = _outdated_index_plan(tmp_path / "b", "indexes")
    stale = apply_mutation(layout, indexes_plan, commit=None)
    assert not stale.ok
    assert any(STALE_INVENTORY_DETAIL in failure for failure in stale.failures)
