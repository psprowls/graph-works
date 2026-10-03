"""Hash-row refresh reads text only for vectors that need replacing."""

import sqlite3

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
