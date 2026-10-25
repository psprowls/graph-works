"""The Orca-mutating orchestrate module (spec §4.2/§4.3).

Every Orca call goes through OrcaPort and Git runs through probe_git. Where a
worker runs is computed (`placing.resolve`) and made so by the placement engine
(`placing.realize`) before any Orca call; every worker launches with
`--worktree path:<computed>`. Journal attempted before effects and done after
verified results; only an uncertain create may be resolved by title, while
other pending effects halt.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.provenance import probe_git
from gw_pipeline.execute_return import pending_return, return_dispatch_admission
from gw_pipeline.placement import Placement, PlacementNotifier
from subagents_io.backend import BackendError
from work_tracker_okf.pipeline import code_phases

from gw_orchestrate import delivery
from gw_orchestrate import dispatch_record as dr
from gw_orchestrate.orca_port import OrcaPort
from gw_orchestrate.placing import Refused, realize, resolve

_CODE_PHASES: frozenset[str] = code_phases()

FAILURE_REASONS: frozenset[str] = frozenset(
    {
        "plan-invalid",
        "key-not-in-plan",
        "override-invalid",
        "placement-refused",
        "task-create-failed",
        "launch-failed",
        "receipt-mismatch",
        "outcome-unknown",
        "placement-mismatch",
        "placement-unrecorded",
        "unsent",
        "task-update-failed",
        "recovery-inspection",
        "record-invalid",
        "no-task",
        "reroute-live",
        "reason-missing",
    }
)
PROBE_OUTCOMES = ("submitted-heartbeat", "submitted-transcript", "nudged", "inconclusive", "no-terminal", "skipped")
DELIVERY_READ_LIMIT = 200
DELIVERY_READS = 3
RECORDED = ("recorded", "unchanged")
_TERMINAL_WORKER_STATES = ("succeeded", "failed", "stopped")
#: Journal `placement` keys only the pre-computed-placement dispatcher wrote. An
#: attempt carrying any of them is read tolerantly (`_validate_legacy`), never migrated.
_LEGACY_PLACEMENT_KEYS = frozenset({"action", "repo_id", "parent_worktree_id", "marker", "reused"})
_LEGACY_ACTIONS = ("reuse", "main", "fork-child", "create-top-level", "pin-detached")
_LEGACY_RECORDED = (*RECORDED, "skipped:read-only-descendant", "reader-receipt", "reader-replayed")


@dataclass(frozen=True, slots=True)
class DispatchFailure:
    step: str
    reason: str
    detail: str
    task_id: str | None = None
    dispatch_id: str | None = None
    refusal: str | None = None


@dataclass(frozen=True, slots=True)
class ObservedPlacement:
    path: str
    #: The checked-out branch; `None` for a workspace-root launch, which has no branch to verify.
    branch: str | None
    display_name: str | None
    notes: tuple[str, ...]
    #: The execute baseline the engine recorded; `None` outside execute.
    start_sha: str | None


@dataclass(frozen=True, slots=True)
class DispatchResult:
    status: Literal["dispatched", "resumed", "existing"] | None
    key: str
    run_id: str
    task_id: str | None = None
    task_title: str | None = None
    display_name: str | None = None
    dispatch_id: str | None = None
    terminal: str | None = None
    placement: ObservedPlacement | None = None
    recorded: str | None = None
    probe: str | None = None
    delivery: str | None = None
    record_path: str | None = None
    failure: DispatchFailure | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


def encode_launch_envelope(
    *,
    key: str,
    agent: str,
    model: str | None,
    reasoning_effort: str | None,
    placement_argv: list[str],
    mode: str,
    worktree_path: str | None,
    prompt: str,
) -> str:
    envelope = {
        "version": 2,
        "dispatch_key": key,
        "agent": agent,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "placement_argv": placement_argv,
        "mode": mode,
        "worktree_path": worktree_path,
    }
    return "GW_LAUNCH_V1 " + json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n" + prompt


class _Stop(Exception):
    def __init__(self, reason: str, detail: str, *, refusal: str | None = None) -> None:
        self.reason, self.detail, self.refusal = reason, detail, refusal


@dataclass
class _Dispatch:
    layout: WorkspaceLayout
    port: OrcaPort
    today: date
    clock: Callable[[], datetime]
    result: DispatchResult
    notifier: PlacementNotifier | None = None
    root: str = ""
    entry: dict[str, Any] | None = None
    record: dr.DispatchRecord | None = None
    persisted: dr.DispatchRecord | None = None
    step: str = "validate"

    @property
    def item(self) -> dict[str, Any]:
        assert self.entry is not None
        return self.entry

    @property
    def journal(self) -> dr.DispatchRecord:
        assert self.record is not None
        return self.record

    @property
    def attempt(self) -> dr.Attempt:
        return self.journal.attempts[-1]

    def save(self, record: dr.DispatchRecord) -> None:
        dr.compare_and_swap_record(self.layout, record, expected=self.persisted)
        self.record = self.persisted = record

    def mark(
        self,
        state: Literal["attempted", "done", "skipped"],
        *,
        result: Mapping[str, Any] | None = None,
        reason: str | None = None,
        **fields: Any,  # noqa: ANN401 - forwarded dataclass field updates
    ) -> None:
        self.save(
            self.journal.with_step(
                len(self.journal.attempts) - 1,
                self.step,
                dr.StepState(state, at=self.clock().isoformat(), result=result, reason=reason),
                **fields,
            )
        )


def _validate(c: _Dispatch, plan: object) -> None:
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("path"), str)
        or not isinstance(plan.get("dispatches"), list)
        or not c.result.run_id.strip()
    ):
        raise _Stop("plan-invalid", "expected a plan path, dispatches list and nonblank run id")
    if any(not isinstance(e, dict) for e in plan["dispatches"]):
        raise _Stop("plan-invalid", "dispatch entries must be objects")
    entry = next((e for e in plan["dispatches"] if e.get("key") == c.result.key), None)
    if entry is None:
        raise _Stop("key-not-in-plan", "dispatch key is absent from plan")
    if (
        any(not isinstance(entry.get(n), str) for n in ("path", "phase", "mode", "agent", "prompt"))
        or any(entry.get(n) is not None and not isinstance(entry[n], str) for n in ("model", "reasoning_effort"))
        or (entry.get("repo") is not None and not isinstance(entry["repo"], dict))
    ):
        raise _Stop("plan-invalid", "dispatch entry has invalid launch fields")
    if "placement" not in entry or not _planned_placement(entry["placement"]):
        raise _Stop("plan-invalid", "dispatch entry needs a placement (branch, worktree, parents, repo) or null")
    if pending_return(c.layout, entry["path"]) is not None:
        raise _Stop("recovery-inspection", "execute return is pending; inspect or resume before dispatch")
    c.root, c.entry = plan["path"], entry
    path = dr.record_file(c.layout, entry["path"], c.result.key)
    loaded = dr.load_record(path)
    if loaded is not None and (loaded.key, loaded.work_path, loaded.phase, loaded.run_id) != (
        c.result.key,
        entry["path"],
        entry["phase"],
        c.result.run_id,
    ):
        raise _Stop("record-invalid", "record identity differs from dispatch entry/run")
    c.persisted = loaded
    c.record = loaded or dr.DispatchRecord(c.result.key, entry["path"], entry["phase"], c.result.run_id)
    c.result = replace(
        c.result,
        task_title=c.result.key,
        display_name=f"{entry['path']} · {entry['phase']}",
        record_path="/" + dr.record_ref(entry["path"], c.result.key),
    )
    if c.journal.attempts and c.attempt.task_id not in c.journal.superseded:
        try:
            _validate_persisted(c.attempt, c.result.key)
        except dr.DispatchRecordError:
            # IDs are independently readable even when another persisted field
            # is corrupt. Do not attempt placement/result restoration on error.
            _restore_ids(c)
            raise


def _string(value: object) -> bool:
    return isinstance(value, str)


_PLACEMENT_FIELDS = ("branch", "worktree", "parent_branch", "parent_worktree")


def _planned_placement(value: object) -> bool:
    """A planned code placement, or `None` for a stage with no code repository."""
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and all(isinstance(value.get(n), str) and value[n] for n in _PLACEMENT_FIELDS)
        and isinstance(value.get("repo"), dict)
    )


def _nullable_string(value: object) -> bool:
    return value is None or _string(value)


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _shape(
    value: object,
    label: str,
    required: Mapping[str, Callable[[object], bool]],
    optional: Mapping[str, Callable[[object], bool]] | None = None,
) -> None:
    optional = optional or {}
    if (
        not isinstance(value, Mapping)
        or not required.keys() <= value.keys()
        or value.keys() - (required.keys() | optional.keys())
    ):
        raise dr.DispatchRecordError(f"{label}: missing or unexpected fields")
    for name, check in {**required, **optional}.items():
        if name in value and not check(value[name]):
            raise dr.DispatchRecordError(f"{label}.{name}: invalid value")


def _valid_receipt(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"version", "token"}
        and type(value["version"]) is int
        and value["version"] == delivery.RECEIPT_VERSION
        and isinstance(value["token"], str)
        and re.fullmatch(r"[0-9a-f]{16}", value["token"]) is not None
    )


def _flag(value: object) -> bool:
    return type(value) is bool


def _chain(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(step, Mapping)
        and set(step) == {"path", "action", "refresh"}
        and _string(step["path"])
        and _string(step["action"])
        and _nullable_string(step["refresh"])
        for step in value
    )


def _legacy(a: dr.Attempt) -> bool:
    """Whether the dispatcher before computed placement wrote *a* (its `placement` names an action or Orca ids)."""
    return isinstance(a.placement, Mapping) and bool(_LEGACY_PLACEMENT_KEYS & a.placement.keys())


def _validate_persisted(a: dr.Attempt, key: str) -> None:
    """Check dispatch-owned shapes before any restore, lookup or resumed effect.

    The shared journal reader guarantees JSON mappings, not these producer
    contracts. Attempted and skipped steps may lack results; done data steps may not.
    The strict unknown-key rule applies to the records this version writes; an attempt
    journaled before computed placement is checked against the shape it was written in,
    and only its `path`, `placement_argv`, `start_sha` and `worktree_id` are ever read.
    """
    legacy = _legacy(a)
    _shape(
        a.envelope,
        "envelope",
        {
            "version": lambda v: type(v) is int and v == 2,
            "dispatch_key": lambda v: v == key,
            "agent": _string,
            "model": _nullable_string,
            "reasoning_effort": _nullable_string,
            "placement_argv": _strings,
            "mode": _string,
            "worktree_path": _nullable_string,
        },
    )

    def action(value: object) -> bool:
        return value in _LEGACY_ACTIONS

    if legacy:
        _shape(
            a.placement,
            "placement",
            {
                "action": action,
                "placement_argv": _strings,
                "repo_id": _nullable_string,
                "parent_worktree_id": _nullable_string,
            },
            {
                "worktree_id": _string,
                "start_sha": _string,
                "path": _string,
                "attempt_id": _string,
                "marker": _string,
                "reused": _flag,
            },
        )
        settle: dict[str, Callable[[object], bool]] = {
            "action": action,
            "path": lambda v: isinstance(v, str) and bool(v),
            "branch": lambda v: v is None or (isinstance(v, str) and bool(v)),
            "display_name": _nullable_string,
            "repo_id": _nullable_string,
            "parent_worktree_id": _nullable_string,
            "lineage_set": _flag,
            "notes": _strings,
        }
    else:
        _shape(
            a.placement,
            "placement",
            {
                "path": lambda v: isinstance(v, str) and bool(v),
                "branch": _nullable_string,
                "parent_path": _nullable_string,
                "start_sha": _nullable_string,
                "placement_argv": _strings,
                "chain": _chain,
                "orca": _nullable_string,
            },
            {"worktree_id": _string},
        )
        settle = {
            "path": lambda v: isinstance(v, str) and bool(v),
            "branch": lambda v: v is None or (isinstance(v, str) and bool(v)),
            "display_name": _nullable_string,
            "notes": _strings,
            "start_sha": _nullable_string,
        }
    recorded = _LEGACY_RECORDED if legacy else RECORDED
    schemas: dict[str, dict[str, Callable[[object], bool]]] = {
        "place": {"notes": _strings},
        "create": {"task_id": _string},
        "launch": dict.fromkeys(("dispatch_id", "terminal", "worktree_id"), _nullable_string),
        "settle": settle,
        "record": {"recorded": lambda v: v in recorded},
        "probe": {"probe": lambda v: v in PROBE_OUTCOMES},
    }
    for name, state in a.steps.items():
        if name == "reroute":  # This verb does not consume reroute-owned results.
            continue
        schema = schemas.get(name, {})
        if state.result is None and (state.state in ("attempted", "skipped") or not schema):
            continue
        optional: dict[str, Callable[[object], bool]] = {}
        if name == "create":
            optional = {"adopted": _flag, "retry_of": _string}
        elif name == "attend":
            optional = {"warning": _string}
        elif name == "settle" and legacy:
            optional = {"start_sha": _nullable_string}
        elif name == "encode":
            optional = {"receipt": _valid_receipt}
        elif name == "probe":
            optional = {"delivery": lambda v: v in delivery.DELIVERY_OUTCOMES}
        _shape(state.result, f"{name}.result", schema, optional)
        if state.state == "done":
            if name == "create" and a.task_id is None:
                raise dr.DispatchRecordError("create: completed step requires task_id")
            if name == "launch" and a.dispatch_id is None:
                raise dr.DispatchRecordError("launch: completed step requires dispatch_id")
            if name == "settle" and (a.placement is None or not _string(a.placement.get("worktree_id"))):
                raise dr.DispatchRecordError("settle: completed step requires placement.worktree_id")


def _restore_ids(c: _Dispatch) -> None:
    a = c.attempt
    launched = a.steps.get("launch", dr.StepState("attempted")).result or {}
    created = a.steps.get("create", dr.StepState("attempted")).result or {}

    def known(value: object) -> str | None:
        return value if isinstance(value, str) else None

    c.result = replace(
        c.result,
        task_id=c.result.task_id or a.task_id or known(created.get("task_id")),
        dispatch_id=c.result.dispatch_id or a.dispatch_id or known(launched.get("dispatch_id")),
        terminal=c.result.terminal or known(launched.get("terminal")),
        display_name=a.display_name,
    )


def _restore(c: _Dispatch) -> None:
    a = c.attempt
    launch = a.steps.get("launch", dr.StepState("attempted")).result or {}
    settled = a.steps.get("settle", dr.StepState("attempted")).result
    placement = None
    if settled is not None:
        # A legacy settle result also carries action and Orca ids; only these fields are read.
        notes = tuple(settled["notes"])
        placement = ObservedPlacement(
            settled["path"], settled["branch"], settled["display_name"], notes, settled.get("start_sha")
        )
    attended = a.steps.get("attend", dr.StepState("attempted")).result or {}
    if placement is not None and "warning" in attended:
        placement = replace(placement, notes=(*placement.notes, f"in-review not set: {attended['warning']}"))
    record_step = a.steps.get("record", dr.StepState("attempted"))
    probe_step = a.steps.get("probe", dr.StepState("attempted"))
    recorded = record_step.result or {}
    probed = probe_step.result or {}
    c.result = replace(
        c.result,
        task_id=a.task_id,
        dispatch_id=a.dispatch_id,
        display_name=a.display_name,
        terminal=launch.get("terminal"),
        placement=placement,
        recorded=recorded.get(
            "recorded",
            f"skipped:{record_step.reason}" if record_step.state == "skipped" and record_step.reason else None,
        ),
        probe=probed.get("probe", "skipped" if probe_step.state == "skipped" else None),
        delivery=probed.get(
            "delivery",
            "skipped" if probe_step.state == "skipped" else ("unverified" if probe_step.state == "done" else None),
        ),
    )


def _resolve(c: _Dispatch) -> bool:
    """Return whether an attempt exists to resume; an existing result ends the call."""
    c.step = "create"
    try:
        tasks = c.port.task_list(c.result.run_id)
    except BackendError as exc:
        if c.journal.attempts and c.attempt.task_id not in c.journal.superseded:
            _restore(c)
        raise _Stop("recovery-inspection", f"cannot resolve current Tasks: {exc}") from exc
    titled = [t for t in tasks if t["title"] == c.result.key and t["id"] not in c.journal.superseded]
    a = c.journal.attempts[-1] if c.journal.attempts else None
    if a is not None and a.task_id not in c.journal.superseded and not a.complete:
        _restore(c)
        pending = a.pending()
        if pending == "create":
            if len(titled) > 1:
                raise _Stop("recovery-inspection", "multiple Tasks match an uncertain create")
            if titled:
                task_id = titled[0]["id"]
                c.result = replace(c.result, task_id=task_id)
                c.mark("done", result={"task_id": task_id, "adopted": True}, task_id=task_id)
        elif pending is not None:
            c.step = pending
            raise _Stop("recovery-inspection", "previous effect is attempted but not confirmed")
        elif a.task_id not in {t["id"] for t in titled}:
            raise _Stop("recovery-inspection", "incomplete journal Task is absent from title lookup")
        c.result = replace(c.result, status="resumed")
        return True
    if len(titled) > 1:
        raise _Stop("recovery-inspection", "multiple current Tasks share the dispatch title")
    if titled:
        if a is not None and a.task_id == titled[0]["id"]:
            _restore(c)
        else:
            c.result = replace(c.result, delivery="unverified")
        c.result = replace(c.result, status="existing", task_id=titled[0]["id"], display_name=titled[0]["display_name"])
    return False


def _same_path(left: object, right: object) -> bool:
    return isinstance(left, str) and isinstance(right, str) and os.path.realpath(left) == os.path.realpath(right)


def _matches(planned: Mapping[str, Any], computed: Placement) -> str | None:
    """Why the plan's placement is not the one computed now, or `None` when they agree."""
    for name in ("branch", "parent_branch"):
        if planned[name] != getattr(computed, name):
            return f"{name} {planned[name]!r} != {getattr(computed, name)!r}"
    for name in ("worktree", "parent_worktree"):
        if not _same_path(planned[name], str(getattr(computed, name))):
            return f"{name} {planned[name]} != {getattr(computed, name)}"
    return None


