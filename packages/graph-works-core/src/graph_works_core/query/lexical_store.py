"""Content-hash synchronized SQLite BM25 postings, beside the embedding pages table.

Every query compares document hashes in a read transaction. Changed pages are
prepared outside write locks; one transaction rechecks and publishes the schema,
postings, excerpts and corpus statistics together, then scores that corpus.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from okf_ext import search as ext_search
from okf_io import Document, read_member

from graph_works_core.agent_substrate.agent_tools import bounded_excerpt, missing_concept_excerpt

logger = logging.getLogger(__name__)

LEX_SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5000
_TABLES = ("lex_postings", "lex_docs", "lex_meta")
_COLUMNS = {
    "lex_postings": "term, concept_id, tf",
    "lex_docs": "concept_id, sha256, length, excerpt",
    "lex_meta": "key, value",
}
_DDL = (
    "CREATE TABLE lex_docs (concept_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL,"
    " length INTEGER NOT NULL, excerpt TEXT NOT NULL)",
    "CREATE TABLE lex_postings (term TEXT NOT NULL, concept_id TEXT NOT NULL, tf INTEGER NOT NULL,"
    " PRIMARY KEY (term, concept_id)) WITHOUT ROWID",
    "CREATE INDEX lex_postings_by_doc ON lex_postings (concept_id)",
    "CREATE TABLE lex_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


class LexicalBusy(Exception):
    """The locked store does not hold this session's content after the timeout."""


@dataclass(frozen=True)
class Prepared:
    """A page's postings, length and bounded excerpt, associated with its hash."""

    sha256: str
    tf: Mapping[str, int]
    length: int
    excerpt: str


@dataclass(frozen=True)
class LexicalResult:
    ranked: tuple[tuple[str, float], ...]
    tokenized: int
    wrote: bool
    rebuilt: str | None


def prepare_page(root: Path, concept_id: str, sha256: str, *, excerpt_chars: int) -> Prepared:
    """Prepare one concept exactly as build_index does, tolerating unreadable pages."""
    result = read_member(root, f"{concept_id}.md")
    if not isinstance(result, Document):
        return Prepared(sha256, MappingProxyType({}), 0, missing_concept_excerpt(concept_id))
    tf, length = ext_search.term_postings(result)
    return Prepared(sha256, tf, length, bounded_excerpt(result, concept_id, max_chars=excerpt_chars))


