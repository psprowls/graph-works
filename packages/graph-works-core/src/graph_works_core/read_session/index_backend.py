"""Display reads over one pinned index generation and query-time membership.

Ignored unreadable files become members before alias and link resolution. Overlay
collisions follow the loader's component-sorted depth-first walk on POSIX.
Windows canonical collisions select a full load: native comparison ties lose
enumeration order in the pinned rows. Inexact work frontmatter also selects a
full load before any result escapes; indexed queries never reread document
bodies. SQLite errors may select a full load only
before the first public result (including an empty result) has been returned.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from sys import platform
from types import MappingProxyType

from okf_ext.readindex import Diagnostics, MemberRow, ReadIndex, member_row
from okf_ext.readindex.view import IndexView
from okf_io import Heading, Link, resolve_member
from okf_io.bundle import MemberKind, canonical_id
from work_tracker_okf.items import IGNORE
from work_tracker_okf.paths import parse_item_path
from work_tracker_okf.snapshot import WorkSnapshot

from graph_works_core.read_session.bundle_backend import BundleSession
from graph_works_core.read_session.location import warn_fallback
from graph_works_core.read_session.model import Backend, FallbackReason
from graph_works_core.workspace.bundle import ignored_by


@dataclass(frozen=True)
class _Overlay:
    rows: Mapping[str, MemberRow]
    canonical: Mapping[str, str]
    diagnostics: Diagnostics


class IndexSession:
    """Use a pinned view, or one bundle backend chosen before serving results."""

    def __init__(self, index: ReadIndex, view: IndexView, *, root: Path, db_path: Path) -> None:
        self._index = index
        self._view = view
        self._root = root
        self._db_path = db_path
        self._bundle: BundleSession | None = None
        self._served = False
        self._unreadable: frozenset[str] | None = None
        self._overlays: dict[tuple[str, ...], _Overlay] = {}
        try:
            inexact = self._index.connection.execute(
                "SELECT id FROM members WHERE kind='concept' AND unreadable IS NULL AND fm_exact=0"
            )
            if any(parse_item_path(mid[:-3]) is not None for (mid,) in inexact) or self._ambiguous_collision_order():
                self._switch("unavailable")
                # Freeze the ordinary display and work loads at selection time,
                # before a subsequent file edit can change the fidelity fallback.
                assert self._bundle is not None
                self._bundle.members()
                self._bundle.work_snapshot()
        except sqlite3.Error as exc:
            self._switch("error", exc)

    def _switch(self, reason: FallbackReason, exc: BaseException | None = None) -> None:
        self._bundle = BundleSession(self._root, fallback=reason)
        warn_fallback(reason, self._db_path, exc)

    def _ambiguous_collision_order(self) -> bool:
        if platform != "win32":
            return False
        # Include unreadable files: an arbitrary later ignore policy can admit
        # them. Detect the risk before any result, even an unrelated empty one.
        # The stored collision table omits those files and cannot prove safety.
        # Native Path ties preserve scandir enumeration order, which these rows
        # do not retain; a Windows sort key alone cannot reconstruct the winner.
        return (
            self._index.connection.execute(
                "SELECT 1 FROM members GROUP BY canonical HAVING COUNT(*) > 1 LIMIT 1"
            ).fetchone()
            is not None
        )

    @property
    def backend(self) -> Backend:
        return "bundle" if self._bundle is not None else "index"

    @property
    def fallback(self) -> FallbackReason | None:
        return self._bundle.fallback if self._bundle is not None else None

    @property
    def generation(self) -> int | None:
        return None if self._bundle is not None else self._view.generation

    def _guard[T](self, on_index: Callable[[], T], on_bundle: Callable[[BundleSession], T]) -> T:
        if self._bundle is not None:
            return on_bundle(self._bundle)
        try:
            result = on_index()
        except sqlite3.Error as exc:
            if self._served:
                raise
            self._switch("error", exc)
            assert self._bundle is not None
            return on_bundle(self._bundle)
        self._served = True
        return result

    def _unreadable_files(self) -> frozenset[str]:
        if self._unreadable is None:
            self._unreadable = frozenset(
                mid for (mid,) in self._index.connection.execute("SELECT id FROM members WHERE unreadable IS NOT NULL")
            )
        return self._unreadable

    def _overlay(self, ignore: Sequence[str]) -> _Overlay:
        key = tuple(ignore)
        if key not in self._overlays:
            rows = {
                row.id: replace(member_row(row.id, "ignored") if ignored_by(row.id, key) else row, sha256=None)
                for row in self._view.members()
            }
            unreadable = self._unreadable_files()
            rows.update(
                {mid: replace(member_row(mid, "ignored"), sha256=None) for mid in unreadable if ignored_by(mid, key)}
            )
            canonical: dict[str, str] = {}
            collisions: dict[str, list[str]] = {}
            # Path components match the loader's sorted directory traversal,
            # including canonically equivalent directory names.
            for mid in sorted(rows, key=PurePosixPath):
                if mid.isascii():
                    continue
                cid = canonical_id(mid)
                if cid in canonical:
                    collisions.setdefault(cid, [canonical[cid]]).append(mid)
                canonical[cid] = mid
            d = self._view.diagnostics()
            diagnostics = Diagnostics(
                unreadable=MappingProxyType(
                    {
                        mid: reason
                        for mid, reason in d.unreadable.items()
                        if mid not in unreadable or not ignored_by(mid, key)
                    }
                ),
                parse_errors=MappingProxyType(
                    {mid: error for mid, error in d.parse_errors.items() if not ignored_by(mid, key)}
                ),
                collisions=MappingProxyType({cid: tuple(ids) for cid, ids in sorted(collisions.items())}),
                pruned=d.pruned,
            )
            self._overlays[key] = _Overlay(
                MappingProxyType(dict(sorted(rows.items()))), MappingProxyType(canonical), diagnostics
            )
        return self._overlays[key]

    def _resolve(self, path: str, overlay: _Overlay) -> str | None:
        return resolve_member(
            path,
            root=self._root,
            has_raw=overlay.rows.__contains__,
            canonical=overlay.canonical.get,
            pruned=overlay.diagnostics.pruned,
        )

    def _members(
        self,
        *,
        prefix: str = "",
        kind: MemberKind | None = None,
        type: str | None = None,
        ignore: Sequence[str] = (),
    ) -> tuple[MemberRow, ...]:
        if not ignore:
            return tuple(replace(row, sha256=None) for row in self._view.members(prefix=prefix, kind=kind, type=type))
        return tuple(
            row
            for row in self._overlay(ignore).rows.values()
            if row.id.startswith(prefix) and (kind is None or row.kind == kind) and (type is None or row.type == type)
        )

    def members(
        self,
        *,
        prefix: str = "",
        kind: MemberKind | None = None,
        type: str | None = None,
        ignore: Sequence[str] = (),
    ) -> tuple[MemberRow, ...]:
        return self._guard(
            lambda: self._members(prefix=prefix, kind=kind, type=type, ignore=ignore),
            lambda bundle: bundle.members(prefix=prefix, kind=kind, type=type, ignore=ignore),
        )

    def _member(self, path: str, ignore: Sequence[str]) -> MemberRow | None:
        if not ignore:
            row = self._view.member(path)
            return replace(row, sha256=None) if row is not None else None
        overlay = self._overlay(ignore)
        mid = self._resolve(path, overlay)
        return overlay.rows.get(mid) if mid is not None else None

    def member(self, path: str, *, ignore: Sequence[str] = ()) -> MemberRow | None:
        return self._guard(lambda: self._member(path, ignore), lambda bundle: bundle.member(path, ignore=ignore))

    def _outlinks(self, cid: str, ignore: Sequence[str]) -> tuple[Link, ...]:
        return () if ignored_by(cid + ".md", ignore) else self._view.outlinks(cid)

    def outlinks(self, concept_id: str, *, ignore: Sequence[str] = ()) -> tuple[Link, ...]:
        return self._guard(
            lambda: self._outlinks(concept_id, ignore), lambda bundle: bundle.outlinks(concept_id, ignore=ignore)
        )

    def _links(self, overlay: _Overlay) -> tuple[Link, ...]:
        return tuple(
            sorted(
                (
                    link
                    for row in overlay.rows.values()
                    if row.concept_id is not None
                    for link in self._view.outlinks(row.concept_id)
                ),
                key=lambda link: (link.source, link.line or 0, link.raw, link.image),
            )
        )

    def _backlinks(self, cid: str, ignore: Sequence[str]) -> tuple[str, ...]:
        if not ignore:
            return self._view.backlinks(cid)
        overlay = self._overlay(ignore)
        target = cid + ".md"
        row = overlay.rows.get(target)
        if row is None or row.kind != "concept":
            return ()
        return tuple(
            sorted(
                {
                    link.source
                    for link in self._links(overlay)
                    if not link.external
                    and not link.image
                    and link.target is not None
                    and self._resolve(link.target, overlay) == target
                }
            )
        )

    def backlinks(self, concept_id: str, *, ignore: Sequence[str] = ()) -> tuple[str, ...]:
        return self._guard(
            lambda: self._backlinks(concept_id, ignore), lambda bundle: bundle.backlinks(concept_id, ignore=ignore)
        )

    def _broken(self, source: str | None, ignore: Sequence[str]) -> tuple[Link, ...]:
        if not ignore:
            return self._view.broken(source)
        overlay = self._overlay(ignore)
        return tuple(
            link
            for link in self._links(overlay)
            if (source is None or link.source == source)
            and not link.external
            and (link.target is None or self._resolve(link.target, overlay) is None)
        )

    def broken(self, source: str | None = None, *, ignore: Sequence[str] = ()) -> tuple[Link, ...]:
        return self._guard(lambda: self._broken(source, ignore), lambda bundle: bundle.broken(source, ignore=ignore))

    def headings(self, member_id: str) -> tuple[Heading, ...]:
        return self._guard(lambda: self._view.headings(member_id), lambda bundle: bundle.headings(member_id))

    def diagnostics(self, *, ignore: Sequence[str] = ()) -> Diagnostics:
        return self._guard(
            lambda: self._overlay(ignore).diagnostics if ignore else self._view.diagnostics(),
            lambda bundle: bundle.diagnostics(ignore=ignore),
        )

    def _work_snapshot(self, ignore: Sequence[str]) -> WorkSnapshot:
        return WorkSnapshot.from_rows(
            (row.concept_id, row.fm or {})
            for row in self._members(kind="concept", ignore=ignore)
            if row.concept_id is not None
        )

    def work_snapshot(self, *, ignore: Sequence[str] = IGNORE) -> WorkSnapshot:
        return self._guard(lambda: self._work_snapshot(ignore), lambda bundle: bundle.work_snapshot(ignore=ignore))
