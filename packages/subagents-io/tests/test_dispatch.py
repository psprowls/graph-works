"""The dispatch value types are a contract, held field by field.

These types ship with no consumer — the planner that constructs them is still
in band 2 — so nothing else in the workspace would notice a renamed or
reordered field. This test is what notices.
"""

from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass

import pytest
from subagents_io.dispatch import DISPATCH_MODES, PlannedDispatch, WorktreePlacement

# (name, annotation) in declaration order. Annotations are compared as the
# strings PEP 563 leaves them as, which is exact enough to catch a widened
# `str | None` and cheap enough to need no typing introspection.
WORKTREE_PLACEMENT_FIELDS = [
    ("path", "str"),
    ("branch", "str | None"),
    ("parent_path", "str | None"),
]

PLANNED_DISPATCH_FIELDS = [
    ("key", "str"),
    ("slug", "str"),
    ("phase", "str"),
    ("kind", "str"),
    ("effort", "str | None"),
    ("skill", "str"),
    ("mode", "str"),
    ("agent", "str"),
    ("model", "str | None"),
    ("reasoning_effort", "str | None"),
    ("worktree", "WorktreePlacement"),
    ("merge_target", "str"),
    ("prompt", "str"),
    ("auto_merge", "bool"),
]


@pytest.mark.parametrize(
    ("cls", "expected"),
    [(WorktreePlacement, WORKTREE_PLACEMENT_FIELDS), (PlannedDispatch, PLANNED_DISPATCH_FIELDS)],
    ids=["WorktreePlacement", "PlannedDispatch"],
)
def test_field_names_annotations_and_order(cls, expected):
    assert is_dataclass(cls)
    assert [(f.name, f.type) for f in fields(cls)] == expected


@pytest.mark.parametrize("cls", [WorktreePlacement, PlannedDispatch], ids=["WorktreePlacement", "PlannedDispatch"])
def test_is_frozen(cls):
    assert cls.__dataclass_params__.frozen is True


@pytest.mark.parametrize("cls", [WorktreePlacement, PlannedDispatch], ids=["WorktreePlacement", "PlannedDispatch"])
def test_no_field_carries_a_default(cls):
    # A default here would let a caller construct a half-populated dispatch and
    # have it look complete.
    for f in fields(cls):
        assert f.default is MISSING, f.name
        assert f.default_factory is MISSING, f.name


def test_instances_are_frozen_at_runtime():
    placement = WorktreePlacement(path="/tmp/wt", branch="b", parent_path=None)
    with pytest.raises(AttributeError):
        placement.branch = "other"  # type: ignore[misc]


def test_value_equality():
    assert WorktreePlacement("/tmp/wt", "b", None) == WorktreePlacement("/tmp/wt", "b", None)


def test_worktree_placement_fields():
    assert tuple(f.name for f in fields(WorktreePlacement)) == ("path", "branch", "parent_path")


def test_parent_path_round_trips():
    placement = WorktreePlacement(path="/wt/child", branch="b", parent_path="/wt/epic")
    assert placement.parent_path == "/wt/epic"
    assert placement != WorktreePlacement(path="/wt/child", branch="b", parent_path=None)


def test_detached_placement_has_no_branch():
    assert WorktreePlacement("/wt/reader", None, None).branch is None


def test_planned_dispatch_composes_a_worktree_placement():
    worktree = WorktreePlacement(path="/wt/x", branch="b", parent_path=None)
    dispatch = PlannedDispatch(
        agent="arbitrary-inert-agent",
        key="slug#plan",
        slug="slug",
        phase="plan",
        kind="feature",
        effort=None,
        skill="writing-plans",
        mode="autonomous",
        model=None,
        reasoning_effort=None,
        worktree=worktree,
        merge_target="main",
        auto_merge=False,
        prompt="do the thing",
    )
    assert dispatch.agent == "arbitrary-inert-agent"
    assert dispatch.worktree is worktree
    assert dispatch.mode in DISPATCH_MODES


def test_dispatch_modes_membership():
    assert frozenset({"autonomous", "attend", "relay"}) == DISPATCH_MODES
