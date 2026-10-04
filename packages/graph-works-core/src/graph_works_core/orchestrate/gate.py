"""`gw work gate run | wait | check`: gw runs the repository gate and records it."""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from okf_ext.locking import locked
from work_tracker_okf.affects import code_affects
from work_tracker_okf.items import WorkItem, load_items

from graph_works_core.orchestrate import gate_git, gate_units
from graph_works_core.orchestrate.gate_receipts import (
    GateEvaluation,
    GateEvidence,
    GateRecord,
    GateRun,
    GateScope,
    RepoWideEntry,
    Reuse,
    UnitEntry,
    UnitEvidence,
    any_for_tree,
    evaluate,
    record_gate_run,
)
from graph_works_core.orchestrate.gate_wake import resume_line
from graph_works_core.orchestrate.wait import WaitClock
from graph_works_core.workspace import provenance
from graph_works_core.workspace.bundle import load_work_bundle
from graph_works_core.workspace.commits import item_stem
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.gate_config import ScopedGate, repo_gate, unit_jobs
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import resolve_checked_key
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
    "units-invalid",
    "units-command-failed",
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
    return ScopedCommand(" && ".join(scoped.command.replace("{name}", shlex.quote(n)) for n in ordered), ordered, ())


@dataclass(frozen=True, slots=True)
class PlannedUnit:
    name: str
    hash: str
    planned: bool
    reused_from: UnitEvidence | None


@dataclass(frozen=True, slots=True)
class GatePlan:
    state: gate_units.UnitState
    evaluation: GateEvaluation
    repo_wide: bool
    units: tuple[PlannedUnit, ...]
    jobs: int

    @property
    def planned_names(self) -> tuple[str, ...]:
        return tuple(u.name for u in self.units if u.planned)


def plan_gate(
    state: gate_units.UnitState,
    evaluation: GateEvaluation,
    *,
    scope: GateScope,
    fresh: bool,
    affects_files: Sequence[str] | None,
    unit_jobs: int | None,
) -> GatePlan | tuple[GateRefusal, str]:
    """Which units (and whether repo-wide) one run executes; the rest are reused evidence."""
    manifest = state.manifest
    stale = set(state.hashes) if fresh else set(evaluation.stale)
    if scope == "scoped":
        files = list(affects_files or ())
        uncovered = [f for f in files if not gate_units.units_matching(manifest, [f])]
        if uncovered or not files:
            what = f"no gate unit's inputs match: {', '.join(uncovered)}" if uncovered else "no code `affects` to scope"
            return "scope-uncovered", f"{what}; run the full gate"
        stale &= gate_units.reverse_closure(manifest, gate_units.units_matching(manifest, files))
    units = tuple(
        PlannedUnit(
            unit.name, state.hashes[unit.name], unit.name in stale,
            None if unit.name in stale else evaluation.green.get(unit.name),
        )
        for unit in sorted(manifest.units, key=lambda u: u.name)
    )  # fmt: skip
    repo_wide = manifest.repo_wide is not None and (fresh or not evaluation.repo_wide_green)
    if scope == "scoped" and not any(u.planned for u in units):
        repo_wide = False
    jobs = manifest.jobs if unit_jobs is None else min(manifest.jobs, unit_jobs)
    return GatePlan(state, evaluation, repo_wide, units, jobs)


@dataclass(frozen=True, slots=True)
class GateTarget:
    """What one request resolved to."""

    path: str
    repo: str
    repo_path: Path
    worktree: Path
    head: str
    tree: str


TERMINAL_ENV = "ORCA_TERMINAL_HANDLE"
NotifyReason = Literal["no-terminal", "satisfied", "runner-failed"]


@dataclass(frozen=True, slots=True)
class NotifyResult:
    """What `--notify` did: registered on `terminal`, or why not (D-007, D-008)."""

    registered: bool
    terminal: str | None
    reason: NotifyReason | None


@dataclass(frozen=True, slots=True)
class GateRunResult:
    status: Literal["satisfied", "running", "started", "queued", "current"] | None
    refusal: GateRefusal | None
    detail: str
    run_id: str | None
    match: GateEvidence | None
    command: str | None
    names: tuple[str, ...]
    log_path: str | None
    warnings: tuple[str, ...]
    units: tuple[PlannedUnit, ...] = ()
    stale: tuple[str, ...] = ()
    position: int | None = None
    notify: NotifyResult | None = None


@dataclass(frozen=True, slots=True)
class GateCheckResult:
    status: Literal["satisfied", "unsatisfied"] | None
    refusal: GateRefusal | None
    detail: str
    match: GateEvidence | None
    tree: str | None
    warnings: tuple[str, ...]
    stale: tuple[str, ...] = ()


Spawn = Callable[[Path], None]

WINDOWS_DETACHED_FLAGS = 0x00000200 | 0x00000008  # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
RUNNER_START_TIMEOUT = 10.0
REPLACE_ATTEMPTS = 5
REPLACE_PAUSE = 0.05
RUN_ID_PATTERN = re.compile(r"[0-9]{8}T\d{6}Z-[0-9a-f]{8}")
START_GRACE = timedelta(seconds=30)
MAX_CONCURRENT_KEY = "workflow.gate.max_concurrent"


