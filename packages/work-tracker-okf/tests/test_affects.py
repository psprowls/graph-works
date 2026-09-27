from __future__ import annotations

import pytest
from work_tracker_okf.affects import (
    WORKSPACE_AFFECTS,
    AffectsDrift,
    affects_drift,
    code_affects,
    needs_affects_hint,
    plan_files,
    touches_workspace,
)


def test_the_reserved_value_is_gw_workspace() -> None:
    assert WORKSPACE_AFFECTS == "gw:workspace"


def test_code_affects_drops_only_the_reserved_value_in_order() -> None:
    assert code_affects(["packages/b", "gw:workspace", "packages/a"]) == ("packages/b", "packages/a")
    assert code_affects(["gw:workspace"]) == ()
    assert code_affects([]) == ()
    # A typo is not the reserved value: it stays a (bad) path for targets.affects-missing to report.
    assert code_affects(["gw:worksapce"]) == ("gw:worksapce",)


def test_touches_workspace() -> None:
    assert touches_workspace(["gw:workspace"])
    assert touches_workspace(["packages/a", "gw:workspace"])
    assert not touches_workspace(["packages/a"])
    assert not touches_workspace([])


PLAN = """\
### Task 1: Thing

**Files:**
- Create: `packages/x/src/x/new.py`
- Modify: `packages/x/src/x/old.py:123-145` — import block (~line 54)
- Modify: `packages/x/src/x/other.py:7`
- Test: `packages/x/tests/test_new.py`
- Delete: `packages/x/src/x/gone.py`
- Modify: `/work/feature-a/references/02-plan.md`
  - Modify: `packages/x/src/x/new.py`

Some prose naming `packages/not-a-bullet.py`.
- Consumes: `packages/x/src/x/ignored.py`
"""


def test_plan_files_parses_file_bullets() -> None:
    assert plan_files(PLAN) == (
        "packages/x/src/x/new.py",
        "packages/x/src/x/old.py",
        "packages/x/src/x/other.py",
        "packages/x/tests/test_new.py",
        "packages/x/src/x/gone.py",
    )


def test_plan_files_of_a_plan_with_no_bullets_is_empty() -> None:
    assert plan_files("# Plan\n\nNothing here.\n") == ()


def test_drift_with_everything_covered_is_empty() -> None:
    assert affects_drift(["packages/a/x.py", "packages/a"], ["packages/a"]) == AffectsDrift((), ())


def test_drift_containment_is_by_segment_and_one_directional() -> None:
    # `packages/ab` is not under `packages/a`; the entry must contain the file, not the reverse.
    drift = affects_drift(["packages/ab/x.py", "packages"], ["packages/a"])
    assert drift.uncovered == ("packages/ab/x.py", "packages")


def test_drift_widening_takes_the_shared_prefix_when_two_segments_deep() -> None:
    drift = affects_drift(
        ["packages/graph-works-core/tests/orchestrate/test_x.py"],
        ["packages/graph-works-core/src/graph_works_core/orchestrate"],
    )
    assert drift.uncovered == ("packages/graph-works-core/tests/orchestrate/test_x.py",)
    assert drift.widening == ("packages/graph-works-core",)


def test_drift_widening_falls_back_to_the_parent_directory_and_collapses() -> None:
    drift = affects_drift(
        ["packages/b/tests/test_x.py", "packages/b/tests/sub/test_y.py", "README.md"],
        ["packages/a"],
    )
    assert drift.uncovered == ("packages/b/tests/test_x.py", "packages/b/tests/sub/test_y.py", "README.md")
    # the shared prefix with packages/a is one segment deep: use parent dirs, collapse, sort;
    # a top-level file has no parent directory and widens to itself
    assert drift.widening == ("README.md", "packages/b/tests")


def test_drift_with_empty_affects_uncovers_everything() -> None:
    drift = affects_drift(["packages/a/x.py", "packages/a/y.py", "docs/z.md"], [])
    assert drift.uncovered == ("packages/a/x.py", "packages/a/y.py", "docs/z.md")
    assert drift.widening == ("docs", "packages/a")


def test_drift_ignores_blank_entries_before_containment() -> None:
    drift = affects_drift(["packages/a/x.py", "docs/z.md"], ["", "  ", "packages/a"])
    assert drift == AffectsDrift(("docs/z.md",), ("docs",))


def test_drift_explicit_dot_entry_covers_repository_root() -> None:
    assert affects_drift(["packages/a/x.py"], [" ", "."]) == AffectsDrift((), ())


@pytest.mark.parametrize(
    ("type_", "has_parent", "has_children", "affects", "expected"),
    [
        ("Feature", True, False, [], True),
        ("Bug", True, False, [], True),
        ("TechDebt", True, False, [], True),
        ("TestGap", True, False, [], True),
        ("Spike", True, False, [], True),
        ("Epic", True, False, [], False),
        ("Release", True, False, [], False),
        ("Feature", False, False, [], False),
        ("Feature", True, True, [], False),
        ("Feature", True, False, ["gw:workspace"], False),
        ("Feature", True, False, ["packages/a"], False),
        ("Feature", True, False, ["  "], True),
    ],
)
def test_needs_affects_hint(type_, has_parent, has_children, affects, expected) -> None:
    assert needs_affects_hint(type_, has_parent=has_parent, has_children=has_children, affects=affects) is expected
