"""Queries over one pinned WAL generation, without reading document bodies."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from types import MappingProxyType
from typing import cast
from urllib.parse import unquote

from okf_io import Heading, Link, ParseError, resolve_member
from okf_io.bundle import MemberKind, canonical_id

from okf_ext.readindex._sql import execute
from okf_ext.readindex.content import StoredContent
from okf_ext.readindex.model import Diagnostics, MemberRow
from okf_ext.readindex.store import ReadIndex


def _parse_error(value: str | None) -> ParseError | None:
    return ParseError(**json.loads(value)) if value is not None else None


def _link(row: tuple[object, ...]) -> Link:
    source, raw, target, fragment, external, image, line = row
    return Link(
        cast(str, source),
        cast(str, raw),
        cast(str | None, target),
        cast(str | None, fragment),
        bool(external),
        bool(image),
        cast(int | None, line),
    )


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


class IndexView:
    """A read transaction's membership and graph projections."""

    def __init__(self, index: ReadIndex, generation: int) -> None:
        self.generation = generation
        self._index = index
        self._conn = index.connection
        self._pruned = frozenset(row[0] for row in execute(self._conn, "SELECT id FROM pruned"))
        self._resolved: dict[str, str | None] = {}

    def _has_raw(self, path: str) -> bool:
        return (
            execute(self._conn, "SELECT 1 FROM members WHERE id=? AND unreadable IS NULL", (path,)).fetchone()
            is not None
        )

    def _canonical(self, path: str) -> str | None:
        collision = execute(self._conn, "SELECT ids_json FROM collisions WHERE canonical=?", (path,)).fetchone()
        if collision is not None:
            return cast(str, json.loads(collision[0])[-1])
        row = execute(self._conn, "SELECT id FROM members WHERE canonical=? AND unreadable IS NULL", (path,)).fetchone()
        return row[0] if row is not None else None

    def resolve(self, path: str) -> str | None:
        """Resolve exact or NFC membership, including safe pruned probes."""
        if path not in self._resolved:
            self._resolved[path] = resolve_member(
                path, root=self._index.root, has_raw=self._has_raw, canonical=self._canonical, pruned=self._pruned
            )
        return self._resolved[path]

    def _member_row(self, row: tuple[object, ...]) -> MemberRow:
        mid, kind, sha, type_, title, status, fm, exact, error, failures = row
        tags = tuple(
            r[0] for r in execute(self._conn, "SELECT tag FROM tags WHERE member=? ORDER BY CAST(tag AS BLOB)", (mid,))
        )
        return MemberRow(
            cast(str, mid),
            cast(MemberKind, kind),
            cast(str | None, sha),
            cast(str | None, type_),
            cast(str | None, title),
            cast(str | None, status),
            tags,
            MappingProxyType(json.loads(cast(str, fm))) if fm is not None else None,
            bool(exact),
            _parse_error(cast(str | None, error)),
            frozenset(json.loads(cast(str, failures))),
        )

    def members(
        self, *, prefix: str = "", kind: MemberKind | None = None, type: str | None = None
    ) -> tuple[MemberRow, ...]:
        """List readable members by case-sensitive raw prefix and metadata."""
        rows = execute(
            self._conn,
            "SELECT id, kind, sha256, type, title, status, fm_json, fm_exact, parse_error, coercion_failures "
            "FROM members "
            "WHERE unreadable IS NULL AND substr(CAST(id AS BLOB), 1, length(CAST(? AS BLOB))) = CAST(? AS BLOB) "
            "AND (? IS NULL OR kind = ?) AND (? IS NULL OR type = ?) ORDER BY CAST(id AS BLOB)",
            (prefix, prefix, kind, kind, type, type),
        )
        return tuple(self._member_row(row) for row in rows)

    def member(self, path: str) -> MemberRow | None:
        """Return a modelled member; pruned files have no persisted row."""
        mid = self.resolve(path)
        row = execute(
            self._conn,
            "SELECT id, kind, sha256, type, title, status, fm_json, fm_exact, parse_error, coercion_failures "
            "FROM members WHERE id=? AND unreadable IS NULL",
            (mid,),
        ).fetchone()
        return self._member_row(row) if row is not None else None

    def stored_content(self, member_id: str) -> StoredContent | None:
        """The stored columns a verified content read needs, for one exact raw markdown id."""
        row = execute(
            self._conn,
            "SELECT sha256, fm_json, fm_exact, body_line_offset, parse_error, coercion_failures FROM members "
            "WHERE id=? AND unreadable IS NULL AND kind IN ('concept', 'index', 'log')",
            (member_id,),
        ).fetchone()
        if row is None:
            return None
        sha, fm, exact, offset, error, failures = row
        return StoredContent(
            cast(str | None, sha),
            MappingProxyType(json.loads(cast(str, fm))) if fm is not None else None,
            bool(exact),
            cast(int | None, offset),
            _parse_error(cast(str | None, error)),
            frozenset(json.loads(cast(str, failures))),
        )

    def outlinks(self, concept_id: str) -> tuple[Link, ...]:
        return tuple(
            _link(row)
            for row in execute(
                self._conn,
                "SELECT source, raw, target, fragment, external, image, line FROM links "
                "WHERE source=? ORDER BY ordinal",
                (concept_id,),
            )
        )

    def backlinks(self, concept_id: str) -> tuple[str, ...]:
        target = concept_id + ".md"
        member = self.member(target)
        if member is None or member.kind != "concept" or member.id != target:
            return ()
        candidates = execute(
            self._conn,
            "SELECT DISTINCT source, target FROM links "
            "WHERE external=0 AND image=0 AND (target=? OR target_canonical=?)",
            (target, canonical_id(target)),
        )
        return tuple(sorted({source for source, destination in candidates if self.resolve(destination) == target}))

    def broken(self, source: str | None = None) -> tuple[Link, ...]:
        links = (
            _link(row)
            for row in execute(
                self._conn,
                "SELECT source, raw, target, fragment, external, image, line FROM links "
                "WHERE external=0 AND (? IS NULL OR source=?) "
                "ORDER BY CAST(source AS BLOB), coalesce(line, 0), CAST(raw AS BLOB), image",
                (source, source),
            )
        )
        return tuple(link for link in links if link.target is None or self.resolve(link.target) is None)

    def referrers(self, roots: Sequence[str]) -> frozenset[str]:
        """Raw ids of members holding a reference form that names a root or anything beneath it."""
        found: set[str] = set()
        for root in roots:
            r = canonical_id(root)
            rows = execute(
                self._conn,
                "SELECT member, target_canonical FROM refs WHERE target_canonical >= ? AND target_canonical < ?",
                (r, r + "\U0010ffff"),
            )
            found.update(m for m, t in rows if t == r or t == r + ".md" or t.startswith(r + "/"))
        return frozenset(found)

    def mentions(self, needles: Sequence[str]) -> frozenset[str]:
        """Raw ids of readable members whose frontmatter strings contain a needle, plus every inexact row."""
        wanted = tuple(canonical_id(n) for n in needles)
        found: set[str] = set()
        rows = execute(
            self._conn,
            "SELECT id, fm_json, fm_exact FROM members "
            "WHERE unreadable IS NULL AND (fm_json IS NOT NULL OR fm_exact=0)",
        )
        for mid, text, exact in rows:
            if not exact or text is None:
                found.add(mid)
                continue
            if text.isascii() and "%" not in text and "\\" not in text and not any(n in text for n in wanted):
                continue
            if any(n in canonical_id(unquote(s)) for s in _strings(json.loads(text)) for n in wanted):
                found.add(mid)
        return frozenset(found)

    def headings(self, member_id: str) -> tuple[Heading, ...]:
        return tuple(
            Heading(level, text, line, bool(quoted))
            for level, text, line, quoted in execute(
                self._conn,
                "SELECT level, text, line, quoted FROM headings WHERE member=? ORDER BY ordinal",
                (member_id,),
            )
        )

    def diagnostics(self) -> Diagnostics:
        unreadable = dict(execute(self._conn, "SELECT id, unreadable FROM members WHERE unreadable IS NOT NULL"))
        unreadable.update(execute(self._conn, "SELECT id, reason FROM unreadable_dirs"))
        errors = {
            mid: error
            for mid, value in execute(self._conn, "SELECT id, parse_error FROM members WHERE parse_error IS NOT NULL")
            if (error := _parse_error(value)) is not None
        }
        collisions = {
            key: tuple(json.loads(ids))
            for key, ids in execute(self._conn, "SELECT canonical, ids_json FROM collisions")
        }
        return Diagnostics(
            MappingProxyType(dict(sorted(unreadable.items()))),
            MappingProxyType(dict(sorted(errors.items()))),
            MappingProxyType(dict(sorted(collisions.items()))),
            self._pruned,
        )


@contextmanager
def read(index: ReadIndex) -> Iterator[IndexView]:
    """Pin the WAL snapshot by reading generation, then release with ROLLBACK."""
    conn: sqlite3.Connection = index.connection
    execute(conn, "BEGIN")
    try:
        generation = int(execute(conn, "SELECT value FROM meta WHERE key='generation'").fetchone()[0])
        yield IndexView(index, generation)
    finally:
        execute(conn, "ROLLBACK")
