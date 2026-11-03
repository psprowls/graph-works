"""`scripts/read_bench_serve.py`: in-process gw-serve GET probes for the read benchmark."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bench_reads  # noqa: E402
import read_bench_serve  # noqa: E402
from bench_reads import READS, Size  # noqa: E402
from read_bench_corpus import generate  # noqa: E402

ALL_COUNTERS = {
    "files_parsed", "link_graph_builds", "git_calls", "pages_hashed", "pages_tokenized",
    "walks", "markdown_parses",
}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("bench-serve") / "c"
    generated = generate(root, items=12, wiki_pages=8)
    index = root / "host" / ".git" / "index"
    stamp = index.stat()
    os.utime(index, ns=(stamp.st_atime_ns, stamp.st_mtime_ns - 3_000_000_000))
    return generated


@pytest.fixture(scope="module")
def target(corpus):
    return bench_reads.Target("synthetic", corpus.layout, corpus.sample_item, corpus.sample_page, 12, 8)


def test_every_serve_read_returns_its_json_projection(target) -> None:
    for name in read_bench_serve.SERVE_READS:
        assert isinstance(READS[name](target), dict), name
    assert READS["serve.work.item"](target)["path"] == target.sample_item
    assert READS["serve.wiki.page"](target)["id"] == target.sample_page


def test_warm_serve_reads_reuse_one_sidecar_snapshot(target) -> None:
    rows = bench_reads.measure_target(target, list(read_bench_serve.SERVE_READS), 2, 0)
    assert [row.read for row in rows] == list(read_bench_serve.SERVE_READS)
    for row in rows:
        assert row.error is None and row.mode == "warm" and row.repeats == 2 and row.counts_stable, row.read
        assert (row.counts["files_parsed"], row.counts["link_graph_builds"], row.counts["walks"]) == (0, 0, 0), row.read


def test_cold_serve_read_runs_in_a_fresh_process(target, monkeypatch) -> None:
    def parent_only(_target):
        raise RuntimeError("parent registry must not reach the child")

    monkeypatch.setitem(READS, "serve.work.status", parent_only)
    row = bench_reads.measure_cold(target, "serve.work.status", 1)
    assert row.error is None, row.error
    assert row.mode == "cold" and row.repeats == 1
    assert set(row.counts) == ALL_COUNTERS and all(type(v) is int and v >= 0 for v in row.counts.values())


def test_missing_sidecar_is_an_unavailable_error_row(target, monkeypatch) -> None:
    monkeypatch.setattr(read_bench_serve, "build_app", None)
    row = bench_reads.measure_warm(target, "serve.work.status", READS["serve.work.status"], 1)
    assert row.error == "LookupError: sidecar unavailable in this environment"


def test_unknown_route_is_route_unavailable(target) -> None:
    with pytest.raises(LookupError, match="sidecar route unavailable in this revision: /v1/no-such-route"):
        read_bench_serve.get(target, "/v1/no-such-route")


def test_refused_read_is_a_sidecar_error(target) -> None:
    with pytest.raises(read_bench_serve.SidecarError, match=r"GET /v1/work/item -> 404"):
        read_bench_serve.get(target, "/v1/work/item", {"path": "work/no-such-item"})


def test_serve_page_without_a_sample_page_is_an_error_row(tmp_path) -> None:
    small = generate(tmp_path / "c", items=1, wiki_pages=0, malformed=False)
    small_target = bench_reads.Target("synthetic", small.layout, small.sample_item, None, 1, 0)
    row = bench_reads.measure_warm(small_target, "serve.wiki.page", READS["serve.wiki.page"], 1)
    assert row.error is not None and "sample page" in row.error


def test_run_benchmark_puts_serve_rows_in_the_cli_table(tmp_path) -> None:
    result = bench_reads.run_benchmark(
        sizes=[Size(12, 8, 1, 0)], workspace=None, reads=["work.status", "serve.work.status"],
        warm=1, cold=0, workspace_repeats=(1, 0), scratch=tmp_path, idle=None,
    )
    assert [row["read"] for row in result["rows"]] == ["work.status", "serve.work.status"]
    assert "| serve.work.status | warm |" in bench_reads.render_markdown(result)
