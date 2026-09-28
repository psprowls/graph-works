"""`gw work gate run | wait | check`: gw runs the repository gate and records it."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Literal

from okf_ext.locking import locked
from okf_io import load_bundle
from work_tracker_okf.affects import code_affects
from work_tracker_okf.items import IGNORE, WorkItem, load_items

from graph_works_core.orchestrate import gate_git
from graph_works_core.orchestrate.gate_receipts import GateMatch, GateScope, any_for_tree, find_satisfying
from graph_works_core.workspace import provenance
from graph_works_core.workspace.commits import item_stem
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.gate_config import ScopedGate, repo_gate
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_item_repo

GateRefusal = Literal[
    "unknown-item",
    "no-worktree",
    "foreign-worktree",
    "git-unavailable",
    "dirty-tree",
    "no-gate-configured",
    "no-scoped-gate",
    "scope-uncovered",
    "runner-failed",
    "no-run",
]


@dataclass(frozen=True, slots=True)
class ScopedCommand:
    command: str | None
    names: tuple[str, ...]
    uncovered: tuple[str, ...]


def expand_scoped(scoped: ScopedGate, code_paths: Sequence[str]) -> ScopedCommand:
    """One `&&`-joined command over the distinct roots the paths fall under.

    A scoped run never covers less than the item touches: any unmatched path,
    or no path at all, refuses.
    """
    pattern = PurePosixPath(scoped.roots).parts
    names: set[str] = set()
    uncovered: list[str] = []
    for path in code_paths:
        parts = PurePosixPath(path).parts
        if len(parts) >= len(pattern) and all(fnmatchcase(p, g) for p, g in zip(parts, pattern, strict=False)):
            names.add(parts[len(pattern) - 1])
        else:
            uncovered.append(path)
    if uncovered or not names:
        return ScopedCommand(None, tuple(sorted(names)), tuple(uncovered))
    ordered = tuple(sorted(names))
    return ScopedCommand(" && ".join(scoped.command.replace("{name}", n) for n in ordered), ordered, ())


@dataclass(frozen=True, slots=True)
class GateTarget:
    """What one request resolved to."""

    path: str
    repo: str
    repo_path: Path
    worktree: Path
    head: str
    tree: str


@dataclass(frozen=True, slots=True)
class GateRunResult:
    status: Literal["satisfied", "running", "started"] | None
    refusal: GateRefusal | None
    detail: str
    run_id: str | None
    match: GateMatch | None
    command: str | None
    names: tuple[str, ...]
    log_path: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GateCheckResult:
    status: Literal["satisfied", "unsatisfied"] | None
    refusal: GateRefusal | None
    detail: str
    match: GateMatch | None
    tree: str | None
    warnings: tuple[str, ...]


Spawn = Callable[[Path], None]


def runs_dir(layout: WorkspaceLayout, path: str) -> Path:
    return layout.cache_dir / "gate-runs" / hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def new_run_id(now: datetime, token: str) -> str:
    if re.fullmatch(r"[0-9a-f]{8}", token) is None:
        raise ValueError("token must be 8 lowercase hex characters")
    return f"{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{token}"


def _alive(record_path: Path) -> bool:
    """Whether the runner still holds its lock; the only liveness probe (no pid signals)."""
    try:
        with locked(record_path.with_suffix(".lock"), blocking=False):
            return False
    except OSError:
        return True


def _check_refusal(reason: GateRefusal, detail: str, *, tree: str | None = None) -> GateCheckResult:
    status: Literal["unsatisfied"] | None = "unsatisfied" if reason == "dirty-tree" else None
    return GateCheckResult(status, reason, detail, None, tree, ())


def _run_refusal(reason: GateRefusal, detail: str, warnings: tuple[str, ...] = ()) -> GateRunResult:
    return GateRunResult(None, reason, detail, None, None, None, (), None, warnings)


def _git_detail(failure: provenance.GitFailure) -> str:
    return f"git unavailable ({failure.cause}): {failure.detail}"


def _resolve(
    layout: WorkspaceLayout, path: str, worktree: Path | None
) -> tuple[GateTarget, WorkItem] | GateCheckResult:
    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    by_path = {item.path: item for item in items}
    item = by_path.get(path)
    if item is None:
        return _check_refusal("unknown-item", f"{path}: no such work item")
    try:
        chosen = resolve_item_repo(layout, item, by_path)
    except WorkspaceError as exc:
        return _check_refusal("no-gate-configured", str(exc))
    if chosen.name is None or chosen.path is None:
        return _check_refusal("no-gate-configured", chosen.note or f"{path}: no code repository resolved")
    raw = worktree if worktree is not None else (Path(item.worktree) if item.worktree else None)
    if raw is None:
        return _check_refusal("no-worktree", f"{path} has no `worktree:` stamp; pass --worktree")
    executable = provenance.gate_git(layout)
    if isinstance(executable, provenance.GitFailure):
        return _check_refusal("git-unavailable", _git_detail(executable))
    try:
        if not raw.is_dir() or not gate_git.same_repository(raw, chosen.path, git=executable):
            return _check_refusal("foreign-worktree", f"{raw} is not a checkout of {chosen.name} ({chosen.path})")
        snap = gate_git.snapshot(raw, git=executable)
    except gate_git.GitUnavailable as exc:
        return _check_refusal("git-unavailable", str(exc))
    if snap.dirty:
        return _check_refusal("dirty-tree", "uncommitted changes: " + ", ".join(snap.dirty), tree=snap.tree)
    return GateTarget(path, chosen.name, chosen.path, raw.resolve(), snap.head, snap.tree), item


def resolve_target(layout: WorkspaceLayout, path: str, *, worktree: Path | None) -> GateTarget | GateCheckResult:
    resolved = _resolve(layout, path, worktree)
    return resolved if isinstance(resolved, GateCheckResult) else resolved[0]


def _existing_run(directory: Path, request: dict[str, object]) -> str | None:
    for record_path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("result") is not None:
            continue
        if all(data.get(key) == value for key, value in request.items()) and _alive(record_path):
            run_id = data.get("run_id")
            if isinstance(run_id, str):
                return run_id
    return None


def run_gate_run(
    layout: WorkspaceLayout,
    path: str,
    *,
    scope: GateScope = "full",
    worktree: Path | None = None,
    now: datetime,
    token: str,
    spawn: Spawn,
) -> GateRunResult:
    """Start (or join, or short-circuit) one gate run for the item's worktree tree."""
    resolved = _resolve(layout, path, worktree)
    if isinstance(resolved, GateCheckResult):
        return _run_refusal(resolved.refusal or "no-run", resolved.detail)
    target, item = resolved
    try:
        gate = repo_gate(layout, target.repo)
    except WorkspaceError as exc:
        return _run_refusal("no-gate-configured", str(exc))
    if gate.full is None:
        return _run_refusal("no-gate-configured", f"set repositories.{target.repo}.gate.full in {layout.manifest_path}")
    lookup = find_satisfying(layout.bundle_dir, repo=target.repo, tree=target.tree, command=gate.full)
    if lookup.match is not None:
        return GateRunResult("satisfied", None, "", None, lookup.match, gate.full, (), None, lookup.warnings)
    command = gate.full
    names: tuple[str, ...] = ()
    if scope == "scoped":
        if gate.scoped is None:
            return _run_refusal(
                "no-scoped-gate",
                f"set repositories.{target.repo}.gate.scoped in {layout.manifest_path}",
                lookup.warnings,
            )
        paths = code_affects(item.affects)
        expanded = expand_scoped(gate.scoped, paths)
        if expanded.command is None:
            if expanded.uncovered:
                detail = f"no root in {gate.scoped.roots} matches: {', '.join(expanded.uncovered)}; run the full gate"
            else:
                detail = "no code `affects` to scope; run the full gate"
            return _run_refusal("scope-uncovered", detail, lookup.warnings)
        command, names = expanded.command, expanded.names
    run_id = new_run_id(now, token)
    executable = provenance.gate_git(layout)
    if isinstance(executable, provenance.GitFailure):
        return _run_refusal("git-unavailable", _git_detail(executable), lookup.warnings)
    try:
        log_dir = gate_git.gate_log_dir(target.worktree, item_stem(path), git=executable)
    except gate_git.GitUnavailable as exc:
        return _run_refusal("git-unavailable", str(exc), lookup.warnings)
    log_path = str(log_dir / f"{run_id}.log")
    directory = runs_dir(layout, path)
    directory.mkdir(parents=True, exist_ok=True)
    request: dict[str, object] = {"repo": target.repo, "tree": target.tree, "scope": scope, "command": command}
    record = {
        "schema": "gw-gate-run",
        "version": 1,
        "workspace": str(layout.root),
        "owner": path,
        "run_id": run_id,
        "repo": target.repo,
        "worktree": str(target.worktree),
        "head": target.head,
        "tree": target.tree,
        "scope": scope,
        "command": command,
        "names": list(names),
        "log_path": log_path,
        "started": f"{now.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}",
        "runner_started": False,
        "result": None,
        "recorded": False,
    }
    record_path = directory / f"{run_id}.json"
    with locked(directory / ".dir.lock"):
        joined = _existing_run(directory, request)
        if joined is not None:
            return GateRunResult("running", None, "", joined, None, command, names, log_path, lookup.warnings)
        with record_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, indent=2) + "\n")
    try:
        spawn(record_path)
    except OSError as exc:
        record["result"] = {"exit": None, "error": str(exc)}
        record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
        return GateRunResult(None, "runner-failed", str(exc), run_id, None, command, names, log_path, lookup.warnings)
    return GateRunResult("started", None, "", run_id, None, command, names, log_path, lookup.warnings)


