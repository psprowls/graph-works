"""Retrieval: the bundle corpus, the embedding index, and RRF fusion.

No network and no vendor: the embedder is a fake returning fixed vectors, and
the bundle is a temp directory. Every assertion here is about arithmetic or
about which pages entered the corpus.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from graph_works_core.query import commands as q
from graph_works_core.workspace.layout import layout_for
from okf_io import load_bundle


class FakeEmbedder:
    """Deterministic vectors keyed on text length. Counts its own calls."""

    model_id = "fake-embed-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        n = float(len(text) % 7 + 1)
        return [n, 1.0 / n, 0.5]


def test_default_embedder_names_its_model(monkeypatch):
    """The vendor decision is named, and the name travels with the client."""
    seen: list[tuple[str, str]] = []

    class _Client:
        def embed_query(self, text: str) -> list[float]:
            return [1.0, 2.0, 3.0]

    def _fake_make(model_id: str, *, region: str):
        seen.append((model_id, region))
        return _Client()

    monkeypatch.setattr(q, "make_bedrock_embeddings", _fake_make)

    embedder = q.default_embedder()
    assert embedder.model_id == q.DEFAULT_EMBED_MODEL_ID
    assert embedder.embed_query("anything") == [1.0, 2.0, 3.0]
    assert seen == [(q.DEFAULT_EMBED_MODEL_ID, q.DEFAULT_EMBED_REGION)]


def _bundle_dir(tmp_path: Path) -> Path:
    root = tmp_path / "okf"
    (root / "concepts").mkdir(parents=True)
    (root / "concepts" / "auth.md").write_text(
        "---\ntitle: Auth\n---\n\nToken exchange and refresh rotation.\n", encoding="utf-8"
    )
    (root / "concepts" / "storage.md").write_text(
        "---\ntitle: Storage\n---\n\nBlob storage, retention, and lifecycle rules.\n", encoding="utf-8"
    )
    (root / "index.md").write_text("---\ntitle: Index\n---\n\n- [Auth](/concepts/auth.md)\n", encoding="utf-8")
    (root / "log.md").write_text("---\ntitle: Log\n---\n\n## [2026-08-13] scan | ran\n", encoding="utf-8")
    return root


def _layout(tmp_path: Path):
    return layout_for(tmp_path)


def test_rrf_fuse_matches_hand_computed_scores():
    fused = q._rrf_fuse({"a": 1, "b": 2}, {"a": 2, "b": 1}, k=60)
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)


def test_a_page_missing_from_one_ranking_uses_the_sentinel_rank():
    # n = 2 distinct pages, k = 60, so the sentinel rank is 62.
    fused = q._rrf_fuse({"a": 1, "b": 2}, {"a": 1}, k=60)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 122)


def test_the_corpus_is_concepts_only(tmp_path):
    bundle = load_bundle(_bundle_dir(tmp_path))
    corpus = q._corpus(bundle)
    assert [concept_id for concept_id, _ in corpus] == ["concepts/auth", "concepts/storage"]


def test_building_twice_re_embeds_nothing(tmp_path):
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))
    embedder = FakeEmbedder()

    q.build_index(bundle, layout.cache_dir, embedder=embedder)
    first = len(embedder.calls)
    assert first == 2

    q.build_index(bundle, layout.cache_dir, embedder=embedder)
    assert len(embedder.calls) == first  # unchanged pages are not re-embedded


def test_editing_one_page_re_embeds_only_that_page(tmp_path):
    layout = _layout(tmp_path)
    root = _bundle_dir(tmp_path)
    embedder = FakeEmbedder()
    q.build_index(load_bundle(root), layout.cache_dir, embedder=embedder)
    embedder.calls.clear()

    (root / "concepts" / "auth.md").write_text(
        "---\ntitle: Auth\n---\n\nToken exchange, refresh rotation, and revocation.\n", encoding="utf-8"
    )
    q.build_index(load_bundle(root), layout.cache_dir, embedder=embedder)
    assert len(embedder.calls) == 1


def test_the_index_lives_under_the_cache_dir(tmp_path):
    layout = _layout(tmp_path)
    q.build_index(load_bundle(_bundle_dir(tmp_path)), layout.cache_dir, embedder=FakeEmbedder())
    assert (layout.cache_dir / "search" / "search.db").is_file()
    # The lexical index is built in memory per query; only embeddings persist.
    assert not (layout.cache_dir / "search" / "bm25").exists()


def test_prepare_retrieval_returns_ranked_concept_ids(tmp_path):
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))
    q.build_index(bundle, layout.cache_dir, embedder=FakeEmbedder())

    prepared = q._prepare_query_retrieval("token refresh", layout, bundle, top_k=3, embedder=FakeEmbedder())
    assert set(prepared.top_pages) <= set(bundle.concepts)
    for scores in prepared.search_scores.values():
        assert set(scores) == {"bm25", "embed", "rrf"}


def test_prepare_retrieval_builds_a_missing_index(tmp_path):
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))
    embedder = FakeEmbedder()
    q._prepare_query_retrieval("token refresh", layout, bundle, top_k=3, embedder=embedder)
    assert (layout.cache_dir / "search" / "search.db").is_file()


@pytest.mark.parametrize("top_k", [2, 11, 0, -1])
def test_top_k_outside_the_band_is_a_value_error(tmp_path, top_k):
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))
    with pytest.raises(ValueError, match="top_k"):
        q._prepare_query_retrieval("q", layout, bundle, top_k=top_k, embedder=FakeEmbedder())


def test_the_corpus_fingerprint_is_injective_over_renames_and_edits():
    # "ab" + "c" must not hash the same as "a" + "bc": the delimiters are the point.
    left = q._corpus_fingerprint({"ab": "c"})
    right = q._corpus_fingerprint({"a": "bc"})
    assert left != right
    # Order of insertion is irrelevant; content is not.
    assert q._corpus_fingerprint({"a": "1", "b": "2"}) == q._corpus_fingerprint({"b": "2", "a": "1"})
    assert q._corpus_fingerprint({"a": "1"}) != q._corpus_fingerprint({"a": "2"})


def test_the_manifest_round_trips(tmp_path):
    layout = _layout(tmp_path)
    manifest = q._Manifest(q.INDEX_SCHEMA_VERSION, "model-x", "cafe1234")
    q._write_manifest(layout.cache_dir, manifest)
    assert q._read_manifest(layout.cache_dir) == manifest


@pytest.mark.parametrize(
    "payload",
    [
        None,  # file absent entirely
        b"not json at all",
        b"[1, 2, 3]",  # valid JSON, wrong shape
        b'{"schema_version": "1", "embed_signature": "m", "corpus_fingerprint": "f"}',
        b'{"schema_version": 1, "embed_signature": 7, "corpus_fingerprint": "f"}',
        b'{"schema_version": 1, "embed_signature": "m"}',
        b'{"schema_version": true, "embed_signature": "m", "corpus_fingerprint": "f"}',
    ],
)
def test_an_unusable_manifest_reads_as_none(tmp_path, payload):
    layout = _layout(tmp_path)
    if payload is not None:
        path = layout.cache_dir / "search" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    assert q._read_manifest(layout.cache_dir) is None


def _rows(layout) -> set[str]:
    """The paths currently in the embedding table, read directly."""
    conn = sqlite3.connect(str(layout.cache_dir / "search" / "search.db"))
    try:
        return {str(row[0]) for row in conn.execute("SELECT path FROM pages")}
    finally:
        conn.close()


def test_a_deleted_concept_is_pruned_from_the_embedding_table(tmp_path):
    layout = _layout(tmp_path)
    root = _bundle_dir(tmp_path)
    q.build_index(load_bundle(root), layout.cache_dir, embedder=FakeEmbedder())
    assert _rows(layout) == {"concepts/auth", "concepts/storage"}

    (root / "concepts" / "auth.md").unlink()
    result = q.build_index(load_bundle(root), layout.cache_dir, embedder=FakeEmbedder())

    assert _rows(layout) == {"concepts/storage"}
    assert result.pruned == 1
    assert result.rebuilt is True


def test_changing_the_embedding_model_re_embeds_every_page(tmp_path):
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))

    first = FakeEmbedder()
    q.build_index(bundle, layout.cache_dir, embedder=first)
    assert len(first.calls) == 2

    class OtherModel(FakeEmbedder):
        model_id = "fake-embed-v2"

    second = OtherModel()
    result = q.build_index(bundle, layout.cache_dir, embedder=second)
    assert len(second.calls) == 2  # no row survives a signature it cannot vouch for
    assert result.embedded == 2


def test_building_an_empty_bundle_is_a_no_op(tmp_path):
    layout = _layout(tmp_path)
    root = tmp_path / "empty"
    root.mkdir()
    result = q.build_index(load_bundle(root), layout.cache_dir, embedder=FakeEmbedder())
    assert result == q.IndexRefresh(rebuilt=False, reason="empty-bundle", pages=0, embedded=0, pruned=0)
    assert not (layout.cache_dir / "search" / "manifest.json").exists()


def test_refresh_on_an_unchanged_bundle_does_not_rebuild(tmp_path):
    """The gate must gate — an always-rebuild would pass every other test here."""
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))
    q.refresh_index(bundle, layout.cache_dir, embedder=FakeEmbedder())

    embedder = FakeEmbedder()
    result = q.refresh_index(bundle, layout.cache_dir, embedder=embedder)
    assert result.rebuilt is False
    assert result.reason is None
    assert result.pages == 2
    assert embedder.calls == []


def test_refresh_rebuilds_when_a_concept_is_added_or_removed(tmp_path):
    layout = _layout(tmp_path)
    root = _bundle_dir(tmp_path)
    q.refresh_index(load_bundle(root), layout.cache_dir, embedder=FakeEmbedder())

    (root / "concepts" / "billing.md").write_text(
        "---\ntitle: Billing\n---\n\nInvoices, dunning, and settlement.\n", encoding="utf-8"
    )
    added = q.refresh_index(load_bundle(root), layout.cache_dir, embedder=FakeEmbedder())
    assert (added.rebuilt, added.reason) == (True, "corpus-changed")

    (root / "concepts" / "auth.md").unlink()
    removed = q.refresh_index(load_bundle(root), layout.cache_dir, embedder=FakeEmbedder())
    assert (removed.rebuilt, removed.reason, removed.pruned) == (True, "corpus-changed", 1)


def test_refresh_rebuilds_when_the_model_changes(tmp_path):
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))
    q.refresh_index(bundle, layout.cache_dir, embedder=FakeEmbedder())

    class OtherModel(FakeEmbedder):
        model_id = "fake-embed-v2"

    result = q.refresh_index(bundle, layout.cache_dir, embedder=OtherModel())
    assert (result.rebuilt, result.reason, result.embedded) == (True, "model-changed", 2)


def test_refresh_rebuilds_on_an_unknown_schema_version(tmp_path):
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))
    q.refresh_index(bundle, layout.cache_dir, embedder=FakeEmbedder())

    current = q._read_manifest(layout.cache_dir)
    assert current is not None
    q._write_manifest(layout.cache_dir, q._Manifest(999, current.embed_signature, current.corpus_fingerprint))

    result = q.refresh_index(bundle, layout.cache_dir, embedder=FakeEmbedder())
    assert (result.rebuilt, result.reason) == (True, "schema-changed")


def test_refresh_rebuilds_when_the_manifest_is_missing_or_malformed(tmp_path):
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))

    first = q.refresh_index(bundle, layout.cache_dir, embedder=FakeEmbedder())
    assert (first.rebuilt, first.reason) == (True, "no-manifest")

    (layout.cache_dir / "search" / "manifest.json").write_bytes(b"{ not json")
    garbage = q.refresh_index(bundle, layout.cache_dir, embedder=FakeEmbedder())
    assert (garbage.rebuilt, garbage.reason) == (True, "no-manifest")


def test_refresh_on_an_empty_bundle_is_a_no_op(tmp_path):
    layout = _layout(tmp_path)
    root = tmp_path / "empty"
    root.mkdir()
    result = q.refresh_index(load_bundle(root), layout.cache_dir, embedder=FakeEmbedder())
    assert result == q.IndexRefresh(rebuilt=False, reason="empty-bundle", pages=0, embedded=0, pruned=0)
    assert not (layout.cache_dir / "search" / "manifest.json").exists()


def test_a_dimension_mismatch_is_a_named_error_not_a_truncated_score(tmp_path):
    """The 0.98 this used to return over a truncated overlap is the forbidden value."""
    from graph_works_core import QueryError

    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))
    q.build_index(bundle, layout.cache_dir, embedder=FakeEmbedder())  # 3-dim rows

    with pytest.raises(QueryError) as excinfo:
        q._cosine_search_sqlite(layout.cache_dir, [1.0] * 8, top_k=3)

    message = str(excinfo.value)
    assert "3" in message and "8" in message
    assert "search" in message  # names the cache directory to delete


def test_a_concept_added_between_queries_enters_the_candidate_set(tmp_path):
    layout = _layout(tmp_path)
    root = _bundle_dir(tmp_path)
    q._prepare_query_retrieval("token refresh", layout, load_bundle(root), top_k=3, embedder=FakeEmbedder())

    (root / "concepts" / "billing.md").write_text(
        "---\ntitle: Billing\n---\n\nInvoices, dunning, and settlement.\n", encoding="utf-8"
    )
    prepared = q._prepare_query_retrieval(
        "invoices settle", layout, load_bundle(root), top_k=3, embedder=FakeEmbedder()
    )
    assert "concepts/billing" in prepared.top_pages


def test_a_concept_deleted_between_queries_leaves_the_candidate_set(tmp_path):
    """The headline regression: a deleted page was still ranked first."""
    layout = _layout(tmp_path)
    root = _bundle_dir(tmp_path)
    q._prepare_query_retrieval("token refresh", layout, load_bundle(root), top_k=3, embedder=FakeEmbedder())

    (root / "concepts" / "auth.md").unlink()
    bundle = load_bundle(root)
    prepared = q._prepare_query_retrieval("token refresh", layout, bundle, top_k=3, embedder=FakeEmbedder())

    assert "concepts/auth" not in prepared.top_pages
    assert set(prepared.top_pages) <= set(bundle.concepts)  # cannot pass on a lucky ranking


def test_querying_a_concept_free_bundle_is_a_named_refusal(tmp_path):
    from graph_works_core import QueryError

    layout = _layout(tmp_path)
    root = tmp_path / "empty"
    root.mkdir()
    bundle = load_bundle(root)

    with pytest.raises(QueryError) as excinfo:
        q._prepare_query_retrieval("anything", layout, bundle, top_k=3, embedder=FakeEmbedder())

    message = str(excinfo.value)
    assert str(root) in message
    assert "params.index.json" not in message and "_cache" not in message
    assert not isinstance(excinfo.value, FileNotFoundError)


def test_a_query_with_no_usable_terms_skips_the_lexical_index(tmp_path, monkeypatch):
    # okf_ext.search returns the WHOLE corpus at score 0.0 for text it cannot
    # tokenize — empty, all-stopwords, or any non-Latin script. That means
    # `search_scores`/`top_pages` look the same with or without the guard: the
    # thing the guard actually changes is whether the lexical index runs at
    # all, so that is what this asserts, not the shape of the output.
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(q, "lexical_query", lambda bundle, query_text, top_k: calls.append(query_text) or ([], []))

    q._prepare_query_retrieval("数据", layout, bundle, top_k=3, embedder=FakeEmbedder())
    assert calls == []


def test_a_query_with_usable_terms_calls_the_lexical_index(tmp_path, monkeypatch):
    layout = _layout(tmp_path)
    bundle = load_bundle(_bundle_dir(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(q, "lexical_query", lambda bundle, query_text, top_k: calls.append(query_text) or ([], []))

    q._prepare_query_retrieval("token refresh", layout, bundle, top_k=3, embedder=FakeEmbedder())
    assert calls == ["token refresh"]


def test_a_leftover_bm25_directory_is_removed_without_re_embedding(tmp_path):
    layout = _layout(tmp_path)
    root = _bundle_dir(tmp_path)
    embedder = FakeEmbedder()
    q.build_index(load_bundle(root), layout.cache_dir, embedder=embedder)
    embedded_first = len(embedder.calls)

    stale = layout.cache_dir / "search" / "bm25"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "vocab.index.json").write_text("{}", encoding="utf-8")

    q.build_index(load_bundle(root), layout.cache_dir, embedder=embedder)
    assert not stale.exists()
    # The manifest still vouches for the embedding table, so nothing re-embeds.
    assert len(embedder.calls) == embedded_first
