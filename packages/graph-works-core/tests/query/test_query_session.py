"""Retrieval on the read session versus the in-memory oracle: equal answers, near-zero work."""

from __future__ import annotations

import logging
import sqlite3

import pytest
from conftest import FakeEmbedder, settle_mtimes
from graph_works_core.query import commands as q
from graph_works_core.query import lexical_store as ls
from graph_works_core.workspace.bundle import load_workspace_bundle


class StopAfterRetrieval(BaseException):
    """Stop the roles without triggering the normal exception fallback."""


QUERIES = ["token refresh", "storage retention blob", "rotation", "metric cohort audit", "zzz"]
KINDS = ["acme_retail", "ga4", "synthetic"]


def _oracle(layout, query, **kw):
    return q.plan_query_brief(query, layout, bundle=load_workspace_bundle(layout), **kw)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("hybrid", [False, True])
def test_brief_equals_the_oracle(make_workspace, kind, hybrid) -> None:
    layout = make_workspace(kind)
    for query in QUERIES:
        for top_k in (3, 10):
            kw = {"embedder": FakeEmbedder() if hybrid else None, "top_k": top_k}
            assert q.plan_query_brief(query, layout, **kw) == _oracle(layout, query, **kw)


@pytest.mark.parametrize("seed", range(5))
def test_pinned_brief_equals_the_oracle(make_workspace, seed) -> None:
    layout = make_workspace("synthetic", seed)
    for page in ("s/p0", "s/p7", "a", "a-b"):
        kw = {"embedder": None, "top_k": 5, "page": page}
        assert q.plan_query_brief("token rotation", layout, **kw) == _oracle(layout, "token rotation", **kw)


def test_warm_unchanged_brief_does_no_corpus_work(make_workspace, probes) -> None:
    layout = make_workspace("synthetic")
    q.plan_query_brief("token", layout, embedder=FakeEmbedder(), top_k=5, page="s/p1")
    probes.clear()
    q.plan_query_brief("token", layout, embedder=FakeEmbedder(), top_k=5, page="s/p1")
    assert dict(probes) == {}


def test_one_edit_tokenizes_one_page_and_n_additions_tokenize_n_once(make_workspace, probes) -> None:
    layout = make_workspace("synthetic")
    q.plan_query_brief("token", layout, embedder=None, top_k=5)
    (layout.bundle_dir / "s/p3.md").write_text("---\ntitle: P3\n---\nnew words\n", encoding="utf-8", newline="\n")
    probes.clear()
    q.plan_query_brief("token", layout, embedder=None, top_k=5)
    assert probes["tokenized"] == 1 and probes["hashed"] == 0 and probes["full_loads"] == 0
    for i in range(7):
        (layout.bundle_dir / f"u{i}.md").write_text(
            f"---\ntitle: U{i}\n---\nunrelated\n", encoding="utf-8", newline="\n"
        )
    probes.clear()
    q.plan_query_brief("token", layout, embedder=None, top_k=5)
    assert probes["tokenized"] == 7
    probes.clear()
    q.plan_query_brief("token", layout, embedder=None, top_k=5)
    assert probes["tokenized"] == 0


def test_read_index_rebuild_converges(make_workspace) -> None:
    from graph_works_core.read_session import location

    layout = make_workspace("synthetic")
    q.plan_query_brief("token", layout, embedder=None, top_k=5)
    for path in location.sidecar_paths(location.database_path(layout)):
        path.unlink(missing_ok=True)
    (layout.bundle_dir / "s/p2.md").unlink()
    assert q.plan_query_brief("token", layout, embedder=None, top_k=10) == _oracle(
        layout, "token", embedder=None, top_k=10
    )


def test_untokenizable_query_still_syncs_and_excerpts(make_workspace) -> None:
    layout = make_workspace("synthetic")
    for query in ("数据", "the and of"):
        kw = {"embedder": FakeEmbedder(), "top_k": 5}
        brief = q.plan_query_brief(query, layout, **kw)
        assert brief == _oracle(layout, query, **kw)
        assert all(page.search_scores["bm25"] == 0.0 for page in brief.top_pages)


def test_pinned_page_deleted_between_briefs_is_refused(make_workspace) -> None:
    layout = make_workspace("synthetic")
    q.plan_query_brief("token", layout, embedder=None, top_k=5, page="s/p4")
    (layout.bundle_dir / "s/p4.md").unlink()
    brief = q.plan_query_brief("token", layout, embedder=None, top_k=5, page="s/p4")
    assert brief.refusal == "unknown-page" and brief == _oracle(layout, "token", embedder=None, top_k=5, page="s/p4")