def _is_busy(exc: sqlite3.DatabaseError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return "locked" in str(exc).lower() or "busy" in str(exc).lower()


def _is_corrupt(exc: sqlite3.DatabaseError) -> bool:
    """Only file corruption justifies discarding the shared embedding database."""
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
    message = str(exc).lower()
    return "not a database" in message or "database disk image is malformed" in message


def _connect(db_path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=busy_timeout_ms / 1000, isolation_level=None)
    try:
        _enter_wal(conn, busy_timeout_ms)
    except BaseException:
        conn.close()
        raise
    return conn


def _enter_wal(conn: sqlite3.Connection, busy_timeout_ms: int) -> None:
    """Switch to WAL, retrying within the busy budget.

    SQLite's busy handler does not cover the journal-mode switch on a fresh
    database: a second process opening the file while the first one switches
    gets SQLITE_BUSY at once. Retry until the budget is spent, then let the
    busy error surface (callers map it to LexicalBusy).
    """
    deadline = time.monotonic() + busy_timeout_ms / 1000
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.DatabaseError as exc:
            if not _is_busy(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _begin_write(conn: sqlite3.Connection) -> None:
    """The write transaction seam used by tests and counters."""
    conn.execute("BEGIN IMMEDIATE")


def _wanted() -> dict[str, str]:
    return {"schema_version": str(LEX_SCHEMA_VERSION), "scoring_version": ext_search.SCORING_VERSION}


def _rebuild_reason(conn: sqlite3.Connection) -> str | None:
    tables = {
        name
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('lex_postings', 'lex_docs', 'lex_meta')"
        )
    }
    if not tables:
        return "created"
    if tables != set(_TABLES):
        return "schema"
    try:
        for table, columns in _COLUMNS.items():
            conn.execute(f"SELECT {columns} FROM {table} LIMIT 0")
    except sqlite3.DatabaseError as exc:
        if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_ERROR:
            return "schema"
        raise
    found = dict(conn.execute("SELECT key, value FROM lex_meta"))
    wanted = _wanted()
    if found.get("schema_version") != wanted["schema_version"]:
        return "schema"
    if found.get("scoring_version") != wanted["scoring_version"]:
        return "scoring"
    try:
        stats = [int(found[key]) for key in ("n", "total_length")]
    except (KeyError, TypeError, ValueError):
        return "schema"
    if any(value < 0 for value in stats):
        return "schema"
    return None


def _replace_schema(conn: sqlite3.Connection) -> None:
    """Replace only lexical tables within the caller's publication transaction."""
    for table in _TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    for ddl in _DDL:
        conn.execute(ddl)
    conn.executemany(
        "INSERT INTO lex_meta (key, value) VALUES (?, ?)", [*_wanted().items(), ("n", "0"), ("total_length", "0")]
    )


def _stored(conn: sqlite3.Connection) -> dict[str, str]:
    return {_string(cid): str(sha) for cid, sha in conn.execute("SELECT concept_id, sha256 FROM lex_docs")}


def _binding(value: str) -> str | bytes:
    """Keep ordinary strings as TEXT; persist reader surrogates losslessly as BLOB."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return value.encode("utf-8", errors="surrogatepass")
    return value


def _string(value: str | bytes) -> str:
    """Decode only lexical string columns, leaving shared embedding BLOBs alone."""
    return value.decode("utf-8", errors="surrogatepass") if isinstance(value, bytes) else value


def _apply(
    conn: sqlite3.Connection, stored: Mapping[str, str], target: Mapping[str, str], prepared: Mapping[str, Prepared]
) -> None:
    gone = [cid for cid in stored if cid not in target]
    changed = [cid for cid, sha in target.items() if stored.get(cid) != sha]
    for cid in (*gone, *changed):
        conn.execute("DELETE FROM lex_postings WHERE concept_id = ?", (_binding(cid),))
        conn.execute("DELETE FROM lex_docs WHERE concept_id = ?", (_binding(cid),))
    for cid in changed:
        page = prepared[cid]
        conn.execute(
            "INSERT INTO lex_docs (concept_id, sha256, length, excerpt) VALUES (?, ?, ?, ?)",
            (_binding(cid), page.sha256, page.length, _binding(page.excerpt)),
        )
        conn.executemany(
            "INSERT INTO lex_postings (term, concept_id, tf) VALUES (?, ?, ?)",
            [(term, _binding(cid), tf) for term, tf in page.tf.items()],
        )
    row = conn.execute("SELECT COUNT(*), COALESCE(SUM(length), 0) FROM lex_docs").fetchone()
    assert row is not None
    n, total_length = row
    conn.executemany(
        "UPDATE lex_meta SET value = ? WHERE key = ?", [(str(n), "n"), (str(total_length), "total_length")]
    )


def _score(conn: sqlite3.Connection, query: Sequence[str], limit: int) -> tuple[tuple[str, float], ...]:
    if not query or limit <= 0:
        return ()
    meta = dict(conn.execute("SELECT key, value FROM lex_meta WHERE key IN ('n', 'total_length')"))
    terms = sorted(set(query))
    marks = ",".join("?" * len(terms))
    df = {
        str(term): int(count)
        for term, count in conn.execute(
            f"SELECT term, COUNT(*) FROM lex_postings WHERE term IN ({marks}) GROUP BY term", terms
        )
    }
    tf_by_doc: dict[str, dict[str, int]] = {}
    lengths: dict[str, int] = {}
    for term, cid, tf, length in conn.execute(
        f"SELECT p.term, p.concept_id, p.tf, d.length FROM lex_postings AS p"
        f" JOIN lex_docs AS d USING (concept_id) WHERE p.term IN ({marks})",
        terms,
    ):
        concept_id = _string(cid)
        tf_by_doc.setdefault(concept_id, {})[str(term)] = int(tf)
        lengths[concept_id] = int(length)
    postings: list[tuple[str, Mapping[str, int], int]] = [
        (cid, tf_by_doc[cid], lengths[cid]) for cid in sorted(tf_by_doc)
    ]
    return ext_search.bm25_from_stats(
        total=int(meta["n"]), total_length=int(meta["total_length"]), df=df, postings=postings, query=query
    )[:limit]


def _verify_and_score(
    conn: sqlite3.Connection, target: Mapping[str, str], query: Sequence[str], limit: int
) -> tuple[str | None, dict[str, str], tuple[tuple[str, float], ...] | None]:
    """Verify versions and content, and score current rows, in one read snapshot."""
    conn.execute("BEGIN")
    try:
        reason = _rebuild_reason(conn)
        stored = _stored(conn) if reason is None else {}
        ranked = _score(conn, query, limit) if reason is None and stored == target else None
        return reason, stored, ranked
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def _run(
    db_path: Path,
    target: Mapping[str, str],
    load: Callable[[str, str], Prepared],
    query: Sequence[str],
    limit: int,
    busy_timeout_ms: int,
) -> LexicalResult:
    conn = _connect(db_path, busy_timeout_ms)
    try:
        reason, stored, ranked = _verify_and_score(conn, target, query, limit)
        if ranked is not None:
            return LexicalResult(ranked, 0, False, None)
        prepared: dict[str, Prepared] = {}
        tokenized = 0
        while True:
            # Another session can replace formerly current rows between our read
            # and write transactions. Prepare any newly required pages after
            # releasing the lock, then recheck; loading never holds a write lock.
            for cid, sha in target.items():
                if stored.get(cid) != sha and cid not in prepared:
                    prepared[cid] = load(cid, sha)
                    tokenized += 1
            try:
                _begin_write(conn)
            except sqlite3.DatabaseError as exc:
                if not _is_busy(exc):
                    raise
                _, _, ranked = _verify_and_score(conn, target, query, limit)
                if ranked is None:
                    raise LexicalBusy(f"lexical index busy past {busy_timeout_ms} ms: {db_path}") from exc
                return LexicalResult(ranked, tokenized, False, None)
            try:
                reason = _rebuild_reason(conn)
                stored = _stored(conn) if reason is None else {}
                if any(stored.get(cid) != sha and cid not in prepared for cid, sha in target.items()):
                    conn.execute("ROLLBACK")
                    continue
                if reason is None and stored == target:
                    ranked = _score(conn, query, limit)
                    conn.execute("COMMIT")
                    return LexicalResult(ranked, tokenized, False, None)
                if reason is not None:
                    _replace_schema(conn)
                _apply(conn, stored, target, prepared)
                ranked = _score(conn, query, limit)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            if reason not in (None, "created"):
                logger.warning("lexical index rebuilt (%s)", reason)
            return LexicalResult(ranked, tokenized, True, reason)
    finally:
        conn.close()


def sync_and_score(
    db_path: Path,
    target: Mapping[str, str],
    load: Callable[[str, str], Prepared],
    query: Sequence[str],
    limit: int,
    *,
    discard: Callable[[], None],
    busy_timeout_ms: int = BUSY_TIMEOUT_MS,
) -> LexicalResult:
    """Synchronize and rank this session's corpus; discard unreadable databases once.

    ``discard`` also invalidates the embedding manifest. Busy stores are never
    discarded: callers can fall back to their in-memory index on LexicalBusy.
    """
    try:
        return _run(db_path, target, load, query, limit, busy_timeout_ms)
    except sqlite3.DatabaseError as exc:
        if _is_busy(exc):
            raise LexicalBusy(f"lexical index busy past {busy_timeout_ms} ms: {db_path}") from exc
        if not _is_corrupt(exc):
            raise
        logger.warning("lexical index unreadable (%s); recreating %s", exc, db_path)
        discard()
        try:
            result = _run(db_path, target, load, query, limit, busy_timeout_ms)
        except sqlite3.DatabaseError as retry_exc:
            if _is_busy(retry_exc):
                raise LexicalBusy(f"lexical index busy past {busy_timeout_ms} ms: {db_path}") from retry_exc
            raise
        return replace(result, rebuilt="corrupt")


def stored_excerpts(db_path: Path, ids: Sequence[str], target: Mapping[str, str]) -> dict[str, str]:
    """Return excerpts only for requested concepts whose stored hashes are current."""
    if not ids:
        return {}
    conn = _connect(db_path, BUSY_TIMEOUT_MS)
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT concept_id, sha256, excerpt FROM lex_docs WHERE concept_id IN ({marks})",
            [_binding(cid) for cid in ids],
        ).fetchall()
        return {_string(cid): _string(text) for cid, sha, text in rows if target.get(_string(cid)) == sha}
    finally:
        conn.close()


def drop_tables(db_path: Path) -> None:
    """Drop lexical tables atomically, preserving embedding pages for cold-sync probes."""
    conn = _connect(db_path, BUSY_TIMEOUT_MS)
    try:
        _begin_write(conn)
        for table in _TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


__all__ = [
    "BUSY_TIMEOUT_MS",
    "LEX_SCHEMA_VERSION",
    "LexicalBusy",
    "LexicalResult",
    "Prepared",
    "drop_tables",
    "prepare_page",
    "stored_excerpts",
    "sync_and_score",
]
