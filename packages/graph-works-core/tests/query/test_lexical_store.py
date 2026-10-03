"""The postings store: exact BM25 parity, content-diff sync, one-transaction publication, recovery."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

import pytest
from graph_works_core.query import lexical_store as ls
from okf_ext import search as ext_search
from okf_io import load_bundle


def _write(root: Path, files: Mapping[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def _target(root: Path) -> dict[str, str]:
    import hashlib

    bundle = load_bundle(root)
    return {cid: hashlib.sha256((root / f"{cid}.md").read_bytes()).hexdigest() for cid in sorted(bundle.concepts)}


def _loader(root: Path, seen: list[str] | None = None):
    def load(cid: str, sha: str) -> ls.Prepared:
        if seen is not None:
            seen.append(cid)
        return ls.prepare_page(root, cid, sha, excerpt_chars=1_500)

    return load


def _oracle(root: Path, query: str, limit: int) -> list[tuple[str, float]]:
    hits = ext_search.search(ext_search.build_index(load_bundle(root)), query, limit=limit)
    return [(hit.concept_id, hit.score) for hit in hits]


def _sync(db: Path, root: Path, query: str = "", limit: int = 15, seen=None) -> ls.LexicalResult:
    return ls.sync_and_score(
        db,
        _target(root),
        _loader(root, seen),
        ext_search.tokenize(query),
        limit,
        discard=lambda: db.unlink(missing_ok=True),
    )


def _dump(db: Path) -> tuple[list[tuple], list[tuple], dict[str, str]]:
    with closing(sqlite3.connect(db)) as conn, conn:
        docs = conn.execute("SELECT * FROM lex_docs ORDER BY concept_id").fetchall()
        postings = conn.execute("SELECT * FROM lex_postings ORDER BY term, concept_id").fetchall()
        meta = dict(conn.execute("SELECT key, value FROM lex_meta").fetchall())
    return docs, postings, meta


CORPUS = {
    "a.md": "---\ntitle: Token\n---\nrefresh token rotation\n",
    "a-b.md": "---\ntitle: Token\n---\nrefresh token rotation\n",  # ties with `a`; concept ids sort `a` before `a-b`
    "c/storage.md": "---\ntitle: Storage\ntags: [blob]\n---\nblob storage retention\n",
    "c/broken.md": "---\ntitle: [unclosed\n---\ntoken in a broken page\n",
    "c/empty.md": "---\ntitle: Empty\n---\n",
}


@pytest.mark.parametrize("query", ["token", "token token refresh", "blob storage", "zzz", "token zzz"])
def test_scores_equal_the_in_memory_search(tmp_path, query) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    assert list(_sync(db, root, query).ranked) == _oracle(root, query, 15)


def test_ties_break_in_concept_id_order(tmp_path) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    ranked = [cid for cid, _ in _sync(db, root, "rotation").ranked]
    assert ranked == ["a", "a-b"] == [cid for cid, _ in _oracle(root, "rotation", 15)]


@pytest.mark.parametrize("concept_id", ["\ud800", "\udc80", "\ud800\udc00", r"literal\uD800", "café 東京 𐀀"])
def test_identifiers_round_trip_through_sync_scoring_excerpts_updates_and_deletion(tmp_path, concept_id):
    db = tmp_path / "search.db"
    target = {concept_id: "first"}
    page = ls.Prepared("first", {"token": 1}, 1, "# \ud800 café 東京 𐀀")

    def sync():
        return ls.sync_and_score(db, target, lambda cid, sha: page, ["token"], 1, discard=lambda: None)

    cold = sync()
    assert cold.ranked == ((concept_id, 0.28768207245178085),)
    assert ls.stored_excerpts(db, [concept_id], target) == {concept_id: "# \ud800 café 東京 𐀀"}
    warm = sync()
    assert warm.ranked == cold.ranked and not warm.wrote and warm.tokenized == 0
    target[concept_id] = "second"
    page = ls.Prepared("second", {"token": 1}, 1, "# \udc00 edited")
    assert sync().ranked == cold.ranked
    assert ls.stored_excerpts(db, [concept_id], target) == {concept_id: "# \udc00 edited"}
    assert ls.stored_excerpts(db, [concept_id], {concept_id: "stale"}) == {}
    target.clear()
    assert sync().ranked == ()
    assert ls.stored_excerpts(db, [concept_id], {concept_id: "second"}) == {}


def test_limit_cuts_after_ranking(tmp_path) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    assert list(_sync(db, root, "token", limit=2).ranked) == _oracle(root, "token", 2)


def test_unchanged_sync_tokenizes_nothing_and_writes_nothing(tmp_path, monkeypatch) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    first = _sync(db, root)
    assert first.wrote and first.rebuilt == "created" and first.tokenized == len(CORPUS)
    writes: list[int] = []
    real = ls._begin_write
    monkeypatch.setattr(ls, "_begin_write", lambda conn: writes.append(1) or real(conn))
    seen: list[str] = []
    second = _sync(db, root, "token", seen=seen)
    assert (seen, writes, second.wrote, second.rebuilt) == ([], [], False, None)


@pytest.mark.parametrize("change", ["edit", "add", "delete", "rename", "touch"])
def test_one_sync_converges_to_a_fresh_build(tmp_path, change) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    page = root / "c/storage.md"
    if change == "edit":
        page.write_text("---\ntitle: Storage\n---\nnew words entirely\n", encoding="utf-8", newline="\n")
    elif change == "add":
        _write(root, {"c/new.md": "---\ntitle: New\n---\nfresh page\n"})
    elif change == "delete":
        page.unlink()
    elif change == "rename":
        page.rename(root / "c/stored.md")
    else:
        import os

        os.utime(page, None)
    seen: list[str] = []
    _sync(db, root, seen=seen)
    fresh = tmp_path / "fresh.db"
    _sync(fresh, root)
    assert _dump(db) == _dump(fresh)
    assert len(seen) == {"edit": 1, "add": 1, "delete": 0, "rename": 1, "touch": 0}[change]


def test_excerpts_come_from_the_store_and_only_when_current(tmp_path) -> None:
    from graph_works_core.agent_substrate.agent_tools import read_bounded_page

    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    bundle, target = load_bundle(root), _target(root)
    got = ls.stored_excerpts(db, ["a", "c/storage", "c/missing"], target)
    assert got == {cid: read_bounded_page(bundle, cid, max_chars=1_500) for cid in ("a", "c/storage")}
    stale = dict(target, a="0" * 64)
    assert "a" not in ls.stored_excerpts(db, ["a"], stale)


def test_old_excerpt_display_cache_rebuilds_with_single_normalization(tmp_path):
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, {"nested.md.md": "---\ntitle: ''\n---\ntoken\n"})
    _sync(db, root)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE lex_meta SET value = '1' WHERE key = 'schema_version'")
        conn.execute("UPDATE lex_docs SET excerpt = ?", ("# Nested\n\ntoken",))
    result = _sync(db, root, "token")
    assert result.rebuilt == "schema"
    assert ls.stored_excerpts(db, ["nested.md"], _target(root)) == {"nested.md": "# Nested.Md\n\ntoken"}


@pytest.mark.parametrize(("key", "reason"), [("schema_version", "schema"), ("scoring_version", "scoring")])
def test_version_mismatch_rebuilds(tmp_path, key, reason) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE lex_meta SET value = 'stale' WHERE key = ?", (key,))
    result = _sync(db, root, "token")
    assert result.rebuilt == reason and list(result.ranked) == _oracle(root, "token", 15)


def test_corrupt_file_is_discarded_and_rebuilt(tmp_path) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    db.write_bytes(b"this is not a database" * 100)
    discarded: list[int] = []

    def discard() -> None:
        discarded.append(1)
        db.unlink()

    result = ls.sync_and_score(db, _target(root), _loader(root), ["token"], 15, discard=discard)
    assert discarded == [1] and result.rebuilt == "corrupt"
    assert list(result.ranked) == _oracle(root, "token", 15)


@pytest.mark.parametrize("key", ["schema_version", "scoring_version"])
def test_pages_table_is_never_touched(tmp_path, key) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE pages (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL, embedding BLOB NOT NULL)")
        conn.execute("INSERT INTO pages VALUES ('x', 'h', x'00')")
    _sync(db, root)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE lex_meta SET value = 'stale' WHERE key = ?", (key,))
    _sync(db, root)
    with closing(sqlite3.connect(db)) as conn, conn:
        assert conn.execute("SELECT path FROM pages").fetchall() == [("x",)]


def test_failure_before_commit_keeps_the_previous_sync(tmp_path, monkeypatch) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    before = _dump(db)
    (root / "a.md").write_text("---\ntitle: A\n---\nchanged\n", encoding="utf-8", newline="\n")
    real = ls._apply

    def boom(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(ls, "_apply", boom)
    with pytest.raises(RuntimeError):
        _sync(db, root)
    assert _dump(db) == before
    monkeypatch.setattr(ls, "_apply", real)
    _sync(db, root)
    fresh = tmp_path / "fresh.db"
    _sync(fresh, root)
    assert _dump(db) == _dump(fresh)


def test_busy_with_stale_rows_raises_and_busy_with_current_rows_scores(tmp_path) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        current = ls.sync_and_score(
            db, _target(root), _loader(root), ["token"], 15, discard=lambda: None, busy_timeout_ms=100
        )
        assert not current.wrote and list(current.ranked) == _oracle(root, "token", 15)
        (root / "a.md").write_text("---\ntitle: A\n---\nchanged\n", encoding="utf-8", newline="\n")
        with pytest.raises(ls.LexicalBusy):
            ls.sync_and_score(
                db, _target(root), _loader(root), ["token"], 15, discard=lambda: None, busy_timeout_ms=100
            )
    finally:
        holder.execute("ROLLBACK")
        holder.close()


class _WalConn:
    """A connection whose journal-mode switch is busy for the first ``busy`` calls."""

    def __init__(self, busy: int) -> None:
        self.busy, self.calls = busy, 0

    def execute(self, sql: str) -> None:
        self.calls += 1
        if self.calls <= self.busy:
            raise sqlite3.OperationalError("database is locked")


def test_wal_switch_retries_a_busy_database_within_the_budget() -> None:
    conn = _WalConn(busy=2)
    ls._enter_wal(conn, 1000)  # type: ignore[arg-type]
    assert conn.calls == 3


def test_wal_switch_surfaces_busy_once_the_budget_is_spent() -> None:
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        ls._enter_wal(_WalConn(busy=10**6), 0)  # type: ignore[arg-type]


def test_wal_switch_does_not_retry_other_errors() -> None:
    class Broken:
        def execute(self, sql: str) -> None:
            raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        ls._enter_wal(Broken(), 1000)  # type: ignore[arg-type]


def test_two_processes_converge(tmp_path) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, {f"p{i}.md": f"---\ntitle: P{i}\n---\ntoken {i} words\n" for i in range(200)})
    script = (
        "import sys; sys.path.insert(0, sys.argv[3]);"
        "from pathlib import Path; import test_lexical_store as t;"
        "t._sync(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    here = str(Path(__file__).resolve().parent)
    procs = [subprocess.Popen([sys.executable, "-c", script, str(db), str(root), here]) for _ in range(2)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    fresh = tmp_path / "fresh.db"
    _sync(fresh, root)
    assert _dump(db) == _dump(fresh)


@pytest.mark.parametrize("key", ["schema_version", "scoring_version", "n", "total_length"])
def test_rebuild_publishes_schema_postings_and_statistics_atomically(tmp_path, monkeypatch, key) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE lex_meta SET value = 'stale' WHERE key = ?", (key,))
    before = _dump(db)
    real = ls._apply
    writes = []
    begin = ls._begin_write
    monkeypatch.setattr(ls, "_begin_write", lambda conn: writes.append(1) or begin(conn))

    def observe(conn, stored, target, prepared):
        real(conn, stored, target, prepared)
        assert _dump(db) == before

    monkeypatch.setattr(ls, "_apply", observe)
    result = _sync(db, root, "token")
    assert result.wrote and result.tokenized == len(CORPUS) and writes == [1]
    assert list(result.ranked) == _oracle(root, "token", 15)
    assert _dump(db)[2][key] != "stale"


@pytest.mark.parametrize("key", ["schema_version", "scoring_version", "n", "total_length"])
def test_failed_rebuild_preserves_previous_schema_and_rows(tmp_path, monkeypatch, key) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE lex_meta SET value = 'stale' WHERE key = ?", (key,))
    before = _dump(db)
    real = ls._apply

    def fail(*args):
        real(*args)
        raise RuntimeError("publication failed")

    monkeypatch.setattr(ls, "_apply", fail)
    with pytest.raises(RuntimeError, match="publication failed"):
        _sync(db, root)
    assert _dump(db) == before


@pytest.mark.parametrize("failure", ["missing", "utf8", "directory"])
def test_unreadable_page_has_no_postings_and_a_missing_excerpt(tmp_path, failure) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    page = root / "a.md"
    if failure == "utf8":
        page.write_bytes(b"\xff")
    elif failure == "directory":
        page.mkdir()
    got = ls.prepare_page(root, "a", "hash", excerpt_chars=12)
    assert (got.sha256, dict(got.tf), got.length) == ("hash", {}, 0)
    assert got.excerpt == "ERROR: no concept 'a' in this bundle"


@pytest.mark.parametrize("damage", ["lex_postings", "lex_docs", "lex_meta", "columns"])
def test_lexical_schema_damage_rebuilds_without_discarding_embeddings(tmp_path, damage) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    marker = tmp_path / "embedding-manifest.json"
    marker.write_text("embedding manifest", encoding="utf-8", newline="\n")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE pages (path TEXT PRIMARY KEY, embedding BLOB NOT NULL)")
        conn.execute("INSERT INTO pages VALUES ('embedding', x'000102')")
        if damage == "columns":
            conn.execute("ALTER TABLE lex_postings RENAME COLUMN tf TO broken_tf")
        else:
            conn.execute(f"DROP TABLE {damage}")
    discarded = []

    def discard():
        discarded.append(1)
        db.unlink()
        marker.unlink()

    result = ls.sync_and_score(db, _target(root), _loader(root), ["token"], 15, discard=discard)
    assert discarded == [] and result.rebuilt == "schema" and result.wrote
    assert result.tokenized == len(CORPUS)
    assert list(result.ranked) == _oracle(root, "token", 15)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT * FROM pages").fetchall() == [("embedding", b"\x00\x01\x02")]
    assert marker.read_text(encoding="utf-8") == "embedding manifest"


@pytest.mark.parametrize("key", ["n", "total_length"])
@pytest.mark.parametrize("bad_value", [None, "nonnumeric", "-1", "1.5"])
def test_missing_or_invalid_statistics_rebuild_without_discarding_embeddings(tmp_path, key, bad_value) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    marker = tmp_path / "embedding-manifest.json"
    marker.write_text("embedding manifest", encoding="utf-8", newline="\n")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE pages (path TEXT PRIMARY KEY, embedding BLOB NOT NULL)")
        conn.execute("INSERT INTO pages VALUES ('embedding', x'000102')")
        if bad_value is None:
            conn.execute("DELETE FROM lex_meta WHERE key = ?", (key,))
        else:
            conn.execute("UPDATE lex_meta SET value = ? WHERE key = ?", (bad_value, key))

    def discard():
        pytest.fail("damaged lexical statistics must preserve the database and embedding manifest")

    result = ls.sync_and_score(db, _target(root), _loader(root), ["token"], 15, discard=discard)
    assert result.rebuilt == "schema" and result.wrote and result.tokenized == len(CORPUS)
    assert list(result.ranked) == _oracle(root, "token", 15)
    fresh = tmp_path / "fresh.db"
    _sync(fresh, root)
    assert _dump(db) == _dump(fresh)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT * FROM pages").fetchall() == [("embedding", b"\x00\x01\x02")]
    assert marker.read_text(encoding="utf-8") == "embedding manifest"


def test_connect_closes_connection_when_initialization_fails(tmp_path, monkeypatch) -> None:
    db = tmp_path / "search.db"
    db.write_bytes(b"not a database" * 100)
    real = sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        conn = real(*args, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(ls.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError):
        ls._connect(db, 100)
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")


def test_drop_tables_keeps_embedding_pages(tmp_path) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE pages (path TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO pages VALUES ('embedding')")
    ls.drop_tables(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'lex_%'").fetchall() == []
        assert conn.execute("SELECT * FROM pages").fetchall() == [("embedding",)]
    assert _sync(db, root).rebuilt == "created"


def test_racing_writer_requires_new_pages_to_be_prepared_outside_write_lock(tmp_path, monkeypatch) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    _write(root, {"c/storage.md": "---\ntitle: Changed\n---\ntoken changed\n"})
    target = _target(root)
    real_begin = ls._begin_write
    begins = []
    seen = []

    def begin(conn):
        if not begins:
            # Replace a formerly matching row after the read diff, as a query
            # from a different session's corpus could do.
            with closing(sqlite3.connect(db)) as other, other:
                other.execute("UPDATE lex_docs SET sha256 = 'different-session' WHERE concept_id = 'a'")
        begins.append(1)
        real_begin(conn)

    def load(cid, sha):
        # A loader running under the store's write lock would fail this probe.
        with closing(sqlite3.connect(db, timeout=0)) as probe:
            probe.execute("BEGIN IMMEDIATE")
            probe.execute("ROLLBACK")
        seen.append(cid)
        return _loader(root)(cid, sha)

    monkeypatch.setattr(ls, "_begin_write", begin)
    result = ls.sync_and_score(db, target, load, ["token"], 15, discard=lambda: None)
    assert seen == ["c/storage", "a"] and result.tokenized == 2
    assert list(result.ranked) == _oracle(root, "token", 15)
    fresh = tmp_path / "fresh.db"
    _sync(fresh, root)
    assert _dump(db) == _dump(fresh)


def test_a_writer_that_converged_during_preparation_needs_no_publication(tmp_path, monkeypatch) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    _write(root, {"a.md": "---\ntitle: Changed\n---\ntoken changed\n"})
    target = _target(root)
    converged = []

    def load(cid, sha):
        if not converged:
            converged.append(1)
            _sync(db, root)
        return _loader(root)(cid, sha)

    result = ls.sync_and_score(db, target, load, ["token"], 15, discard=lambda: None)
    assert not result.wrote and result.rebuilt is None and result.tokenized == 1
    assert list(result.ranked) == _oracle(root, "token", 15)


def test_busy_after_preparation_scores_if_another_writer_has_converged(tmp_path, monkeypatch) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    _write(root, {"a.md": "---\ntitle: Changed\n---\ntoken changed\n"})
    real_begin = ls._begin_write

    def busy(conn):
        monkeypatch.setattr(ls, "_begin_write", real_begin)
        _sync(db, root)
        raise sqlite3.OperationalError("database is locked")

    def discard():
        pytest.fail("a busy database must never be discarded")

    monkeypatch.setattr(ls, "_begin_write", busy)
    result = ls.sync_and_score(db, _target(root), _loader(root), ["token"], 15, discard=discard)
    assert not result.wrote and result.tokenized == 1
    assert list(result.ranked) == _oracle(root, "token", 15)


def test_failed_initial_sync_does_not_publish_empty_schema(tmp_path, monkeypatch) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    real = ls._apply

    def fail(*args):
        real(*args)
        raise RuntimeError("initial publication failed")

    monkeypatch.setattr(ls, "_apply", fail)
    with pytest.raises(RuntimeError, match="initial publication failed"):
        _sync(db, root)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'lex_%'").fetchall() == []


def test_empty_corpus_and_nonpositive_limits(tmp_path) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    root.mkdir()
    result = _sync(db, root, "token")
    assert result.ranked == () and result.wrote and result.rebuilt == "created" and result.tokenized == 0
    assert not _sync(db, root, "token").wrote
    _write(root, CORPUS)
    assert _sync(db, root, "token", limit=0).ranked == ()
    assert _sync(db, root, "token", limit=-1).ranked == ()
    assert ls.stored_excerpts(db, [], _target(root)) == {}


def test_busy_initialization_never_discards_the_database(tmp_path) -> None:
    db = tmp_path / "search.db"
    with closing(sqlite3.connect(db, isolation_level=None)) as holder:
        holder.execute("CREATE TABLE pages (path TEXT PRIMARY KEY)")
        holder.execute("BEGIN EXCLUSIVE")

        def discard():
            pytest.fail("a busy database must never be discarded")

        with pytest.raises(ls.LexicalBusy):
            ls.sync_and_score(
                db, {}, lambda cid, sha: pytest.fail("unexpected load"), [], 15, discard=discard, busy_timeout_ms=10
            )
        holder.execute("ROLLBACK")
        assert holder.execute("SELECT name FROM sqlite_master WHERE name = 'pages'").fetchall() == [("pages",)]


def test_unrecoverable_corruption_is_discarded_only_once(tmp_path) -> None:
    db = tmp_path / "search.db"
    bad_bytes = b"not a database" * 100
    db.write_bytes(bad_bytes)
    discarded = []
    with pytest.raises(sqlite3.DatabaseError):
        ls.sync_and_score(
            db, {}, lambda cid, sha: pytest.fail("unexpected load"), [], 15, discard=lambda: discarded.append(1)
        )
    assert discarded == [1] and db.read_bytes() == bad_bytes


def test_busy_recovery_attempt_never_discards_a_second_database(tmp_path) -> None:
    db = tmp_path / "search.db"
    db.write_bytes(b"not a database" * 100)
    discarded = []
    holders = []

    def discard():
        discarded.append(1)
        db.unlink()
        conn = sqlite3.connect(db, isolation_level=None)
        holders.append(conn)
        conn.execute("CREATE TABLE pages (path TEXT PRIMARY KEY)")
        conn.execute("BEGIN EXCLUSIVE")

    try:
        with pytest.raises(ls.LexicalBusy):
            ls.sync_and_score(
                db, {}, lambda cid, sha: pytest.fail("unexpected load"), [], 15, discard=discard, busy_timeout_ms=10
            )
        assert discarded == [1]
    finally:
        for holder in holders:
            holder.execute("ROLLBACK")
            holder.close()


def test_drop_failure_rolls_back_already_dropped_tables(tmp_path, monkeypatch) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    before = _dump(db)
    real = ls._connect

    def connect(*args):
        conn = real(*args)

        def authorizer(action, name, *rest):
            return (
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DROP_TABLE and name == "lex_docs" else sqlite3.SQLITE_OK
            )

        conn.set_authorizer(authorizer)
        return conn

    monkeypatch.setattr(ls, "_connect", connect)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        ls.drop_tables(db)
    assert _dump(db) == before


def test_failed_lexical_repair_preserves_embeddings_and_does_not_retry_discard(tmp_path, monkeypatch) -> None:
    root, db = tmp_path / "okf", tmp_path / "search.db"
    _write(root, CORPUS)
    _sync(db, root)
    marker = tmp_path / "embedding-manifest.json"
    marker.write_text("embedding manifest", encoding="utf-8", newline="\n")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE pages (path TEXT PRIMARY KEY, embedding BLOB NOT NULL)")
        conn.execute("INSERT INTO pages VALUES ('embedding', x'000102')")
        conn.execute("DELETE FROM lex_meta WHERE key = 'n'")
    before = _dump(db)
    real = ls._connect
    attempted = []

    def connect(*args):
        conn = real(*args)

        def authorizer(action, name, *rest):
            if action == sqlite3.SQLITE_CREATE_TABLE and name == "lex_docs":
                attempted.append(1)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorizer)
        return conn

    def discard():
        pytest.fail("a failed lexical repair must never discard healthy embedding data")

    monkeypatch.setattr(ls, "_connect", connect)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        ls.sync_and_score(db, _target(root), _loader(root), ["token"], 15, discard=discard)
    assert attempted == [1] and _dump(db) == before
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT * FROM pages").fetchall() == [("embedding", b"\x00\x01\x02")]
    assert marker.read_text(encoding="utf-8") == "embedding manifest"