def test_disabled_read_index_runs_the_oracle_and_leaves_the_store_alone(make_workspace) -> None:
    layout = make_workspace("synthetic")
    layout.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    brief = q.plan_query_brief("token", layout, embedder=None, top_k=5)
    assert brief == _oracle(layout, "token", embedder=None, top_k=5)
    db = q._search_db(layout.cache_dir)
    if db.exists():
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'lex_%'").fetchall() == []


def test_corrupt_search_db_recovers_and_reembeds(make_workspace) -> None:
    layout = make_workspace("synthetic")
    q.plan_query_brief("token", layout, embedder=FakeEmbedder(), top_k=5)
    q._search_db(layout.cache_dir).write_bytes(b"garbage" * 500)
    embedder = FakeEmbedder()
    brief = q.plan_query_brief("token", layout, embedder=embedder, top_k=5)
    assert brief.retrieval == "hybrid" and brief.warnings == ()
    assert len(embedder.calls) > 1  # every page re-embedded, then the query
    assert brief == _oracle(layout, "token", embedder=FakeEmbedder(), top_k=5)


@pytest.mark.parametrize("loss", ["database", "table"])
def test_session_retrieval_recovers_lost_embedding_store(make_workspace, loss) -> None:
    layout = make_workspace("synthetic")
    expected = q.plan_query_brief("token", layout, embedder=FakeEmbedder(), top_k=5)
    bundle = load_workspace_bundle(layout)
    db = q._search_db(layout.cache_dir)
    lexical_tables = ("lex_docs", "lex_postings", "lex_meta")
    with sqlite3.connect(db) as conn:
        schemas = conn.execute("SELECT name, sql FROM sqlite_master WHERE name LIKE 'lex_%' ORDER BY name").fetchall()
        rows = {name: conn.execute(f"SELECT * FROM {name} ORDER BY 1, 2").fetchall() for name in lexical_tables}
        if loss == "table":
            conn.execute("DROP TABLE pages")
    if loss == "database":
        db.unlink()

    # Session retrieval synchronizes the actual lexical store before refreshing
    # embeddings, so database deletion reaches the missing-pages branch too.
    embedder = FakeEmbedder()
    brief = q.plan_query_brief("token", layout, embedder=embedder, top_k=5)
    assert brief.retrieval == "hybrid"
    assert brief.warnings == ()
    assert brief == expected
    assert len(embedder.calls) == len(bundle.concepts) + 1
    assert set(embedder.calls[:-1]) == {doc.raw_text for doc in bundle.concepts.values()}
    assert embedder.calls[-1] == "token"
    with sqlite3.connect(db) as conn:
        assert (
            conn.execute("SELECT name, sql FROM sqlite_master WHERE name LIKE 'lex_%' ORDER BY name").fetchall()
            == schemas
        )
        assert {name: conn.execute(f"SELECT * FROM {name} ORDER BY 1, 2").fetchall() for name in lexical_tables} == rows
    embedder.calls.clear()
    assert q.plan_query_brief("token", layout, embedder=embedder, top_k=5) == brief
    assert embedder.calls == ["token"]


