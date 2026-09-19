"""Contract-test inputs for `graph_works_wire.events`."""

from __future__ import annotations

from collections.abc import Callable

from graph_works_core.events import Change, ChangeEvent, EventKind
from graph_works_wire import events

_PAGE = ChangeEvent(kind=EventKind.PAGE, path="concepts/a.md", member="concepts/a.md", change=Change.DELETED)
_CONFIG = ChangeEvent(kind=EventKind.CONFIG, path="projection", member=None, change=Change.MODIFIED)

EVENTS: dict[str, tuple[Callable[[], object], ...]] = {
    "events.change_event_payload": (
        lambda: events.change_event_payload(_PAGE),
        lambda: events.change_event_payload(_CONFIG),
    ),
    "events.changes_payload": (
        lambda: events.changes_payload(0, ()),
        lambda: events.changes_payload(7, (_PAGE, _CONFIG)),
    ),
}
