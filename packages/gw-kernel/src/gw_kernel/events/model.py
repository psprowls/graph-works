"""The closed change-event vocabulary.

`Change` is core's own type so core never imports `watchfiles`; serve maps
`watchfiles.Change` onto it. `EventKind` is closed -- adding a kind is a
contract change for the serve watcher and every client. Events carry
identity, never content (the epic's decision 003): a client re-fetches.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Change(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


class EventKind(StrEnum):
    WORK_ITEM = "work-item"
    DECISIONS = "decisions"
    PAGE = "page"
    CONFIG = "config"
    LOG = "log"


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    """One classified change.

    `path` is the canonical identity to re-fetch: an extensionless item path
    for `work-item`/`decisions`, a bundle-relative `.md` path for `page`,
    `log.md` for `log`, and a closed name token for `config`. `member` is the
    bundle-relative POSIX file that changed, or `None` for `config` -- a
    config file can live outside the workspace root, so no path for it is
    canonical.
    """

    kind: EventKind
    path: str
    member: str | None
    change: Change


__all__ = ["Change", "ChangeEvent", "EventKind"]