def _occupied(c: _Dispatch) -> Callable[[str], str | None]:
    """Who holds *path* in this Run: another key's unfinished attempt or a non-terminal Orca worker.

    Conservative: an unreadable journal or an Orca error answers with a holder, never `None`.
    """

    def holder(path: str) -> str | None:
        for journal in sorted(c.layout.bundle_dir.glob("work/**/references/orca-dispatch/*.json")):
            try:
                other = dr.load_record(journal)
            except dr.DispatchRecordError as exc:
                return f"unreadable dispatch journal {journal.name}: {exc}"
            if other is None or other.run_id != c.result.run_id or other.key == c.result.key or not other.attempts:
                continue
            last = other.attempts[-1]
            if last.task_id in other.superseded or last.complete:
                continue
            placed = last.placement or {}
            if _same_path(placed.get("path"), path):
                return f"dispatch {other.key} in Run {c.result.run_id}"
        try:
            row = c.port.worktree_show(f"path:{path}")
            if row is None:
                return None
            for worker in c.port.worker_list(c.result.run_id):
                if worker["worktree_id"] == row["id"] and worker["state"] not in _TERMINAL_WORKER_STATES:
                    return f"Orca worker {worker['dispatch_id']}"
        except BackendError as exc:
            return f"Orca cannot say who holds {path}: {exc}"
        return None

    return holder


