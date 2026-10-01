"""Startup benchmark for `gw`: median wall time per probe, and an -X importtime aggregate.

Evidence, not a gate (D-004): wall-clock thresholds flake on a contended host. The
deterministic regression guard is packages/graph-works-cli/tests/test_lazy_startup.py.

Run under the CLI's environment so the probes see its dependencies:

    uv run --package graph-works-cli python scripts/bench_startup.py --runs 10 --importtime
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from collections import defaultdict

PROBES: dict[str, list[str]] = {
    "empty interpreter": ["-c", "pass"],
    "import graph_works_core": ["-c", "import graph_works_core"],
    "import subagents_io": ["-c", "import subagents_io"],
    "gw version": ["-m", "graph_works_cli.cli", "version"],
    "gw work status --help": ["-m", "graph_works_cli.cli", "work", "status", "--help"],
    "gw next --help": ["-m", "graph_works_cli.cli", "next", "--help"],
}


def _run(args: list[str], *, importtime: bool = False) -> tuple[float, str]:
    command = [sys.executable, *(["-X", "importtime"] if importtime else []), *args]
    started = time.perf_counter()
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    return time.perf_counter() - started, done.stderr


def _importtime_by_package(stderr: str) -> dict[str, int]:
    """Sum each line's *self* microseconds under its top-level package."""
    totals: dict[str, int] = defaultdict(int)
    for line in stderr.splitlines():
        if not line.startswith("import time:"):
            continue
        fields = [part.strip() for part in line.removeprefix("import time:").split("|")]
        if len(fields) != 3 or not fields[0].isdigit():
            continue  # the header row
        totals[fields[2].split(".")[0]] += int(fields[0])
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--importtime", action="store_true", help="also print per-package import self time")
    options = parser.parse_args()

    for label, args in PROBES.items():
        _run(args)  # discarded warm-up: bytecode, page cache
        timings = [_run(args)[0] for _ in range(options.runs)]
        print(
            f"{label:<26} median {statistics.median(timings) * 1000:7.0f} ms"
            f"   min {min(timings) * 1000:7.0f}   max {max(timings) * 1000:7.0f}   (n={options.runs})"
        )
        if options.importtime:
            totals = _importtime_by_package(_run(args, importtime=True)[1])
            for package, micros in sorted(totals.items(), key=lambda item: -item[1])[:8]:
                print(f"    {package:<28} {micros / 1000:8.1f} ms self")


if __name__ == "__main__":
    main()
