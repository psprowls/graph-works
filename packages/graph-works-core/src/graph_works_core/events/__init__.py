"""Change events: what a filesystem change means to a client.

Layer-2 vertical. A pure `classify` over the workspace layout and the
work-item path grammar -- the one place that knows both -- so
`graph-works-serve`'s watcher owns only the I/O loop. Not hoisted to the
`graph_works_core` front door: `classify` is too generic a name there.
"""

from __future__ import annotations

from graph_works_core.events.model import Change, ChangeEvent, EventKind
from graph_works_core.events.rules import classify

__all__ = ["Change", "ChangeEvent", "EventKind", "classify"]
