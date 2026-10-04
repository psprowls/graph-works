"""The workspace's package units -- the one table `just types`, `just check-pkg`
and `scripts/affected_packages.py` read.

A unit is what the gate checks as one thing. `okf-io` and `okf-ext` share the
root `testpaths` and one bare `uv run`, so they are one unit named `okf-io`;
`okf-ext` is an alias for it.

    package_units.py names            one unit name per line
    package_units.py env <name>       shell assignments for `check-pkg` to eval
    package_units.py mypy-jobs        "<arm>/<unit> <command...>" per line, every unit x arm
    package_units.py gate-json        the gw gate-unit manifest (JSON)
"""

from __future__ import annotations

import io
import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Unit:
    name: str
    uv_flags: tuple[str, ...]
    src: tuple[str, ...]
    lint: tuple[str, ...]
    testpath: str
    cov: tuple[str, ...]
    floor: int


def _pkg(name: str, *, floor: int = 95, extras: tuple[str, ...] = ()) -> Unit:
    module = name.replace("-", "_")
    flags = ("--package", name, *(f for e in extras for f in ("--extra", e)))
    return Unit(
        name=name,
        uv_flags=flags,
        src=(f"packages/{name}/src",),
        lint=(f"packages/{name}",),
        testpath=f"packages/{name}/tests",
        cov=(f"--cov={module}",),
        floor=floor,
    )


UNITS: tuple[Unit, ...] = (
    Unit(
        name="okf-io",
        uv_flags=(),
        src=("packages/okf-io/src", "packages/okf-ext/src"),
        lint=("packages/okf-io", "packages/okf-ext"),
        testpath="",
        cov=("--cov=okf_io", "--cov=okf_ext"),
        floor=95,
    ),
    _pkg("code-graph-io", floor=90),
    _pkg("code-wiki-okf"),
    _pkg("work-tracker-okf"),
    _pkg("config-io"),
    _pkg("plugin-fork-io"),
    _pkg("models-io", extras=("bedrock", "vercel")),
    _pkg("subagents-io"),
    _pkg("doc-wiki-okf"),
    _pkg("repositories-okf"),
    _pkg("graph-works-core"),
    _pkg("workflow-local"),
    _pkg("workflow-orca"),
    _pkg("graph-works-wire"),
    _pkg("graph-works-cli"),
    _pkg("graph-works-serve"),
)

ALIASES: dict[str, str] = {"okf-ext": "okf-io"}
ARMS: tuple[str, ...] = ("linux", "win32")
_BY_NAME = {u.name: u for u in UNITS}

SHARED_INPUTS: tuple[str, ...] = (
    "uv.lock",
    "pyproject.toml",
    "conftest.py",
    "justfile",
    "scripts/package_units.py",
    "scripts/affected_packages.py",
)


def gate_manifest(root: Path, *, cpu_count: int, jobs: int) -> dict[str, object]:
    """Describe the gate using the package-unit and dependency tables."""
    from affected_packages import dependency_graph  # lazy: affected_packages imports this module

    graph = dependency_graph(root)
    workers = str(max(1, cpu_count // jobs))
    units: list[dict[str, object]] = []
    for unit in UNITS:
        inputs = [f"{path}/**" for path in unit.lint]
        if unit.name == "okf-io":
            inputs.append("scripts/**")  # scripts/tests run in the root testpaths suite
        units.append(
            {
                "name": unit.name,
                "inputs": inputs,
                "depends_on": sorted(graph[unit.name]),
                "command": f"just _gate-unit {unit.name}",
                "env": {"PYTEST_XDIST_AUTO_NUM_WORKERS": workers},
            }
        )
    units.append(
        {
            "name": "plugin",
            "inputs": ["plugins/**"],
            "depends_on": ["graph-works-cli"],
            "command": "just test-plugin",
            "env": {},
        }
    )
    return {
        "version": 1,
        "jobs": jobs,
        "setup": {"command": "just preflight sync"},
        "repo_wide": {"command": "just gate-repo-wide"},
        "shared_inputs": list(SHARED_INPUTS),
        "units": units,
    }


def resolve(name: str) -> Unit:
    return _BY_NAME[ALIASES.get(name, name)]


def mypy_command(unit: Unit, arm: str) -> list[str]:
    return [
        "uv",
        "run",
        *unit.uv_flags,
        "mypy",
        "--strict",
        "--platform",
        arm,
        "--cache-dir",
        f".mypy_cache/{arm}/{unit.name}",
        *unit.src,
    ]


def _env(unit: Unit) -> str:
    values = {
        "UNIT": unit.name,
        "UVFLAGS": " ".join(unit.uv_flags),
        "SRC": " ".join(unit.src),
        "LINT": " ".join(unit.lint),
        "TESTPATH": unit.testpath,
        "MODULES": " ".join(unit.cov),
        "FLOOR": str(unit.floor),
    }
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items())


def force_lf_stdout() -> None:
    """Make stdout write `\\n` on Windows too: `xargs -L 1` would keep a trailing `\\r`."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(newline="\n")


def main(argv: list[str] | None = None) -> int:
    force_lf_stdout()
    args = sys.argv[1:] if argv is None else argv
    if args == ["gate-json"]:
        jobs = int(os.environ.get("GW_COV_JOBS", "3"))
        data = gate_manifest(Path.cwd(), cpu_count=os.cpu_count() or 1, jobs=jobs)
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
        return 0
    if args == ["names"]:
        sys.stdout.write("".join(f"{u.name}\n" for u in UNITS))
        return 0
    if args == ["mypy-jobs"]:
        for arm in ARMS:
            for unit in UNITS:
                sys.stdout.write(f"{arm}/{unit.name} {shlex.join(mypy_command(unit, arm))}\n")
        return 0
    if len(args) == 2 and args[0] == "env":
        try:
            unit = resolve(args[1])
        except KeyError:
            sys.stderr.write(f"unknown package '{args[1]}' -- see packages/ for valid names\n")
            return 1
        sys.stdout.write(_env(unit))
        return 0
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
