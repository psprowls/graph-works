"""Which package units a change touches -- the selection behind `just check-affected`.

Changed paths are `git diff --name-only --no-renames <base>` (committed since
<base> plus the working tree) and untracked files. <base> defaults to
`git merge-base HEAD main`. Each path is classified:

    packages/<dir>/**           the unit owning <dir> (okf-ext -> okf-io)
    plugins/**                  `test-plugin`
    *.md elsewhere              repo-wide checks only
    anything else               the full `just check`

and the selected units are widened to everything that depends on them,
transitively, from each package's declared requirements: `[project] dependencies`,
`[project.optional-dependencies]` and every `[dependency-groups]` list (tests import
dev-only dependencies too).

Run from the repository root:  affected_packages.py [--base <ref>]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from package_units import ALIASES, UNITS, force_lf_stdout  # noqa: E402

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_RANK = {"none": 0, "repo-only": 1, "packages": 2, "full": 3}


class GitError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Selection:
    mode: str
    plugin: bool
    units: tuple[str, ...]
    reasons: tuple[str, ...]


def _unit(name: str) -> str:
    return ALIASES.get(name, name)


def _req_name(requirement: str) -> str | None:
    match = _NAME.match(requirement)
    return None if match is None else match.group(1).lower().replace("_", "-")


def dependency_graph(root: Path) -> dict[str, set[str]]:
    units = {u.name for u in UNITS}
    graph: dict[str, set[str]] = {u: set() for u in units}
    for pyproject in sorted((root / "packages").glob("*/pyproject.toml")):
        owner = _unit(pyproject.parent.name)
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
        project = data.get("project", {})
        lists = [project.get("dependencies", []), *project.get("optional-dependencies", {}).values()]
        lists += data.get("dependency-groups", {}).values()
        for requirement in (r for deps in lists for r in deps if isinstance(r, str)):
            name = _req_name(requirement)
            if name is None:
                continue
            dep = _unit(name)
            if dep in units and dep != owner:
                graph[owner].add(dep)
    return graph


def reverse_closure(graph: dict[str, set[str]], seeds: set[str]) -> set[str]:
    dependents: dict[str, set[str]] = {u: set() for u in graph}
    for owner, deps in graph.items():
        for dep in deps:
            dependents[dep].add(owner)
    selected = set(seeds)
    frontier = list(seeds)
    while frontier:
        for dependent in dependents.get(frontier.pop(), ()):
            if dependent not in selected:
                selected.add(dependent)
                frontier.append(dependent)
    return selected


def select(paths: list[str], graph: dict[str, set[str]], package_dirs: set[str]) -> Selection:
    mode = "none"
    plugin = False
    seeds: set[str] = set()
    reasons: list[str] = []

    def bump(new: str) -> None:
        nonlocal mode
        if _RANK[new] > _RANK[mode]:
            mode = new

    for path in sorted(set(paths)):
        parts = path.split("/")
        if parts[0] == "packages" and len(parts) > 2 and parts[1] in package_dirs:
            unit = _unit(parts[1])
            seeds.add(unit)
            bump("packages")
            reasons.append(f"{path} -> {unit}")
        elif parts[0] == "plugins":
            plugin = True
            bump("repo-only")
            reasons.append(f"{path} -> test-plugin")
        elif path.endswith(".md") and parts[0] != "packages":
            bump("repo-only")
            reasons.append(f"{path} -> repo-wide checks")
        else:
            bump("full")
            reasons.append(f"{path} -> full check")
    units = tuple(sorted(reverse_closure(graph, seeds))) if mode == "packages" else ()
    return Selection(mode=mode, plugin=plugin, units=units, reasons=tuple(reasons))


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def changed_paths(root: Path, base: str | None) -> list[str]:
    if base is None:
        try:
            base = _git(root, "merge-base", "HEAD", "main").strip()
        except GitError as error:
            raise GitError(f"{error}\nno merge-base with 'main'; pass --base <ref>") from None
    diff = _git(root, "diff", "--name-only", "--no-renames", base).splitlines()
    untracked = _git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({p for p in (*diff, *untracked) if p})


def render(sel: Selection) -> str:
    lines = [f"mode: {sel.mode}", f"plugin: {'yes' if sel.plugin else 'no'}"]
    lines += [f"unit: {u}" for u in sel.units]
    lines += [f"reason: {r}" for r in sel.reasons]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    force_lf_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=None)
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        paths = changed_paths(root, args.base or None)
    except GitError as error:
        sys.stderr.write(f"affected_packages: {error}\n")
        return 2
    package_dirs = {p.parent.name for p in (root / "packages").glob("*/pyproject.toml")}
    sys.stdout.write(render(select(paths, dependency_graph(root), package_dirs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
