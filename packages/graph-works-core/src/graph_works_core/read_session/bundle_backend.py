"""Lazy full-bundle display reads, the oracle for the indexed backend."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from okf_ext.readindex import Diagnostics, MemberRow, member_row
from okf_io import Bundle, Document, Heading, Link, LinkGraph, build_link_graph, document_headings
from okf_io.bundle import MemberKind
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.snapshot import WorkSnapshot

from graph_works_core.read_session.model import Backend, FallbackReason
from graph_works_core.workspace.bundle import load_bundle_at


def _dir_file(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def _documents(bundle: Bundle) -> dict[str, Document]:
    return {
        **{f"{cid}.md": doc for cid, doc in bundle.concepts.items()},
        **{_dir_file(d, "index.md"): doc for d, doc in bundle.indexes.items()},
        **{_dir_file(d, "log.md"): doc for d, doc in bundle.logs.items()},
    }


@dataclass(frozen=True)
class _Loaded:
    bundle: Bundle
    graph: LinkGraph
    rows: Mapping[str, MemberRow]


class BundleSession:
    """Pin one lazy full load per caller-supplied ignore tuple for a session."""

    def __init__(self, root: Path, *, fallback: FallbackReason | None) -> None:
        self._root = root
        self._fallback = fallback
        self._loads: dict[tuple[str, ...], _Loaded] = {}

    @property
    def backend(self) -> Backend:
        return "bundle"

    @property
    def fallback(self) -> FallbackReason | None:
        return self._fallback

    @property
    def generation(self) -> int | None:
        return None

    def _load(self, ignore: Sequence[str] = ()) -> _Loaded:
        key = tuple(ignore)
        if key not in self._loads:
            if not self._root.exists():
                raise FileNotFoundError(f"bundle root not found: {self._root}")
            bundle = load_bundle_at(self._root, ignore=key)
            projected = [member_row(f"{cid}.md", "concept", doc) for cid, doc in bundle.concepts.items()]
            projected.extend(member_row(_dir_file(d, "index.md"), "index", doc) for d, doc in bundle.indexes.items())
            projected.extend(member_row(_dir_file(d, "log.md"), "log", doc) for d, doc in bundle.logs.items())
            projected.extend(member_row(a, "asset") for a in bundle.assets)
            projected.extend(member_row(i, "ignored") for i in bundle.ignored)
            rows = MappingProxyType(
                {row.id: replace(row, sha256=None) for row in sorted(projected, key=lambda r: r.id)}
            )
            self._loads[key] = _Loaded(bundle, build_link_graph(bundle), rows)
        return self._loads[key]

    def concept_hashes(self, *, ignore: Sequence[str] = ()) -> Mapping[str, str] | None:
        """Full loads have no stored byte hashes; callers use their oracle path."""
        return None

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
            for row in self._load(ignore).rows.values()
            if row.id.startswith(prefix) and (kind is None or row.kind == kind) and (type is None or row.type == type)
        )

    def member(self, path: str, *, ignore: Sequence[str] = ()) -> MemberRow | None:
        loaded = self._load(ignore)
        mid = loaded.bundle.member_id(path)
        return loaded.rows.get(mid) if mid is not None else None

    def content_hash(self, member_id: str) -> str | None:
        """Return no change stamp: the full load keeps no bytes (D-003)."""
        return None

    def outlinks(self, concept_id: str, *, ignore: Sequence[str] = ()) -> tuple[Link, ...]:
        return self._load(ignore).graph.out.get(concept_id, ())

    def backlinks(self, concept_id: str, *, ignore: Sequence[str] = ()) -> tuple[str, ...]:
        return self._load(ignore).graph.backlinks.get(concept_id, ())

    def broken(self, source: str | None = None, *, ignore: Sequence[str] = ()) -> tuple[Link, ...]:
        links = self._load(ignore).graph.broken
        return links if source is None else tuple(link for link in links if link.source == source)

    def headings(self, member_id: str) -> tuple[Heading, ...]:
        doc = _documents(self._load().bundle).get(member_id)
        return document_headings(doc) if doc is not None else ()

    def diagnostics(self, *, ignore: Sequence[str] = ()) -> Diagnostics:
        bundle = self._load(ignore).bundle
        return Diagnostics(
            unreadable=MappingProxyType(dict(bundle.unreadable)),
            parse_errors=MappingProxyType(
                {mid: doc.parse_error for mid, doc in _documents(bundle).items() if doc.parse_error is not None}
            ),
            collisions=MappingProxyType(dict(bundle.canonical_collisions)),
            pruned=bundle.pruned,
        )

    def work_snapshot(self, *, ignore: Sequence[str] = IGNORE) -> WorkSnapshot:
        return load_items(self._load(ignore).bundle)
