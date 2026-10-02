"""Optimistically prepare bundle projections, then atomically publish them."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from okf_io import Document, document_headings, document_links, effective_status, read_member, walk
from okf_io.bundle import Member, MemberStat, Unreadable, canonical_id

from okf_ext.readindex._sql import bindings, execute
from okf_ext.readindex.model import IndexBusy, Reconcile
from okf_ext.readindex.store import ReadIndex, require_identity

RACY_WINDOW_NS = 2_000_000_000
_COLUMNS = (
    "id",
    "canonical",
    "kind",
    "size",
    "mtime_ns",
    "ino",
    "ctime_ns",
    "sha256",
    "racy",
    "unreadable",
    "type",
    "title",
    "status",
    "fm_json",
    "fm_exact",
    "parse_error",
    "coercion_failures",
    "body_line_offset",
)


@dataclass(frozen=True)
class _Stored:
    kind: str
    stat: MemberStat | None
    digest: str | None
    racy: bool
    unreadable: str | None


@dataclass(frozen=True)
class _Prepared:
    member_id: str
    row: tuple[object, ...]
    tags: tuple[tuple[object, ...], ...]
    headings: tuple[tuple[object, ...], ...]
    links: tuple[tuple[object, ...], ...]


def _prepare(member: Member, row: list[object], doc: Document | None) -> _Prepared:
    tags: tuple[tuple[object, ...], ...] = ()
    headings: tuple[tuple[object, ...], ...] = ()
    links: tuple[tuple[object, ...], ...] = ()
    if doc is not None:
        _project(row, doc)
        tags = tuple(bindings((member.id, tag)) for tag in set(doc.fm.tags))
        headings = tuple(
            bindings((member.id, n, h.level, h.text, h.line, int(h.quoted)))
            for n, h in enumerate(document_headings(doc))
        )
        if member.kind == "concept":
            links = tuple(
                bindings(
                    (
                        link.source,
                        n,
                        link.raw,
                        link.target,
                        canonical_id(link.target) if link.target is not None else None,
                        link.fragment,
                        int(link.external),
                        int(link.image),
                        link.line,
                    )
                )
                for n, link in enumerate(document_links(doc, source_id=member.id[:-3]))
            )
    return _Prepared(member.id, bindings(row), tags, headings, links)


def _stored(conn: sqlite3.Connection) -> dict[str, _Stored]:
    return {
        row[0]: _Stored(row[1], MemberStat(*row[2:6]) if row[2] is not None else None, row[6], bool(row[7]), row[8])
        for row in execute(
            conn, "SELECT id, kind, size, mtime_ns, ino, ctime_ns, sha256, racy, unreadable FROM members"
        )
    }


def _auxiliary(conn: sqlite3.Connection) -> dict[str, list[tuple[str, ...]]]:
    return {
        table: sorted(execute(conn, f"SELECT * FROM {table}").fetchall())
        for table in ("collisions", "unreadable_dirs", "pruned")
    }


def _read_bytes(root: Path, member_id: str) -> bytes:
    with root.joinpath(*member_id.split("/")).open("rb") as stream:
        return stream.read()


def _stable(root: Path, member: Member) -> bool:
    try:
        current = MemberStat.of(root.joinpath(*member.id.split("/")).stat())
    except OSError:
        return False
    return member.stat is not None and member.stat.same(current)


def _bump_generation(conn: sqlite3.Connection, generation: int) -> None:
    execute(conn, "UPDATE meta SET value=? WHERE key='generation'", (str(generation),))


def _generation(conn: sqlite3.Connection) -> int:
    return int(execute(conn, "SELECT value FROM meta WHERE key='generation'").fetchone()[0])


def _base(member: Member, digest: str | None, racy: bool, unreadable: str | None) -> list[object]:
    stat = member.stat
    return [
        member.id,
        canonical_id(member.id),
        member.kind,
        stat.size if stat else None,
        stat.mtime_ns if stat else None,
        stat.ino if stat else None,
        stat.ctime_ns if stat else None,
        digest,
        int(racy),
        unreadable,
        None,
        None,
        None,
        None,
        1,
        None,
        "[]",
        None,
    ]


def _project(row: list[object], doc: Document) -> None:
    exact = True

    def inexact(value: object) -> str:
        nonlocal exact
        exact = False
        try:
            return repr(value)
        except RecursionError:
            return "<recursive frontmatter value>"

    def finite(value: object) -> object:
        if isinstance(value, float) and not math.isfinite(value):
            return inexact(value)
        if isinstance(value, dict):
            return {key: finite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [finite(item) for item in value]
        return value

    try:
        data = doc.fm_data(dates="iso")
    except RecursionError:
        # Preserve JSON-supported fields; only unrepresentable frontmatter
        # values use repr. Never include the document body in this fallback.
        data = {}
        for key, value in doc.fm_raw.items():
            try:
                data[str(key)] = json.loads(json.dumps(value, allow_nan=False, default=inexact))
            except (TypeError, ValueError, RecursionError):
                data[str(key)] = inexact(value)
    fm_json = json.dumps(finite(data), ensure_ascii=False, allow_nan=False, default=inexact)
    row[10:] = [
        doc.fm.type,
        doc.fm.title,
        effective_status(doc.fm),
        fm_json,
        int(exact),
        json.dumps(asdict(doc.parse_error), ensure_ascii=False) if doc.parse_error is not None else None,
        json.dumps(sorted(doc.fm.coercion_failures)),
        doc.body_line_offset,
    ]


def _delete_children(conn: sqlite3.Connection, member_id: str) -> None:
    execute(conn, "DELETE FROM tags WHERE member=?", (member_id,))
    execute(conn, "DELETE FROM headings WHERE member=?", (member_id,))
    if member_id.endswith(".md"):
        execute(conn, "DELETE FROM links WHERE source=?", (member_id[:-3],))


def reconcile(index: ReadIndex) -> Reconcile:
    """Refresh changed members without holding a transaction during file I/O."""
    start_ns = time.time_ns()
    conn = index.connection
    # Pin metadata, member observations and auxiliary rows to one WAL snapshot.
    # Release the read transaction before any filesystem or parser work.
    execute(conn, "BEGIN")
    try:
        require_identity(index)
        stored = _stored(conn)
        auxiliary = _auxiliary(conn)
        generation = _generation(conn)
    finally:
        execute(conn, "ROLLBACK")
    walked = walk(index.root, ignore=index.ignore, prune=index.prune)
    added: list[str] = []
    changed: list[str] = []
    unsettled: list[str] = []
    rows: list[_Prepared] = []
    refreshes: list[tuple[object, ...]] = []
    state = dict(stored)
    parsed = 0
    for member in walked.members:
        old = stored.get(member.id)
        if old and old.kind == member.kind and member.stat is not None and member.stat.same(old.stat) and not old.racy:
            continue
        racy = member.stat is not None and member.stat.mtime_ns > start_ns - RACY_WINDOW_NS
        digest: str | None = None
        failure: str | None = None
        doc: Document | None = None
        if member.kind not in ("asset", "ignored"):
            try:
                data = _read_bytes(index.root, member.id)
            except OSError as exc:
                failure = f"could not be read: {exc}"
            else:
                if not _stable(index.root, member):
                    unsettled.append(member.id)
                    continue
                digest = hashlib.sha256(data).hexdigest()
                if not (old and old.digest == digest and old.kind == member.kind and old.unreadable is None):
                    result = read_member(index.root, member.id)
                    parsed += 1
                    # read_member reads again: its projection must match the hashed stat.
                    if not _stable(index.root, member):
                        unsettled.append(member.id)
                        continue
                    if isinstance(result, Unreadable):
                        failure = result.reason
                    else:
                        doc = result
        visible = old is None or old.kind != member.kind or old.digest != digest or old.unreadable != failure
        row = _base(member, digest, racy, failure)
        if visible:
            (added if old is None else changed).append(member.id)
            rows.append(_prepare(member, row, doc))
        else:
            refreshes.append(bindings((*row[3:7], int(racy), member.id)))
        state[member.id] = _Stored(member.kind, member.stat, digest, racy, failure)
    seen = {member.id for member in walked.members}
    removed = tuple(sorted(stored.keys() - seen))
    for member_id in removed:
        del state[member_id]
    canonical: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    for member in walked.members:
        entry = state.get(member.id)
        if entry is None or (entry.kind not in ("asset", "ignored") and entry.unreadable is not None):
            continue
        if not member.id.isascii():
            key = canonical_id(member.id)
            if key in canonical:
                collisions.setdefault(key, [canonical[key]]).append(member.id)
            canonical[key] = member.id
    replacements: dict[str, list[tuple[str, ...]]] = {
        "collisions": [(key, json.dumps(ids)) for key, ids in sorted(collisions.items())],
        "unreadable_dirs": sorted(walked.unreadable_dirs.items()),
        "pruned": [(key,) for key in sorted(walked.pruned)],
    }
    replacements = {table: values for table, values in replacements.items() if auxiliary[table] != values}
    prepared_replacements = {table: tuple(bindings(row) for row in values) for table, values in replacements.items()}
    published = bool(rows or refreshes or removed or replacements)
    if published:
        try:
            execute(conn, "BEGIN IMMEDIATE")
            require_identity(index)
            # Every optimistic decision (including skips, deletes and auxiliary
            # replacements) depends on this snapshot. Never attach a fresh stat
            # to another publisher's projection, even when generation is unchanged.
            if _stored(conn) != stored or _auxiliary(conn) != auxiliary:
                raise IndexBusy("read-index snapshot changed during preparation; retry reconcile")
            generation = _generation(conn)
            for member_id in removed:
                _delete_children(conn, member_id)
                execute(conn, "DELETE FROM members WHERE id=?", (member_id,))
            for prepared in rows:
                _delete_children(conn, prepared.member_id)
                # All body parsing, frontmatter access and binding preparation
                # finished before BEGIN IMMEDIATE; cache eviction is irrelevant.
                conn.execute(
                    f"INSERT OR REPLACE INTO members ({','.join(_COLUMNS)}) "
                    f"VALUES ({','.join('?' for _ in prepared.row)})",
                    prepared.row,
                )
                conn.executemany("INSERT INTO tags VALUES (?, ?)", prepared.tags)
                conn.executemany("INSERT INTO headings VALUES (?, ?, ?, ?, ?, ?)", prepared.headings)
                conn.executemany("INSERT INTO links VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", prepared.links)
            conn.executemany("UPDATE members SET size=?, mtime_ns=?, ino=?, ctime_ns=?, racy=? WHERE id=?", refreshes)
            for table, values in prepared_replacements.items():
                execute(conn, f"DELETE FROM {table}")
                columns = 1 if table == "pruned" else 2
                conn.executemany(f"INSERT INTO {table} VALUES ({','.join('?' for _ in range(columns))})", values)
            if added or changed or removed or replacements:
                generation += 1
                _bump_generation(conn, generation)
            execute(conn, "UPDATE meta SET value=? WHERE key='last_reconcile_ns'", (str(start_ns),))
            execute(conn, "COMMIT")
        except BaseException as exc:
            if conn.in_transaction:
                execute(conn, "ROLLBACK")
            if isinstance(exc, sqlite3.OperationalError) and any(
                word in str(exc).lower() for word in ("locked", "busy")
            ):
                raise IndexBusy(str(exc)) from exc
            raise
    return Reconcile(
        generation,
        tuple(added),
        tuple(changed),
        removed,
        parsed,
        tuple(unsettled),
        tuple(sorted(key for key, entry in state.items() if entry.racy)),
        published,
    )


def verify(index: ReadIndex) -> tuple[str, ...]:
    """Re-read hashed markdown members and report drift without writing."""
    changed: list[str] = []
    for member_id, digest in execute(
        index.connection,
        "SELECT id, sha256 FROM members WHERE kind IN ('concept', 'index', 'log') "
        "AND sha256 IS NOT NULL ORDER BY CAST(id AS BLOB)",
    ):
        try:
            current = hashlib.sha256(_read_bytes(index.root, member_id)).hexdigest()
        except OSError:
            changed.append(member_id)
        else:
            if current != digest:
                changed.append(member_id)
    return tuple(changed)
