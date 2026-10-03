"""Plain-data projections for `graph-works-serve`'s change stream (`GET /v1/events`).

An event is identity only -- `kind`, `path`, `member`, `change` -- never
content (the epic's decision 003); a client re-fetches what moved. SSE byte
framing and JSON encoding stay in serve: this module only shapes the data.
`generation` is additive (SCHEMA_VERSION unchanged): the read generation the
batch was reconciled into.
"""

from __future__ import annotations

from collections.abc import Sequence

from graph_works_core.events import ChangeEvent

SCHEMA_VERSION = 1


def change_event_payload(event: ChangeEvent) -> dict[str, object]:
    """One classified change; `member` is `None` for `config` events."""
    return {
        "kind": str(event.kind.value),
        "path": event.path,
        "member": event.member,
        "change": str(event.change.value),
    }


def changes_payload(seq: int, events: Sequence[ChangeEvent], *, generation: int | None = None) -> dict[str, object]:
    """One published batch, in order, optionally labelled with its read generation."""
    payload: dict[str, object] = {"schema_version": SCHEMA_VERSION, "seq": seq}
    if generation is not None:
        payload["generation"] = generation
    payload["events"] = [change_event_payload(event) for event in events]
    return payload