def _repo_hint(c: _Dispatch) -> str | None:
    repo_name = c.item["placement"]["repo"].get("name")
    return repo_name if isinstance(repo_name, str) and repo_name else None


def _computed(c: _Dispatch) -> Placement | None:
    """The placement computed now, checked against the plan's; `None` for a stage with no code placement.

    The plan's placement must still be the one computed now (an item reparented
    or retyped since `orchestrate` stops `plan-invalid` before any mutation).
    """
    planned = c.item["placement"]
    if planned is None:
        return None
    resolved = resolve(c.layout, c.item["path"], repo_name=_repo_hint(c))
    if isinstance(resolved, Refused):
        raise _Stop("placement-refused", resolved.detail, refusal=resolved.kind)
    computed = resolved.placement
    if computed is None:
        raise _Stop("plan-invalid", f"{c.item['path']} has no code placement now; replan")
    mismatch = _matches(planned, computed)
    if mismatch is not None:
        raise _Stop("plan-invalid", f"planned placement differs from the computed one ({mismatch}); replan")
    return computed


def _root_placed(c: _Dispatch) -> dict[str, Any]:
    """A stage with no code placement launches in the workspace root, which has no branch to verify."""
    path = str(c.layout.root)
    return {
        "path": path,
        "branch": None,
        "parent_path": None,
        "start_sha": None,
        "placement_argv": ["--worktree", f"path:{path}"],
        "chain": [],
        "orca": None,
    }


