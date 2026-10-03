from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bench_reads  # noqa: E402
import read_bench_corpus  # noqa: E402
import read_bench_search as rbs  # noqa: E402


def _target(tmp_path: Path) -> bench_reads.Target:
    corpus = read_bench_corpus.generate(tmp_path / "w", items=20, wiki_pages=40)
    return bench_reads.Target("t", corpus.layout, corpus.sample_item, corpus.sample_page, 20, 40)


def test_bench_embedder_is_deterministic() -> None:
    assert rbs.BenchEmbedder().embed_query("x") == rbs.BenchEmbedder().embed_query("x")
    assert len(rbs.BenchEmbedder().embed_query("x")) == 64


def test_search_reads_are_registered_and_run(tmp_path: Path) -> None:
    target = _target(tmp_path)
    assert {"search.brief-lexical", "search.brief-hybrid"} <= set(bench_reads.READS)
    lexical = bench_reads.READS["search.brief-lexical"](target)
    assert lexical.top_pages and lexical.retrieval == "lexical"
    assert bench_reads.READS["search.brief-hybrid"](target).retrieval == "hybrid"


def test_warm_brief_counts_are_zero(tmp_path: Path) -> None:
    row = bench_reads.measure_warm(_target(tmp_path), "search.brief-lexical", bench_reads.READS["search.brief-lexical"], 2)
    assert row.error is None and row.counts_stable
    assert row.counts["files_parsed"] == row.counts["pages_hashed"] == row.counts["pages_tokenized"] == 0
    assert row.counts["link_graph_builds"] == 0


def test_cosine_and_cold_sync_rows(tmp_path: Path) -> None:
    target = _target(tmp_path)
    timings, counts = rbs.time_cosine_scan(target, 2)
    assert len(timings) == 2 and counts["vectors"] > 0
    timings, counts = rbs.time_cold_sync(target, 2)
    assert len(timings) == 2 and counts["pages_tokenized"] > 0 and counts["store_bytes"] > 0


def test_live_workspace_target_uses_a_scratch_cache(tmp_path: Path) -> None:
    target = _target(tmp_path)
    live = bench_reads.Target("workspace", target.layout, target.sample_item, target.sample_page, None, None)
    scratch = rbs.search_layout(live).cache_dir
    assert scratch != target.layout.cache_dir
    assert not scratch.is_relative_to(target.layout.root)
    assert rbs.search_layout(live).cache_dir == scratch
    assert rbs.search_layout(target).cache_dir == target.layout.cache_dir


def test_cold_lexical_read_resolves_in_child(tmp_path: Path) -> None:
    row = bench_reads.measure_cold(_target(tmp_path), "search.brief-lexical", 1)
    assert row.error is None
    assert "pages_tokenized" in row.counts
    assert all(type(value) is int and value >= 0 for value in row.counts.values())


def test_extra_rows_are_conditional_and_cold_sync_is_capped(tmp_path: Path) -> None:
    target = _target(tmp_path)
    lexical = bench_reads.measure_target(target, ["search.brief-lexical"], 1, 0)
    assert [row.read for row in lexical] == ["search.brief-lexical"]
    rows = bench_reads.measure_target(target, ["search.brief-hybrid"], 4, 0)
    assert [(row.read, row.repeats) for row in rows] == [
        ("search.brief-hybrid", 4), ("search.cosine-scan", 4), ("search.lexical-cold-sync", 3),
    ]
    assert all(row.error is None for row in rows)


def test_extra_probe_errors_become_rows_and_next_probe_runs(tmp_path: Path, monkeypatch) -> None:
    def boom(target, repeats):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(rbs, "time_cosine_scan", boom)
    rows = bench_reads.measure_target(_target(tmp_path), ["search.brief-hybrid"], 1, 0)
    assert rows[-2].error == "RuntimeError: scan failed"
    assert rows[-1].read == "search.lexical-cold-sync" and rows[-1].error is None


def test_legacy_environment_marks_cold_sync_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rbs, "lexical_store", None)
    with pytest.raises(LookupError, match="lexical store unavailable in this revision"):
        rbs.time_cold_sync(_target(tmp_path), 1)
