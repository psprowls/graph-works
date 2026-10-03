"""Read benchmark: p50/p95 latency and structural counts for graph-works display reads.

Evidence, not a gate (D-004): timings are recorded and reported, never asserted.
The counts (files parsed, link graphs built, git processes) are host-independent;
the before-and-after report child asserts invariants over them.

Run under graph-works-core's environment:

    uv run --package graph-works-core python scripts/bench_reads.py run --json out.json --markdown out.md
    uv run --package graph-works-core python scripts/bench_reads.py run --no-synthetic --workspace "$GRAPH_WORKS_DIR"
    uv run --package graph-works-core python scripts/bench_reads.py generate /tmp/corpus --items 1000 --wiki-pages 1000

See scripts/bench-reads.md.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import read_bench_corpus  # noqa: E402
import read_bench_search  # noqa: E402
from graph_works_core.wiki_page.citations import run_wiki_citations  # noqa: E402
from graph_works_core.wiki_page.commands import run_page_read, run_wiki_tree  # noqa: E402
from graph_works_core.work import commands as work  # noqa: E402
from graph_works_core.workspace.discovery import resolve  # noqa: E402
from graph_works_core.workspace.layout import WorkspaceLayout  # noqa: E402
from read_bench_counters import counting  # noqa: E402

SCRIPT = Path(__file__).resolve()
REPO_ROOT = SCRIPT.parents[1]
DEFAULT_MATRIX = ("100x1000", "1000x1000", "10000x1000@3/2", "1000x100", "1000x5000")


@dataclass(frozen=True, slots=True)
class Target:
    label: str
    layout: WorkspaceLayout
    sample_item: str
    sample_page: str | None
    items: int | None
    wiki_pages: int | None


def _page(target: Target) -> str:
    if target.sample_page is None:
        raise LookupError("no sample page in this target")
    return target.sample_page


READS: dict[str, Callable[[Target], object]] = {
    "work.status": lambda t: work.run_status(t.layout),
    "work.list": lambda t: work.run_work_list(t.layout),
    "work.item": lambda t: work.run_item_read(t.layout, t.sample_item),
    "work.queue": lambda t: work.run_work_queue(t.layout),
    "wiki.tree": lambda t: run_wiki_tree(t.layout),
    "wiki.page": lambda t: run_page_read(t.layout, _page(t)),
    "wiki.citations": lambda t: run_wiki_citations(t.layout, _page(t)),
}

READS.update(read_bench_search.SEARCH_READS)


@dataclass(slots=True)
class Row:
    target: str
    items: int | None
    wiki_pages: int | None
    read: str
    mode: str
    repeats: int
    p50_ms: float | None = None
    p95_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None
    counts: dict[str, int | None] = field(default_factory=dict)
    counts_stable: bool = True
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Size:
    items: int
    wiki_pages: int
    warm: int | None
    cold: int | None


def parse_size(text: str) -> Size:
    """`ITEMSxWIKI` or `ITEMSxWIKI@WARM/COLD`."""
    shape, has_reps, reps = text.partition("@")
    items, has_x, wiki = shape.partition("x")
    if not has_x:
        raise ValueError(f"size must be ITEMSxWIKI[@WARM/COLD], got {text!r}")
    warm = cold = None
    if has_reps:
        warm_text, has_slash, cold_text = reps.partition("/")
        if not has_slash:
            raise ValueError(f"repeats must be WARM/COLD, got {reps!r}")
        warm, cold = int(warm_text), int(cold_text)
    size = Size(int(items), int(wiki), warm, cold)
    if size.items < 1 or size.wiki_pages < 0:
        raise ValueError(f"need items >= 1 and wiki pages >= 0, got {text!r}")
    if warm is not None and (warm < 1 or cold is None or cold < 0):
        raise ValueError(f"need warm >= 1 and cold >= 0, got {text!r}")
    return size


def percentile(samples: Sequence[float], fraction: float) -> float:
    """Nearest rank: the smallest sample with at least *fraction* of the samples at or below it."""
    if not samples:
        raise ValueError("no samples")
    ordered = sorted(samples)
    return ordered[max(1, math.ceil(fraction * len(ordered))) - 1]


def _row(target: Target, name: str, mode: str, timings: list[float], seen: list[dict[str, int | None]]) -> Row:
    return Row(
        target=target.label,
        items=target.items,
        wiki_pages=target.wiki_pages,
        read=name,
        mode=mode,
        repeats=len(timings),
        p50_ms=round(percentile(timings, 0.5), 3),
        p95_ms=round(percentile(timings, 0.95), 3),
        min_ms=round(min(timings), 3),
        max_ms=round(max(timings), 3),
        counts=seen[0],
        counts_stable=all(counts == seen[0] for counts in seen),
    )


def _error_row(target: Target, name: str, mode: str, repeats: int, error: str) -> Row:
    return Row(target.label, target.items, target.wiki_pages, name, mode, repeats, error=error)


def measure_warm(target: Target, name: str, read: Callable[[Target], object], repeats: int) -> Row:
    """One discarded call, then *repeats* counted and timed calls in this process."""
    timings: list[float] = []
    seen: list[dict[str, int | None]] = []
    try:
        read(target)
        for _ in range(repeats):
            with counting() as counts:
                started = time.perf_counter()
                read(target)
                timings.append((time.perf_counter() - started) * 1000)
            seen.append(counts.as_dict())
    except Exception as exc:  # noqa: BLE001 -- a failing read is a result, not a crash
        return _error_row(target, name, "warm", repeats, f"{type(exc).__name__}: {exc}")
    return _row(target, name, "warm", timings, seen)


def measure_cold(target: Target, name: str, repeats: int) -> Row:
    """*repeats* fresh interpreters, each timed end to end: what one `gw` call pays."""
    command = [sys.executable, str(SCRIPT), "one-shot", "--workspace", str(target.layout.root), "--read", name,
               "--sample-item", target.sample_item]
    if target.sample_page is not None:
        command += ["--sample-page", target.sample_page]
    timings: list[float] = []
    seen: list[dict[str, int | None]] = []
    for _ in range(repeats):
        started = time.perf_counter()
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
        elapsed = (time.perf_counter() - started) * 1000
        if done.returncode != 0:
            lines = done.stderr.strip().splitlines()
            return _error_row(target, name, "cold", repeats, lines[-1] if lines else f"exit {done.returncode}")
        timings.append(elapsed)
        seen.append(json.loads(done.stdout)["counts"])
    return _row(target, name, "cold", timings, seen)


def measure_target(target: Target, reads: Sequence[str], warm: int, cold: int) -> list[Row]:
    rows: list[Row] = []
    for name in reads:
        rows.append(measure_warm(target, name, READS[name], warm))
        if cold > 0:
            rows.append(measure_cold(target, name, cold))
    if warm > 0 and "search.brief-hybrid" in reads:
        for name, probe, repeats in (
            ("search.cosine-scan", read_bench_search.time_cosine_scan, warm),
            ("search.lexical-cold-sync", read_bench_search.time_cold_sync, max(1, min(warm, 3))),
        ):
            try:
                timings, counts = probe(target, repeats)
                rows.append(_row(target, name, "warm", timings, [counts]))
            except Exception as exc:  # noqa: BLE001 -- a failing probe is a result
                rows.append(_error_row(target, name, "warm", repeats, f"{type(exc).__name__}: {exc}"))
    return rows


def _first_docs_page(layout: WorkspaceLayout) -> str | None:
    docs = layout.bundle_dir / "docs"
    if not docs.is_dir():
        return None
    for path in sorted(docs.rglob("*.md")):
        if path.name == "index.md" or path.stem == "malformed":
            continue
        return path.relative_to(layout.bundle_dir).with_suffix("").as_posix()
    return None


def target_for_workspace(root: Path, *, sample_item: str | None = None, sample_page: str | None = None) -> Target:
    """An existing workspace as a target; samples default to its first item and first docs page."""
    layout = resolve(workspace=root)
    if sample_item is None:
        items = work.run_work_list(layout)
        if not items:
            raise LookupError(f"no work items in {root}")
        sample_item = items[0].path
    page = sample_page if sample_page is not None else _first_docs_page(layout)
    return Target("workspace", layout, sample_item, page, None, None)


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def _loadavg() -> list[float] | None:
    return [round(value, 2) for value in os.getloadavg()] if hasattr(os, "getloadavg") else None


def wait_for_idle(max_load: float, wait_seconds: float, poll_seconds: float = 30.0) -> bool | None:
    """Wait until 1-minute load per CPU is at most *max_load*; `None` when load is unknowable."""
    cpus = os.cpu_count() or 1
    deadline = time.monotonic() + wait_seconds
    while True:
        load = _loadavg()
        if load is None:
            return None
        if load[0] / cpus <= max_load:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_seconds)


def run_benchmark(
    *,
    sizes: Sequence[Size],
    workspace: Path | None,
    reads: Sequence[str],
    warm: int,
    cold: int,
    workspace_repeats: tuple[int, int],
    scratch: Path | None,
    idle: bool | None,
) -> dict[str, object]:
    loadavg_before = _loadavg()
    rows: list[Row] = []
    with tempfile.TemporaryDirectory(dir=scratch, prefix="gw-read-bench-") as tmp:
        for index, size in enumerate(sizes):
            root = Path(tmp) / f"corpus-{index}"
            corpus = read_bench_corpus.generate(root, items=size.items, wiki_pages=size.wiki_pages)
            target = Target("synthetic", corpus.layout, corpus.sample_item, corpus.sample_page,
                            size.items, size.wiki_pages)
            rows += measure_target(target, reads, size.warm or warm, cold if size.cold is None else size.cold)
            shutil.rmtree(root)
    workspace_head = None
    if workspace is not None:
        target = target_for_workspace(workspace)
        workspace_head = _git(target.layout.root, "rev-parse", "HEAD") or None
        rows += measure_target(target, reads, *workspace_repeats)
    return {
        "schema": 1,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": {
            "graph_works": _git(REPO_ROOT, "rev-parse", "HEAD") or None,
            "dirty": bool(_git(REPO_ROOT, "status", "--porcelain")),
        },
        "workspace_head": workspace_head,
        "generator_version": read_bench_corpus.GENERATOR_VERSION,
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "loadavg_before": loadavg_before,
            "loadavg_after": _loadavg(),
            "idle": idle,
        },
        "rows": [asdict(row) for row in rows],
    }


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def render_markdown(result: dict[str, object]) -> str:
    source = result["source"]
    host = result["host"]
    assert isinstance(source, dict) and isinstance(host, dict)
    lines = [
        "# Read benchmark",
        "",
        f"- Recorded: {result['recorded_at']}",
        f"- graph-works: `{source['graph_works']}` (dirty: {source['dirty']}); workspace: `{result['workspace_head']}`",
        f"- Host: {host['platform']}, {host['machine']}, {host['cpu_count']} CPUs, Python {host['python']}",
        f"- Load average before {host['loadavg_before']}, after {host['loadavg_after']}; idle: {host['idle']}",
        f"- Generator version {result['generator_version']}. p95 is nearest-rank: with n < 20 it is the maximum.",
        "",
        "| Target | Items | Wiki pages | Read | Mode | n | p50 ms | p95 ms | Files parsed | Link graphs | Git calls | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = result["rows"]
    assert isinstance(rows, list)
    for row in rows:
        counts = row["counts"]
        note = row["error"] or ("" if row["counts_stable"] else "counts varied between repeats")
        lines.append(
            f"| {row['target']} | {_cell(row['items'])} | {_cell(row['wiki_pages'])} | {row['read']} | {row['mode']} "
            f"| {row['repeats']} | {_cell(row['p50_ms'])} | {_cell(row['p95_ms'])} "
            f"| {_cell(counts.get('files_parsed'))} | {_cell(counts.get('link_graph_builds'))} "
            f"| {_cell(counts.get('git_calls'))} | {note} |"
        )
    return "\n".join(lines) + "\n"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _pair(text: str) -> tuple[int, int]:
    warm, _, cold = text.partition("/")
    return int(warm), int(cold)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    gen = commands.add_parser("generate", help="write one synthetic workspace for inspection")
    gen.add_argument("root", type=Path)
    gen.add_argument("--items", type=int, required=True)
    gen.add_argument("--wiki-pages", type=int, required=True)

    run = commands.add_parser("run", help="measure the matrix and optionally a real workspace")
    run.add_argument("--matrix", default=",".join(DEFAULT_MATRIX))
    run.add_argument("--no-synthetic", action="store_true")
    run.add_argument("--workspace", type=Path)
    run.add_argument("--reads", default=",".join(READS))
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--cold-repeats", type=int, default=3)
    run.add_argument("--workspace-repeats", default="3/2")
    run.add_argument("--scratch", type=Path)
    run.add_argument("--max-load", type=float, default=0.5, help="1-minute load per CPU counted as idle")
    run.add_argument("--idle-wait", type=float, default=600.0, help="seconds to wait for an idle host")
    run.add_argument("--json", type=Path)
    run.add_argument("--markdown", type=Path)

    one = commands.add_parser("one-shot", help="internal: one counted read in a fresh process")
    one.add_argument("--workspace", type=Path, required=True)
    one.add_argument("--read", choices=sorted(READS), required=True)
    one.add_argument("--sample-item", required=True)
    one.add_argument("--sample-page")

    options = parser.parse_args(argv)
    if options.command == "generate":
        corpus = read_bench_corpus.generate(options.root, items=options.items, wiki_pages=options.wiki_pages)
        print(corpus.layout.root)
        return 0
    if options.command == "one-shot":
        target = Target("one-shot", resolve(workspace=options.workspace), options.sample_item, options.sample_page,
                        None, None)
        with counting() as counts:
            READS[options.read](target)
        print(json.dumps({"counts": counts.as_dict()}))
        return 0

    reads = [name for name in options.reads.split(",") if name]
    unknown = sorted(set(reads) - set(READS))
    if unknown:
        parser.error(f"unknown reads: {', '.join(unknown)}")
    sizes = [] if options.no_synthetic else [parse_size(text) for text in options.matrix.split(",") if text]
    idle = wait_for_idle(options.max_load, options.idle_wait)
    result = run_benchmark(
        sizes=sizes, workspace=options.workspace, reads=reads, warm=options.repeats, cold=options.cold_repeats,
        workspace_repeats=_pair(options.workspace_repeats), scratch=options.scratch, idle=idle,
    )
    table = render_markdown(result)
    if options.json is not None:
        _write_text(options.json, json.dumps(result, indent=2, sort_keys=True) + "\n")
    if options.markdown is not None:
        _write_text(options.markdown, table)
    print(table, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