def _realize(c: _Dispatch, *, verify_only: bool) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Run the engine for the item's computed placement; a refusal stops `placement-refused`."""
    assert c.notifier is not None
    realized = realize(
        c.layout,
        c.item["path"],
        phase=c.item["phase"],
        verify_only=verify_only,
        notifier=c.notifier,
        occupied=_occupied(c),
        today=c.today,
        repo_name=_repo_hint(c),
    )
    if isinstance(realized, Refused):
        raise _Stop("placement-refused", realized.detail, refusal=realized.kind)
    path = str(realized.placement.worktree)
    placed = {
        "path": path,
        "branch": realized.placement.branch,
        "parent_path": str(realized.placement.parent_worktree),
        "start_sha": realized.start_sha,
        "placement_argv": ["--worktree", f"path:{path}"],
        "chain": [{"path": step.worktree, "action": step.action, "refresh": step.refresh} for step in realized.chain],
        "orca": realized.chain[-1].orca if realized.chain else None,
    }
    notes = tuple(f"{step.worktree}: {step.orca}" for step in realized.chain if step.orca is not None)
    return placed, notes


def _place(c: _Dispatch) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Make the planned placement so through the engine, before any Orca call.

    A stage with no code placement launches in the workspace root and calls
    no engine.
    """
    c.step = "place"
    if _computed(c) is None:
        return _root_placed(c), ()
    return _realize(c, verify_only=False)


def _retried(c: _Dispatch, retry_of: str) -> dr.Attempt:
    """The attempt `--retry-of` relaunches, after every precondition (own D-002); stops `recovery-inspection`.

    The journal's latest attempt must name a Task no reroute superseded, its
    dispatch must be *retry_of* (an earlier attempt on the same Task with that
    dispatch means it was already retried), and the Task must still be current
    in `task_list`. Nothing here mutates the port or the journal.
    """
    c.step = "create"
    if not c.journal.attempts:
        raise _Stop("recovery-inspection", f"no attempt to retry: {c.result.key} has no journaled dispatch")
    a = c.attempt
    if a.task_id is None:
        raise _Stop("recovery-inspection", "the latest attempt created no Task; there is nothing to retry")
    c.result = replace(c.result, task_id=a.task_id)
    if a.task_id in c.journal.superseded:
        raise _Stop("recovery-inspection", f"Task {a.task_id} was superseded by a reroute; dispatch the key afresh")
    if a.dispatch_id != retry_of:
        if any(p.task_id == a.task_id and p.dispatch_id == retry_of for p in c.journal.attempts[:-1]):
            later = a.dispatch_id or "an attempt that has not launched yet"
            raise _Stop("recovery-inspection", f"{retry_of} was already retried as {later}; leave it alone")
        raise _Stop(
            "recovery-inspection",
            f"the latest attempt on Task {a.task_id} is dispatch {a.dispatch_id}, not {retry_of}",
        )
    try:
        tasks = c.port.task_list(c.result.run_id)
    except BackendError as exc:
        raise _Stop("recovery-inspection", f"cannot resolve current Tasks: {exc}") from exc
    if not any(t["id"] == a.task_id for t in tasks):
        raise _Stop("recovery-inspection", f"Task {a.task_id} is absent from task_list for Run {c.result.run_id}")
    return a


def _argv_path(envelope: Mapping[str, Any]) -> str | None:
    """The `path:` an envelope's placement argv launched into, if it named one."""
    argv = envelope.get("placement_argv") or []
    return next((arg.removeprefix("path:") for arg in argv if isinstance(arg, str) and arg.startswith("path:")), None)


