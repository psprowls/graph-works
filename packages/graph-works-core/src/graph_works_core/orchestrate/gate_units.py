"""A repository's gate units: the manifest `gate.units` prints, and each unit's input hash.

The manifest names units (inputs globs, depends_on, command, env), the inputs every
unit shares, an optional repo-wide command and an optional setup command (D-006).
A unit's hash covers the blob SHAs of its own inputs, its transitive dependencies'
inputs, the shared and extra inputs, its command and its env -- never file content.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal, NoReturn

from graph_works_core.orchestrate import gate_git
from graph_works_core.workspace import provenance
from graph_works_core.workspace.gate_config import RepoGate, is_repository_relative_glob

UnitsRefusal = Literal["units-invalid", "units-command-failed"]
TREE_UNIT = "tree"
_UNIT_TAG = "gw-gate-unit/2"
_TREE_TAG = "gw-gate-tree/1"
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")


class ManifestError(ValueError):
    """The manifest could not be produced or does not validate."""

    def __init__(self, reason: UnitsRefusal, message: str) -> None:
        super().__init__(message)
        self.reason: UnitsRefusal = reason


@dataclass(frozen=True, slots=True)
class GateUnit:
    name: str
    inputs: tuple[str, ...]
    depends_on: tuple[str, ...]
    command: str
    env: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class UnitManifest:
    jobs: int
    repo_wide: str | None
    setup: str | None
    shared_inputs: tuple[str, ...]
    units: tuple[GateUnit, ...]
    digest: str
    implicit: bool = False


def _invalid(message: str) -> ManifestError:
    return ManifestError("units-invalid", message)


def _glob(value: object, where: str) -> str:
    if not isinstance(value, str) or not is_repository_relative_glob(value):
        raise _invalid(f"{where}: {value!r} is not a repository-relative glob")
    return value


def _globs(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _invalid(f"{where}: not a list of repository-relative globs")
    return tuple(_glob(item, where) for item in value)


def _command(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{where}: command must be a non-empty string")
    return value


def _optional_command(data: Mapping[str, object], key: str) -> str | None:
    if key not in data:
        return None
    block = data[key]
    if not isinstance(block, dict):
        raise _invalid(f"{key}: must be a mapping with a command")
    return _command(block.get("command"), key)


def _unit(value: object) -> GateUnit:
    if not isinstance(value, dict):
        raise _invalid("units: every unit must be a mapping")
    name = value.get("name")
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise _invalid(f"units: name {name!r} must match {_NAME.pattern}")
    depends = value.get("depends_on", [])
    if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
        raise _invalid(f"units.{name}.depends_on: not a list of unit names")
    env = value.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise _invalid(f"units.{name}.env: must map strings to strings")
    return GateUnit(
        name=name,
        inputs=_globs(value.get("inputs"), f"units.{name}.inputs"),
        depends_on=tuple(sorted(set(depends))),
        command=_command(value.get("command"), f"units.{name}"),
        env=tuple(sorted(env.items())),
    )


def _check_graph(units: Sequence[GateUnit]) -> None:
    names = [u.name for u in units]
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise _invalid(f"units: duplicate unit name {name!r}")
        seen.add(name)
    for unit in units:
        for dep in unit.depends_on:
            if dep not in seen:
                raise _invalid(f"units.{unit.name}.depends_on: {dep!r} is not a declared unit")
    graph = {u.name: u.depends_on for u in units}
    state: dict[str, int] = {}

    for name in names:
        if state.get(name) == 2:
            continue
        state[name] = 1
        stack: list[tuple[str, Iterator[str]]] = [(name, iter(graph[name]))]
        while stack:
            node, dependencies = stack[-1]
            dependency = next(dependencies, None)
            if dependency is None:
                state[node] = 2
                stack.pop()
            elif state.get(dependency) == 1:
                trail = (*[frame[0] for frame in stack], dependency)
                raise _invalid(f"units: dependency cycle {' -> '.join(trail)}")
            elif state.get(dependency) != 2:
                state[dependency] = 1
                stack.append((dependency, iter(graph[dependency])))


def _digest(data: object) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    try:
        encoded = canonical.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid("manifest: strings must be encodable as UTF-8") from None
    return hashlib.sha256(encoded).hexdigest()


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    data: dict[str, object] = {}
    for key, value in pairs:
        if key in data:
            raise _invalid(f"manifest: duplicate JSON object key {key!r}")
        data[key] = value
    return data


def _non_json_constant(value: str) -> NoReturn:
    raise ValueError(f"{value} is not a JSON value")


def parse_manifest(text: str) -> UnitManifest:
    try:
        data = json.loads(text, object_pairs_hook=_json_object, parse_constant=_non_json_constant)
    except ManifestError:
        raise
    except ValueError as exc:
        raise ManifestError("units-command-failed", f"gate.units printed no JSON manifest ({exc})") from None
    if not isinstance(data, dict):
        raise _invalid("manifest: not a JSON object")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise _invalid(f"version: {data.get('version')!r} is not 1")
    jobs = data.get("jobs")
    if type(jobs) is not int or jobs < 1:
        raise _invalid(f"jobs: {jobs!r} is not a positive integer")
    raw_units = data.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        raise _invalid("units: must be a non-empty list")
    units = tuple(_unit(u) for u in raw_units)
    _check_graph(units)
    return UnitManifest(
        jobs=jobs,
        repo_wide=_optional_command(data, "repo_wide"),
        setup=_optional_command(data, "setup"),
        shared_inputs=_globs(data.get("shared_inputs", []), "shared_inputs"),
        units=units,
        digest=_digest(data),
    )


def implicit_manifest(full_command: str) -> UnitManifest:
    unit = GateUnit(TREE_UNIT, (), (), full_command, ())
    return UnitManifest(1, None, None, (), (unit,), _digest({"implicit": full_command}), implicit=True)


def glob_match(pattern: str, path: str) -> bool:
    """Case-sensitive fnmatch over repository-relative POSIX paths."""
    return fnmatchcase(path, pattern)


def _by_name(manifest: UnitManifest) -> dict[str, GateUnit]:
    return {u.name: u for u in manifest.units}


def dependency_closure(manifest: UnitManifest, name: str) -> tuple[str, ...]:
    units = _by_name(manifest)
    seen = {name}
    frontier = [name]
    while frontier:
        for dep in units[frontier.pop()].depends_on:
            if dep not in seen:
                seen.add(dep)
                frontier.append(dep)
    return tuple(sorted(seen))


def reverse_closure(manifest: UnitManifest, seeds: Iterable[str]) -> frozenset[str]:
    selected = set(seeds)
    changed = True
    while changed:
        changed = False
        for unit in manifest.units:
            if unit.name not in selected and selected.intersection(unit.depends_on):
                selected.add(unit.name)
                changed = True
    return frozenset(selected)


def units_matching(manifest: UnitManifest, files: Iterable[str]) -> frozenset[str]:
    paths = tuple(files)
    return frozenset(u.name for u in manifest.units if any(glob_match(g, p) for g in u.inputs for p in paths))


def unit_hashes(
    manifest: UnitManifest, listing: Mapping[str, gate_git.GitLeaf], extra_inputs: Sequence[str]
) -> dict[str, str]:
    units = _by_name(manifest)
    common = (*manifest.shared_inputs, *extra_inputs)
    hashes: dict[str, str] = {}
    for unit in manifest.units:
        globs = (*common, *(g for name in dependency_closure(manifest, unit.name) for g in units[name].inputs))
        files = sorted(
            (path, leaf.mode, leaf.object_sha)
            for path, leaf in listing.items()
            if any(glob_match(g, path) for g in globs)
        )
        hashes[unit.name] = _digest(
            {"tag": _UNIT_TAG, "files": files, "command": unit.command, "env": [list(kv) for kv in unit.env]}
        )
    return hashes


def tree_hash(tree: str, full_command: str) -> str:
    """The implicit `tree` unit's hash (D-007): the tree and `gate.full`, nothing else."""
    return _digest({"tag": _TREE_TAG, "tree": tree, "command": full_command})


