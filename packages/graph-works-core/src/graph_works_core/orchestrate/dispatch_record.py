"""Strict, owner-locked journal for an Orca dispatch attempt."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Collection, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from okf_ext.locking import locked
from work_tracker_okf.paths import references_dir

from graph_works_core.workspace.decision_owner import locked_decision_owner
from graph_works_core.workspace.layout import WorkspaceLayout

RECORD_SCHEMA = "gw-orca-dispatch"
RECORD_VERSION = 1
STEPS = ("validate", "place", "encode", "create", "launch", "settle", "record", "attend", "probe", "task-update")
JOURNAL_STEPS = (*STEPS, "reroute")
SECRET_MARKERS = ("dcap_", "--dispatch-capability")


class DispatchRecordError(ValueError):
    """A dispatch record is malformed or unsafe to persist."""


class DispatchRecordConflict(ValueError):
    """The durable record changed after the caller made its decision."""


@contextmanager
def execution_owner(layout: WorkspaceLayout, key: str) -> Iterator[None]:
    """Own this key across dispatch/reroute decisions, effects and journal writes.

    This is a separate, non-reentrant OS lock, held *before* any short item
    owner lock, which in turn precedes placement's bundle/executor locks.
    No item owner lock is held across an Orca call or record-placement call.
    The key is workspace-wide (not Run-specific), so another Run cannot bypass
    ownership. Never unlink the lock file: process death releases its descriptor.
    """
    _component(key, "key")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    path = layout.cache_dir / "dispatch-execution" / f"{digest}.lock"
    with ExitStack() as ownership:
        try:
            ownership.enter_context(locked(path, blocking=False))
        except OSError as exc:
            raise DispatchRecordConflict(f"cannot own dispatch key {key!r}; inspect/retry: {exc}") from exc
        yield


class OverrideInvalid(ValueError):
    """A requested agent/model/effort override is invalid."""


@dataclass(frozen=True, slots=True)
class StepState:
    state: Literal["attempted", "done", "skipped"]
    at: str | None = None
    result: Mapping[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Overrides:
    agent: str | None = None
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class Attempt:
    task_id: str | None
    dispatch_id: str | None
    display_name: str
    envelope: Mapping[str, Any]
    placement: Mapping[str, Any] | None
    steps: Mapping[str, StepState]

    @property
    def complete(self) -> bool:
        return all(name in self.steps and self.steps[name].state in ("done", "skipped") for name in STEPS)

    def pending(self) -> str | None:
        return next((name for name in STEPS if name in self.steps and self.steps[name].state == "attempted"), None)


@dataclass(frozen=True, slots=True)
class Reroute:
    at: str
    reason: str
    superseded_task_id: str
    superseded_dispatch_id: str | None
    overrides: Overrides


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    key: str
    work_path: str
    phase: str
    run_id: str
    attempts: tuple[Attempt, ...] = ()
    reroutes: tuple[Reroute, ...] = ()

    @property
    def superseded(self) -> frozenset[str]:
        return frozenset(reroute.superseded_task_id for reroute in self.reroutes)

    def latest_overrides(self) -> Overrides | None:
        return self.reroutes[-1].overrides if self.reroutes else None

    def with_attempt(self, attempt: Attempt) -> DispatchRecord:
        if self.attempts and self.attempts[-1].task_id in (None, attempt.task_id):
            return replace(self, attempts=(*self.attempts[:-1], attempt))
        return replace(self, attempts=(*self.attempts, attempt))

    def with_step(self, index: int, step: str, state: StepState, **fields: Any) -> DispatchRecord:  # noqa: ANN401
        if step not in JOURNAL_STEPS:
            raise DispatchRecordError(f"unknown journal step {step!r}")
        attempt = self.attempts[index]
        updated = replace(attempt, steps={**attempt.steps, step: state}, **fields)
        return replace(self, attempts=(*self.attempts[:index], updated, *self.attempts[index + 1 :]))

    def with_reroute(self, reroute: Reroute) -> DispatchRecord:
        return replace(self, reroutes=(*self.reroutes, reroute))


def _component(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value in (".", "..") or any(c in value for c in "/\\:\x00"):
        raise DispatchRecordError(f"invalid {name}: {value!r}")
    return value


def record_ref(work_path: str, key: str) -> str:
    if not isinstance(work_path, str):
        raise DispatchRecordError(f"invalid work_path: {work_path!r}")
    parts = work_path.split("/")
    if len(parts) < 2 or parts[0] != "work":
        raise DispatchRecordError(f"invalid work_path: {work_path!r}")
    for part in parts:
        _component(part, "work_path component")
    _component(key, "key")
    return f"{references_dir(work_path).rel}/orca-dispatch/{key}.json"


def record_file(layout: WorkspaceLayout, work_path: str, key: str) -> Path:
    return layout.bundle_dir / record_ref(work_path, key)


def _contains_secret(value: object) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in SECRET_MARKERS)
    if isinstance(value, Mapping):
        return any(_contains_secret(k) or _contains_secret(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(v) for v in value)
    return False


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        temp.replace(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def to_json(record: DispatchRecord) -> dict[str, Any]:
    return {
        "schema": RECORD_SCHEMA,
        "version": RECORD_VERSION,
        "key": record.key,
        "work_path": record.work_path,
        "phase": record.phase,
        "run_id": record.run_id,
        "attempts": [
            {
                "task_id": attempt.task_id,
                "dispatch_id": attempt.dispatch_id,
                "display_name": attempt.display_name,
                "envelope": dict(attempt.envelope),
                "placement": None if attempt.placement is None else dict(attempt.placement),
                "steps": {
                    name: {
                        k: v
                        for k, v in {
                            "state": state.state,
                            "at": state.at,
                            "result": None if state.result is None else dict(state.result),
                            "reason": state.reason,
                        }.items()
                        if v is not None
                    }
                    for name, state in attempt.steps.items()
                },
            }
            for attempt in record.attempts
        ],
        "reroutes": [
            {
                "at": reroute.at,
                "reason": reroute.reason,
                "superseded_task_id": reroute.superseded_task_id,
                "superseded_dispatch_id": reroute.superseded_dispatch_id,
                "overrides": {
                    "agent": reroute.overrides.agent,
                    "model": reroute.overrides.model,
                    "effort": reroute.overrides.effort,
                },
            }
            for reroute in record.reroutes
        ],
    }


def _error(path: str, field: str, detail: str) -> DispatchRecordError:
    return DispatchRecordError(f"{path}: {field}: {detail}")


def _object(
    value: object, path: str, field: str, required: Collection[str], optional: Collection[str] = ()
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, field, "expected object")
    if any(not isinstance(key, str) for key in value):
        raise _error(path, field, "expected string key")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing:
        raise _error(path, f"{field}.{sorted(missing)[0]}", "missing field")
    if extra:
        raise _error(path, f"{field}.{sorted(extra)[0]}", "extra field")
    return value


def _string(value: object, path: str, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise _error(path, field, "expected string" + (" or null" if nullable else ""))
    return value


def _json_value(value: object, path: str, field: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _json_value(item, path, f"{field}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _error(path, field, "expected string key")
            _json_value(item, path, f"{field}.{key}")
        return
    raise _error(path, field, "expected JSON value")


def _mapping(value: object, path: str, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, field, "expected object")
    _json_value(value, path, field)
    _reject_prompt_carriers(value, path, field)
    return value


def _reject_prompt_carriers(value: object, path: str, field: str) -> None:
    if isinstance(value, str):
        if "GW_LAUNCH_V1 " in value:
            raise _error(path, field, "encoded prompt/preamble is forbidden")
    elif isinstance(value, dict):
        for key, item in value.items():
            name = re.sub(r"[^a-z0-9]", "", key.casefold())
            if "prompt" in name or "preamble" in name:
                raise _error(path, f"{field}.{key}", "prompt/preamble field is forbidden")
            _reject_prompt_carriers(item, path, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_prompt_carriers(item, path, f"{field}[{index}]")


def _parse_record(value: object, path: str) -> DispatchRecord:
    if _contains_secret(value):
        raise _error(path, "record", "secret (dispatch capability) is forbidden")
    root = _object(
        value, path, "record", {"schema", "version", "key", "work_path", "phase", "run_id", "attempts", "reroutes"}
    )
    if root["schema"] != RECORD_SCHEMA:
        raise _error(path, "schema", f"expected {RECORD_SCHEMA!r}")
    if type(root["version"]) is not int or root["version"] != RECORD_VERSION:
        raise _error(path, "version", f"expected {RECORD_VERSION}")
    key = _string(root["key"], path, "key")
    work_path = _string(root["work_path"], path, "work_path")
    phase = _string(root["phase"], path, "phase")
    run_id = _string(root["run_id"], path, "run_id")
    assert key is not None and work_path is not None and phase is not None and run_id is not None
    try:
        record_ref(work_path, key)
    except DispatchRecordError as error:
        raise _error(path, "work_path/key", str(error)) from error
    if not isinstance(root["attempts"], list):
        raise _error(path, "attempts", "expected array")
    if not isinstance(root["reroutes"], list):
        raise _error(path, "reroutes", "expected array")
    attempts: list[Attempt] = []
    for i, raw_attempt in enumerate(root["attempts"]):
        loc = f"attempts[{i}]"
        item = _object(
            raw_attempt, path, loc, {"task_id", "dispatch_id", "display_name", "envelope", "placement", "steps"}
        )
        task_id = _string(item["task_id"], path, f"{loc}.task_id", nullable=True)
        dispatch_id = _string(item["dispatch_id"], path, f"{loc}.dispatch_id", nullable=True)
        display_name = _string(item["display_name"], path, f"{loc}.display_name")
        envelope = _mapping(item["envelope"], path, f"{loc}.envelope")
        placement = item["placement"]
        if placement is not None:
            placement = _mapping(placement, path, f"{loc}.placement")
        steps = _object(item["steps"], path, f"{loc}.steps", set(), set(JOURNAL_STEPS))
        parsed_steps: dict[str, StepState] = {}
        for name, raw_step in steps.items():
            state_loc = f"{loc}.steps.{name}"
            step = _object(raw_step, path, state_loc, {"state"}, {"at", "result", "reason"})
            for optional_name in ("at", "result", "reason"):
                if optional_name in step and step[optional_name] is None:
                    raise _error(path, f"{state_loc}.{optional_name}", "omit null field")
            state = _string(step["state"], path, f"{state_loc}.state")
            if state not in ("attempted", "done", "skipped"):
                raise _error(path, f"{state_loc}.state", "invalid state")
            at = _string(step.get("at"), path, f"{state_loc}.at", nullable=True)
            reason = _string(step.get("reason"), path, f"{state_loc}.reason", nullable=True)
            result = step.get("result")
            if result is not None:
                result = _mapping(result, path, f"{state_loc}.result")
            parsed_steps[name] = StepState(cast(Literal["attempted", "done", "skipped"], state), at, result, reason)
        assert display_name is not None
        attempts.append(Attempt(task_id, dispatch_id, display_name, envelope, placement, parsed_steps))
    reroutes: list[Reroute] = []
    for i, raw_reroute in enumerate(root["reroutes"]):
        loc = f"reroutes[{i}]"
        item = _object(
            raw_reroute, path, loc, {"at", "reason", "superseded_task_id", "superseded_dispatch_id", "overrides"}
        )
        at = _string(item["at"], path, f"{loc}.at")
        reason = _string(item["reason"], path, f"{loc}.reason")
        task_id = _string(item["superseded_task_id"], path, f"{loc}.superseded_task_id")
        dispatch_id = _string(item["superseded_dispatch_id"], path, f"{loc}.superseded_dispatch_id", nullable=True)
        overrides = _object(item["overrides"], path, f"{loc}.overrides", {"agent", "model", "effort"})
        agent = _string(overrides["agent"], path, f"{loc}.overrides.agent", nullable=True)
        model = _string(overrides["model"], path, f"{loc}.overrides.model", nullable=True)
        effort = _string(overrides["effort"], path, f"{loc}.overrides.effort", nullable=True)
        assert at is not None and reason is not None and task_id is not None
        reroutes.append(Reroute(at, reason, task_id, dispatch_id, Overrides(agent, model, effort)))
    return DispatchRecord(key, work_path, phase, run_id, tuple(attempts), tuple(reroutes))


def load_record(path: Path) -> DispatchRecord | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeError as error:
        raise _error(str(path), "record", f"invalid UTF-8: {error}") from error
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except ValueError as error:
        raise _error(str(path), "record", f"invalid JSON: {error}") from error
    return _parse_record(payload, str(path))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field {key!r}")
        result[key] = value
    return result


def save_record(layout: WorkspaceLayout, record: DispatchRecord) -> Path:
    """Atomic replace under *work_path*'s decision-owner lock; never call while holding it."""
    return _save_record(layout, record, expected=None, compare=False)