def _launched_at(a: dr.Attempt) -> tuple[str | None, str | None, str | None]:
    """The path, branch and `start_sha` attempt *a* launched with; all a retry reads of a legacy attempt."""
    placed = a.placement or {}
    if not _legacy(a):
        return placed.get("path"), placed.get("branch"), placed.get("start_sha")
    settled = a.steps.get("settle", dr.StepState("attempted")).result or {}
    path = placed.get("path") or settled.get("path") or _argv_path(a.envelope)
    return path, settled.get("branch"), placed.get("start_sha") or settled.get("start_sha")


def _retry_place(c: _Dispatch, prior: dr.Attempt) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Verify, never refresh: the retried stage continues in the place it was launched (own D-005).

    A retried attempt launched anywhere but the computed path (placed under the
    old rules) stops `recovery-inspection` before the engine runs; a human re-points it.
    """
    c.step = "place"
    path, branch, start_sha = _launched_at(prior)
    if path is None:
        raise _Stop("recovery-inspection", "the retried attempt journaled no launch path; inspect it before retrying")
    computed = _computed(c)
    root: dict[str, Any] | None = None
    if computed is None:
        root = _root_placed(c)
        want_path, want_branch = root["path"], root["branch"]
    else:
        want_path, want_branch = str(computed.worktree), computed.branch
    if not _same_path(path, want_path):
        raise _Stop(
            "recovery-inspection",
            f"the retried attempt ran in {path}, not the computed {want_path}; re-point it by hand before retrying",
        )
    if branch is not None and branch != want_branch:
        raise _Stop(
            "recovery-inspection",
            f"the retried attempt ran on {branch}, not the computed {want_branch}; re-point it by hand before retrying",
        )
    if root is not None:
        return root, ()
    placed, notes = _realize(c, verify_only=True)
    # A verify-only ensure records no baseline: the continued stage keeps the one it started with.
    return {**placed, "start_sha": start_sha}, notes


def _journal_retry(c: _Dispatch, prior: dr.Attempt, retry_of: str) -> None:
    """Place verify-only, then journal a new attempt on the same Task with `create` skipped."""
    placed, notes = _retry_place(c, prior)
    at = c.clock().isoformat()
    envelope = {**dict(prior.envelope), "placement_argv": placed["placement_argv"], "worktree_path": placed["path"]}
    encoded = prior.steps.get("encode", dr.StepState("attempted")).result
    steps = {
        "validate": dr.StepState("done", at=at),
        "place": dr.StepState("done", at=at, result={"notes": list(notes)}),
        # The Task's frozen spec carries the original receipt instruction; keep its token.
        "encode": dr.StepState("done", at=at, result=encoded),
        "create": dr.StepState(
            "skipped", at=at, result={"task_id": prior.task_id, "retry_of": retry_of}, reason="retry-of"
        ),
    }
    attempt = dr.Attempt(prior.task_id, None, prior.display_name, envelope, placed, steps)
    c.save(c.journal.with_retry(attempt))
    c.result = replace(c.result, status="dispatched", task_id=prior.task_id, display_name=prior.display_name)


def _encode(c: _Dispatch, placed: dict[str, Any], profile: tuple[str, str | None, str | None]) -> dict[str, Any]:
    c.step = "encode"
    agent, model, effort = profile
    spec = encode_launch_envelope(
        key=c.result.key,
        agent=agent,
        model=model,
        reasoning_effort=effort,
        placement_argv=placed["placement_argv"],
        mode=c.item["mode"],
        worktree_path=placed["path"],
        prompt="",
    )
    envelope: dict[str, Any] = json.loads(spec.split("\n", 1)[0].removeprefix("GW_LAUNCH_V1 "))
    return envelope


def _receipt(a: dr.Attempt) -> str | None:
    encoded = a.steps.get("encode", dr.StepState("attempted")).result or {}
    receipt = encoded.get("receipt")
    token = receipt.get("token") if isinstance(receipt, Mapping) else None
    return token if isinstance(token, str) else None


def _create(c: _Dispatch) -> None:
    a = c.attempt
    prompt = c.item["prompt"]
    token = _receipt(a)
    if token is not None:
        if delivery.receipt_token(c.result.key, prompt) != token:
            raise _Stop("recovery-inspection", "planned brief changed since the delivery receipt was encoded")
        prompt = prompt.rstrip("\n") + "\n\n" + delivery.receipt_instruction(c.result.key, token) + "\n"
    spec = "GW_LAUNCH_V1 " + json.dumps(dict(a.envelope), ensure_ascii=False, separators=(",", ":")) + "\n" + prompt
    task_id = c.port.task_create(c.result.run_id, spec=spec, title=c.result.key, display_name=a.display_name)
    c.result = replace(c.result, task_id=task_id)
    c.mark("done", result={"task_id": task_id}, task_id=task_id)


def _launch(c: _Dispatch) -> None:
    assert c.result.task_id is not None
    created = c.attempt.steps.get("create", dr.StepState("attempted")).result or {}
    retry_of = created.get("retry_of")
    try:
        start = c.port.worker_start(
            c.result.run_id,
            c.result.task_id,
            request=dict(c.attempt.envelope),
            placement_argv=c.attempt.envelope["placement_argv"],
            retry_of=retry_of if isinstance(retry_of, str) else None,
        )
    except BackendError as exc:
        # BackendError is the cross-band seam. Whitelist IDs; never persist the
        # backend's request, receipt, prompt or preamble carriers.
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            dispatch = details.get("dispatchId")
            terminal = details.get("agentTerminalHandle")
            worktree_id = None
            effects = details.get("effects")
            for effect in effects if isinstance(effects, list) else []:
                if not isinstance(effect, dict) or not isinstance(effect.get("id"), str):
                    continue
                if effect.get("kind") == "worktree":
                    worktree_id = effect["id"]
                elif effect.get("kind") == "terminal" and effect.get("role") == "agent" and terminal is None:
                    terminal = effect["id"]
            c.result = replace(
                c.result,
                dispatch_id=dispatch if isinstance(dispatch, str) else None,
                terminal=terminal if isinstance(terminal, str) else None,
            )
            c.mark(
                "attempted",
                dispatch_id=c.result.dispatch_id,
                result={"dispatch_id": c.result.dispatch_id, "terminal": c.result.terminal, "worktree_id": worktree_id},
            )
        raise
    c.result = replace(c.result, dispatch_id=start["dispatch_id"], terminal=start["terminal"])
    known = {k: start[k] for k in ("dispatch_id", "terminal", "worktree_id")}
    if start["state"] == "outcome_unknown" or start["dispatch_id"] is None or start["receipt_problem"]:
        c.mark("attempted", result=known, dispatch_id=start["dispatch_id"])
        if start["state"] == "outcome_unknown":
            raise _Stop("outcome-unknown", "worker start outcome is unknown")
        if start["dispatch_id"] is None:
            raise _Stop("launch-failed", "worker start returned no dispatch id")
        raise _Stop("receipt-mismatch", start["receipt_problem"] or "launch receipt mismatch")
    c.mark("done", result=known, dispatch_id=start["dispatch_id"])


def _settle(c: _Dispatch) -> None:
    """Verify the worker landed in the computed path on the computed branch; link nothing, name nothing."""
    assert c.result.dispatch_id is not None
    launched = c.attempt.steps["launch"].result or {}
    wid = launched.get("worktree_id") or c.port.worker_show(c.result.dispatch_id)["worktree_id"]
    if not wid:
        raise _Stop("placement-mismatch", "worker has no observed worktree id")
    row = c.port.worktree_show(f"id:{wid}")
    if row is None:
        raise _Stop("placement-mismatch", "worker worktree is absent")
    placed = c.attempt.placement or {}
    legacy = _legacy(c.attempt)
    expected = placed.get("path")
    if expected is None and legacy:
        expected = _argv_path(c.attempt.envelope)
    if expected is None:
        raise _Stop("placement-mismatch", "this attempt journaled no launch path; inspect before settling")
    path = row["path"]
    if not path or not _same_path(path, expected):
        raise _Stop("placement-mismatch", f"observed worktree path {path} differs from the computed {expected}")
    for worker in c.port.worker_list(c.result.run_id):
        if (
            worker["task_id"] != c.result.task_id
            and worker["worktree_id"] == wid
            and worker["state"] not in _TERMINAL_WORKER_STATES
        ):
            raise _Stop("placement-mismatch", "path already claimed by another dispatch this Run")
    if not Path(path).is_dir():
        raise _Stop("placement-mismatch", "observed path not reachable from this host; cannot verify")
    branch = row["branch"] if legacy else placed.get("branch")
    if branch is not None:
        head = probe_git(Path(path), "symbolic-ref", "--short", "HEAD")
        if head.returncode != 0 or head.stdout.strip() != branch:
            raise _Stop(
                "placement-mismatch", f"{path} has {head.stdout.strip() or 'no branch'} checked out, not {branch}"
            )
    place_step = c.attempt.steps["place"].result or {}
    observed = ObservedPlacement(
        path, branch, row["display_name"], tuple(place_step.get("notes", ())), placed.get("start_sha")
    )
    c.result = replace(c.result, placement=observed)
    # Keep the resolved id even when worker-start did not return it.
    c.mark("done", result={**asdict(observed), "notes": list(observed.notes)}, placement={**placed, "worktree_id": wid})


def _record(c: _Dispatch) -> None:
    """The journal's `record` step. It stays so a Run that straddles the upgrade resumes; it writes nothing.

    Placement is computed, so no stamp is recorded on the item or any owner. A
    stage with no code placement still records `skipped:<reason>`; a code
    placement marks the step done as `unchanged`.
    """
    observed = c.result.placement
    assert observed is not None
    phase = c.item["phase"]
    if phase not in _CODE_PHASES or observed.branch is None:
        reason = "read-only-phase" if phase not in _CODE_PHASES else "no-branch"
        c.result = replace(c.result, recorded=f"skipped:{reason}")
        c.mark("skipped", reason=reason)
        return
    c.result = replace(c.result, recorded="unchanged")
    c.mark("done", result={"recorded": c.result.recorded})


def _attend(c: _Dispatch) -> None:
    if c.item["mode"] != "attend":
        c.mark("skipped", reason="not-attend")
        return
    assert c.attempt.placement is not None
    warning: dict[str, str] = {}
    try:
        c.port.worktree_set_status(c.attempt.placement["worktree_id"], "in-review")
    except BackendError as exc:
        warning = {"warning": str(exc)}
        assert c.result.placement is not None
        c.result = replace(
            c.result,
            placement=replace(c.result.placement, notes=(*c.result.placement.notes, f"in-review not set: {exc}")),
        )
    c.mark("done", result=warning)


def _delivery(c: _Dispatch, sleep: Callable[[float], None], seconds: float, *, reads: int) -> str:
    token = _receipt(c.attempt)
    if token is None or c.result.task_id is None or c.result.dispatch_id is None:
        return "unverified"
    expected = delivery.expected_line(
        task_id=c.result.task_id, dispatch_id=c.result.dispatch_id, key=c.result.key, token=token
    )
    outcome = "inconclusive"
    for attempt in range(reads):
        if attempt:
            sleep(seconds)
        try:
            outcome = delivery.assess(c.port.worker_read(c.result.dispatch_id, limit=DELIVERY_READ_LIMIT), expected)
        except BackendError:
            return "inconclusive"
        if outcome != "unverified":
            return outcome
    return outcome


def _recheck_delivery(c: _Dispatch) -> None:
    """Upgrade only the exact completed attempt; never downgrade or launch."""
    if (
        not c.journal.attempts
        or c.result.task_id is None
        or c.attempt.task_id != c.result.task_id
        or c.result.dispatch_id is None
        or c.attempt.dispatch_id != c.result.dispatch_id
        or not c.attempt.complete
    ):
        return
    probe_step = c.attempt.steps.get("probe")
    if probe_step is None or probe_step.state != "done" or c.result.delivery == "verified":
        return
    if _delivery(c, lambda _s: None, 0.0, reads=1) == "verified":
        c.step = "probe"
        c.mark("done", result={**(probe_step.result or {}), "delivery": "verified"})
        c.result = replace(c.result, delivery="verified")


def _probe(c: _Dispatch, enabled: bool, seconds: float, sleep: Callable[[float], None]) -> None:
    def finish(outcome: str) -> None:
        delivered = (
            outcome if outcome in ("skipped", "inconclusive") else _delivery(c, sleep, seconds, reads=DELIVERY_READS)
        )
        c.result = replace(c.result, probe=outcome, delivery=delivered)
        c.mark(
            "skipped" if outcome == "skipped" else "done",
            result={"probe": outcome, "delivery": delivered},
            reason="probe-disabled" if outcome == "skipped" else None,
        )

    if not enabled:
        finish("skipped")
        return
    if not c.result.terminal:
        finish("no-terminal")
        return
    assert c.result.dispatch_id is not None
    try:
        sleep(seconds)
        if c.port.worker_show(c.result.dispatch_id)["last_heartbeat_at"]:
            finish("submitted-heartbeat")
            return
        for sent in range(3):
            read = c.port.worker_read(c.result.dispatch_id, limit=5)
            if read["source"] != "transcript":
                finish("inconclusive")
                return
            if read["message_count"] > 0:
                finish("nudged" if sent else "submitted-transcript")
                return
            # Re-check immediately before EVERY Enter, including the first:
            # a heartbeat may have arrived during either transcript read.
            if c.port.worker_show(c.result.dispatch_id)["last_heartbeat_at"]:
                finish("submitted-heartbeat")
                return
            if sent == 2:
                break
            c.port.terminal_send_enter(c.result.terminal)
            sleep(seconds)
    except BackendError:
        finish("inconclusive")
        return
    raise _Stop("unsent", "prompt remains unsubmitted after two Enter nudges")


def _profile(c: _Dispatch) -> tuple[str, str | None, str | None]:
    return dr.apply_overrides(
        c.item["agent"], c.item.get("model"), c.item.get("reasoning_effort"), c.journal.latest_overrides()
    )


def _task_update(c: _Dispatch) -> None:
    assert c.result.task_id is not None
    c.port.task_update(c.result.run_id, c.result.task_id, "dispatched")
    c.mark("done")


def _begin(c: _Dispatch) -> bool:
    """Journal a fresh attempt or find the one to resume; `False` when an existing Task ends the call."""
    placed: dict[str, Any] | None = None
    notes: tuple[str, ...] = ()
    profile: tuple[str, str | None, str | None] | None = None
    if not c.journal.attempts or c.attempt.task_id in c.journal.superseded:
        # Nothing to resume: reject overrides, then place, before any Orca call.
        c.step = "encode"
        profile = _profile(c)
        placed, notes = _place(c)
    resuming = _resolve(c)
    if c.result.status == "existing":
        return False
    if not resuming:
        if placed is None or profile is None:
            # Reject overrides before *any* placement write or provisioning.
            c.step = "encode"
            profile = _profile(c)
            placed, notes = _place(c)
        envelope = _encode(c, placed, profile)
        at = c.clock().isoformat()
        steps = {name: dr.StepState("done", at=at) for name in ("validate", "place", "encode")}
        steps["place"] = dr.StepState("done", at=at, result={"notes": list(notes)})
        token = delivery.receipt_token(c.result.key, c.item["prompt"])
        steps["encode"] = dr.StepState(
            "done", at=at, result={"receipt": {"version": delivery.RECEIPT_VERSION, "token": token}}
        )
        steps["create"] = dr.StepState("attempted", at=at)
        attempt = dr.Attempt(None, None, c.result.display_name or "", envelope, placed, steps)
        c.save(c.journal.with_attempt(attempt))
        c.result = replace(c.result, status="dispatched")
    return True


def run_dispatch(
    layout: WorkspaceLayout,
    key: str,
    *,
    plan: object,
    run_id: str,
    port: OrcaPort,
    notifier: PlacementNotifier,
    today: date,
    clock: Callable[[], datetime],
    probe: bool = True,
    settle_seconds: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
    retry_of: str | None = None,
) -> DispatchResult:
    """Dispatch one planned stage: place it through the engine, then launch, settle and record.

    A fresh dispatch places before any Orca call, so a refusal leaves no Task,
    envelope or journal attempt; a resumed attempt never places again.

    *retry_of* relaunches the journal's latest attempt (a parked or failed
    worker) on the same Task: its preconditions are checked under the key's
    execution owner before anything else, place runs verify-only, and a new
    attempt with `create` skipped is launched with `--retry-of <dispatch-id>`.
    """
    c = _Dispatch(layout, port, today, clock, DispatchResult(None, key, run_id), notifier)
    with ExitStack() as ownership:
        try:
            _validate(c, plan)
            ownership.enter_context(dr.execution_owner(layout, key))
            ownership.enter_context(return_dispatch_admission(layout, c.item["path"]))
            # Validation read is only a snapshot until execution ownership is held.
            if dr.load_record(dr.record_file(layout, c.item["path"], key)) != c.persisted:
                raise dr.DispatchRecordConflict("dispatch record changed before execution ownership; retry")
            if pending_return(layout, c.item["path"]) is not None:
                raise _Stop("recovery-inspection", "execute return is pending; inspect or resume before dispatch")
            if retry_of is not None:
                _journal_retry(c, _retried(c, retry_of), retry_of)
            elif not _begin(c):
                if probe:
                    _recheck_delivery(c)
                return c.result
            operations: tuple[tuple[str, Callable[[], None]], ...] = (
                ("create", lambda: _create(c)),
                ("launch", lambda: _launch(c)),
                ("settle", lambda: _settle(c)),
                ("record", lambda: _record(c)),
                ("attend", lambda: _attend(c)),
                ("probe", lambda: _probe(c, probe, settle_seconds, sleep)),
                ("task-update", lambda: _task_update(c)),
            )
            for name, operation in operations:
                c.step = name
                state = c.attempt.steps.get(name)
                if state is not None and state.state in ("done", "skipped"):
                    continue
                if state is None:
                    c.mark("attempted")
                operation()
            return c.result
        except (dr.DispatchRecordError, dr.OverrideInvalid, _Stop, BackendError, OSError, ValueError) as exc:
            reason = {
                "validate": "plan-invalid",
                "create": "task-create-failed",
                "place": "placement-refused",
                "encode": "plan-invalid",
                "launch": "launch-failed",
                "settle": "placement-mismatch",
                "record": "placement-unrecorded",
                "attend": "record-invalid",
                "probe": "recovery-inspection",
                "task-update": "task-update-failed",
            }[c.step]
            refusal = None
            detail = str(exc)
            if isinstance(exc, dr.DispatchRecordConflict):
                reason = "recovery-inspection"
                if c.record is not None and c.record.attempts:
                    _restore_ids(c)
            elif isinstance(exc, dr.DispatchRecordError):
                reason = "record-invalid"
            elif isinstance(exc, dr.OverrideInvalid):
                reason = "override-invalid"
            elif isinstance(exc, _Stop):
                reason, detail, refusal = exc.reason, exc.detail, exc.refusal
            failure = DispatchFailure(c.step, reason, detail, c.result.task_id, c.result.dispatch_id, refusal)
            return replace(c.result, status=None, failure=failure)
