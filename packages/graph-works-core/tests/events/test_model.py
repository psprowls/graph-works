from __future__ import annotations

import dataclasses

import pytest
from graph_works_core.events.model import Change, ChangeEvent, EventKind


def test_change_values_are_the_wire_strings() -> None:
    assert [c.value for c in Change] == ["added", "modified", "deleted"]


def test_event_kind_is_closed() -> None:
    """Adding a kind is a contract change for C6 and the app -- this pins it."""
    assert {k.value for k in EventKind} == {"work-item", "decisions", "page", "config", "log"}


def test_change_event_is_frozen_and_plain() -> None:
    event = ChangeEvent(kind=EventKind.PAGE, path="index.md", member="index.md", change=Change.MODIFIED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.path = "other.md"  # type: ignore[misc]
    assert [f.name for f in dataclasses.fields(ChangeEvent)] == ["kind", "path", "member", "change"]
    assert event.kind == "page" and event.change == "modified"
