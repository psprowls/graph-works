"""The shared display-read contract for indexed and full-bundle backends."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

from okf_ext.readindex import Diagnostics, MemberRow
from okf_io import Heading, Link
from okf_io.bundle import MemberKind
from work_tracker_okf.items import IGNORE
from work_tracker_okf.snapshot import WorkSnapshot

Backend = Literal["index", "bundle"]
FallbackReason = Literal["disabled", "busy", "unavailable", "error"]


class ReadSession(Protocol):
    """One backend and generation for display reads, with query-time ignores."""

    @property
    def backend(self) -> Backend: ...

    @property
    def fallback(self) -> FallbackReason | None: ...

    @property
    def generation(self) -> int | None: ...

    def concept_hashes(self, *, ignore: Sequence[str] = ()) -> Mapping[str, str] | None:
        """Stored file-byte hashes by concept id, or None when no stamps exist.

        Derived caches use this index change stamp. Member rows keep sha256=None
        (read-session D-006); this is the one place stamps leave the session.
        """
        ...

    def members(
        self,
        *,
        prefix: str = "",
        kind: MemberKind | None = None,
        type: str | None = None,
        ignore: Sequence[str] = (),
    ) -> tuple[MemberRow, ...]: ...

    def member(self, path: str, *, ignore: Sequence[str] = ()) -> MemberRow | None: ...

    def content_hash(self, member_id: str) -> str | None: ...

    def outlinks(self, concept_id: str, *, ignore: Sequence[str] = ()) -> tuple[Link, ...]: ...

    def backlinks(self, concept_id: str, *, ignore: Sequence[str] = ()) -> tuple[str, ...]: ...

    def broken(self, source: str | None = None, *, ignore: Sequence[str] = ()) -> tuple[Link, ...]: ...

    def headings(self, member_id: str) -> tuple[Heading, ...]: ...

    def diagnostics(self, *, ignore: Sequence[str] = ()) -> Diagnostics: ...

    def work_snapshot(self, *, ignore: Sequence[str] = IGNORE) -> WorkSnapshot: ...


IndexRevision = tuple[str | None, int | None]


def index_revision(session: ReadSession) -> IndexRevision | None:
    """Pair an index incarnation with its pinned generation without widening the protocol.

    Built-in indexed and materialized sessions retain the read index's epoch.
    A custom session lacking the optional epoch remains comparable by generation.
    """
    if session.backend != "index":
        return None
    epoch: object = getattr(session, "index_epoch", None)
    return epoch if isinstance(epoch, str) else None, session.generation
