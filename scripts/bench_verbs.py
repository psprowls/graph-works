"""Single-verb latency benchmark for `gw`: fresh-process wall time, git spawns and parse counts.

Evidence, not a gate. Every `gw` call is `uv run --project <project> --package gw-cli gw ...`, so the
same script measures one checkout (before: main) or another (after: a branch worktree).

    python scripts/bench_verbs.py --project . --workspace "$GRAPH_WORKS_DIR" \
        --item work/some-item --runs 5 --out bench.md

Every probe runs against one throwaway, slimmed copy of `--workspace` (no `.git`, so no workspace
commits; no archive, cache, worktrees, scratch or reference-repository clones, which can total many GB);
`--workspace` itself is never written.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GIT_SPAWN = re.compile(r"trace: (built-in|exec): git ")

PROFILE_SCRIPT = """
import cProfile, pstats, sys
from gw_cli.cli import app
profiler = cProfile.Profile()
profiler.enable()
try:
    app(sys.argv[1:], standalone_mode=False)
except SystemExit:
    pass
profiler.disable()
yaml_loads = checks = rule_sets = 0
for (filename, _line, name), (_cc, ncalls, *_rest) in pstats.Stats(profiler).stats.items():
    if name == "load" and filename.replace("\\\\", "/").endswith("ruamel/yaml/main.py"):
        yaml_loads += ncalls
    elif name == "check_schema":
        checks += ncalls
    elif name == "_extra_rules":
        rule_sets += ncalls
sys.stderr.write(f"PROFILE {yaml_loads} {checks} {rule_sets}\\n")
"""


def _gw(project: str, args: list[str]) -> list[str]:
    return ["uv", "run", "--project", project, "--package", "gw-cli", "gw", *args]


def _env(workspace: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["GRAPH_WORKS_DIR"] = str(workspace)
    env.update(extra or {})
    return env


def _time(command: list[str], env: dict[str, str]) -> float:
    started = time.perf_counter()
    subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False, env=env)
    return time.perf_counter() - started


def _git_spawns(command: list[str], workspace: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        trace = Path(tmp) / "trace.txt"
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env=_env(workspace, {"GIT_TRACE": str(trace)}),
        )
        if not trace.exists():
            return 0
        text = trace.read_text(encoding="utf-8", errors="replace")
    return sum(1 for line in text.splitlines() if GIT_SPAWN.search(line))


def _profile(project: str, args: list[str], workspace: Path) -> tuple[int, int, int]:
    command = ["uv", "run", "--project", project, "--package", "gw-cli", "python", "-c", PROFILE_SCRIPT, *args]
    done = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False, env=_env(workspace)
    )
    match = re.search(r"PROFILE (\d+) (\d+) (\d+)", done.stderr)
    if match is None:
        return (-1, -1, -1)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _copy_workspace(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
            "worktrees", "cache", "perf", ".git", "scratch", "node_modules", "_archive", "repositories"
        ),)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--project", required=True, help="uv project directory (the checkout to measure)")
    parser.add_argument("--workspace", required=True, help="a live graph-works workspace (read only)")
    parser.add_argument("--item", required=True, help="work-item path, e.g. work/some-item")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--out", help="also write the markdown table here")
    options = parser.parse_args()

    workspace = Path(options.workspace)
    item = options.item
    rows: list[tuple[str, float, float, int, int, int, int]] = []

    def measure(label: str, make_args, ws: Path, runs: int) -> None:  # noqa: ANN001
        _time(_gw(options.project, make_args(0)), _env(ws))  # warm-up: bytecode, page cache
        timings = [_time(_gw(options.project, make_args(i + 1)), _env(ws)) for i in range(runs)]
        spawns = _git_spawns(_gw(options.project, make_args(runs + 1)), ws)
        loads, checks, rule_sets = _profile(options.project, make_args(runs + 2), ws)
        rows.append((label, statistics.median(timings), min(timings), spawns, loads, checks, rule_sets))

    with tempfile.TemporaryDirectory() as tmp:
        scratch = _copy_workspace(workspace, Path(tmp) / "ws")
        measure("gw work next", lambda _: ["work", "next", item, "--json", "--file", ""], scratch, options.runs)
        measure("gw work lint", lambda _: ["work", "lint", item], scratch, options.runs)
        measure(
            "gw work decision add",
            lambda i: [
                "work", "decision", "add", item,
                "--question", f"bench {i}",
                "--status", "assumed",
                "--answer", "a",
                "--if-wrong", "b",
                "--decided-by", "bench",
                "--affects", item,
            ],
            scratch,
            options.runs,
        )  # fmt: skip
        measure("gw work regen-index", lambda _: ["work", "regen-index"], scratch, options.runs)

    lines = [
        "| probe | median s | min s | git spawns | YAML loads | check_schema | rule sets |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *(
            f"| {label} | {median:.2f} | {fastest:.2f} | {spawns} | {loads} | {checks} | {sets} |"
            for label, median, fastest, spawns, loads, checks, sets in rows
        ),
    ]
    table = "\n".join(lines) + "\n"
    sys.stdout.write(table)
    if options.out:
        with open(options.out, "w", encoding="utf-8", newline="") as handle:
            handle.write(table)


if __name__ == "__main__":
    main()
