"""The work types join the proposal pool; their promotion covers `_base`'s required keys."""

import importlib.resources
from datetime import date

from okf_ext.schemas import declared_proposables, load_schemas

WORK_TYPES = ("Bug", "Epic", "Feature", "Release", "Spike", "TechDebt", "TestGap")


def _found():
    return declared_proposables(load_schemas(str(importlib.resources.files("work_tracker_okf") / "assets" / "schema")))


def test_every_work_type_is_in_the_pool_under_work() -> None:
    found = _found()
    assert tuple(entry.name for entry in found.types) == WORK_TYPES
    assert {entry.directory for entry in found.types} == {"work/"}
    assert found.locked == () and found.refused == ()


def test_the_work_promotion_is_undated_and_fills_status_and_dates() -> None:
    for entry in _found().types:
        assert entry.promotion is not None and not entry.promotion.dated
        assert entry.promotion.frontmatter_on(date(2026, 10, 4)) == {
            "work_status": "open",
            "opened": "2026-10-04",
            "updated": "2026-10-04",
        }


def test_every_work_type_carries_its_own_guidance() -> None:
    summaries = [entry.guidance.summary for entry in _found().types]
    assert len(set(summaries)) == len(WORK_TYPES)