def compare_and_swap_record(
    layout: WorkspaceLayout, record: DispatchRecord, *, expected: DispatchRecord | None
) -> Path:
    """Replace only the expected snapshot (None means absent), else raise conflict.

    Compare and write share one owner-lock hold; never call while holding it.
    """
    return _save_record(layout, record, expected=expected, compare=True)


def _save_record(
    layout: WorkspaceLayout, record: DispatchRecord, *, expected: DispatchRecord | None, compare: bool
) -> Path:
    payload = to_json(record)
    if _contains_secret(payload):
        raise DispatchRecordError("dispatch record would store a secret (dispatch capability); refused")
    path = record_file(layout, record.work_path, record.key)
    _parse_record(payload, str(path))
    with locked_decision_owner(layout, record.work_path):
        owner = layout.bundle_dir / record.work_path
        bundle_root = layout.bundle_dir.resolve()
        owner_root = owner.resolve()
        if (
            owner_root != bundle_root / record.work_path
            or not path.parent.resolve().is_relative_to(owner_root)
            or path.is_symlink()
        ):
            raise DispatchRecordError(f"{path}: record path escapes its owner directory")
        existing = load_record(path)
        if existing is not None and (existing.key, existing.work_path) != (record.key, record.work_path):
            raise DispatchRecordError(f"{path}: existing record identity differs")
        if compare and existing != expected:
            raise DispatchRecordConflict(f"{path}: dispatch record changed; inspect the durable result")
        write_json_atomic(path, payload)
    return path


def apply_overrides(
    agent: str, model: str | None, effort: str | None, overrides: Overrides | None
) -> tuple[str, str | None, str | None]:
    if overrides is not None:
        for name, value in (("agent", overrides.agent), ("model", overrides.model), ("effort", overrides.effort)):
            if value is not None and not value.strip():
                raise OverrideInvalid(f"--{name} must be nonblank when given")
        if overrides.agent is not None and overrides.agent != agent:
            agent, model, effort = overrides.agent, None, None
        if overrides.model is not None:
            model = overrides.model
        if overrides.effort is not None:
            effort = overrides.effort
    if effort is not None and model is None:
        raise OverrideInvalid("Set a model or clear reasoning_effort.")
    return agent, model, effort