def test_busy_store_with_stale_rows_falls_back_to_the_oracle(make_workspace, monkeypatch, caplog) -> None:
    layout = make_workspace("synthetic")
    q.plan_query_brief("token", layout, embedder=None, top_k=5)
    (layout.bundle_dir / "s/p5.md").write_text(
        "---\ntitle: P5\n---\ntoken token token\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(ls, "BUSY_TIMEOUT_MS", 100)
    holder = sqlite3.connect(q._search_db(layout.cache_dir), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with caplog.at_level(logging.WARNING):
            brief = q.plan_query_brief("token", layout, embedder=None, top_k=5)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert "lexical index busy" in caplog.text
    assert brief == _oracle(layout, "token", embedder=None, top_k=5)


def test_identity_switch_reembeds_nothing(make_workspace) -> None:
    layout = make_workspace("synthetic")
    q.refresh_index(load_workspace_bundle(layout), layout.cache_dir, embedder=FakeEmbedder())  # oracle hashes
    embedder = FakeEmbedder()
    q.plan_query_brief("token", layout, embedder=embedder, top_k=5)
    assert embedder.calls == ["token"]  # BOM and CRLF pages included: bytes hash == text hash


def test_run_query_retrieval_runs_on_the_session(make_workspace, monkeypatch, probes) -> None:
    layout = make_workspace("synthetic")
    q.plan_query_brief("token", layout, embedder=FakeEmbedder(), top_k=5)
    captured = {}

    def stop(prepared, **kwargs):
        captured["prepared"] = prepared
        raise StopAfterRetrieval()

    monkeypatch.setattr(q, "_initial_candidates", lambda prepared, **kw: stop(prepared))
    monkeypatch.setattr(q, "_load_query_graph_tools", lambda target: (None, []))
    probes.clear()
    import asyncio

    with pytest.raises(StopAfterRetrieval):
        asyncio.run(q.run_query("token", layout, embedder=FakeEmbedder(), top_k=5, use_legacy=False))
    assert probes["hashed"] == 0 and probes["tokenized"] == 0 and probes["full_loads"] == 1
    oracle = q._prepare_query_retrieval(
        "token", layout, load_workspace_bundle(layout), top_k=5, embedder=FakeEmbedder()
    )
    got = captured["prepared"]
    assert (got.top_pages, got.candidates, got.retrieval) == (oracle.top_pages, oracle.candidates, oracle.retrieval)


@pytest.mark.parametrize("top_k", (2, 11))
def test_session_brief_rejects_invalid_top_k(make_workspace, top_k) -> None:
    layout = make_workspace("synthetic")
    with pytest.raises(ValueError, match="top_k must be between"):
        q.plan_query_brief("token", layout, embedder=None, top_k=top_k)
    assert not q._search_db(layout.cache_dir).exists()


def test_session_brief_refuses_empty_corpus(make_workspace) -> None:
    from graph_works_core.workspace.errors import QueryError

    layout = make_workspace("synthetic")
    for path in layout.bundle_dir.rglob("*.md"):
        path.unlink()
    with pytest.raises(QueryError, match="no concepts to search"):
        q.plan_query_brief("token", layout, embedder=None, top_k=5)


def test_session_pinning_resolves_links_and_uses_stored_excerpt(make_workspace, probes) -> None:
    layout = make_workspace("synthetic")
    (layout.bundle_dir / "pin.md").write_text(
        "---\ntitle: Pin\n---\n[out](/a-b.md) [missing](/absent.md) [web](https://example.com)\n",
        encoding="utf-8",
        newline="\n",
    )
    (layout.bundle_dir / "a.md").write_text(
        "---\ntitle: Tie\n---\ntoken token [back](/pin.md)\n", encoding="utf-8", newline="\n"
    )
    settle_mtimes(layout.bundle_dir)
    first = q.plan_query_brief("token", layout, embedder=None, top_k=3, page="pin")
    assert [page.path for page in first.top_pages] == ["pin", "a-b", "a"]
    assert first.top_pages[0].search_scores == {"bm25": 0.0, "embed": 0.0, "rrf": 0.0}
    assert first == _oracle(layout, "token", embedder=None, top_k=3, page="pin")
    probes.clear()
    assert q.plan_query_brief("token", layout, embedder=None, top_k=3, page="pin") == first
    assert dict(probes) == {}


def test_session_hybrid_edit_reembeds_only_changed_page(make_workspace, probes) -> None:
    layout = make_workspace("synthetic")
    q.plan_query_brief("token", layout, embedder=FakeEmbedder(), top_k=5)
    text = "---\ntitle: Changed\n---\nrotation token changed\n"
    (layout.bundle_dir / "s/p3.md").write_text(text, encoding="utf-8", newline="\n")
    probes.clear()
    embedder = FakeEmbedder()
    brief = q.plan_query_brief("token", layout, embedder=embedder, top_k=5)
    assert embedder.calls == [text, "token"]
    assert probes["tokenized"] == 1 and probes["full_loads"] == probes["hashed"] == 0
    assert brief == _oracle(layout, "token", embedder=FakeEmbedder(), top_k=5)


@pytest.mark.parametrize("backend", ("disabled", "busy"))
def test_run_query_fallback_retains_the_role_bundle(make_workspace, monkeypatch, backend) -> None:
    import asyncio

    layout = make_workspace("synthetic")
    q.plan_query_brief("token", layout, embedder=FakeEmbedder(), top_k=5)
    holder = None
    if backend == "disabled":
        layout.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    else:
        monkeypatch.setattr(ls, "BUSY_TIMEOUT_MS", 100)
        holder = sqlite3.connect(q._search_db(layout.cache_dir), isolation_level=None)
        # Stale lexical rows with fresh embeddings exercise busy fallback while
        # keeping the embedding cache usable for the oracle under the same lock.
        holder.execute("UPDATE lex_docs SET sha256 = 'stale' WHERE concept_id = 'a'")
        holder.execute("BEGIN IMMEDIATE")
    captured = {}

    def stop(prepared, **kwargs):
        captured["prepared"] = prepared
        raise StopAfterRetrieval()

    monkeypatch.setattr(q, "_initial_candidates", stop)
    monkeypatch.setattr(q, "_load_query_graph_tools", lambda target: (None, []))
    try:
        with pytest.raises(StopAfterRetrieval):
            asyncio.run(q.run_query("token", layout, embedder=FakeEmbedder(), top_k=5))
    finally:
        if holder is not None:
            holder.execute("ROLLBACK")
            holder.close()
    prepared = captured["prepared"]
    oracle = q._prepare_query_retrieval("token", layout, prepared.bundle, top_k=5, embedder=FakeEmbedder())
    assert prepared.top_pages == oracle.top_pages
    assert prepared.candidates == oracle.candidates
    assert prepared.bundle.concept("a") is not None


@pytest.mark.parametrize(
    ("concept_id", "normalized"),
    ((" leading", "leading"), ("trailing ", "trailing"), ("double.md", "double"), ("nested.md.md", "nested.md")),
)
@pytest.mark.parametrize("target_exists", (False, True))
def test_session_excerpt_preserves_bundle_key_normalization(
    make_workspace, concept_id, normalized, target_exists
) -> None:
    layout = make_workspace("synthetic")
    (layout.bundle_dir / f"{concept_id}.md").write_text(
        "---\ntitle: Original\n---\ntoken source\n", encoding="utf-8", newline="\n"
    )
    target = layout.bundle_dir / f"{normalized}.md"
    if target_exists:
        target.write_text("---\ntitle: Normalized\n---\ntoken target\n", encoding="utf-8", newline="\n")
    settle_mtimes(layout.bundle_dir)
    kw = {"embedder": None, "top_k": 3, "page": concept_id}
    brief = q.plan_query_brief("token", layout, **kw)
    assert brief.top_pages[0].path == concept_id
    assert brief == _oracle(layout, "token", **kw)
    if target_exists:
        assert brief.top_pages[0].excerpt == "# Normalized\n\ntoken target"
        target.write_text("---\ntitle: Edited\n---\ntoken updated\n", encoding="utf-8", newline="\n")
        updated = q.plan_query_brief("token", layout, **kw)
        assert updated == _oracle(layout, "token", **kw)
        assert updated.top_pages[0].excerpt == "# Edited\n\ntoken updated"
        target.unlink()
        assert q.plan_query_brief("token", layout, **kw) == _oracle(layout, "token", **kw)
    else:
        assert brief.top_pages[0].excerpt == f"ERROR: no concept {normalized!r} in this bundle"


@pytest.mark.parametrize(
    ("title", "expected"),
    [(r"\uD800", "\ud800"), (r"\uDC00", "\udc00"), (r"\uD800\uDC00", "\ud800\udc00"), ("café 東京 𐀀", "café 東京 𐀀")],
)
def test_reader_titles_and_unicode_ids_have_exact_cold_and_warm_parity(make_workspace, probes, title, expected):
    layout = make_workspace("synthetic")
    concept_id = "café-東京"
    (layout.bundle_dir / f"{concept_id}.md").write_text(
        f'---\ntitle: "{title}"\n---\nunicoderegressiontoken\n', encoding="utf-8", newline="\n"
    )
    settle_mtimes(layout.bundle_dir)
    oracle = _oracle(layout, "unicoderegressiontoken", embedder=None, top_k=3)
    assert oracle.top_pages[0].path == concept_id
    assert oracle.top_pages[0].excerpt == f"# {expected}\n\nunicoderegressiontoken"
    assert q.plan_query_brief("unicoderegressiontoken", layout, embedder=None, top_k=3) == oracle
    probes.clear()
    assert q.plan_query_brief("unicoderegressiontoken", layout, embedder=None, top_k=3) == oracle
    assert dict(probes) == {}


@pytest.mark.parametrize(
    ("concept_id", "resolved", "missing", "present"),
    [
        ("nested.md.md", "nested.md", "ERROR: no concept 'nested.md' in this bundle", "# Nested.Md\n\ntoken"),
        ("space .md", "space ", "ERROR: no concept 'space ' in this bundle", "# Space \n\ntoken"),
    ],
)
@pytest.mark.parametrize("target_exists", [False, True])
def test_session_normalizes_excerpt_keys_once(make_workspace, concept_id, resolved, missing, present, target_exists):
    layout = make_workspace("synthetic")
    (layout.bundle_dir / f"{concept_id}.md").write_text(
        "---\ntitle: Source\n---\ntoken\n", encoding="utf-8", newline="\n"
    )
    if target_exists:
        (layout.bundle_dir / f"{resolved}.md").write_text(
            "---\ntitle: ''\n---\ntoken\n", encoding="utf-8", newline="\n"
        )
    settle_mtimes(layout.bundle_dir)
    expected = present if target_exists else missing
    oracle = _oracle(layout, "token", embedder=None, top_k=3, page=concept_id)
    assert oracle.top_pages[0].excerpt == expected
    for _ in range(2):
        brief = q.plan_query_brief("token", layout, embedder=None, top_k=3, page=concept_id)
        assert brief == oracle
        assert brief.top_pages[0].excerpt == expected
