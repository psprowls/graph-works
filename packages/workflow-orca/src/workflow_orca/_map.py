"""Translation between Orca's vocabulary and the protocol's closed one.

Pure: no process, no argv, no I/O. Everything here is a function of a dict
that `backend.py` already fetched, which is what lets `test_map.py` exercise
every branch off captured fixtures with no session at all.

The state table is close to identity — `EVENT_KINDS` and Orca's message types
are the same four words, and `WORKER_STATES` needs exactly one rename. That is
not a coincidence: the protocol was derived from this loop's shape.
"""

from __future__ import annotations

import json
from typing import Any

from subagents_io.backend import (
    BackendError,
    Escalation,
    Heartbeat,
    WorkerDone,
    WorkerEvent,
    WorkerQuestion,
)

#: Orca `workerState` -> `WorkerRecord.state`. Anything absent is `"unknown"`,
#: which is the honest answer for a state this package has not been taught —
#: `"failed"` would be a claim, and a wrong one for a key that is recoverable.
WORKER_STATE_BY_ORCA: dict[str, str] = {
    "ready": "pending",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "stopped": "stopped",
}


def worker_state(orca_state: str | None) -> str:
    if orca_state is None:
        return "unknown"
    return WORKER_STATE_BY_ORCA.get(orca_state, "unknown")


def parse_task_result(raw: str | None) -> dict[str, Any]:
    """Open a settled task's `result`, which Orca stores as embedded JSON.

    Tolerant by design: a settled key must reconstruct from `task-list` alone,
    and a `result` this package cannot read is a reason to report less, never
    a reason to raise in the middle of enumerating every worker in a Run.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    """Decode current embedded JSON before attribution or batch completion checks.

    Historical receipts already carry a mapping. Malformed payloads refuse the
    whole delivery: silently dropping a success could let a sibling ack it.
    """
    payload = message.get("payload")
    if payload is None:
        payload = {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BackendError(
                f"Invalid message payload for {message.get('id')!r}; inspect delivery recovery."
            ) from exc
    if not isinstance(payload, dict):
        raise BackendError(f"Invalid message payload for {message.get('id')!r}; inspect delivery recovery.")
    return {**message, "payload": payload}


def _tuple_of_str(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def event_from_message(
    message: dict[str, Any],
    keys_by_task: dict[str, str],
    *,
    delivery_id: str | None,
) -> WorkerEvent | None:
    """One Orca message as a protocol event, or `None` if it is not one.

    `None` rather than a raise: an unrecognised type must not blind the
    coordinator to the rest of the batch it arrived in.
    """
    payload = message.get("payload") or {}
    task_id: str | None = payload.get("taskId")
    tid = str(task_id) if task_id else ""
    key: str = keys_by_task.get(tid, tid)
    handle: str = payload.get("dispatchId") or ""
    kind = message.get("type")

    if kind == "worker_done":
        return WorkerDone(
            key=key,
            handle=handle,
            delivery_id=delivery_id,
            kind="worker_done",
            outcome=str(payload.get("outcome") or "failed"),
            summary=str(message.get("body") or message.get("subject") or ""),
            files_modified=_tuple_of_str(payload.get("filesModified")),
            report_path=payload.get("reportPath"),
        )
    if kind == "question":
        return WorkerQuestion(
            key=key,
            handle=handle,
            delivery_id=delivery_id,
            kind="question",
            question=str(payload.get("question") or message.get("body") or ""),
            options=_tuple_of_str(payload.get("options")),
            # The message's own id: `reply --id <msg_id>` is the CLI, so
            # there is no second correlation scheme to keep in sync.
            reply_token=str(message.get("id") or ""),
        )
    if kind == "escalation":
        return Escalation(
            key=key,
            handle=handle,
            delivery_id=delivery_id,
            kind="escalation",
            subject=str(message.get("subject") or ""),
            body=str(message.get("body") or ""),
        )
    if kind == "heartbeat":
        return Heartbeat(
            key=key,
            handle=handle,
            delivery_id=delivery_id,
            kind="heartbeat",
            phase=payload.get("phase"),
        )
    return None
