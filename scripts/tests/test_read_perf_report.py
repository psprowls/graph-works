"""Tests for scripts/read_perf_report.py: stdlib only, runs under the root pytest."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "read_perf_report.py"
sys.path.insert(0, str(SCRIPT.parent))

import read_perf_report as rpr  # noqa: E402


def run(revision: str, samples: list[dict], *, load: float = 0.1, cores: int = 8) -> dict:
    return {
        "revision": revision,
        "host": {"cpu": "test-cpu", "cores": cores, "memory_gb": 16, "os": "test-os", "python": "3.12.0"},
        "load_avg_1m": load,
        "cache_state": "cold+warm",
        "repetitions": 5,
        "samples": samples,
    }


def sample(probe: str, size: str, mode: str, seconds: float, parsed: int = 0) -> dict:
    return {
        "probe": probe,
        "size": size,
        "mode": mode,
        "seconds": seconds,
        "files_parsed": parsed,
        "link_graph_builds": 0,
        "git_calls": 0,
    }


def write(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    return path


def test_percentiles_use_nearest_rank() -> None:
    assert rpr.percentile([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], 50) == 0.5
    assert rpr.percentile([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], 95) == 1.0
    assert rpr.percentile([0.3], 95) == 0.3


def test_budget_is_one_and_a_half_times_p95_rounded_up_to_two_significant_figures() -> None:
    assert rpr.budget_ms(0.0123) == 19.0  # 18.45 -> 19
    assert rpr.budget_ms(0.2) == 300.0
    assert rpr.budget_ms(1.234) == 1900.0  # 1851 -> 1900


def test_renders_before_after_table_with_budget(tmp_path: Path) -> None:
    before = write(tmp_path, "b.json", run("aaaaaaa", [sample("work.list", "100", "warm", s) for s in (0.4, 0.5, 0.6)]))
    after = write(
        tmp_path, "a.json", run("bbbbbbb", [sample("work.list", "100", "warm", s) for s in (0.01, 0.02, 0.03)])
    )
    out = tmp_path / "r.md"
    assert rpr.main(["--before", str(before), "--after", str(after), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| work.list | 100 | warm | 500 | 600 | 20 | 30 | 45 |" in text
    assert "aaaaaaa" in text and "bbbbbbb" in text


def test_missing_before_probe_renders_na(tmp_path: Path) -> None:
    before = write(tmp_path, "b.json", run("a", [sample("work.list", "100", "warm", 0.5)]))
    after = write(tmp_path, "a.json", run("b", [sample("wiki.citations", "100", "warm", 0.01)]))
    out = tmp_path / "r.md"
    assert rpr.main(["--before", str(before), "--after", str(after), "--out", str(out)]) == 0
    assert "| wiki.citations | 100 | warm | n/a | n/a | 10 | 10 | 15 |" in out.read_text(encoding="utf-8")


def test_warm_invariant_violation_in_after_exits_2_and_is_listed_first(tmp_path: Path) -> None:
    before = write(tmp_path, "b.json", run("a", []))
    after = write(tmp_path, "a.json", run("b", [sample("work.status", "10000", "warm", 0.01, parsed=3)]))
    out = tmp_path / "r.md"
    assert rpr.main(["--before", str(before), "--after", str(after), "--out", str(out)]) == 2
    text = out.read_text(encoding="utf-8")
    assert text.index("## Invariant violations") < text.index("## Latency")
    assert "work.status at 10000 (warm): files_parsed=3" in text


def test_contended_run_is_flagged(tmp_path: Path) -> None:
    before = write(tmp_path, "b.json", run("a", [sample("work.list", "100", "warm", 0.5)], load=6.0, cores=8))
    after = write(tmp_path, "a.json", run("b", [sample("work.list", "100", "warm", 0.01)]))
    out = tmp_path / "r.md"
    rpr.main(["--before", str(before), "--after", str(after), "--out", str(out)])
    assert "before: contended" in out.read_text(encoding="utf-8")


def test_refuses_truncated_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "b.json"
    bad.write_text('{"revision": ', encoding="utf-8", newline="\n")
    good = write(tmp_path, "a.json", run("b", [sample("work.list", "100", "warm", 0.01)]))
    assert rpr.main(["--before", str(bad), "--after", str(good), "--out", str(tmp_path / "r.md")]) == 1
    assert f"read_perf_report: {bad}: not valid JSON" in capsys.readouterr().err


def test_refuses_probe_without_samples(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = run("b", [])
    data["samples"] = [
        {
            "probe": "work.list",
            "size": "100",
            "mode": "warm",
            "seconds": None,
            "files_parsed": 0,
            "link_graph_builds": 0,
            "git_calls": 0,
        }
    ]
    after = write(tmp_path, "a.json", data)
    before = write(tmp_path, "b.json", run("a", []))
    assert rpr.main(["--before", str(before), "--after", str(after), "--out", str(tmp_path / "r.md")]) == 1
    assert "work.list at 100 (warm): no timed samples" in capsys.readouterr().err


def test_runs_as_a_script(tmp_path: Path) -> None:
    before = write(tmp_path, "b.json", run("a", [sample("work.list", "100", "warm", 0.5)]))
    after = write(tmp_path, "a.json", run("b", [sample("work.list", "100", "warm", 0.01)]))
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--before", str(before), "--after", str(after), "--out", str(tmp_path / "r.md")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert done.returncode == 0, done.stderr


def aggregate(rows: list[dict]) -> dict:
    return {
        "schema": 1,
        "source": {"graph_works": "aggregate-rev", "dirty": True},
        "host": {
            "platform": "test-os",
            "machine": "test-machine",
            "python": "3.12",
            "cpu_count": 8,
            "loadavg_before": [0.1, 0.1, 0.1],
            "loadavg_after": [0.2, 0.2, 0.2],
            "idle": True,
        },
        "rows": rows,
    }


def row(**changes: object) -> dict:
    data = {
        "target": "synthetic",
        "items": 100,
        "wiki_pages": 20,
        "read": "work.list",
        "mode": "warm",
        "repeats": 5,
        "p50_ms": 0.123,
        "p95_ms": 0.456,
        "counts": {"files_parsed": 0, "link_graph_builds": 0, "git_calls": 0},
        "counts_stable": True,
        "error": None,
    }
    data.update(changes)
    return data


def report(tmp_path: Path, after: dict, before: dict | None = None) -> tuple[int, str]:
    b = write(tmp_path, "before.json", before or run("before", []))
    a = write(tmp_path, "after.json", after)
    out = tmp_path / "report.md"
    code = rpr.main(["--before", str(b), "--after", str(a), "--out", str(out)])
    return code, out.read_text(encoding="utf-8") if out.exists() else ""


def test_aggregate_preserves_percentiles_precision_metadata_and_unknown_counts(tmp_path: Path) -> None:
    before = aggregate([row(p50_ms=0.789, p95_ms=1.234, counts={})])
    code, text = report(tmp_path, aggregate([row()]), before)
    assert code == 0
    assert "| work.list | synthetic: 100x20 | warm | 0.789 | 1.234 | 0.123 | 0.456 | 0.69 |" in text
    assert "aggregate-rev" in text and "dirty: True" in text
    assert "test-machine" in text and "0.2" in text
    assert "| n/a | n/a | n/a | 0 | 0 | 0 |" in text


@pytest.mark.parametrize("timing", [None, True, -1, float("nan"), float("inf"), "0.1"])
def test_refuses_invalid_sample_timings(tmp_path: Path, timing: object) -> None:
    data = sample("work.list", "100", "warm", 0.1)
    data["seconds"] = timing
    assert report(tmp_path, run("after", [data]))[0] == 1


@pytest.mark.parametrize(
    "changes",
    [{"p50_ms": True}, {"p95_ms": -1}, {"p95_ms": float("nan")}, {"p50_ms": 2, "p95_ms": 1}, {"error": "read failed"}],
)
def test_refuses_invalid_aggregate_rows(tmp_path: Path, changes: dict) -> None:
    assert report(tmp_path, aggregate([row(**changes)]))[0] == 1


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"samples": []},
        {"samples": [None]},
        {"schema": 1, "rows": []},
        {"schema": 1, "rows": [{}]},
        {"samples": [{"seconds": 1}]},
    ],
)
def test_refuses_malformed_or_empty_after(tmp_path: Path, data: dict) -> None:
    assert report(tmp_path, data)[0] == 1


def test_unstable_counts_are_flagged_honestly(tmp_path: Path) -> None:
    code, text = report(tmp_path, aggregate([row(counts_stable=False)]))
    assert code == 2
    assert "counts varied between repeats" in text
    assert text.index("## Invariant violations") < text.index("## Latency")


@pytest.mark.parametrize(
    "probe,expected", [("search.lexical-cold-sync", 0), ("search.brief-hybrid", 2), ("search.brief-lexical", 2)]
)
def test_search_count_invariants(tmp_path: Path, probe: str, expected: int) -> None:
    code, text = report(tmp_path, aggregate([row(read=probe, counts={name: 1 for name in rpr.COUNTERS})]))
    assert code == expected
    if expected:
        for name in rpr.COUNTERS:
            assert f"{name}=1" in text


def test_missing_after_counts_cannot_claim_zero_work(tmp_path: Path) -> None:
    code, text = report(tmp_path, aggregate([row(counts={})]))
    assert code == 2
    assert "files_parsed=n/a" in text


def test_zero_budget_and_submillisecond_samples(tmp_path: Path) -> None:
    assert rpr.budget_ms(0) == 0
    code, text = report(tmp_path, run("after", [sample("work.list", "100", "warm", 0.000123)]))
    assert code == 0
    assert "| 0.123 | 0.123 | 0.19 |" in text


def test_output_failure_is_one_line_refusal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    b = write(tmp_path, "b.json", run("b", []))
    a = write(tmp_path, "a.json", aggregate([row()]))
    assert rpr.main(["--before", str(b), "--after", str(a), "--out", str(tmp_path)]) == 1
    assert capsys.readouterr().err.startswith("read_perf_report: ")


def test_aggregate_repeats_and_before_unstable_counts_are_visible(tmp_path: Path) -> None:
    code, text = report(tmp_path, aggregate([row(repeats=7)]), aggregate([row(counts_stable=False)]))
    assert code == 0
    assert "before counts varied between repeats" in text
    assert "after repeats: 7" in text


def test_duplicate_aggregate_rows_are_refused(tmp_path: Path) -> None:
    assert report(tmp_path, aggregate([row(), row()]))[0] == 1


def test_actual_host_load_after_marks_contention(tmp_path: Path) -> None:
    after = aggregate([row()])
    after["host"]["loadavg_after"] = [6, 2, 1]
    code, text = report(tmp_path, after)
    assert code == 0
    assert "after: contended" in text


@pytest.mark.parametrize(
    "changes", [{"items": "100"}, {"wiki_pages": -2}, {"repeats": False}, {"counts": {"git_calls": True}}]
)
def test_malformed_aggregate_counts_and_dimensions_are_refused(tmp_path: Path, changes: dict) -> None:
    assert report(tmp_path, aggregate([row(**changes)]))[0] == 1


def test_baseline_unavailable_cold_sync_renders_na_but_after_error_is_refused(tmp_path: Path) -> None:
    unavailable = row(
        read="search.lexical-cold-sync",
        error="LookupError: lexical store unavailable in this revision",
        p50_ms=None,
        p95_ms=None,
        counts={},
    )
    measured = row(read="search.lexical-cold-sync", counts={"pages_tokenized": 20})
    code, text = report(tmp_path, aggregate([measured]), aggregate([unavailable]))
    assert code == 0
    assert "| warm | n/a | n/a | 0.123 | 0.456 |" in text
    assert "lexical store unavailable" in text
    assert report(tmp_path, aggregate([unavailable]))[0] == 1


def test_cosine_scan_auxiliary_counts_do_not_claim_display_counts(tmp_path: Path) -> None:
    code, text = report(tmp_path, aggregate([row(read="search.cosine-scan", counts={"vectors": 20})]))
    assert code == 0
    assert "| n/a | n/a | n/a |" in text


def test_harness_revision_and_cpu_memory_metadata_are_visible(tmp_path: Path) -> None:
    after = aggregate([row()])
    after["source"]["harness_revision"] = "harness-head"
    after["host"].update(cpu="CPU model", memory_gb=32)
    code, text = report(tmp_path, after)
    assert code == 0
    assert "harness-head" in text and "CPU model" in text and "32 GB" in text


@pytest.mark.parametrize(
    "probe,mode", [("work.list", "cold"), ("search.cosine-scan", "warm"), ("search.lexical-cold-sync", "warm")]
)
def test_unstable_counts_outside_display_scope_are_evidence_only(tmp_path: Path, probe: str, mode: str) -> None:
    code, text = report(tmp_path, aggregate([row(read=probe, mode=mode, counts_stable=False)]))
    assert code == 0
    assert "after counts varied between repeats" in text
    assert "## Invariant violations" not in text


@pytest.mark.parametrize("probe", ["work.list", "search.brief-hybrid", "search.brief-lexical"])
def test_unstable_warm_display_counts_remain_invariant_failures(tmp_path: Path, probe: str) -> None:
    code, text = report(tmp_path, aggregate([row(read=probe, counts_stable=False)]))
    assert code == 2
    assert "## Invariant violations" in text


@pytest.mark.parametrize("actual_schema", [False, True])
def test_unavailable_host_load_is_unknown(tmp_path: Path, actual_schema: bool) -> None:
    if actual_schema:
        after = aggregate([row()])
        after["host"].update(idle=None, loadavg_before=None, loadavg_after=None)
    else:
        after = run("after", [sample("work.list", "100", "warm", 0.01)])
        after["load_avg_1m"] = None
    code, text = report(tmp_path, after)
    assert code == 0
    assert "after: unknown" in text


@pytest.mark.parametrize(
    "changes",
    [
        {"repeats": "five"},
        {"repeats": None},
        {"repeats": False},
        {"repeats": 0},
        {"repeats": -1},
        {"counts_stable": "yes"},
        {"counts_stable": None},
        {"counts_stable": 0},
    ],
)
def test_malformed_raw_repetition_fields_are_one_line_refusals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], changes: dict
) -> None:
    raw = sample("work.list", "100", "warm", 0.1)
    raw.update(changes)
    assert report(tmp_path, run("after", [raw]))[0] == 1
    errors = capsys.readouterr().err.splitlines()
    assert len(errors) == 1
    assert errors[0].startswith("read_perf_report: ")
