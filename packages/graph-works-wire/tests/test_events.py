"""The change-event projections are the `/v1/events` wire contract."""

from __future__ import annotations

from dataclasses import fields

from graph_works_core.events import Change, ChangeEvent, EventKind
from graph_works_wire.events import SCHEMA_VERSION, change_event_payload, changes_payload

ITEM = ChangeEvent(
    kind=EventKind.WORK_ITEM,
    path="work/epic-x/children/feature-y",
    member="work/epic-x/children/feature-y.md",
    change=Change.MODIFIED,
)
CONFIG = ChangeEvent(kind=EventKind.CONFIG, path="manifest", member=None, change=Change.ADDED)


def test_change_event_payload_is_exactly_the_four_identity_keys() -> None:
    assert change_event_payload(ITEM) == {
        "kind": "work-item",
        "path": "work/epic-x/children/feature-y",
        "member": "work/epic-x/children/feature-y.md",
        "change": "modified",
    }


def test_values_are_plain_str_not_enum_members() -> None:
    payload = change_event_payload(ITEM)
    assert type(payload["kind"]) is str and type(payload["change"]) is str


def test_config_event_member_is_null() -> None:
    assert change_event_payload(CONFIG) == {"kind": "config", "path": "manifest", "member": None, "change": "added"}


def test_changes_payload_keeps_order_and_carries_seq() -> None:
    assert changes_payload(42, (ITEM, CONFIG)) == {
        "schema_version": 1,
        "seq": 42,
        "events": [change_event_payload(ITEM), change_event_payload(CONFIG)],
    }
    assert SCHEMA_VERSION == 1


def test_the_projection_names_every_change_event_field() -> None:
    """A field added to ChangeEvent fails here first; a human decides whether the wire gains it."""
    assert [f.name for f in fields(ChangeEvent)] == ["kind", "path", "member", "change"]