def run_gate_check(layout: WorkspaceLayout, path: str, *, worktree: Path | None = None) -> GateCheckResult:
    """Whether the worktree's current tree already has a satisfying receipt. Writes nothing."""
    resolved = _resolve(layout, path, worktree)
    if isinstance(resolved, GateCheckResult):
        if resolved.refusal == "dirty-tree":
            return GateCheckResult("unsatisfied", None, "dirty", None, resolved.tree, ())
        return resolved
    target = resolved[0]
    try:
        gate = repo_gate(layout, target.repo)
    except WorkspaceError as exc:
        return _check_refusal("no-gate-configured", str(exc))
    if gate.full is None:
        return _check_refusal(
            "no-gate-configured", f"set repositories.{target.repo}.gate.full in {layout.manifest_path}"
        )
    lookup = find_satisfying(layout.bundle_dir, repo=target.repo, tree=target.tree, command=gate.full)
    if lookup.match is not None:
        return GateCheckResult("satisfied", None, "", lookup.match, target.tree, lookup.warnings)
    seen = any_for_tree(layout.bundle_dir, repo=target.repo, tree=target.tree)
    detail = "only red/scoped/stale receipts" if seen else "no receipt"
    return GateCheckResult("unsatisfied", None, detail, None, target.tree, lookup.warnings)
