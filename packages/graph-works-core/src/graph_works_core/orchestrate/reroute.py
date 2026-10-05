"""Supersede one settled Orca Task with an owner-locked, recoverable journal."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, cast

from subagents_io.backend import BackendError

from graph_works_core.orchestrate import dispatch_record as dr
from graph_works_core.orchestrate.dispatch import DispatchFailure
from graph_works_core.orchestrate.orca_port import OrcaPort, OrcaTask
from graph_works_core.workspace.layout import WorkspaceLayout

_PREFIX = "GW_LAUNCH_V1 "
_V1 = frozenset({"version", "dispatch_key", "agent", "model", "reasoning_effort", "placement_argv"})
_V2 = _V1 | {"mode", "worktree_path"}


@dataclass(frozen=True, slots=True)
class RerouteResult:
    status: Literal["rerouted"] | None
    key: str
    run_id: str
    reason: str
    superseded_task_id: str | None = None
    superseded_dispatch_id: str | None = None
    overrides: dr.Overrides | None = None
    record_path: str | None = None
    failure: DispatchFailure | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


class _Refusal(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(detail)


def _decoded(spec: str | None, key: str) -> dict[str, object] | None:
    if not isinstance(spec, str):
        return None
    first, separator, _ = spec.partition("\n")
    if not separator or not first.startswith(_PREFIX):
        return None
    try:
        envelope = json.loads(first[len(_PREFIX) :])
    except ValueError:
        return None
    if not isinstance(envelope, dict) or type(envelope.get("version")) is not int:
        return None
    fields = {1: _V1, 2: _V2}.get(envelope["version"])
    if fields is None or envelope.keys() != fields or envelope["dispatch_key"] != key:
        return None
    if not isinstance(envelope["agent"], str) or not envelope["agent"].strip():
        return None
    if any(
        envelope[name] is not None and (not isinstance(envelope[name], str) or not envelope[name].strip())
        for name in ("model", "reasoning_effort")
    ):
        return None
    if envelope["reasoning_effort"] is not None and envelope["model"] is None:
        return None
    if not isinstance(envelope["placement_argv"], list) or not all(
        isinstance(arg, str) for arg in envelope["placement_argv"]
    ):
        return None
    if envelope["version"] == 2 and (
        not isinstance(envelope["mode"], str)
        or not envelope["mode"].strip()
        or (envelope["worktree_path"] is not None and not isinstance(envelope["worktree_path"], str))
    ):
        return None
    return cast(dict[str, object], envelope)


def _baseline(task: OrcaTask, record: dr.DispatchRecord, key: str) -> dict[str, object]:
    decoded = _decoded(task["spec"], key)
    if decoded is not None:
        return decoded
    for attempt in reversed(record.attempts):
        if attempt.task_id == task["id"] and attempt.envelope.get("dispatch_key") == key:
            agent = attempt.envelope.get("agent")
            model = attempt.envelope.get("model")
            effort = attempt.envelope.get("reasoning_effort")
            if (
                isinstance(agent, str)
                and agent.strip()
                and (model is None or isinstance(model, str))
                and (effort is None or isinstance(effort, str))
            ):
                return dict(attempt.envelope)
    raise _Refusal("record-invalid", "Task spec and matching record attempt have no readable launch envelope")


def _display(task: OrcaTask) -> tuple[str, str]:
    value = task["display_name"]
    if not isinstance(value, str):
        raise _Refusal("record-invalid", "Task display_name is missing")
    parts = value.split(" · ")
    if len(parts) != 2 or not all(parts):
        raise _Refusal("record-invalid", "Task display_name does not carry work path and phase")
    return parts[0], parts[1]


def _result(result: RerouteResult, entry: dr.Reroute) -> RerouteResult:
    return replace(
        result,
        status="rerouted",
        reason=entry.reason,
        superseded_task_id=entry.superseded_task_id,
        superseded_dispatch_id=entry.superseded_dispatch_id,
        overrides=entry.overrides,
    )


def run_reroute(
    layout: WorkspaceLayout,
    key: str,
    *,
    run_id: str,
    reason: str,
    port: OrcaPort,
    clock: Callable[[], datetime],
    agent: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> RerouteResult:
    result = RerouteResult(None, key, run_id, reason)
    if not reason.strip():
        return replace(result, failure=DispatchFailure("reroute", "reason-missing", "--reason must be nonblank"))
    task_id: str | None = None
    dispatch_id: str | None = None
    with ExitStack() as ownership:
        try:
            if not run_id.strip():
                raise _Refusal("record-invalid", "run id must be nonblank")
            titled = [task for task in port.task_list(run_id) if task["title"] == key]
            if not titled:
                raise _Refusal("no-task", "dispatch key has no Task")
            work_path, phase = _display(titled[0])
            path = dr.record_file(layout, work_path, key)
            expected = dr.load_record(path)
            record = expected or dr.DispatchRecord(key, work_path, phase, run_id)
            result = replace(result, record_path="/" + dr.record_ref(work_path, key))
            if (record.key, record.work_path, record.phase, record.run_id) != (key, work_path, phase, run_id):
                raise _Refusal("record-invalid", "record identity differs from Task/run")
            current = [task for task in titled if task["id"] not in record.superseded]
            if len(current) == 1:
                task_id = current[0]["id"]
                prior = next((a for a in reversed(record.attempts) if a.task_id == task_id), None)
                dispatch_id = prior.dispatch_id if prior is not None else None
            ownership.enter_context(dr.execution_owner(layout, key))
            if dr.load_record(path) != expected or titled != [
                task for task in port.task_list(run_id) if task["title"] == key
            ]:
                raise _Refusal("recovery-inspection", "dispatch evidence changed before execution ownership; retry")
            if record.reroutes and not current:
                previous = record.reroutes[-1]
                if any(task["id"] == previous.superseded_task_id and task["status"] == "blocked" for task in titled):
                    return _result(result, previous)
            if not current:
                raise _Refusal("no-task", "dispatch key has no current Task")
            if len(current) != 1:
                raise _Refusal("recovery-inspection", "multiple current Tasks share the dispatch title")
            task = current[0]
            task_id = task["id"]
            if _display(task) != (work_path, phase):
                raise _Refusal("record-invalid", "current Task display_name differs")
            prior = next((a for a in reversed(record.attempts) if a.task_id == task_id), None)
            if prior is not None:
                dispatch_id = prior.dispatch_id
            if prior is not None and prior.steps.get("reroute", dr.StepState("done")).state == "attempted":
                raise _Refusal("recovery-inspection", "previous reroute effect has unknown outcome")
            latest = next((worker for worker in port.worker_list(run_id) if worker["task_id"] == task_id), None)
            if latest is not None:
                dispatch_id = latest["dispatch_id"]
                if latest["state"] == "outcome_unknown" or latest["dispatch_status"] == "outcome_unknown":
                    raise _Refusal("recovery-inspection", "current Task's worker outcome is unknown")
                if latest["state"] in ("ready", "running"):
                    raise _Refusal("reroute-live", "current Task still has a live worker")
            baseline = _baseline(task, record, key)
            requested = dr.Overrides(agent, model, effort)
            dr.apply_overrides(
                cast(str, baseline["agent"]),
                cast(str | None, baseline["model"]),
                cast(str | None, baseline["reasoning_effort"]),
                requested,
            )
            at = clock().isoformat()
            index = next(
                (i for i in range(len(record.attempts) - 1, -1, -1) if record.attempts[i].task_id == task_id), None
            )
            if index is None:
                synthetic = dr.Attempt(task_id, dispatch_id, task["display_name"] or "", baseline, None, {})
                record = replace(record, attempts=(*record.attempts, synthetic))
                index = len(record.attempts) - 1
            record = record.with_step(index, "reroute", dr.StepState("attempted", at=at))
            dr.compare_and_swap_record(layout, record, expected=expected)
            try:
                port.task_update(run_id, task_id, "blocked")
            except BackendError as exc:
                code = getattr(exc, "code", None)
                # Orca's task-status precondition refusal proves no mutation.
                # Transport errors and unclassified codes remain uncertain.
                if type(code) is str and code == "task_not_startable":
                    failed = record.with_step(
                        index, "reroute", dr.StepState("failed", at=clock().isoformat(), reason=code)
                    )
                    dr.compare_and_swap_record(layout, failed, expected=record)
                raise _Refusal("task-update-failed", str(exc)) from exc
            entry = dr.Reroute(at, reason, task_id, dispatch_id, requested)
            completed = record.with_step(index, "reroute", dr.StepState("done", at=clock().isoformat()))
            dr.compare_and_swap_record(layout, completed.with_reroute(entry), expected=record)
            return _result(result, entry)
        except (dr.DispatchRecordError, dr.OverrideInvalid, _Refusal, BackendError, OSError, ValueError) as exc:
            if isinstance(exc, _Refusal):
                failure_reason = exc.reason
            elif isinstance(exc, dr.OverrideInvalid):
                failure_reason = "override-invalid"
            elif isinstance(exc, dr.DispatchRecordError):
                failure_reason = "record-invalid"
            else:
                failure_reason = "recovery-inspection"
            return replace(
                result,
                failure=DispatchFailure("reroute", failure_reason, str(exc), task_id, dispatch_id),
            )
