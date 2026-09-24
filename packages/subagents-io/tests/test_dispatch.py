"""The dispatch value types are a contract, held field by field.

These types ship with no consumer — the planner that constructs them is still
in band 2 — so nothing else in the workspace would notice a renamed or
reordered field. This test is what notices.
"""

from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass

import pytest
from subagents_io.dispatch import DISPATCH_MODES, WORKTREE_ACTIONS, PlannedDispatch, WorktreeAction

# (name, annotation) in declaration order. Annotations are compared as the
# strings PEP 563 leaves them as, which is exact enough to catch a widened
# `str | None` and cheap enough to need no typing introspection.
WORKTREE_ACTION_FIELDS = [
    ("action", "str"),
    ("path", "str | None"),
    ("branch", "str"),
    ("base_branch", "str | None"),
    ("exists", "bool | None"),
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
    ("worktree", "WorktreeAction"),
    ("merge_target", "str"),
    ("prompt", "str"),
    ("auto_merge", "bool"),
]


@pytest.mark.parametrize(
    ("cls", "expected"),
    [(WorktreeAction, WORKTREE_ACTION_FIELDS), (PlannedDispatch, PLANNED_DISPATCH_FIELDS)],
    ids=["WorktreeAction", "PlannedDispatch"],
)
def test_field_names_annotations_and_order(cls, expected):
    assert is_dataclass(cls)
    assert [(f.name, f.type) for f in fields(cls)] == expected


@pytest.mark.parametrize("cls", [WorktreeAction, PlannedDispatch], ids=["WorktreeAction", "PlannedDispatch"])
def test_is_frozen(cls):
    assert cls.__dataclass_params__.frozen is True


@pytest.mark.parametrize("cls", [WorktreeAction, PlannedDispatch], ids=["WorktreeAction", "PlannedDispatch"])
def test_no_field_carries_a_default(cls):
    # A default here would let a caller construct a half-populated dispatch and
    # have it look complete.
    for f in fields(cls):
        assert f.default is MISSING, f.name
        assert f.default_factory is MISSING, f.name


def test_instances_are_frozen_at_runtime():
    action = WorktreeAction(action="reuse", path="/tmp/wt", branch="b", base_branch=None, exists=True, parent_path=None)
    with pytest.raises(AttributeError):
        action.branch = "other"  # type: ignore[misc]


def test_value_equality():
    def make():
        return WorktreeAction(
            action="reuse", path="/tmp/wt", branch="b", base_branch=None, exists=True, parent_path=None
        )

    assert make() == make()


def test_parent_path_round_trips():
    action = WorktreeAction(
        action="fork-child", path=None, branch="b", base_branch="epic/x", exists=None, parent_path="/wt/epic"
    )
    assert action.parent_path == "/wt/epic"
    assert action != WorktreeAction(
        action="fork-child", path=None, branch="b", base_branch="epic/x", exists=None, parent_path=None
    )


def test_planned_dispatch_composes_a_worktree_action():
    worktree = WorktreeAction(
        action="fork-child", path=None, branch="b", base_branch="main", exists=None, parent_path=None
    )
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
    assert dispatch.worktree.action in WORKTREE_ACTIONS


def test_worktree_actions_membership():
    assert frozenset({"reuse", "fork-child", "create-top-level", "main"}) == WORKTREE_ACTIONS


def test_dispatch_modes_membership():
    assert frozenset({"autonomous", "attend", "relay"}) == DISPATCH_MODES