def gate_max_concurrent(layout: WorkspaceLayout) -> int | None:
    """The per-machine gate concurrency cap; None is unlimited (D-002)."""
    value = resolve_checked_key(layout, MAX_CONCURRENT_KEY, environ={}).value
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkspaceError(f"{layout.manifest_path}: {MAX_CONCURRENT_KEY}: expects a positive integer, got {value!r}")
    return value


def limit_or_none(layout: WorkspaceLayout) -> int | None:
    """`gate_max_concurrent`, or None when it cannot be read: a runner never strands a request."""
    try:
        return gate_max_concurrent(layout)
    except (WorkspaceError, OSError):
        return None


RECORD_VERSION = 2
PRUNE_AFTER = timedelta(days=14)
SLOT_POLL = 1.0


def gate_dir(layout: WorkspaceLayout) -> Path:
    return layout.cache_dir / "gate"


def runs_dir(layout: WorkspaceLayout) -> Path:
    """Pending run records, flat and workspace-wide: `runs/<run_id>.json`."""
    return gate_dir(layout) / "runs"


def slots_dir(layout: WorkspaceLayout) -> Path:
    return gate_dir(layout) / "slots"


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


def _parse_started(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _read_live(record_path: Path, now: datetime) -> tuple[dict[str, Any] | None, bool]:
    """The record and whether its runner is alive, probed in the one safe order.

    Liveness is probed BEFORE the record is read. The runner writes `result` before it
    releases its lock, so a free lock means the record read afterwards is final; the
    reverse order can see "no result" and then "no lock" for a run that just finished.
    Alive means the runner holds its lock, or it was spawned moments ago and has not
    taken it yet (the grace; a spawn-failed record carries a `result`, so never qualifies).
    """
    held = _alive(record_path)
    data = _read_record(record_path)
    if held:
        return data, True
    if data is None or data.get("runner_started") or data.get("result") is not None:
        return data, False
    started = _parse_started(data.get("started"))
    return data, started is not None and timedelta(0) <= now - started <= START_GRACE


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
    items = load_items(load_work_bundle(layout))
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


def _execution_request(record: Mapping[str, object]) -> dict[str, object]:
    """The execution identity of a pending plan; equivalent reuse sources do not affect it."""
    request = {
        key: record.get(key)
        for key in (
            "repo",
            "tree",
            "scope",
            "command",
            "manifest_hash",
            "names",
            "repo_wide_planned",
            "implicit",
            "jobs",
            "setup",
        )
    }
    units = record.get("units")
    request["units"] = (
        [
            {key: unit.get(key) for key in ("name", "hash", "command", "env")}
            for unit in units
            if isinstance(unit, dict) and unit.get("planned") is True
        ]
        if isinstance(units, list)
        else units
    )
    wide = record.get("repo_wide")
    request["repo_wide"] = {key: wide.get(key) for key in ("command", "planned")} if isinstance(wide, dict) else wide
    return request


@dataclass(frozen=True, slots=True)
class RunKey:
    """What makes two gate requests the same run, anywhere in the workspace.

    `repo`, `tree`, `scope` and `command` name the run; `request` is the whole execution
    identity `_execution_request` derives (manifest hash, planned units, repo-wide, jobs,
    setup), so `matches` compares exactly the fields the per-package join compares.
    """

    repo: str
    tree: str
    scope: GateScope
    command: str
    request: Mapping[str, object] = field(hash=False)

    @classmethod
    def of(cls, record: Mapping[str, Any]) -> RunKey:
        return cls(
            str(record["repo"]), str(record["tree"]), record["scope"], str(record["command"]),
            _execution_request(record),
        )  # fmt: skip

    def matches(self, data: Mapping[str, Any]) -> bool:
        return _execution_request(data) == dict(self.request)


def requesters(data: Mapping[str, Any]) -> list[str] | None:
    """The record's requester list, or None when missing, empty, not a list or holding a non-string."""
    value = data.get("requesters")
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        return None
    return value


def _stamp(now: datetime) -> str:
    return f"{now.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}"


def new_waiter(path: str, terminal: str, now: datetime) -> dict[str, Any]:
    return {"path": path, "terminal": terminal, "registered_at": _stamp(now), "woken": None}


def waiters(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The record's well-formed waiter entries, as the live dicts; anything malformed is skipped."""
    value = data.get("waiters")
    if not isinstance(value, list):
        return []
    return [
        entry
        for entry in value
        if isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and isinstance(entry.get("terminal"), str)
        and entry["terminal"]
    ]


def add_waiter(data: dict[str, Any], entry: dict[str, Any]) -> bool:
    """Append *entry* unless its `(path, terminal)` already waits. Caller holds `.dir.lock`."""
    if any(w["path"] == entry["path"] and w["terminal"] == entry["terminal"] for w in waiters(data)):
        return False
    listed = data.get("waiters")
    if not isinstance(listed, list):
        listed = []
        data["waiters"] = listed
    listed.append(entry)
    return True


def _notice(notify: bool, terminal: str, reason: NotifyReason | None = None) -> NotifyResult | None:
    if not notify:
        return None
    if not terminal:
        return NotifyResult(False, None, "no-terminal")
    if reason is not None:
        return NotifyResult(False, terminal, reason)
    return NotifyResult(True, terminal, None)


def is_current(data: Mapping[str, Any] | None) -> bool:
    """A version-2 record with a well-formed requester list; every reader skips anything else."""
    return (
        data is not None
        and data.get("schema") == "gw-gate-run"
        and data.get("version") == RECORD_VERSION
        and requesters(data) is not None
    )


def _listed(data: Mapping[str, Any], name: str) -> list[str]:
    value = data.get(name)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _settled(data: Mapping[str, Any]) -> set[str]:
    return {*_listed(data, "recorded_for"), *_listed(data, "skipped_for")}


def update_record(
    record_path: Path, mutate: Callable[[dict[str, Any]], None], *, sleep: Callable[[float], None] = time.sleep
) -> dict[str, Any]:
    """Read-modify-write one record under `.dir.lock`, so joins and the runner never clobber each other."""
    with locked(record_path.parent / ".dir.lock"):
        data = _read_record(record_path)
        if data is None:
            raise ValueError(f"{record_path}: pending record is not a mapping")
        mutate(data)
        write_record(record_path, data, sleep=sleep)
        return data


def pending_waiters(record_path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The record and its unsettled waiters, read under `.dir.lock`.

    The run index writes the result under the same lock and never joins a record that
    has one, so once a result exists this snapshot is complete.
    """
    with locked(record_path.parent / ".dir.lock"):
        data = _read_record(record_path)
    if data is None:
        return None, []
    return data, [entry for entry in waiters(data) if entry.get("woken") is None]


def settle_waiter(
    record_path: Path,
    path: str,
    terminal: str,
    woken: dict[str, str],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Record *woken* on the `(path, terminal)` waiter unless it is already settled (first writer wins)."""

    def mutate(data: dict[str, Any]) -> None:
        for entry in waiters(data):
            if entry["path"] == path and entry["terminal"] == terminal and entry.get("woken") is None:
                entry["woken"] = woken

    update_record(record_path, mutate, sleep=sleep)


def _order(data: Mapping[str, Any]) -> tuple[str, str]:
    return str(data.get("started", "")), str(data.get("run_id", ""))


GateWaitState = Literal["running", "woken", "wake_failed", "orphaned"]
GATE_WAIT_STATES: tuple[str, ...] = ("running", "woken", "wake_failed", "orphaned")
_SETTLED_WAKES = frozenset({"delivered", "collected"})


def _wake_status(woken: object) -> str | None:
    """The wake's `status` when it is a string; a malformed record reads as unsettled, never raises."""
    status = woken.get("status") if isinstance(woken, dict) else None
    return status if isinstance(status, str) else None


def _owed(entry: Mapping[str, Any]) -> bool:
    """True unless the waiter's wake was delivered or collected (a `failed` or malformed wake is still owed)."""
    return _wake_status(entry.get("woken")) not in _SETTLED_WAKES


def _wait_state(alive: bool, result: object, entry: Mapping[str, Any]) -> GateWaitState:
    woken = entry.get("woken")
    status = _wake_status(woken)
    if status in _SETTLED_WAKES:
        return "woken"
    if not isinstance(result, dict):
        return "running" if alive else "orphaned"
    if status == "failed" or not alive:
        return "wake_failed"
    return "running"


def gate_wait_facts(layout: WorkspaceLayout, now: datetime) -> dict[str, dict[str, Any]]:
    """The newest gate wait per Orca terminal, derived from runner locks and records.

    Never set by the worker, so a crash or a lost wake cannot leave a worker reading
    "waiting" forever. A record without well-formed `waiters` contributes nothing.
    """
    directory = runs_dir(layout)
    if not directory.is_dir():
        return {}
    newest: dict[str, tuple[tuple[str, str], dict[str, Any]]] = {}
    for record_path in directory.glob("*.json"):
        data, alive = _read_live(record_path, now)
        if data is None or not is_current(data):
            continue
        result = data.get("result")
        exit_code = result.get("exit") if isinstance(result, dict) else None
        run_id = str(data.get("run_id"))
        order = _order(data)
        for entry in waiters(data):
            state = _wait_state(alive, result, entry)
            line = resume_line(run_id, entry["path"], exit_code) if state in ("wake_failed", "orphaned") else None
            fact = {
                "run_id": run_id,
                "path": entry["path"],
                "terminal": entry["terminal"],
                "state": state,
                "resume_line": line,
            }
            held = newest.get(entry["terminal"])
            if held is None or order > held[0]:
                newest[entry["terminal"]] = (order, fact)
    return {terminal: fact for terminal, (_key, fact) in newest.items()}


def collect_waiters(record_path: Path, path: str, now: datetime) -> None:
    """*path*'s own `gate wait` collected the outcome: no wake is owed to it any more (D-009).

    Overwrites an unsettled, `failed` or malformed wake; `delivered` and `collected` stay.
    """
    data = _read_record(record_path)
    if data is None or not any(e["path"] == path and _owed(e) for e in waiters(data)):
        return
    collected = {"status": "collected", "at": _stamp(now), "detail": "gate wait"}

    def mutate(record: dict[str, Any]) -> None:
        for entry in waiters(record):
            if entry["path"] == path and _owed(entry):
                entry["woken"] = dict(collected)

    try:
        update_record(record_path, mutate)
    except (OSError, ValueError):
        return


def live_pending(directory: Path, now: datetime) -> list[tuple[Path, dict[str, Any]]]:
    """Current, result-less records whose runner is alive, oldest first by `(started, run_id)`."""
    found: list[tuple[Path, dict[str, Any]]] = []
    for record_path in directory.glob("*.json"):
        data, live = _read_live(record_path, now)
        if data is not None and is_current(data) and live and data.get("result") is None:
            found.append((record_path, data))
    return sorted(found, key=lambda pair: _order(pair[1]))


def queue_head(directory: Path, now: datetime) -> Path | None:
    """The oldest live queued record; only it may try for a slot (FIFO). Dead records never block."""
    for record_path, data in live_pending(directory, now):
        if data.get("status") == "queued":
            return record_path
    return None


def queue_state(
    directory: Path, record_path: Path, data: Mapping[str, Any], limit: int | None, now: datetime
) -> int | None:
    """The queue position (live queued records ahead) when this record cannot start yet, else None.

    Reporting only: the runner's slot wait, not this count, decides when a run starts.
    """
    if limit is None or data.get("status") != "queued" or data.get("result") is not None:
        return None
    running = ahead = 0
    for other_path, other in live_pending(directory, now):
        if other_path == record_path:
            continue
        if other.get("status") == "running":
            running += 1
        elif other.get("status") == "queued" and _order(other) < _order(data):
            ahead += 1
    return ahead if running + ahead >= limit else None


def _existing_run(directory: Path, key: RunKey, now: datetime) -> tuple[Path, dict[str, Any]] | None:
    """The live, result-less record for *key*. Caller holds `.dir.lock`."""
    for record_path, data in live_pending(directory, now):
        if key.matches(data):
            return record_path, data
    return None


def prune(directory: Path, now: datetime) -> None:
    """Delete recorded, dead records started more than PRUNE_AFTER ago. Caller holds `.dir.lock`.

    The receipts already hold the facts. The record goes first, then its lock file.
    """
    for record_path in directory.glob("*.json"):
        data = _read_record(record_path)
        if data is None or not is_current(data) or data.get("recorded") is not True:
            continue
        started = _parse_started(data.get("started"))
        if started is None or now - started <= PRUNE_AFTER or _alive(record_path):
            continue
        with suppress(OSError):
            record_path.unlink(missing_ok=True)
            record_path.with_suffix(".lock").unlink(missing_ok=True)


def _affects_files(listing: Mapping[str, gate_git.GitLeaf], affects: Sequence[str]) -> list[str]:
    out: list[str] = []
    for entry in affects:
        prefix = entry.rstrip("/") + "/"
        hits = [path for path in listing if path == entry or path.startswith(prefix)]
        out.extend(hits or [entry])  # an entry naming nothing in the tree stays, and is reported uncovered
    return out


def _display_command(plan: GatePlan) -> str:
    parts = [f"units: {', '.join(plan.planned_names)}"] if plan.planned_names else []
    if plan.repo_wide:
        parts.append("repo-wide")
    return " + ".join(parts)


def _reuse_data(evidence: UnitEvidence | Reuse | None) -> dict[str, str] | None:
    return None if evidence is None else {"owner": evidence.owner, "run_id": evidence.run_id}


def run_gate_run(
    layout: WorkspaceLayout,
    path: str,
    *,
    scope: GateScope = "full",
    worktree: Path | None = None,
    now: datetime,
    token: str,
    spawn: Spawn,
    fresh: bool = False,
    notify: bool = False,
    environ: Mapping[str, str] | None = None,
) -> GateRunResult:
    """Start (or join, or short-circuit) one gate run for the item's worktree tree."""
    env = os.environ if environ is None else environ
    terminal = env.get(TERMINAL_ENV, "").strip() if notify else ""
    limit = gate_max_concurrent(layout)  # a bad value refuses here, before anything is written
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
    # a finished-but-unrecorded result is recorded, never re-run (design 4.6)
    recover_unrecorded(layout, path, now=now)
    executable = provenance.gate_git(layout)
    if isinstance(executable, provenance.GitFailure):
        return _run_refusal("git-unavailable", _git_detail(executable))
    try:
        listing = gate_git.ls_tree(target.worktree, target.tree, git=executable) if gate.units is not None else {}
        state = gate_units.resolve_unit_state(gate, target.worktree, target.tree, git=executable, listing=listing)
        if gate.units is not None:
            after = gate_git.snapshot(target.worktree, git=executable)
            if after.dirty:
                return _run_refusal(
                    "dirty-tree", "manifest command left uncommitted changes: " + ", ".join(after.dirty)
                )
            if after.tree != target.tree:
                return _run_refusal(
                    "git-unavailable",
                    f"manifest command changed gated tree: expected {target.tree}, found {after.tree}",
                )
        jobs_cap = unit_jobs(layout)
    except gate_units.ManifestError as exc:
        return _run_refusal(exc.reason, str(exc))
    except gate_git.GitUnavailable as exc:
        return _run_refusal("git-unavailable", str(exc))
    except WorkspaceError as exc:
        return _run_refusal("no-gate-configured", str(exc))
    evaluation = evaluate(
        layout,
        repo=target.repo,
        tree=target.tree,
        full_command=gate.full,
        repo_wide_command=state.manifest.repo_wide,
        hashes=state.hashes,
    )
    warnings = evaluation.warnings
    if evaluation.satisfied and not fresh:
        return GateRunResult(
            "satisfied",
            None,
            "",
            None,
            evaluation.evidence,
            gate.full,
            (),
            None,
            warnings,
            notify=_notice(notify, terminal, "satisfied"),
        )
    if state.manifest.implicit:
        command = gate.full
        names: tuple[str, ...] = ()
        if scope == "scoped":
            if gate.scoped is None:
                return _run_refusal(
                    "no-scoped-gate",
                    f"set repositories.{target.repo}.gate.scoped in {layout.manifest_path}",
                    warnings,
                )
            expanded = expand_scoped(gate.scoped, code_affects(item.affects))
            if expanded.command is None:
                if expanded.uncovered:
                    detail = (
                        f"no root in {gate.scoped.roots} matches: {', '.join(expanded.uncovered)}; run the full gate"
                    )
                else:
                    detail = "no code `affects` to scope; run the full gate"
                return _run_refusal("scope-uncovered", detail, warnings)
            command, names = expanded.command, expanded.names
            # Scoped implicit evidence hashes the command actually executed (D-007).
            # The runner preserves scope=scoped as a partial v1 receipt, even for equal commands.
            state = gate_units.UnitState(
                state.manifest,
                {gate_units.TREE_UNIT: gate_units.tree_hash(target.tree, command)},
            )
        planned = plan_gate(state, evaluation, scope="full", fresh=True, affects_files=None, unit_jobs=None)
    else:
        affects_files = _affects_files(listing, code_affects(item.affects)) if scope == "scoped" else None
        planned = plan_gate(
            state, evaluation, scope=scope, fresh=fresh, affects_files=affects_files, unit_jobs=jobs_cap
        )
    if isinstance(planned, tuple):
        return _run_refusal(planned[0], planned[1], warnings)
    plan = planned
    if not state.manifest.implicit:
        if not plan.planned_names and not plan.repo_wide:
            return GateRunResult(
                "current", None, "", None, None, None, (), None, warnings, units=plan.units, stale=evaluation.stale
            )
        command = _display_command(plan)
        names = plan.planned_names
    run_id = new_run_id(now, token)
    try:
        log_dir = gate_git.gate_log_dir(target.worktree, item_stem(path), git=executable)
    except gate_git.GitUnavailable as exc:
        return _run_refusal("git-unavailable", str(exc), warnings)
    log_path = str(log_dir / f"{run_id}.log")
    directory = runs_dir(layout)
    directory.mkdir(parents=True, exist_ok=True)
    by_name = {u.name: u for u in state.manifest.units}
    record: dict[str, Any] = {
        "schema": "gw-gate-run",
        "version": RECORD_VERSION,
        "workspace": str(layout.root),
        "requesters": [path],
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
        "status": "queued",
        "fresh": fresh,
        "full_command": gate.full,
        "result": None,
        "recorded_for": [],
        "skipped_for": [],
        "recorded": False,
        "waiters": [new_waiter(path, terminal, now)] if terminal else [],
        "manifest_hash": state.manifest.digest,
        "implicit": state.manifest.implicit,
        "jobs": plan.jobs,
        "setup": state.manifest.setup,
        "repo_wide_planned": plan.repo_wide,
        "repo_wide": None
        if state.manifest.repo_wide is None
        else {
            "command": state.manifest.repo_wide,
            "planned": plan.repo_wide,
            "reused_from": None if plan.repo_wide else _reuse_data(evaluation.repo_wide),
        },
        "units": [
            {
                "name": u.name,
                "hash": u.hash,
                "command": command if state.manifest.implicit else by_name[u.name].command,
                "env": dict(by_name[u.name].env),
                "planned": u.planned,
                "reused_from": _reuse_data(u.reused_from),
            }
            for u in plan.units
        ],
    }
    # Workspace-only extra inputs or job caps can change the plan without a new tree/manifest.
    key = RunKey.of(record)
    record_path = directory / f"{run_id}.json"
    with locked(directory / ".dir.lock"):
        prune(directory, now)
        joined = _existing_run(directory, key, now)
        if joined is not None:
            joined_path, data = joined
            listed = requesters(data) or []
            promote = fresh and not data.get("fresh")
            changed = path not in listed or promote
            if path not in listed:
                listed.append(path)
            if promote:
                # a fresh requester must not be satisfied away by the runner's queued re-check
                data["fresh"] = True
            if terminal and add_waiter(data, new_waiter(path, terminal, now)):
                changed = True
            if changed:
                write_record(joined_path, data)
            joined_log = data.get("log_path")
            joined_names = data.get("names")
            position = queue_state(directory, joined_path, data, limit, now)
            return GateRunResult(
                "queued" if position is not None else "running",
                None,
                "",
                str(data["run_id"]),
                None,
                str(data["command"]),
                tuple(n for n in joined_names if isinstance(n, str)) if isinstance(joined_names, list) else names,
                joined_log if isinstance(joined_log, str) else None,
                warnings,
                units=plan.units,
                stale=evaluation.stale,
                position=position,
                notify=_notice(notify, terminal),
            )
        write_record(record_path, record, exclusive=True)
    try:
        spawn(record_path)
    except OSError as exc:
        held = _alive(record_path)
        current = _read_record(record_path) or record
        if held or current.get("result") is not None:
            # slow to start, not failed: the runner owns the record now
            position = queue_state(directory, record_path, current, limit, now)
            return GateRunResult(
                "queued" if position is not None else "started",
                None,
                "",
                run_id,
                None,
                command,
                names,
                log_path,
                warnings,
                units=plan.units,
                stale=evaluation.stale,
                position=position,
                notify=_notice(notify, terminal),
            )
        failure = {"exit": None, "error": str(exc)}
        update_record(record_path, lambda data: data.update(result=failure))
        return GateRunResult(
            None,
            "runner-failed",
            str(exc),
            run_id,
            None,
            command,
            names,
            log_path,
            warnings,
            units=plan.units,
            stale=evaluation.stale,
            notify=_notice(notify, terminal, "runner-failed"),
        )
    position = queue_state(directory, record_path, _read_record(record_path) or record, limit, now)
    return GateRunResult(
        "queued" if position is not None else "started", None, "", run_id, None, command, names, log_path, warnings,
        units=plan.units, stale=evaluation.stale, position=position,
        notify=_notice(notify, terminal),
    )  # fmt: skip


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
    executable = provenance.gate_git(layout)
    if isinstance(executable, provenance.GitFailure):
        return _check_refusal("git-unavailable", _git_detail(executable))
    try:
        state = gate_units.resolve_unit_state(gate, target.worktree, target.tree, git=executable)
        if gate.units is not None:
            after = gate_git.snapshot(target.worktree, git=executable)
            if after.dirty:
                return _check_refusal(
                    "dirty-tree",
                    "manifest command left uncommitted changes: " + ", ".join(after.dirty),
                    tree=after.tree,
                )
            if after.tree != target.tree:
                return _check_refusal(
                    "git-unavailable",
                    f"manifest command changed gated tree: expected {target.tree}, found {after.tree}",
                )
    except gate_units.ManifestError as exc:
        return _check_refusal(exc.reason, str(exc))
    except gate_git.GitUnavailable as exc:
        return _check_refusal("git-unavailable", str(exc))
    evaluation = evaluate(
        layout,
        repo=target.repo,
        tree=target.tree,
        full_command=gate.full,
        repo_wide_command=state.manifest.repo_wide,
        hashes=state.hashes,
    )
    if evaluation.satisfied:
        return GateCheckResult("satisfied", None, "", evaluation.evidence, target.tree, evaluation.warnings)
    seen = any_for_tree(layout, repo=target.repo, tree=target.tree)
    detail = "only red/scoped/stale receipts" if seen else "no receipt"
    return GateCheckResult("unsatisfied", None, detail, None, target.tree, evaluation.warnings, stale=evaluation.stale)


def _result_exit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("result exit is not an integer")
    return value


def _result_duration(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("result duration_s is not a number")
    try:
        duration = float(value)
    except OverflowError as exc:
        raise ValueError("result duration_s is not finite") from exc
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("result duration_s is not finite and nonnegative")
    return duration


def _result_operation(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("result operation is not a mapping")
    _result_exit(value.get("exit"))
    _result_duration(value.get("duration_s"))
    if value.get("log_path") is not None and not isinstance(value["log_path"], str):
        raise ValueError("result log_path is not a string")
    return value


def gate_run_from_record(record: dict[str, Any], result: dict[str, Any]) -> GateRun:
    """The receipt entry a finished pending record stands for; malformed evidence refuses."""
    if type(result.get("tree_changed")) is not bool:
        raise ValueError("result tree_changed is not a boolean")
    if not isinstance(result.get("log_tail"), str):
        raise ValueError("result log_tail is not a string")
    base = GateRun(
        run_id=record["run_id"],
        repo=record["repo"],
        worktree=record["worktree"],
        head=record["head"],
        tree=record["tree"],
        clean=True,
        tree_changed=result["tree_changed"],
        scope=record["scope"],
        command=record["command"],
        names=tuple(record.get("names", ())),
        exit=_result_exit(result.get("exit")),
        log_path=record["log_path"],
        log_tail=result["log_tail"],
        started=record["started"],
        duration_s=_result_duration(result.get("duration_s")),
    )
    unit_results = result.get("units", {})
    if not isinstance(unit_results, dict):
        raise ValueError("result units is not a mapping")
    for name, got in unit_results.items():
        if not isinstance(name, str):
            raise ValueError("result unit name is not a string")
        _result_operation(got)
    for key in ("setup", "repo_wide"):
        if result.get(key) is not None:
            _result_operation(result[key])
    if record.get("version") != 2 or (record.get("implicit") and record["scope"] == "scoped"):
        # D-007: implicit scoped runs never mint full-tree unit evidence, even for equal commands.
        return base
    setup_failed = result.get("setup") is not None and result["setup"]["exit"] != 0
    entries: list[UnitEntry] = []
    incomplete_or_red = False
    for unit in record["units"]:
        if unit["planned"]:
            got = unit_results.get(unit["name"], {"exit": 1, "duration_s": 0.0})
            if record.get("implicit"):
                got = {"exit": base.exit, "duration_s": base.duration_s, "log_path": base.log_path}
            code = _result_exit(got["exit"])
            if setup_failed and code == 0:
                raise ValueError(f"setup failed but unit {unit['name']} result is green")
            incomplete_or_red |= code != 0
            entries.append(
                UnitEntry(
                    unit["name"], unit["hash"], True, code, got.get("log_path"), _result_duration(got["duration_s"])
                )
            )
        elif unit.get("reused_from") is not None:
            reuse = unit["reused_from"]
            entries.append(
                UnitEntry(unit["name"], unit["hash"], False, reused_from=Reuse(reuse["owner"], reuse["run_id"]))
            )
    wide_entry: RepoWideEntry | None = None
    wide = record.get("repo_wide")
    if wide is not None:
        if wide["planned"]:
            got = result.get("repo_wide") or {"exit": 1, "duration_s": 0.0}
            code = _result_exit(got["exit"])
            if setup_failed and code == 0:
                raise ValueError("setup failed but repo_wide result is green")
            incomplete_or_red |= code != 0
            wide_entry = RepoWideEntry(record["tree"], wide["command"], True, code, _result_duration(got["duration_s"]))
        elif wide.get("reused_from") is not None:
            reuse = wide["reused_from"]
            wide_entry = RepoWideEntry(
                record["tree"], wide["command"], False, reused_from=Reuse(reuse["owner"], reuse["run_id"])
            )
    return replace(
        base,
        exit=base.exit or int(incomplete_or_red),
        manifest_hash=record["manifest_hash"],
        repo_wide=wide_entry,
        units=tuple(entries),
    )


def spawn_runner(record_path: Path) -> None:
    """Start the detached runner, then wait for it to confirm it holds its lock."""
    record = json.loads(record_path.read_text(encoding="utf-8"))
    log_path = Path(record["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-m", "graph_works_core.orchestrate.gate_runner", str(record_path)]
    with log_path.open("ab") as log:
        options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": subprocess.STDOUT,
            "cwd": record["worktree"],
            "close_fds": True,
        }
        if sys.platform == "win32":
            options["creationflags"] = WINDOWS_DETACHED_FLAGS
        else:
            options["start_new_session"] = True
        subprocess.Popen(argv, **options)
    _await_runner_started(record_path, RUNNER_START_TIMEOUT)


def _await_runner_started(record_path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = _read_record(record_path)
        if data is not None and data.get("runner_started"):
            return
        time.sleep(0.05)
    raise OSError(f"gate runner did not start within {timeout:.0f}s ({record_path})")


@dataclass(frozen=True, slots=True)
class GateWaitResult:
    status: Literal["finished", "running", "queued", "orphaned"] | None
    refusal: GateRefusal | None
    detail: str
    run_id: str | None
    exit: int | None
    recorded: bool
    log_path: str | None
    log_tail: str | None
    receipt_path: str | None
    units: tuple[tuple[str, int], ...] = ()
    repo_wide_exit: int | None = None
    position: int | None = None


def _wait_refusal(detail: str) -> GateWaitResult:
    return GateWaitResult(None, "no-run", detail, None, None, False, None, None, None)


def _read_record(record_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _choose_record(directory: Path, run_id: str | None, path: str) -> Path | None:
    """With a run id, that record whoever started it; without, *path*'s newest current record."""
    if run_id is not None:
        if RUN_ID_PATTERN.fullmatch(run_id) is None:
            return None
        chosen = directory / f"{run_id}.json"
        return chosen if chosen.is_file() and is_current(_read_record(chosen)) else None
    ranked: list[tuple[tuple[str, str], Path]] = []
    for candidate in directory.glob("*.json"):
        data = _read_record(candidate)
        if data is not None and is_current(data) and path in (requesters(data) or ()):
            ranked.append((_order(data), candidate))
    return max(ranked)[1] if ranked else None


def write_record(
    record_path: Path,
    data: dict[str, Any],
    *,
    exclusive: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Write a pending record atomically (temp + replace); a Windows reader may briefly block the replace."""
    if exclusive and record_path.exists():
        raise FileExistsError(record_path)
    temp = record_path.with_name(f".{record_path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                temp.replace(record_path)
                return
            except PermissionError:
                if attempt + 1 == REPLACE_ATTEMPTS:
                    raise
                sleep(REPLACE_PAUSE * (attempt + 1))
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _settle(data: dict[str, Any], requester: str, name: str) -> None:
    listed = data.get(name)
    if not isinstance(listed, list):
        listed = data[name] = []
    if requester not in listed:
        listed.append(requester)
    data["recorded"] = set(requesters(data) or ()) <= _settled(data)


def _settler(requester: str, name: str) -> Callable[[dict[str, Any]], None]:
    return lambda data: _settle(data, requester, name)


def record_requesters(
    layout: WorkspaceLayout,
    record_path: Path,
    *,
    today: date,
    delays: Sequence[float] = (0.0,),
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, GateRecord]:
    """Record a finished run on every requester not yet recorded or skipped (D-001).

    Idempotent on `run_id` per receipt. Each requester gets `len(delays)` attempts,
    sleeping `delays[i]` after a failed one; `owner-terminal` is final and lands in
    `skipped_for`. A satisfied-while-queued result records nothing. Requesters are
    frozen once a result exists (joins require `result is None` under `.dir.lock`).
    """
    data = _read_record(record_path)
    if data is None or not is_current(data):
        return {}
    result = data.get("result")
    if not isinstance(result, dict) or type(result.get("exit")) is not int or "satisfied_by" in result:
        return {}
    entry = gate_run_from_record(data, result)
    outcomes: dict[str, GateRecord] = {}
    done = _settled(data)
    for requester in requesters(data) or ():
        if requester in done:
            continue
        outcome = GateRecord("not-attempted", None, False)
        for delay in delays:
            outcome = record_gate_run(layout, requester, entry, today=today)
            if outcome.refusal in (None, "owner-terminal"):
                break
            sleep(delay)
        outcomes[requester] = outcome
        if outcome.refusal in (None, "owner-terminal"):
            name = "recorded_for" if outcome.refusal is None else "skipped_for"
            update_record(record_path, _settler(requester, name), sleep=sleep)
    return outcomes


def recover_unrecorded(layout: WorkspaceLayout, path: str, *, now: datetime, today: date | None = None) -> None:
    """Record every finished, dead-runner record naming *path* whose requesters are not all settled.

    The result is never lost and never re-run. A refused or malformed record is left as it is.
    """
    day = today or now.astimezone(UTC).date()
    for record_path in sorted(runs_dir(layout).glob("*.json")):
        data, alive = _read_live(record_path, now)
        if data is None or not is_current(data) or alive or data.get("recorded"):
            continue
        if path not in (requesters(data) or ()):
            continue
        try:
            record_requesters(layout, record_path, today=day)
        except (KeyError, TypeError, ValueError):
            continue


def _gate_wait(
    layout: WorkspaceLayout,
    path: str,
    *,
    run_id: str | None,
    timeout: float,
    clock: WaitClock,
    sleep: Callable[[float], None],
    today: date,
) -> GateWaitResult:
    """Report a gate run's outcome, waiting up to *timeout* seconds. Never starts a run."""
    if run_id is None:
        recover_unrecorded(layout, path, now=clock.wall(), today=today)
    record_path = _choose_record(runs_dir(layout), run_id, path)
    if record_path is None:
        return _wait_refusal(f"{path}: no gate run to wait for")
    limit = limit_or_none(layout)
    deadline = clock.monotonic() + timeout
    while True:
        data, alive = _read_live(record_path, clock.wall())
        if data is None or not is_current(data):
            return _wait_refusal(f"{record_path}: unreadable pending record")
        rid = str(data.get("run_id"))
        log_path = data.get("log_path")
        log_path = log_path if isinstance(log_path, str) else None
        result = data.get("result")
        if isinstance(result, dict):
            code = result.get("exit")
            if type(code) is not int:
                return GateWaitResult(
                    "orphaned", None, str(result.get("error", "runner failed")), rid, None, False, log_path, None, None
                )
            if "satisfied_by" in result:
                return GateWaitResult(
                    "finished", None, f"satisfied by {result['satisfied_by']}", rid, 0, True, log_path, None, None
                )
            try:
                entry = gate_run_from_record(data, result)
            except (KeyError, TypeError, ValueError) as exc:
                return GateWaitResult(
                    "orphaned", None, f"malformed runner result: {exc}", rid, None, False, log_path, None, None
                )
            code = entry.exit
            units = (
                ()
                if data.get("implicit")
                else tuple(sorted((u.name, u.exit) for u in entry.units if u.ran and u.exit is not None))
            )
            wide_exit = entry.repo_wide.exit if entry.repo_wide is not None and entry.repo_wide.ran else None
            detail = ""
            if not data.get("recorded") and not alive:
                outcome = record_requesters(layout, record_path, today=today).get(path)
                if outcome is not None and outcome.refusal not in (None, "owner-terminal"):
                    detail = outcome.detail or outcome.refusal or ""
                data = _read_record(record_path) or data
            if data.get("recorded") or not alive:
                # `recorded` is caller-relative: a requester asks about its own receipt (a
                # skipped terminal requester is False); anyone else sees the run's overall state.
                mine = path in _listed(data, "recorded_for")
                recorded = mine if path in (requesters(data) or ()) else bool(data.get("recorded"))
                tail = result.get("log_tail")
                return GateWaitResult(
                    "finished", None, detail, rid, code, recorded, log_path, tail if isinstance(tail, str) else None,
                    f"{path}/references/03-gate-receipts.md" if mine else None, units, wide_exit,
                )  # fmt: skip
        elif not alive:
            return GateWaitResult("orphaned", None, "runner is not alive and left no result", rid, None, False,
                                  log_path, None, None)  # fmt: skip
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            position = queue_state(runs_dir(layout), record_path, data, limit, clock.wall())
            return GateWaitResult(
                "queued" if position is not None else "running", None, "", rid, None, False, log_path, None, None,
                position=position,
            )  # fmt: skip
        sleep(min(2.0, remaining))


def run_gate_wait(
    layout: WorkspaceLayout,
    path: str,
    *,
    run_id: str | None,
    timeout: float,
    clock: WaitClock,
    sleep: Callable[[float], None],
    today: date,
) -> GateWaitResult:
    """Report a gate run's outcome; a `finished` or `orphaned` answer also collects *path*'s waiters."""
    result = _gate_wait(layout, path, run_id=run_id, timeout=timeout, clock=clock, sleep=sleep, today=today)
    if result.status in ("finished", "orphaned") and result.run_id is not None:
        collect_waiters(runs_dir(layout) / f"{result.run_id}.json", path, clock.wall())
    return result
