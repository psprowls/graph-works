"""Translation between Orca's vocabulary and the protocol's closed one.

Also holds the pure pending-question join (design §1).

Pure: no process, no argv, no I/O. Everything here is a function of a dict
that `backend.py` already fetched, which is what lets `test_map.py` exercise
every branch off captured fixtures with no session at all.

The state table is close to identity — `EVENT_KINDS` and Orca's message types
are the same four words, and `WORKER_STATES` needs exactly one rename. That is
not a coincidence: the protocol was derived from this loop's shape.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

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


#: `inbox` has no paging cursor (Orca 1.4.211); a read that fills the limit
#: may be missing older messages, which is reported as `truncated`.
INBOX_LIMIT: int = 1000

#: `workerState` values that end a Dispatch. Any other value, including
#: `None` and an unknown future state, leaves its question open: dropping a
#: question strands a blocked worker, keeping one costs a reprint.
SETTLED_ORCA_STATES: frozenset[str] = frozenset({"succeeded", "failed", "stopped"})

_ASK_MARKER = "gw-ask: "


class OrcaPendingQuestion(TypedDict):
    message_id: str
    label: str
    dispatch_id: str | None
    task_id: str | None
    question: str
    options: list[str]
    ask_resource: str | None
    asked_at: str


class OrcaPendingQuestions(TypedDict):
    questions: list[OrcaPendingQuestion]
    truncated: bool
    warnings: list[str]


@dataclass(frozen=True)
class RunQuestion:
    """One `question` from a Run mailbox, read tolerantly. `warning` names a malformed payload."""

    message_id: str
    dispatch_id: str | None
    task_id: str | None
    question: str
    options: tuple[str, ...]
    ask_resource: str | None
    asked_at: str
    warning: str | None


def _message_rows(inbox: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = inbox.get("messages")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _loose_payload(value: object) -> dict[str, Any] | None:
    """A payload mapping, or `None` when it cannot be read. Unlike `normalize_message`, never raises:
    nothing is acked from this read, so keeping a question beats refusing the batch."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _ask_resource(question: str) -> str | None:
    lines = [line for line in question.splitlines() if line.strip()]
    if not lines or not lines[-1].startswith(_ASK_MARKER):
        return None
    return lines[-1][len(_ASK_MARKER) :].strip() or None


def _sequence(row: Mapping[str, Any]) -> int:
    value = row.get("sequence")
    return value if isinstance(value, int) else 0


def run_questions(run_id: str, inbox: Mapping[str, Any]) -> list[RunQuestion]:
    """Every `question` addressed to `run:<run_id>`, oldest first."""
    handle = f"run:{run_id}"
    rows = sorted(_message_rows(inbox), key=lambda row: (str(row.get("created_at") or ""), _sequence(row)))
    questions: list[RunQuestion] = []
    for row in rows:
        message_id = _text(row.get("id"))
        if row.get("type") != "question" or row.get("to_handle") != handle or message_id is None:
            continue
        payload = _loose_payload(row.get("payload"))
        warning = None
        if payload is None:
            warning = f"question {message_id} has an undecodable payload; kept with its body as the question"
            payload = {}
        sender = _text(row.get("from_handle")) or ""
        dispatch_id = sender.removeprefix("dispatch:") if sender.startswith("dispatch:") else None
        dispatch_id = dispatch_id or _text(payload.get("dispatchId"))
        question = _text(payload.get("question")) or _text(row.get("body")) or ""
        questions.append(
            RunQuestion(
                message_id=message_id,
                dispatch_id=dispatch_id,
                task_id=_text(payload.get("taskId")),
                question=question,
                options=_tuple_of_str(payload.get("options")),
                ask_resource=_ask_resource(question),
                asked_at=_text(row.get("created_at")) or "",
                warning=warning,
            )
        )
    return questions


def reply_threads(run_id: str, inbox: Mapping[str, Any]) -> set[str]:
    """Question ids answered by `reply --id`: messages sent from `run:<run_id>`, keyed by `thread_id`."""
    handle = f"run:{run_id}"
    return {
        thread
        for row in _message_rows(inbox)
        if row.get("from_handle") == handle and (thread := _text(row.get("thread_id"))) is not None
    }


def inbox_truncated(inbox: Mapping[str, Any], limit: int = INBOX_LIMIT) -> bool:
    count = inbox.get("count")
    return (isinstance(count, int) and count >= limit) or len(_message_rows(inbox)) >= limit


def question_labels(message_ids: Sequence[str]) -> dict[str, str]:
    """`q-` plus the id's last 4 characters, extended one at a time only where two collide."""
    labels: dict[str, str] = {}
    for message_id in message_ids:
        width = 4
        while width < len(message_id) and any(
            other != message_id and other[-width:] == message_id[-width:] for other in message_ids
        ):
            width += 1
        labels[message_id] = f"q-{message_id[-width:]}"
    return labels


def join_pending_questions(
    questions: Sequence[RunQuestion],
    *,
    worker_states: Mapping[str, str | None],
    replied: set[str],
    truncated: bool,
) -> OrcaPendingQuestions:
    """Pending = no reply in its thread and its Dispatch still live (design §1, finding 3)."""
    # Include answered and ended questions so their collision partners keep
    # the same labels as the Run advances, without persisting a label registry.
    labels = question_labels([q.message_id for q in questions])
    warnings = [q.warning for q in questions if q.warning is not None]
    kept: list[RunQuestion] = []
    for q in questions:
        if q.message_id in replied:
            continue
        if q.dispatch_id is not None:
            if q.dispatch_id not in worker_states:
                warnings.append(
                    f"question {q.message_id} left out: dispatch {q.dispatch_id} is not in this Run's worker-list"
                )
                continue
            state = worker_states[q.dispatch_id]
            if state in SETTLED_ORCA_STATES:
                warnings.append(f"question {q.message_id} left out: dispatch {q.dispatch_id} has ended ({state})")
                continue
        kept.append(q)
    return {
        "questions": [
            {
                "message_id": q.message_id,
                "label": labels[q.message_id],
                "dispatch_id": q.dispatch_id,
                "task_id": q.task_id,
                "question": q.question,
                "options": list(q.options),
                "ask_resource": q.ask_resource,
                "asked_at": q.asked_at,
            }
            for q in kept
        ],
        "truncated": truncated,
        "warnings": warnings,
    }
