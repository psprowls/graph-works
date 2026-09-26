"""Default Titan policy, exercised at the provider and persisted-index boundaries."""

import logging
import sqlite3

import pytest
from botocore.exceptions import ClientError
from graph_works_core.query import commands as q
from graph_works_core.workspace.errors import QueryError
from graph_works_core.workspace.layout import layout_for
from okf_io import load_bundle


def overflow():
    return ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "Too many input tokens. Max input tokens: 8192, request input token count: 12273",
            }
        },
        "InvokeModel",
    )


class Provider:
    def __init__(self, limit=32_000, error=None):
        self.limit = limit
        self.error = error
        self.calls = []

    def embed_query(self, text):
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        if len(text) > self.limit:
            raise overflow()
        return [1.0, 0.5]


def adapter(monkeypatch, provider):
    monkeypatch.setattr(q, "make_bedrock_embeddings", lambda *a, **kw: provider)
    return q.default_embedder()


@pytest.mark.parametrize("text", ["", "short", "界" * 32_000])
def test_accepted_inputs_through_cap_are_unchanged(monkeypatch, text):
    provider = Provider()
    assert adapter(monkeypatch, provider).embed_query(text) == [1.0, 0.5]
    assert provider.calls == [text]


def test_large_input_is_bounded_and_warns_without_content(monkeypatch, caplog):
    provider = Provider()
    text = "privatecontent" * 4_000
    with caplog.at_level(logging.WARNING):
        assert adapter(monkeypatch, provider).embed_query(text) == [1.0, 0.5]
    assert provider.calls == [text[:32_000]]
    assert "56000" in caplog.text and "32000" in caplog.text
    assert q.DEFAULT_EMBED_MODEL_ID in caplog.text
    assert "privatecontent" not in caplog.text
    assert "may truncate" not in caplog.text


def test_dense_unicode_shrinks_after_structured_token_overflow(monkeypatch, caplog):
    provider = Provider(limit=2_000)
    text = "界🙂" * 5_000
    assert adapter(monkeypatch, provider).embed_query(text) == [1.0, 0.5]
    assert [len(t) for t in provider.calls] == [10_000, 5_000, 2_500, 1_250]
    assert all(t == text[: len(t)] for t in provider.calls)
    assert "10000" in caplog.text and "1250" in caplog.text


def test_persistent_overflow_is_finite_and_preserves_cause(monkeypatch):
    error = overflow()
    provider = Provider(error=error)
    with pytest.raises(QueryError, match=r"amazon\.titan") as caught:
        adapter(monkeypatch, provider).embed_query("x" * 50_000)
    assert [len(t) for t in provider.calls] == [
        32000,
        16000,
        8000,
        4000,
        2000,
        1000,
        500,
        250,
        125,
        62,
        31,
        15,
        7,
        3,
        1,
    ]
    assert caught.value.__cause__ is error
    assert "32000" in str(caught.value) and "1" in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"Error": None},
        {"Error": []},
        {"Error": {"Code": "ValidationException", "Message": None}},
        {"Error": {"Code": "ValidationException", "Message": "Malformed input"}},
        {"Error": {"Code": "AccessDeniedException", "Message": "Too many input tokens. Max input tokens: 8192"}},
        {"Error": {"Code": "ThrottlingException", "Message": "Slow down"}},
    ],
)
def test_unrelated_or_malformed_errors_are_not_retried(monkeypatch, response):
    error = RuntimeError("Too many input tokens. Max input tokens: 8192")
    error.response = response
    provider = Provider(error=error)
    with pytest.raises(RuntimeError) as caught:
        adapter(monkeypatch, provider).embed_query("hello")
    assert caught.value is error
    assert provider.calls == ["hello"]


@pytest.mark.parametrize("error", [ValueError("Too many input tokens. Max input tokens: 8192"), OSError("network")])
def test_unstructured_errors_are_not_retried(monkeypatch, error):
    provider = Provider(error=error)
    with pytest.raises(type(error)) as caught:
        adapter(monkeypatch, provider).embed_query("hello")
    assert caught.value is error
    assert provider.calls == ["hello"]


def test_empty_overflow_preserves_original_failure(monkeypatch):
    error = overflow()
    provider = Provider(error=error)
    with pytest.raises(ClientError) as caught:
        adapter(monkeypatch, provider).embed_query("")
    assert caught.value is error
    assert provider.calls == [""]


