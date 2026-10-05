"""Hash-row refresh reads text only for vectors that need replacing."""

import sqlite3
import sys
from pathlib import Path, PureWindowsPath

import pytest
from graph_works_core.query import commands as q


class _Embedder:
    model_id = "fake-rows-v1"

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text)), 1.0]


def test_embedding_refresh_reads_only_changed_page_text(tmp_path):
    texts = {"a": "unchanged", "b": "original"}
    pages = [("a", "hash-a"), ("b", "hash-b")]
    embedder = _Embedder()
    q._embed_pages(pages, texts.__getitem__, tmp_path, embedder=embedder)

    def read_changed(path: str) -> str:
        assert path == "b", "unchanged page text must never be read"
        return "edited page"

    changed = [("a", "hash-a"), ("b", "hash-b-edited")]
    result = q._refresh_embeddings(changed, read_changed, tmp_path, embedder=embedder)
    assert result == q.IndexRefresh(True, "corpus-changed", 2, 1, 0)
    with sqlite3.connect(tmp_path / "search" / "search.db") as conn:
        assert conn.execute("SELECT path, content_hash FROM pages ORDER BY path").fetchall() == changed

    def read_nothing(path: str) -> str:
        raise AssertionError(f"fresh page {path} must never be read")

    assert q._refresh_embeddings(changed, read_nothing, tmp_path, embedder=embedder) == q.IndexRefresh(
        False, None, 2, 0, 0
    )
    assert q._embed_pages(changed, read_nothing, tmp_path, embedder=embedder) == q.IndexRefresh(True, "forced", 2, 0, 0)


@pytest.mark.parametrize(
    ("loss", "reason"),
    [("database", "missing-database"), ("table", "missing-pages"), ("empty", "missing-pages")],
)
def test_matching_manifest_rebuilds_lost_embedding_store(tmp_path, loss, reason):
    texts = {"a": "first page", "b": "second page"}
    pages = [("a", "hash-a"), ("b", "hash-b")]
    embedder = _Embedder()
    q._embed_pages(pages, texts.__getitem__, tmp_path, embedder=embedder)
    db = q._search_db(tmp_path)
    if loss == "table":
        with sqlite3.connect(db) as conn:
            conn.execute("DROP TABLE pages")
    else:
        db.unlink()
        if loss == "empty":
            sqlite3.connect(db).close()

    # A metadata-only probe must not create the missing database.
    assert q._staleness(tmp_path, page_hashes=dict(pages), signature=embedder.model_id) == reason
    if loss == "database":
        assert not db.exists()

    read = []

    def read_text(path):
        read.append(path)
        return texts[path]

    assert q._refresh_embeddings(pages, read_text, tmp_path, embedder=embedder) == q.IndexRefresh(True, reason, 2, 2, 0)
    assert read == ["a", "b"]
    hits = q._cosine_search_sqlite(tmp_path, [1.0, 1.0], top_k=3)
    assert {path for path, _ in hits} == {"a", "b"}
    read.clear()
    assert q._refresh_embeddings(pages, read_text, tmp_path, embedder=embedder) == q.IndexRefresh(False, None, 2, 0, 0)
    assert read == []


def test_freshness_probe_encodes_cache_path(tmp_path):
    cache = tmp_path / "cache space #?% café"
    pages = [("a", "hash-a")]
    embedder = _Embedder()
    q._embed_pages(pages, lambda _: "page", cache, embedder=embedder)
    assert q._staleness(cache, page_hashes=dict(pages), signature=embedder.model_id) is None
    with sqlite3.connect(q._search_db(cache)) as conn:
        conn.execute("DROP TABLE pages")
    assert q._staleness(cache, page_hashes=dict(pages), signature=embedder.model_id) == "missing-pages"


def test_freshness_probe_does_not_disguise_database_errors(tmp_path):
    pages = [("a", "hash-a")]
    embedder = _Embedder()
    q._embed_pages(pages, lambda _: "page", tmp_path, embedder=embedder)
    q._search_db(tmp_path).write_bytes(b"not a SQLite database")
    with pytest.raises(sqlite3.DatabaseError):
        q._staleness(tmp_path, page_hashes=dict(pages), signature=embedder.model_id)


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX root to exercise UNC URI parsing with real SQLite")
def test_freshness_probe_accepts_unc_uri_without_sqlite_authority_extension(tmp_path, monkeypatch):
    pages = [("a", "hash-a")]
    embedder = _Embedder()
    q._embed_pages(pages, lambda _: "page", tmp_path, embedder=embedder)
    db = q._search_db(tmp_path)
    # A Windows UNC path has a nonempty authority in Path.as_uri(). On POSIX,
    # //private/... names the same real file as /private/...; only substitute
    # path conversion, leaving SQLite's URI parser and table read real.
    unc = PureWindowsPath("/" + db.resolve().as_posix())
    resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda path, **kw: unc if path == db else resolve(path, **kw))
    assert q._staleness(tmp_path, page_hashes=dict(pages), signature=embedder.model_id) is None