RunCapture = Callable[[str, Path], tuple[int, str, str]]


def _run_capture(command: str, cwd: Path) -> tuple[int, str, str]:
    argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", command] if sys.platform == "win32" else ["sh", "-c", command]
    done = subprocess.run(
        argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )  # fmt: skip
    return done.returncode, done.stdout, done.stderr


def load_manifest(command: str, worktree: Path, *, run: RunCapture = _run_capture) -> UnitManifest:
    try:
        code, out, err = run(command, worktree)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ManifestError("units-command-failed", f"`{command}` could not run in {worktree}: {exc}") from None
    if code != 0:
        tail = "\n".join(err.strip().splitlines()[-10:])
        raise ManifestError("units-command-failed", f"`{command}` exited {code} in {worktree}: exit {code}\n{tail}")
    return parse_manifest(out)


@dataclass(frozen=True, slots=True)
class UnitState:
    manifest: UnitManifest
    hashes: Mapping[str, str]


def resolve_unit_state(
    gate: RepoGate,
    worktree: Path,
    tree: str,
    *,
    git: provenance.GitExecutable | None,
    run: RunCapture = _run_capture,
    listing: Mapping[str, gate_git.GitLeaf] | None = None,
) -> UnitState:
    """The manifest and unit hashes at *tree*, or the implicit tree unit when no `gate.units`."""
    if gate.units is None:
        if not isinstance(gate.full, str) or not gate.full.strip():
            raise _invalid("gate.full: a non-empty command is required for the implicit tree unit")
        return UnitState(implicit_manifest(gate.full), {TREE_UNIT: tree_hash(tree, gate.full)})
    manifest = load_manifest(gate.units, worktree, run=run)
    files = listing if listing is not None else gate_git.ls_tree(worktree, tree, git=git)
    for unit in manifest.units:
        if not any(glob_match(g, path) for g in unit.inputs for path in files):
            raise _invalid(f"units.{unit.name}.inputs: no file in tree {tree} matches")
    return UnitState(manifest, unit_hashes(manifest, files, gate.extra_inputs))
