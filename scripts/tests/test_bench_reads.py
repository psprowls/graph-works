"""`scripts/bench_reads.py`: latency and structural counts for display reads."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bench_reads  # noqa: E402
from bench_reads import READS, Size, parse_size, percentile  # noqa: E402
from read_bench_corpus import generate  # noqa: E402

SCRIPT = Path(bench_reads.__file__).resolve()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory):
    return generate(tmp_path_factory.mktemp("bench") / "c", items=12, wiki_pages=8)


@pytest.fixture(scope="module")
def target(corpus):
    return bench_reads.Target("synthetic", corpus.layout, corpus.sample_item, corpus.sample_page, 12, 8)


def test_percentile_is_nearest_rank() -> None:
    samples = [float(n) for n in range(1, 21)]
    assert percentile(samples, 0.95) == 19.0
    assert percentile(samples, 0.5) == 10.0
    assert percentile([7.0], 0.95) == 7.0
    with pytest.raises(ValueError):
        percentile([], 0.5)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("100x1000", Size(100, 1000, None, None)), ("10000x0@3/0", Size(10000, 0, 3, 0))],
)
def test_parse_size(text: str, expected: Size) -> None:
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["100", "0x10", "10x-1", "10x10@3", "10x10@0/1", "axb"])
def test_parse_size_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_size(text)


def test_every_read_runs_warm_with_structural_counts(target) -> None:
    rows = {row.read: row for row in bench_reads.measure_target(target, list(READS), 2, 0)}
    assert set(rows) == set(READS)
    for row in rows.values():
        assert row.error is None, (row.read, row.error)
        assert row.mode == "warm" and row.repeats == 2
        assert row.p50_ms is not None and row.p95_ms is not None
        assert row.counts_stable, row.read
    # Baseline values belong in recorded evidence: converted reads may count zero.
    for row in rows.values():
        assert set(row.counts) == {"files_parsed", "link_graph_builds", "git_calls"}
        assert all(type(value) is int and value >= 0 for value in row.counts.values())


def test_cold_reads_run_in_a_fresh_process(target, monkeypatch) -> None:
    def parent_only(_target):
        raise RuntimeError("parent registry must not reach the child")

    monkeypatch.setitem(READS, "work.status", parent_only)
    row = bench_reads.measure_cold(target, "work.status", 1)
    assert row.error is None, row.error
    assert row.mode == "cold" and row.repeats == 1
    assert set(row.counts) == {"files_parsed", "link_graph_builds", "git_calls"}
    assert all(type(value) is int and value >= 0 for value in row.counts.values())


def test_one_shot_prints_counts(corpus) -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "one-shot", "--workspace", str(corpus.layout.root), "--read", "work.list",
         "--sample-item", corpus.sample_item],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    counts = json.loads(done.stdout)["counts"]
    assert set(counts) == {"files_parsed", "link_graph_builds", "git_calls"}
    assert all(type(value) is int and value >= 0 for value in counts.values())


def test_a_raising_read_becomes_an_error_row_and_the_run_continues(target, monkeypatch) -> None:
    def boom(_target: bench_reads.Target) -> object:
        raise RuntimeError("boom")

    monkeypatch.setitem(READS, "boom", boom)
    rows = bench_reads.measure_target(target, ["boom", "work.list"], 1, 0)
    assert rows[0].error == "RuntimeError: boom"
    assert rows[1].error is None


def test_page_reads_without_a_sample_page_are_error_rows(tmp_path) -> None:
    small = generate(tmp_path / "c", items=1, wiki_pages=0, malformed=False)
    target = bench_reads.Target("synthetic", small.layout, small.sample_item, None, 1, 0)
    row = bench_reads.measure_warm(target, "wiki.page", READS["wiki.page"], 1)
    assert row.error is not None and "sample page" in row.error


def test_measuring_a_workspace_leaves_its_bundle_untouched(corpus) -> None:
    def snapshot() -> dict[str, tuple[bytes, int]]:
        return {
            path.relative_to(corpus.layout.bundle_dir).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(corpus.layout.bundle_dir.rglob("*"))
            if path.is_file()
        }

    before = snapshot()
    target = bench_reads.target_for_workspace(corpus.layout.root)
    bench_reads.measure_target(target, list(READS), 1, 0)
    assert snapshot() == before


def test_target_for_workspace_picks_samples(corpus) -> None:
    target = bench_reads.target_for_workspace(corpus.layout.root)
    assert target.label == "workspace"
    assert (corpus.layout.bundle_dir / f"{target.sample_item}.md").is_file()
    assert target.sample_page is not None and target.sample_page.startswith("docs/")
    assert target.sample_page != "docs/explanations/malformed"


def test_run_benchmark_writes_a_complete_result(tmp_path) -> None:
    result = bench_reads.run_benchmark(
        sizes=[Size(12, 8, 1, 0)], workspace=None, reads=["work.list", "wiki.page"], warm=1, cold=0,
        workspace_repeats=(1, 0), scratch=tmp_path, idle=None,
    )
    assert result["schema"] == 1
    assert set(result["host"]) >= {"platform", "cpu_count", "loadavg_before", "loadavg_after", "idle"}
    assert [(row["read"], row["mode"]) for row in result["rows"]] == [("work.list", "warm"), ("wiki.page", "warm")]
    json.dumps(result)
    table = bench_reads.render_markdown(result)
    assert "| Target | Items | Wiki pages | Read | Mode | n |" in table
    assert "work.list" in table
    assert list(tmp_path.iterdir()) == []  # the corpus is removed after measuring


def test_wait_for_idle_returns_false_after_the_deadline(monkeypatch) -> None:
    monkeypatch.setattr(bench_reads, "_loadavg", lambda: [64.0, 64.0, 64.0])
    assert bench_reads.wait_for_idle(0.5, 0.0, poll_seconds=0.0) is False
    monkeypatch.setattr(bench_reads, "_loadavg", lambda: None)
    assert bench_reads.wait_for_idle(0.5, 0.0, poll_seconds=0.0) is None


def test_a_cached_real_read_can_record_zero_structural_work(target) -> None:
    cached_status = READS["work.status"](target)
    row = bench_reads.measure_warm(target, "cached.status", lambda _: cached_status, 2)
    assert row.error is None
    assert row.repeats == 2 and row.counts_stable
    assert row.counts == {"files_parsed": 0, "link_graph_builds": 0, "git_calls": 0}