def corpus(tmp_path):
    layout = layout_for(tmp_path)
    layout.bundle_dir.mkdir()
    text = "---\ntitle: Large\n---\n" + "ordinary words " * 4_000 + " uniquetailterm"
    (layout.bundle_dir / "large.md").write_text(text, encoding="utf-8")
    (layout.bundle_dir / "small.md").write_text("---\ntitle: Small\n---\nsmall page", encoding="utf-8")
    return layout, text


def test_large_refresh_brief_and_full_text_tail_search(monkeypatch, tmp_path):
    layout, text = corpus(tmp_path)
    provider = Provider()
    brief = q.plan_query_brief("uniquetailterm", layout, embedder=adapter(monkeypatch, provider), top_k=3)
    assert {p.path for p in brief.top_pages} == {"large", "small"}
    assert len(provider.calls) == 3
    assert all(len(t) <= 32_000 for t in provider.calls)
    large = next(p for p in brief.top_pages if p.path == "large")
    assert large.search_scores["bm25"] > 0
    assert large.excerpt == q.read_bounded_page(
        load_bundle(layout.bundle_dir), "large", max_chars=q._CANDIDATE_EXCERPT_CHARS
    )
    assert (layout.bundle_dir / "large.md").read_text(encoding="utf-8") == text
    with sqlite3.connect(q._search_db(layout.cache_dir)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 2


def test_policy_invalidates_legacy_once_and_tail_edit_only_its_page(monkeypatch, tmp_path):
    layout, text = corpus(tmp_path)
    provider = Provider(limit=100_000)
    provider.model_id = q.DEFAULT_EMBED_MODEL_ID
    bundle = load_bundle(layout.bundle_dir)
    q.refresh_index(bundle, layout.cache_dir, embedder=provider)
    embedder = adapter(monkeypatch, provider)
    provider.calls.clear()
    assert q.refresh_index(bundle, layout.cache_dir, embedder=embedder).reason == "model-changed"
    assert len(provider.calls) == 2
    provider.calls.clear()
    assert not q.refresh_index(bundle, layout.cache_dir, embedder=embedder).rebuilt
    assert provider.calls == []
    (layout.bundle_dir / "large.md").write_text(text + " tail edit", encoding="utf-8")
    assert q.refresh_index(load_bundle(layout.bundle_dir), layout.cache_dir, embedder=embedder).embedded == 1
    assert provider.calls == [text[:32_000]]


def test_failed_generation_preserves_rows_and_manifest(monkeypatch, tmp_path):
    layout, _ = corpus(tmp_path)
    provider = Provider()
    embedder = adapter(monkeypatch, provider)
    q.refresh_index(load_bundle(layout.bundle_dir), layout.cache_dir, embedder=embedder)
    manifest = (layout.cache_dir / "search" / "manifest.json").read_bytes()
    with sqlite3.connect(q._search_db(layout.cache_dir)) as conn:
        rows = conn.execute("SELECT * FROM pages ORDER BY path").fetchall()
    (layout.bundle_dir / "large.md").unlink()
    (layout.bundle_dir / "a.md").write_text("---\ntitle: A\n---\naccepted", encoding="utf-8")
    (layout.bundle_dir / "z.md").write_text("---\ntitle: Z\n---\n" + "x" * 10_000, encoding="utf-8")
    original = provider.embed_query

    # Reject every retry for Z, while allowing the earlier inserted A.
    failing = False

    def fail_after_a(text):
        nonlocal failing
        failing = failing or text.startswith("---\ntitle: Z")
        if failing:
            raise overflow()
        return original(text)

    provider.embed_query = fail_after_a
    with pytest.raises(QueryError, match="Page z:"):
        q.refresh_index(load_bundle(layout.bundle_dir), layout.cache_dir, embedder=embedder)
    assert (layout.cache_dir / "search" / "manifest.json").read_bytes() == manifest
    with sqlite3.connect(q._search_db(layout.cache_dir)) as conn:
        assert conn.execute("SELECT * FROM pages ORDER BY path").fetchall() == rows


def test_index_unrelated_provider_failure_keeps_exception_and_names_page(monkeypatch, tmp_path, caplog):
    layout, _ = corpus(tmp_path)
    error = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "Denied"}}, "InvokeModel")
    provider = Provider(error=error)
    with pytest.raises(ClientError) as caught:
        q.refresh_index(load_bundle(layout.bundle_dir), layout.cache_dir, embedder=adapter(monkeypatch, provider))
    assert caught.value is error
    assert len(provider.calls) == 1
    assert "Embedding failed for page large" in caplog.text
