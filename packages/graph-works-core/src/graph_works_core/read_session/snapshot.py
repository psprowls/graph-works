"""Materialize display reads into rows without a connection or Document.

The snapshot outlives its source session and serves handler threads without
filesystem access. Ignore overlays retain the producer's alias and graph rules.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from types import MappingProxyType

from okf_ext.readindex import Diagnostics, MemberRow, member_row
from okf_io import Heading, Link
from okf_io.bundle import MemberKind, canonical_id
from work_tracker_okf.items import IGNORE
from work_tracker_okf.snapshot import WorkSnapshot

from graph_works_core.read_session.model import Backend, FallbackReason, ReadSession, index_revision
from graph_works_core.workspace.bundle import ignored_by


def _copy_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _copy_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_copy_value(item) for item in value]
    return deepcopy(value)


def _copy_mapping(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _copy_value(value) for key, value in values.items()})


def _copy_work(work: WorkSnapshot) -> WorkSnapshot:
    return WorkSnapshot(
        replace(
            item,
            repo_stamps=MappingProxyType(dict(item.repo_stamps)),
            sources=tuple(
                replace(
                    source,
                    extra=_copy_mapping(source.extra),
                    usage_window=replace(source.usage_window, extra=_copy_mapping(source.usage_window.extra))
                    if source.usage_window is not None
                    else None,
                )
                for source in item.sources
            ),
        )
        for item in work
    )


def _ignored_row(member_id: str) -> MemberRow:
    return member_row(member_id, "ignored")


@dataclass(frozen=True)
class _Overlay:
    rows: Mapping[str, MemberRow]
    canonical: Mapping[str, str]
    diagnostics: Diagnostics


def _resolve(path: str, overlay: _Overlay) -> str | None:
    member_id = path.strip()
    if member_id in overlay.rows:
        return member_id
    return overlay.canonical.get(canonical_id(member_id)) if not member_id.isascii() else None


class SnapshotSession:
    """One materialized generation, with memoized work projections per ignore set."""

    def __init__(
        self,
        *,
        backend: Backend,
        fallback: FallbackReason | None,
        generation: int | None,
        index_epoch: str | None,
        rows: tuple[MemberRow, ...],
        concept_hashes: Mapping[str, str] | None,
        hashes: Mapping[str, str | None],
        unreadable_files: frozenset[str],
        collision_order: Mapping[str, tuple[str, ...]],
        outlinks: Mapping[str, tuple[Link, ...]],
        backlinks: Mapping[str, tuple[str, ...]],
        broken: tuple[Link, ...],
        headings: Mapping[str, tuple[Heading, ...]],
        diagnostics: Diagnostics,
        work: WorkSnapshot,
        all_work: WorkSnapshot,
    ) -> None:
        self.backend = backend
        self.fallback = fallback
        self.generation = generation
        self.index_epoch = index_epoch
        self._rows = rows
        self._concept_hashes = (
            MappingProxyType(dict(sorted(concept_hashes.items()))) if concept_hashes is not None else None
        )
        self._hashes = hashes
        self._unreadable_files = unreadable_files
        self._collision_order = collision_order
        self._outlinks = outlinks
        self._backlinks = backlinks
        self._broken = broken
        self._headings = headings
        self._diagnostics = diagnostics
        self._overlays: dict[tuple[str, ...], _Overlay] = {}
        self._overlay_lock = threading.Lock()
        # Keep only captured resolutions outside the modelled rows (pruned
        # clone files). Their existence must never be probed by handler threads.
        base = self._overlay(())
        broken_links = frozenset(broken)
        self._resolved_pruned = frozenset(
            link.target
            for links in outlinks.values()
            for link in links
            if not link.external
            and link.target is not None
            and link not in broken_links
            and _resolve(link.target, base) is None
        )
        self._all_work = all_work
        self._work: dict[tuple[str, ...], WorkSnapshot] = {(): all_work, tuple(IGNORE): work}
        self._work_lock = threading.Lock()

    def _overlay(self, ignore: Sequence[str]) -> _Overlay:
        key = tuple(ignore)
        with self._overlay_lock:
            cached = self._overlays.get(key)
            if cached is None:
                cached = self._build_overlay(key)
                self._overlays[key] = cached
            return cached

    def _build_overlay(self, ignore: Sequence[str]) -> _Overlay:
        rows = {row.id: _ignored_row(row.id) if ignored_by(row.id, ignore) else row for row in self._rows}
        rows.update({mid: _ignored_row(mid) for mid in self._unreadable_files if ignored_by(mid, ignore)})
        canonical = {canonical_id(mid): mid for mid in sorted(rows, key=PurePosixPath) if not mid.isascii()}
        collisions: dict[str, tuple[str, ...]] = {}
        # The all-ignored diagnostics record authoritative loader traversal
        # order, including Windows ties and previously unreadable members.
        for cid, order in self._collision_order.items():
            present = tuple(mid for mid in order if mid in rows)
            if present:
                canonical[cid] = present[-1]
            if len(present) > 1:
                collisions[cid] = present
        d = self._diagnostics
        return _Overlay(
            MappingProxyType(dict(sorted(rows.items()))),
            MappingProxyType(canonical),
            d
            if not ignore
            else Diagnostics(
                unreadable=MappingProxyType(
                    {
                        mid: reason
                        for mid, reason in d.unreadable.items()
                        if mid not in self._unreadable_files or not ignored_by(mid, ignore)
                    }
                ),
                parse_errors=MappingProxyType(
                    {mid: error for mid, error in d.parse_errors.items() if not ignored_by(mid, ignore)}
                ),
                collisions=MappingProxyType(dict(sorted(collisions.items()))),
                pruned=d.pruned,
            ),
        )

    def concept_hashes(self, *, ignore: Sequence[str] = ()) -> Mapping[str, str] | None:
        """Retain captured byte hashes after the producer closes, with ignore overlays."""
        if self._concept_hashes is None or not ignore:
            return self._concept_hashes
        return MappingProxyType(
            {cid: stamp for cid, stamp in self._concept_hashes.items() if not ignored_by(cid + ".md", ignore)}
        )

    def members(
        self,
        *,
        prefix: str = "",
        kind: MemberKind | None = None,
        type: str | None = None,
        ignore: Sequence[str] = (),
    ) -> tuple[MemberRow, ...]:
        return tuple(
            row
            for row in self._overlay(ignore).rows.values()
            if row.id.startswith(prefix) and (kind is None or row.kind == kind) and (type is None or row.type == type)
        )

    def member(self, path: str, *, ignore: Sequence[str] = ()) -> MemberRow | None:
        overlay = self._overlay(ignore)
        mid = _resolve(path, overlay)
        return overlay.rows.get(mid) if mid is not None else None

    def content_hash(self, member_id: str) -> str | None:
        """Return the change stamp the source session reported at materialization."""
        return self._hashes.get(member_id)

    def outlinks(self, concept_id: str, *, ignore: Sequence[str] = ()) -> tuple[Link, ...]:
        return () if ignored_by(concept_id + ".md", ignore) else self._outlinks.get(concept_id, ())

    def _links(self, ignore: Sequence[str]) -> tuple[Link, ...]:
        return tuple(
            sorted(
                (
                    link
                    for cid, links in self._outlinks.items()
                    if not ignored_by(cid + ".md", ignore)
                    for link in links
                ),
                key=lambda link: (link.source, link.line or 0, link.raw, link.image),
            )
        )

    def backlinks(self, concept_id: str, *, ignore: Sequence[str] = ()) -> tuple[str, ...]:
        if not ignore:
            return self._backlinks.get(concept_id, ())
        overlay = self._overlay(ignore)
        target = concept_id + ".md"
        row = overlay.rows.get(target)
        if row is None or row.kind != "concept":
            return ()
        return tuple(
            sorted(
                {
                    link.source
                    for link in self._links(ignore)
                    if not link.external
                    and not link.image
                    and link.target is not None
                    and _resolve(link.target, overlay) == target
                }
            )
        )

    def broken(self, source: str | None = None, *, ignore: Sequence[str] = ()) -> tuple[Link, ...]:
        if not ignore:
            return self._broken if source is None else tuple(link for link in self._broken if link.source == source)
        overlay = self._overlay(ignore)
        return tuple(
            link
            for link in self._links(ignore)
            if (source is None or link.source == source)
            and not link.external
            and (
                link.target is None
                or (_resolve(link.target, overlay) is None and link.target not in self._resolved_pruned)
            )
        )

    def headings(self, member_id: str) -> tuple[Heading, ...]:
        return self._headings.get(member_id, ())

    def diagnostics(self, *, ignore: Sequence[str] = ()) -> Diagnostics:
        return self._overlay(ignore).diagnostics

    def work_snapshot(self, *, ignore: Sequence[str] = IGNORE) -> WorkSnapshot:
        key = tuple(ignore)
        with self._work_lock:
            cached = self._work.get(key)
            if cached is None:
                visible = {item.path for item in self._all_work if not ignored_by(item.page_path, key)}
                cached = WorkSnapshot(
                    replace(item, child_paths=tuple(child for child in item.child_paths if child in visible))
                    for item in self._all_work
                    if item.path in visible
                )
                self._work[key] = cached
            return cached


def materialize(session: ReadSession) -> SnapshotSession:
    """Copy an open session's answers; the result retains no session or disk handle."""
    rows = tuple(
        replace(row, sha256=None, fm=_copy_mapping(row.fm) if row.fm is not None else None) for row in session.members()
    )
    hashes = MappingProxyType({row.id: session.content_hash(row.id) for row in rows})
    diagnostics = session.diagnostics()
    all_diagnostics = session.diagnostics(ignore=("*",))
    unreadable_files = frozenset(diagnostics.unreadable) - frozenset(all_diagnostics.unreadable)
    concepts = [row.concept_id for row in rows if row.concept_id is not None]
    outlinks = MappingProxyType({cid: session.outlinks(cid) for cid in concepts})
    backlinks = MappingProxyType({cid: session.backlinks(cid) for cid in concepts})
    headings = MappingProxyType(
        {row.id: h for row in rows if row.id.endswith(".md") and (h := session.headings(row.id))}
    )
    work = _copy_work(session.work_snapshot())
    revision = index_revision(session)
    return SnapshotSession(
        backend=session.backend,
        fallback=session.fallback,
        generation=session.generation,
        index_epoch=revision[0] if revision is not None else None,
        rows=rows,
        concept_hashes=session.concept_hashes(),
        hashes=hashes,
        unreadable_files=unreadable_files,
        collision_order=MappingProxyType(dict(all_diagnostics.collisions)),
        outlinks=outlinks,
        backlinks=backlinks,
        broken=session.broken(),
        headings=headings,
        diagnostics=replace(
            diagnostics,
            unreadable=MappingProxyType(dict(diagnostics.unreadable)),
            parse_errors=MappingProxyType(dict(diagnostics.parse_errors)),
            collisions=MappingProxyType(dict(diagnostics.collisions)),
        ),
        work=work,
        all_work=_copy_work(session.work_snapshot(ignore=())),
    )
