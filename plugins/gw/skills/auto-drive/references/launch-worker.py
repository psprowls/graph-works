#!/usr/bin/env python3
"""Decode the auto-drive launch envelope and classify its settlement recovery without resolving rules."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

PREFIX = "GW_LAUNCH_V1 "
ENVELOPE_V1_FIELDS = {
    "version",
    "dispatch_key",
    "agent",
    "model",
    "reasoning_effort",
    "placement_argv",
}
ENVELOPE_V2_FIELDS = ENVELOPE_V1_FIELDS | {"mode", "worktree_path"}
ENVELOPE_FIELDS_BY_VERSION = {1: ENVELOPE_V1_FIELDS, 2: ENVELOPE_V2_FIELDS}
REJECTED_SUBJECT = "Rejected worker_done:"
RECORD_SCHEMA = "gw-orca-settlement"
CHECKPOINTS = (
    "inspection",
    "stop-requested",
    "stopped-verified",
    "release-requested",
    "released-verified",
    "completion-requested",
    "completed-verified",
)
RECORD_FIELDS = {
    "schema",
    "version",
    "run_id",
    "task_id",
    "dispatch_id",
    "dispatch_key",
    "work_path",
    "phase",
    "spec_sha256",
    "placement",
    "report",
    "evidence",
    "judgment",
    "stop_authority",
    "checkpoint",
    "mutations",
    "history",
    "unresolved",
}
IDENTITY_FIELDS = (
    "schema",
    "version",
    "run_id",
    "task_id",
    "dispatch_id",
    "dispatch_key",
    "work_path",
    "phase",
    "spec_sha256",
    "report",
)
SECRET_MARKERS = ("dcap_", "--dispatch-capability")
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")
VERIFIED_CHECKPOINTS = {"stopped-verified": 1, "released-verified": 2, "completed-verified": 3}
SETTLED_WORKER_STATES = {"stopped", "failed", "succeeded"}
SETTLED_DISPATCH_STATUSES = {"failed", "completed"}
AMBIGUOUS_ATTEMPT = "latest-attempt-ambiguous"


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def read_json(path: str) -> Any:
    try:
        with Path(path).open(encoding="utf-8", newline="") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Cannot read {path}: {error}")


def read_text_exact(path: str) -> str:
    try:
        with Path(path).open(encoding="utf-8", newline="") as stream:
            return stream.read()
    except OSError as error:
        fail(f"Cannot read {path}: {error}")


def nonblank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"Launch envelope {field} must be a nonblank string.")
    return value


def optional_nonblank(value: object, field: str) -> str | None:
    if value is None:
        return None
    return nonblank(value, field)


def validate_envelope(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        fail("Missing or malformed launch envelope; inspect recovery state before retrying.")
    fields = ENVELOPE_FIELDS_BY_VERSION.get(value["version"])
    if fields is None:
        fail("Unsupported launch envelope version; inspect recovery state before retrying.")
    if set(value) != fields:
        fail("Missing or malformed launch envelope; inspect recovery state before retrying.")
    envelope: dict[str, object] = {
        "version": value["version"],
        "dispatch_key": nonblank(value["dispatch_key"], "dispatch_key"),
        "agent": nonblank(value["agent"], "agent"),
        "model": optional_nonblank(value["model"], "model"),
        "reasoning_effort": optional_nonblank(value["reasoning_effort"], "reasoning_effort"),
        "placement_argv": value["placement_argv"],
    }
    if value["version"] == 2:
        envelope["mode"] = nonblank(value["mode"], "mode")
        envelope["worktree_path"] = optional_nonblank(value["worktree_path"], "worktree_path")
    placement = envelope["placement_argv"]
    if not isinstance(placement, list) or not all(isinstance(arg, str) for arg in placement):
        fail("Launch envelope placement_argv must be a list of strings.")
    if envelope["reasoning_effort"] is not None and envelope["model"] is None:
        fail("Set a model or clear reasoning_effort.")
    return envelope


def decode_spec(path: str) -> tuple[dict[str, object], str]:
    return decode_spec_text(read_text_exact(path))


def decode_spec_text(spec: str) -> tuple[dict[str, object], str]:
    first, separator, prompt = spec.partition("\n")
    if not separator or not first.startswith(PREFIX):
        fail("Missing or malformed launch envelope; inspect recovery state before retrying.")
    try:
        raw = json.loads(first.removeprefix(PREFIX))
    except json.JSONDecodeError:
        fail("Missing or malformed launch envelope; inspect recovery state before retrying.")
    return validate_envelope(raw), prompt


def decode(args: argparse.Namespace) -> None:
    envelope, prompt = decode_spec(args.spec)
    sys.stdout.write(json.dumps({"envelope": envelope, "prompt": prompt}, ensure_ascii=False))


_QUESTION_SECTION_RE = re.compile(r"^##\s+Question\s*$", re.MULTILINE)
_NEXT_SECTION_RE = re.compile(r"^##\s+\S", re.MULTILINE)


def _checkpoint_question(checkpoint_text: str) -> str:
    match = _QUESTION_SECTION_RE.search(checkpoint_text)
    if match is None:
        fail("Checkpoint has no '## Question' section; inspect it before resuming.")
    rest = checkpoint_text[match.end() :]
    next_heading = _NEXT_SECTION_RE.search(rest)
    body = rest[: next_heading.start()] if next_heading else rest
    question = body.strip()
    if not question:
        fail("Checkpoint's '## Question' section is empty; inspect it before resuming.")
    return question


def resume_spec(args: argparse.Namespace) -> None:
    """The frozen envelope, plus the composed `## Resume after park` text.

    The relaunch itself is `gw work dispatch --retry-of`, which re-places the
    worker through the placement engine and starts it on the original Task's
    frozen prompt, so nothing written here reaches `worker-start`. The resume
    text is delivered separately, by the coordinator's `send --to
    dispatch:<new id>` follow-up (auto-drive §2.6 step 7), which slices this
    file from its `## Resume after park` heading -- which is why that text is
    composed here, once, rather than in two places.
    """
    envelope, prompt = decode_spec(args.spec)
    checkpoint_text = read_text_exact(args.checkpoint)
    question = _checkpoint_question(checkpoint_text)
    resumed_prompt = (
        f"{prompt}\n\n## Resume after park\n\n"
        f"This Dispatch was parked mid-stage after a question went unanswered past its grace "
        f"period. The question was:\n\n{question}\n\nThe human's answer, now recorded:\n\n"
        f"{args.answer}\n\nContinue from the checkpoint's recorded remaining actions using this "
        f"answer; do not re-ask it."
    )
    payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(f"{PREFIX}{payload}\n{resumed_prompt}")


def check_receipt(request: dict[str, object], receipt: object) -> None:
    if not isinstance(receipt, dict):
        fail("Launch receipt is missing; worker preferences are unverified.")
    for side in ("requested", "effective"):
        actual = receipt.get(side)
        if not isinstance(actual, dict):
            fail(f"Launch receipt {side} block is missing; worker preferences are unverified.")
        expected = {
            "agent": request["agent"],
            "model": request["model"],
            "effort": request["reasoning_effort"],
        }
        for field, wanted in expected.items():
            if wanted is not None and actual.get(field) != wanted:
                fail(f"Launch receipt {side} {field} does not match the frozen request.")


def verified_success(task: dict[str, object], dispatch_id: object, *, orca: str) -> bool:
    """Re-earn settlement from the frozen spec and the durable worker receipt."""
    spec = task.get("spec")
    if not isinstance(spec, str) or task.get("spec_truncated"):
        return False
    if not isinstance(dispatch_id, str) or not dispatch_id.strip():
        return False
    try:
        envelope, _prompt = decode_spec_text(spec)
        if envelope["dispatch_key"] != task.get("task_title"):
            return False
        completed = subprocess.run(
            [orca, "orchestration", "worker-show", "--dispatch", dispatch_id, "--json"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return False
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return False
        result = payload.get("result")
        worker = result.get("worker") if isinstance(result, dict) else None
        options = worker.get("startOptions") if isinstance(worker, dict) else None
        receipt = options.get("launch") if isinstance(options, dict) else None
        check_receipt(envelope, receipt)
    except (SystemExit, OSError, json.JSONDecodeError):
        return False
    return True


def orca_json(orca: str, *argv: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [orca, "orchestration", *argv, "--json"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        fail(f"orca {argv[0]} could not run ({error}); checkpoint not verified.")
    if completed.returncode != 0:
        fail(f"orca {argv[0]} exited {completed.returncode}; checkpoint not verified.")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        fail(f"orca {argv[0]} returned invalid JSON; checkpoint not verified.")
    if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("result"), dict):
        fail(f"orca {argv[0]} returned an unsuccessful envelope; checkpoint not verified.")
    return payload["result"]


def worker_list_rows(orca: str, run_id: str) -> list[object]:
    """Read every worker-list page, refusing any gap in its Run-scoped chain."""
    workers: list[object] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    expected_total: int | None = None
    while True:
        argv = ["worker-list", "--run", run_id]
        if cursor is not None:
            argv += ["--cursor", cursor]
        result = orca_json(orca, *argv)
        page_workers = result.get("workers")
        if not isinstance(page_workers, list):
            fail("Orca readback lacks workers[]; checkpoint not verified.")
        scope = result.get("scope")
        if not isinstance(scope, dict) or scope.get("source") != "flag" or scope.get("run") != run_id:
            fail("Orca worker-list returned an inconsistent run scope; checkpoint not verified.")
        page = result.get("page")
        if not isinstance(page, dict) or type(page.get("hasMore")) is not bool:
            fail("Orca worker-list returned malformed pagination; checkpoint not verified.")
        total = page.get("total")
        if type(total) is not int or total < 0:
            fail("Orca worker-list returned an invalid total; checkpoint not verified.")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            fail("Orca worker-list total changed between pages; checkpoint not verified.")
        if page["hasMore"] and not page_workers:
            fail("Orca worker-list returned an empty nonterminal page; checkpoint not verified.")
        workers.extend(page_workers)
        next_cursor = page.get("nextCursor")
        if not page["hasMore"]:
            if next_cursor is not None:
                fail("Orca worker-list returned a malformed cursor; checkpoint not verified.")
            if len(workers) != expected_total:
                fail("Orca worker-list returned incomplete pagination; checkpoint not verified.")
            return workers
        if next_cursor is None or next_cursor == "":
            fail("Orca worker-list returned a missing cursor; checkpoint not verified.")
        if not isinstance(next_cursor, str) or not next_cursor.strip():
            fail("Orca worker-list returned a malformed cursor; checkpoint not verified.")
        if next_cursor in seen_cursors:
            fail("Orca worker-list returned a repeated cursor; checkpoint not verified.")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def created_instant(value: object) -> datetime | None:
    """Orca's `createdAt`, in UTC; the zone-less `YYYY-MM-DD HH:MM:SS` form is UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        instant = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def latest_attempt(workers: list[object], task_id: object, *, orca: str) -> tuple[dict[str, Any] | None, str | None]:
    """A Task's newest Dispatch row, and why it cannot be trusted, if it cannot.

    worker-list is documented newest first, so the first same-Task row is the
    candidate. When a Task has retries, worker-show's `createdAt` and
    `retryOfDispatchId` must agree with that order; disagreement, an
    unbreakable tie or an unreadable attempt keeps the candidate but reports
    `latest-attempt-ambiguous` rather than guessing.
    """
    attempts = [row for row in workers if isinstance(row, dict) and row.get("taskId") == task_id]
    if len(attempts) <= 1:
        return (attempts[0] if attempts else None), None
    candidate = attempts[0]
    shown: dict[object, tuple[datetime | None, object]] = {}
    for row in attempts:
        try:
            dispatch = orca_json(orca, "worker-show", "--dispatch", str(row.get("dispatchId"))).get("dispatch")
        except SystemExit:
            return candidate, AMBIGUOUS_ATTEMPT
        if not isinstance(dispatch, dict):
            return candidate, AMBIGUOUS_ATTEMPT
        shown[row.get("dispatchId")] = (
            created_instant(dispatch.get("createdAt")),
            dispatch.get("retryOfDispatchId"),
        )
    retried = {retry_of for _created, retry_of in shown.values() if retry_of is not None}
    newest, _ = shown[candidate.get("dispatchId")]
    if newest is None or candidate.get("dispatchId") in retried:
        return candidate, AMBIGUOUS_ATTEMPT
    for dispatch_id, (created, _retry_of) in shown.items():
        if dispatch_id == candidate.get("dispatchId"):
            continue
        if created is None or created > newest or (created == newest and dispatch_id not in retried):
            return candidate, AMBIGUOUS_ATTEMPT
    return candidate, None


def liveness_gap(worker: dict[str, Any]) -> str | None:
    """Recovery needs positive fleet exit; saved authority and PTY status cannot supply it."""
    projection = worker.get("projection")
    liveness = projection.get("liveness") if isinstance(projection, dict) else None
    verdict = liveness.get("verdict") if isinstance(liveness, dict) else None
    if verdict == "exited":
        return None
    return "liveness-live" if verdict == "live" else "liveness-unverifiable"


def mutation_identity_gap(record: dict[str, Any]) -> str | None:
    """An intent with no original ID stays unresolved until a later receipt identifies it."""
    latest = {entry["action"]: entry for entry in record["mutations"]}
    for action, entry in latest.items():
        if entry["request_id"] is None:
            return f"original request identity unknown: {action}"
    return None


def settlement_gap(
    record: dict[str, Any],
    task: object,
    latest: tuple[dict[str, Any] | None, str | None],
    level: int,
    *,
    rerouted: bool = False,
) -> str | None:
    """Why fresh Task/worker rows do not prove a verified checkpoint level, or None."""
    worker, ambiguity = latest
    if worker is None:
        return "worker-missing"
    if ambiguity is not None:
        return ambiguity
    if worker.get("dispatchId") != record["dispatch_id"]:
        return "newer-attempt"
    if worker.get("runId") != record["run_id"]:
        return "run-mismatch"
    if not isinstance(task, dict) or task.get("id") != record["task_id"]:
        return "task-missing"
    if task.get("task_title") != record["dispatch_key"]:
        return "dispatch-key-mismatch"
    spec = task.get("spec")
    if (
        not isinstance(spec, str)
        or task.get("spec_truncated")
        or hashlib.sha256(spec.encode("utf-8")).hexdigest() != record["spec_sha256"]
    ):
        return "spec-mismatch"
    if worker.get("workerState") == "outcome_unknown" or worker.get("dispatchStatus") == ("outcome_unknown"):
        return "outcome-unknown"
    if (
        worker.get("workerState") not in SETTLED_WORKER_STATES
        or worker.get("dispatchStatus") not in SETTLED_DISPATCH_STATUSES
    ):
        return "worker-not-settled"
    gap = liveness_gap(worker)
    if gap is not None:
        return gap
    if level >= 2:
        resource = worker.get("resource")
        if (
            worker.get("terminalState") != "released"
            or not isinstance(resource, dict)
            or resource.get("releaseState") != "released"
        ):
            return "terminal-not-released"
    if level >= 3 and task.get("status") != "completed" and not (rerouted and task.get("status") == "blocked"):
        return "task-not-completed"
    return None


def evidence_gap(record: dict[str, Any]) -> str | None:
    """Re-verify recorded stage evidence instead of trusting the record's judgment."""
    for index, item in enumerate(record["evidence"]):
        if item["kind"] == "file":
            try:
                digest = hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
            except OSError:
                return f"evidence[{index}] file unreadable"
            if digest != item["sha256"]:
                return f"evidence[{index}] file changed"
        elif item["kind"] == "commit":
            try:
                completed = subprocess.run(
                    ["git", "-C", item["repo"], "cat-file", "-e", item["sha"] + "^{commit}"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except OSError:
                return f"evidence[{index}] commit unverifiable"
            if completed.returncode != 0:
                return f"evidence[{index}] commit missing"
        elif item["exit"] != 0:
            return f"evidence[{index}] validation failed"
    return None


def verify_checkpoint(record: dict[str, Any], orca: str) -> None:
    level = VERIFIED_CHECKPOINTS.get(record["checkpoint"])
    if level is None:
        return
    tasks = orca_json(orca, "task-list", "--run", record["run_id"]).get("tasks")
    if not isinstance(tasks, list):
        fail("Orca readback lacks tasks[]; checkpoint not verified.")
    workers = worker_list_rows(orca, record["run_id"])
    task = next(
        (row for row in tasks if isinstance(row, dict) and row.get("id") == record["task_id"]),
        None,
    )
    gap = (
        mutation_identity_gap(record)
        or settlement_gap(record, task, latest_attempt(workers, record["task_id"], orca=orca), level)
        or evidence_gap(record)
    )
    if gap is None and level == 3:
        if not verified_success(task, record["dispatch_id"], orca=orca):
            gap = "launch-proof-unverified"
    if gap is not None:
        fail(f"Checkpoint {record['checkpoint']} is not verified by current Orca state: {gap}.")


def load_records(paths: list[str]) -> dict[str, list[dict[str, Any]]]:
    by_task: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        raw = read_json(path)
        task_id = raw.get("task_id") if isinstance(raw, dict) else None
        if not isinstance(task_id, str) or not task_id.strip():
            fail(f"Recovery record {path} carries no task_id; inspect it before restarting.")
        if Path(path).name != f"{raw.get('dispatch_id')}.json":
            fail(f"Recovery record {path} is not named <dispatch_id>.json; inspect it before restarting.")
        by_task.setdefault(task_id, []).append(raw)
    return by_task


DISPATCH_RECORD_SCHEMA = "gw-orca-dispatch"


def load_dispatch_records(paths: list[str]) -> dict[str, dict[str, Any]]:
    """Map superseded Task IDs to reroutes, retaining identity for recovery checks."""
    superseded: dict[str, dict[str, Any]] = {}
    for path in paths:
        raw = read_json(path)
        if (
            not isinstance(raw, dict)
            or raw.get("schema") != DISPATCH_RECORD_SCHEMA
            or type(raw.get("version")) is not int
            or raw["version"] != 1
            or not isinstance(raw.get("reroutes"), list)
        ):
            fail(f"Dispatch record {path} is malformed; inspect it before restarting.")
        for entry in raw["reroutes"]:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("superseded_task_id"), str)
                or not entry["superseded_task_id"]
                or not isinstance(entry.get("reason"), str)
                or not isinstance(entry.get("at"), str)
            ):
                fail(f"Dispatch record {path} has a malformed reroute; inspect it before restarting.")
            superseded[entry["superseded_task_id"]] = {
                "reason": entry["reason"],
                "at": entry["at"],
                "dispatch_id": entry.get("superseded_dispatch_id"),
                **{field: raw.get(field) for field in ("key", "run_id", "work_path", "phase")},
            }
    return superseded


def validate_recovery_snapshot(payload: object, workers: list[object]) -> None:
    """Refuse incomplete pagination metadata when records make the snapshot authoritative."""
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict) or "page" not in result:
        return
    page = result["page"]
    if not isinstance(page, dict) or type(page.get("hasMore")) is not bool:
        fail("Worker-list recovery snapshot has malformed pagination.")
    total = page.get("total")
    if type(total) is not int or total < 0:
        fail("Worker-list recovery snapshot has an invalid total.")
    if page["hasMore"]:
        fail("Worker-list recovery snapshot is incomplete.")
    if page.get("nextCursor") is not None:
        fail("Worker-list recovery snapshot has a malformed cursor.")
    if len(workers) != total:
        fail("Worker-list recovery snapshot has an incomplete worker count.")


def reconcile_records(
    task: dict[str, object],
    attempt: tuple[dict[str, Any] | None, str | None],
    candidates: list[dict[str, Any]],
    *,
    base: str,
    orca: str,
    reroute: dict[str, Any] | None = None,
) -> tuple[str, dict[str, object]]:
    """Let a saved recovery record refine, never override, fresh Orca evidence."""
    worker = attempt[0]
    latest = worker.get("dispatchId") if worker is not None else None
    current = [record for record in candidates if record.get("dispatch_id") == latest]
    record: dict[str, Any] | None = None
    problem = "record-not-latest-attempt"
    if len(current) > 1:
        problem = "duplicate-records"
    elif current:
        try:
            record = validate_record(current[0])
        except SystemExit as error:
            problem = str(error)
    checkpoint = record["checkpoint"] if record is not None else None
    if base == "live":
        return base, {"checkpoint": checkpoint, "reason": "current-evidence-wins"}
    # Accepted completion is independent of recovery: it can owe release while
    # the reporting agent still idles live. A workerState/launch receipt alone
    # (the legacy shortcut) does not establish that Task AND Dispatch accepted it.
    if (
        base == "settled"
        and worker is not None
        and task.get("status") == "completed"
        and worker.get("dispatchStatus") == "completed"
        and isinstance(task.get("run_id"), str)
        and worker.get("runId") == task["run_id"]
    ):
        return "settled", {"checkpoint": checkpoint, "reason": "current-accepted-success"}
    if record is None:
        return "recovery-inspection", {"checkpoint": None, "reason": problem}
    if worker is not None:
        gap = liveness_gap(worker)
        if gap is not None:
            action = "live" if gap == "liveness-live" else "recovery-inspection"
            return action, {"checkpoint": checkpoint, "reason": gap}
    if record["unresolved"] is not None:
        return "recovery-inspection", {
            "checkpoint": checkpoint,
            "reason": record["unresolved"],
        }
    if checkpoint != "completed-verified":
        return "recovery-inspection", {
            "checkpoint": checkpoint,
            "reason": "recovery-incomplete",
        }
    # A completed recovery remains historical proof after durable reroute
    # blocks its Task. Only a matching reroute explains that status change;
    # every other settlement, identity, evidence and launch check still runs.
    rerouted = reroute is not None and all(
        reroute.get(source) == record[target]
        for source, target in (
            ("key", "dispatch_key"),
            ("run_id", "run_id"),
            ("dispatch_id", "dispatch_id"),
            ("work_path", "work_path"),
            ("phase", "phase"),
        )
    )
    gap = settlement_gap(record, task, attempt, 3, rerouted=rerouted) or evidence_gap(record)
    if gap is None and not verified_success(task, latest, orca=orca):
        gap = "launch-proof-unverified"
    if gap is not None:
        return "recovery-inspection", {"checkpoint": checkpoint, "reason": gap}
    return "recovered-settled", {"checkpoint": checkpoint, "reason": "verified"}


_FRONTMATTER_RE = re.compile(r"\A﻿?---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
DISPLAY_SEPARATOR = " · "


def display_stage(task: dict[str, object]) -> tuple[str, str] | None:
    """The `(work path, phase)` a Task's display name carries, or None.

    A dispatch key is one-way -- nothing recovers a path by parsing one. The
    `--display-name "<work-path> · <phase>"` every task-create writes is the
    only durable carrier, so this is the single place that split lives.
    """
    name = task.get("display_name")
    if not isinstance(name, str):
        return None
    path, separator, phase = name.partition(DISPLAY_SEPARATOR)
    if not separator or not path.strip() or not phase.strip():
        return None
    return path.strip(), phase.strip()


def checkpoint_stage(path: str) -> tuple[str, str]:
    """The `(item, phase)` a park checkpoint's frontmatter names."""
    match = _FRONTMATTER_RE.match(read_text_exact(path))
    if match is None:
        fail(f"Checkpoint {path} has no frontmatter block; inspect it before classifying.")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if line[:1] in {" ", "\t", "#", "-", ""}:
            continue
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip().strip("'\"")
    item, phase = fields.get("item", ""), fields.get("phase", "")
    if not item or not phase:
        fail(f"Checkpoint {path} must name both item: and phase: in its frontmatter.")
    return item, phase


def classify_restart(args: argparse.Namespace) -> None:
    task_payload = read_json(args.tasks)
    worker_payload = read_json(args.workers)
    tasks = envelope_rows(task_payload, "tasks", "Task-list")
    workers = envelope_rows(worker_payload, "workers", "Worker-list")
    records = load_records(getattr(args, "recovery_record", None) or [])
    parked_stages = {checkpoint_stage(path) for path in getattr(args, "checkpoint", None) or []}
    rerouted = load_dispatch_records(getattr(args, "dispatch_record", None) or [])
    if records or rerouted:
        validate_recovery_snapshot(worker_payload, workers)
    rows: list[dict[str, object]] = []
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            fail("Every task must carry an id.")
        task_id = task["id"]
        attempt = latest_attempt(workers, task_id, orca=args.orca)
        worker = attempt[0]
        dispatch_id = worker.get("dispatchId") if worker is not None else None
        if worker is None:
            action = "deliberate-skip" if task.get("status") == "blocked" else "recovery-inspection"
        else:
            state = worker.get("workerState")
            dispatch_status = worker.get("dispatchStatus")
            if state == "outcome_unknown" or dispatch_status == "outcome_unknown":
                action = "recovery-inspection"
            elif state in {"ready", "running"}:
                action = "live"
            elif state == "succeeded":
                action = "settled" if verified_success(task, dispatch_id, orca=args.orca) else "recovery-inspection"
            elif task.get("status") == "blocked" and state in {"failed", "stopped"}:
                action = "deliberate-skip"
            else:
                action = "recovery-inspection"
        # A parked Task and a deliberately skipped one are bit-for-bit
        # identical in Orca (`blocked`, latest attempt `stopped`/`failed`):
        # §2.5.2 writes no marker of its own. The checkpoint on disk is the
        # distinguishing signal, joined by the Task's display name because a
        # dispatch key is not reversible to a path.
        if action == "deliberate-skip" and display_stage(task) in parked_stages:
            action = "parked"
        reroute = rerouted.get(task_id)
        if reroute is not None:
            if action == "live":
                action = "rerouted-but-live"
            elif action in {"deliberate-skip", "parked"}:
                action = "rerouted"
        row: dict[str, object] = {
            "task_id": task_id,
            "task_title": task.get("task_title"),
            "display_name": task.get("display_name"),
            "dispatch_id": dispatch_id,
            "action": action,
        }
        if action == "rerouted-but-live":
            row["action"] = "recovery-inspection"
            row["recovery"] = {"checkpoint": None, "reason": "rerouted-but-live"}
        if attempt[1] is not None:
            row["action"] = "recovery-inspection"
            row["recovery"] = {"checkpoint": None, "reason": attempt[1]}
        elif task_id in records:
            row["action"], row["recovery"] = reconcile_records(
                task,
                attempt,
                records[task_id],
                base=action,
                orca=args.orca,
                reroute=reroute,
            )
        if reroute is not None and row["action"] == "live":
            row["action"] = "recovery-inspection"
            row["recovery"] = {"checkpoint": None, "reason": "rerouted-but-live"}
        elif reroute is not None and row["action"] in {"settled", "recovered-settled"}:
            row["action"] = "rerouted"
        if row["action"] == "rerouted":
            row["reroute"] = {field: reroute[field] for field in ("reason", "at")}
        rows.append(row)
    run_id = task_payload["result"].get("runId")
    listed = {row["task_id"] for row in rows}
    for task_id, orphaned in records.items():
        if task_id in listed:
            continue
        match = next((record for record in orphaned if record.get("run_id") == run_id), None)
        if match is not None:
            rows.append(
                {
                    "task_id": task_id,
                    "task_title": None,
                    "display_name": None,
                    "dispatch_id": match.get("dispatch_id"),
                    "action": "recovery-inspection",
                    "recovery": {"checkpoint": None, "reason": "record-task-not-in-run"},
                }
            )
    json.dump(rows, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def envelope_rows(payload: object, field: str, label: str) -> list[object]:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        fail(f"{label} payload must be a successful Orca JSON envelope.")
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get(field), list):
        fail(f"{label} payload result must contain {field}[].")
    return result[field]


def report_branch(message: object) -> dict[str, object]:
    """Read Orca's rejection marker before the reported outcome (§4.1)."""
    if not isinstance(message, dict) or message.get("type") != "worker_done":
        fail("classify-report accepts exactly one worker_done message object.")
    raw = message.get("payload")
    payload: object = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
    fields = payload if isinstance(payload, dict) else {}
    row: dict[str, object] = {"message_id": message.get("id")}
    for source, target in (("taskId", "task_id"), ("dispatchId", "dispatch_id"), ("outcome", "outcome")):
        value = fields.get(source)
        row[target] = value if isinstance(value, str) and value.strip() else None
    row["rejection"] = None
    if "_orcaLifecycleRejection" in fields:
        marker = fields["_orcaLifecycleRejection"]
        if isinstance(marker, dict) and isinstance(marker.get("code"), str) and isinstance(marker.get("reason"), str):
            row["rejection"] = {"code": marker["code"], "reason": marker["reason"]}
            return {**row, "branch": "claimed-unconfirmed", "reason": "rejection-marker"}
        return {**row, "branch": "claimed-unconfirmed", "reason": "malformed-rejection-marker"}
    subject = message.get("subject")
    if isinstance(subject, str) and subject.startswith(REJECTED_SUBJECT):
        return {**row, "branch": "claimed-unconfirmed", "reason": "legacy-rejection-wrapper"}
    if not isinstance(payload, dict):
        return {**row, "branch": "claimed-unconfirmed", "reason": "payload-unreadable"}
    if row["task_id"] is None or row["dispatch_id"] is None:
        return {**row, "branch": "claimed-unconfirmed", "reason": "identity-missing"}
    if row["outcome"] == "succeeded":
        return {**row, "branch": "accepted-success", "reason": "outcome-succeeded"}
    if row["outcome"] == "failed":
        return {**row, "branch": "accepted-failure", "reason": "outcome-failed"}
    return {**row, "branch": "claimed-unconfirmed", "reason": "outcome-missing"}


def classify_report(args: argparse.Namespace) -> None:
    json.dump(report_branch(read_json(args.message)), sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def attend_target(task: dict[str, object]) -> tuple[str | None, str | None, str | None]:
    """`(mode, worktree path, skip reason)` from a Task's frozen envelope.

    Never fails: an unreadable envelope is a skip reason, so one bad row cannot
    stop the card reconcile for every other worktree.
    """
    spec = task.get("spec")
    if not isinstance(spec, str) or task.get("spec_truncated"):
        return None, None, "spec-unreadable"
    try:
        envelope, _prompt = decode_spec_text(spec)
    except SystemExit:
        return None, None, "envelope-undecodable"
    mode = envelope.get("mode")
    if mode is None:
        return None, None, "mode-unknown"
    if mode != "attend":
        return str(mode), None, None
    path = envelope.get("worktree_path")
    if not isinstance(path, str):
        return "attend", None, "worktree-unknown"
    return "attend", path, None


def attend_cards_result(tasks: list[object], rows: list[object]) -> dict[str, list[Any]]:
    """In-review worktrees and release candidates, derived from §2.1 each cycle (§2.5.3)."""
    live_tasks = {
        row["task_id"]
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("task_id"), str) and row.get("action") == "live"
    }
    live: set[str] = set()
    used: set[str] = set()
    skipped: list[dict[str, object]] = []
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            skipped.append({"task_id": None, "reason": "task-unreadable"})
            continue
        mode, path, reason = attend_target(task)
        if reason is not None:
            skipped.append({"task_id": task["id"], "reason": reason})
            continue
        if mode != "attend" or path is None:
            continue
        used.add(path)
        if task["id"] in live_tasks:
            live.add(path)
    return {
        "set_in_review": sorted(live),
        "release_candidates": sorted(used - live),
        "skipped": sorted(skipped, key=lambda entry: (str(entry["task_id"]), str(entry["reason"]))),
    }


def attend_cards(args: argparse.Namespace) -> None:
    tasks = envelope_rows(read_json(args.tasks), "tasks", "Task-list")
    rows = read_json(args.classification)
    if not isinstance(rows, list):
        fail("attend-cards --classification must be classify-restart's JSON list.")
    json.dump(attend_cards_result(tasks, rows), sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


LIFECYCLE_RELEASE_DONE = {"released", "already_released"}
LIFECYCLE_RELEASE_FOLLOW = {"release_pending", "release_unknown"}
LIFECYCLE_STOP_AUTHORITIES = {"park", "user-authorized", "exit-evidence"}
LIFECYCLE_SETTLED_STATES = {"succeeded", "failed", "stopped", "abandoned"}


def lifecycle_action(op: str, payload: object, *, mode: str | None, authority: str | None) -> dict[str, object]:
    """Classify one lifecycle receipt without performing any lifecycle action.

    Unknown or malformed decision fields require inspection. A result state is
    authoritative; only absent states can fall back to uncertain release errors.
    """
    result = payload.get("result") if isinstance(payload, dict) else None
    fields = result if isinstance(result, dict) else {}
    state = fields.get("state")
    reason = fields.get("reason")
    row: dict[str, object] = {
        "op": op,
        "state": state if isinstance(state, str) else None,
        "reason": reason if isinstance(reason, str) else None,
        "action": "inspect",
    }
    if (
        not isinstance(payload, dict)
        or ("result" in payload and not isinstance(result, dict))
        or ("state" in fields and not isinstance(state, str))
        or (reason is not None and not isinstance(reason, str))
        or (authority is not None and not isinstance(authority, str))
        or (mode is not None and not isinstance(mode, str))
    ):
        return row
    if "state" not in fields and op == "release":
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(code, str) and code in LIFECYCLE_RELEASE_FOLLOW:
            state = code
            row["state"] = state
    if not isinstance(state, str):
        return row
    if op == "release":
        if state in LIFECYCLE_RELEASE_DONE:
            row["action"] = "done"
        elif state == "retained":
            row["action"] = "close-terminal" if reason == "user_takeover" and mode == "attend" else "report-retained"
        elif state in LIFECYCLE_RELEASE_FOLLOW:
            row["action"] = "follow-receipt"
    elif op == "stop":
        if state == "stopped" or (fields.get("alreadySettled") is True and state in LIFECYCLE_SETTLED_STATES):
            row["action"] = "done"
        elif state == "stop_unknown":
            row["action"] = "abandon-then-close" if authority in LIFECYCLE_STOP_AUTHORITIES else "abandon-report"
    return row


def classify_lifecycle(args: argparse.Namespace) -> None:
    row = lifecycle_action(args.op, read_json(args.result), mode=args.mode, authority=args.authority)
    json.dump(row, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def record_fail(message: str) -> NoReturn:
    fail(f"Invalid recovery record: {message}")


def record_text(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        record_fail(f"{field} must be a nonblank string.")
    return value


def validate_evidence(item: object, index: int) -> None:
    label = f"evidence[{index}]"
    if not isinstance(item, dict):
        record_fail(f"{label} must be an object.")
    kind = item.get("kind")
    if kind == "file" and set(item) == {"kind", "path", "sha256"}:
        if not Path(str(record_text(item["path"], f"{label}.path"))).is_absolute():
            record_fail(f"{label}.path must be absolute.")
        if not isinstance(item["sha256"], str) or not HEX64.fullmatch(item["sha256"]):
            record_fail(f"{label}.sha256 must be 64 lowercase hex characters.")
    elif kind == "commit" and set(item) == {"kind", "repo", "sha"}:
        if not Path(str(record_text(item["repo"], f"{label}.repo"))).is_absolute():
            record_fail(f"{label}.repo must be absolute.")
        if not isinstance(item["sha"], str) or not HEX40.fullmatch(item["sha"]):
            record_fail(f"{label}.sha must be a full 40-character commit id.")
    elif kind == "validation" and set(item) == {"kind", "command", "exit", "receipt"}:
        record_text(item["command"], f"{label}.command")
        record_text(item["receipt"], f"{label}.receipt")
        if type(item["exit"]) is not int:
            record_fail(f"{label}.exit must be an integer.")
    else:
        record_fail(f"{label} must be a file, commit or validation item.")


def contains_secret(value: object) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in SECRET_MARKERS)
    if isinstance(value, dict):
        return any(contains_secret(key) or contains_secret(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_secret(item) for item in value)
    return False


def validate_record(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        record_fail("record must be a JSON object.")
    if set(value) != RECORD_FIELDS:
        record_fail("fields must be exactly " + ", ".join(sorted(RECORD_FIELDS)) + ".")
    if value["schema"] != RECORD_SCHEMA or type(value["version"]) is not int or value["version"] != 1:
        record_fail("unsupported schema or version.")
    for field in ("run_id", "task_id", "dispatch_id", "dispatch_key", "work_path", "phase"):
        record_text(value[field], field)
    if not isinstance(value["spec_sha256"], str) or not HEX64.fullmatch(value["spec_sha256"]):
        record_fail("spec_sha256 must be 64 lowercase hex characters.")
    placement = value["placement"]
    if not isinstance(placement, dict) or set(placement) != {"worktree", "branch"}:
        record_fail("placement must carry exactly worktree and branch.")
    record_text(placement["worktree"], "placement.worktree")
    record_text(placement["branch"], "placement.branch", optional=True)
    report = value["report"]
    if not isinstance(report, dict) or set(report) != {"message_id", "outcome", "rejection", "reason"}:
        record_fail("report must carry exactly message_id, outcome, rejection and reason.")
    record_text(report["message_id"], "report.message_id")
    if report["outcome"] not in {"succeeded", "failed", None}:
        record_fail("report.outcome must be succeeded, failed or null.")
    rejection = report["rejection"]
    if rejection is not None and (
        not isinstance(rejection, dict)
        or set(rejection) != {"code", "reason"}
        or not all(isinstance(rejection[key], str) for key in ("code", "reason"))
    ):
        record_fail("report.rejection must be null or {code, reason}.")
    record_text(report["reason"], "report.reason")
    evidence = value["evidence"]
    if not isinstance(evidence, list):
        record_fail("evidence must be a list.")
    for index, item in enumerate(evidence):
        validate_evidence(item, index)
    if value["judgment"] not in {"succeeded", "unestablished"}:
        record_fail("judgment must be succeeded or unestablished.")
    authority = value["stop_authority"]
    if authority is not None:
        if (
            not isinstance(authority, dict)
            or set(authority) != {"kind", "source", "at"}
            or authority["kind"] not in {"exit-evidence", "user-authorized", "already-settled"}
        ):
            record_fail("stop_authority must be null or {kind, source, at}.")
        record_text(authority["source"], "stop_authority.source")
        record_text(authority["at"], "stop_authority.at")
    if value["checkpoint"] not in CHECKPOINTS:
        record_fail("checkpoint is not a known recovery checkpoint.")
    if not isinstance(value["mutations"], list):
        record_fail("mutations must be a list.")
    for index, mutation in enumerate(value["mutations"]):
        if (
            not isinstance(mutation, dict)
            or set(mutation) != {"action", "request_id", "receipt", "at"}
            or mutation["action"]
            not in {"worker-stop", "worker-release", "task-update", "worker-abandon", "terminal-close"}
        ):
            record_fail(f"mutations[{index}] must be {{action, request_id, receipt, at}}.")
        record_text(mutation["request_id"], f"mutations[{index}].request_id", optional=True)
        record_text(mutation["receipt"], f"mutations[{index}].receipt", optional=True)
        record_text(mutation["at"], f"mutations[{index}].at")
    if not isinstance(value["history"], list):
        record_fail("history must be a list.")
    for index, entry in enumerate(value["history"]):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"checkpoint", "at", "note"}
            or entry["checkpoint"] not in CHECKPOINTS
        ):
            record_fail(f"history[{index}] must be {{checkpoint, at, note}}.")
        record_text(entry["at"], f"history[{index}].at")
        record_text(entry["note"], f"history[{index}].note")
    record_text(value["unresolved"], "unresolved", optional=True)
    if CHECKPOINTS.index(value["checkpoint"]) > 0:
        if value["judgment"] != "succeeded":
            record_fail("checkpoints past inspection require judgment succeeded.")
        if not any(item["kind"] in {"file", "commit"} for item in evidence):
            record_fail("checkpoints past inspection require file or commit evidence.")
        if any(item["kind"] == "validation" and item["exit"] != 0 for item in evidence):
            record_fail("checkpoints past inspection require passing validation evidence.")
        if authority is None:
            record_fail("checkpoints past inspection require stop_authority.")
    gap = mutation_identity_gap(value)
    if gap is not None and value["unresolved"] is None:
        record_fail(gap + "; preserve unresolved inspection.")
    if contains_secret(value):
        record_fail("records must not store Dispatch capabilities.")
    return value


def replace_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def write_record(args: argparse.Namespace) -> None:
    record = validate_record(read_json(args.record))
    target = Path(args.path)
    if target.name != f"{record['dispatch_id']}.json":
        fail("Recovery record files are named <dispatch_id>.json.")
    previous = validate_record(read_json(str(target))) if target.exists() else None
    if previous is None:
        if record["checkpoint"] != "inspection":
            fail("A new recovery record must start at inspection.")
        if not record["history"] or record["history"][-1]["checkpoint"] != record["checkpoint"]:
            fail("A new recovery record history must end at the current checkpoint.")
    else:
        for field in IDENTITY_FIELDS:
            if record[field] != previous[field]:
                fail(f"Recovery record {field} is immutable.")
        if CHECKPOINTS.index(record["checkpoint"]) < CHECKPOINTS.index(previous["checkpoint"]):
            fail("Recovery checkpoint cannot move backwards.")
        for field in ("history", "mutations"):
            if record[field][: len(previous[field])] != previous[field]:
                fail(f"Recovery record {field} is append-only.")
        grew = len(record["history"]) > len(previous["history"])
        if record["evidence"] != previous["evidence"] and not (
            grew and record["history"][-1]["note"].startswith("reattested:")
        ):
            fail("Recovery evidence changes only with a new history note starting 'reattested:'.")
        changed = previous != record
        if changed and not grew:
            fail("A changed record must append history.")
        if changed and record["history"][-1]["checkpoint"] != record["checkpoint"]:
            fail("A changed record history must end at the current checkpoint.")
    # A refusal annotates historical progress, not a new attestation. Permit
    # only appended history and a nonempty unresolved reason through this path;
    # every other field (including evidence, judgment, authority and placement)
    # stays byte-for-byte equivalent as JSON data.
    annotation = (
        previous is not None
        and record["unresolved"] is not None
        and len(record["history"]) > len(previous["history"])
        and all(record[field] == previous[field] for field in RECORD_FIELDS - {"history", "unresolved"})
    )
    if not annotation:
        if previous is not None:
            identity_gap = mutation_identity_gap(record)
            if record["checkpoint"] != previous["checkpoint"] and identity_gap is not None:
                # Entering a requested checkpoint journals a new intent; leaving
                # an existing unknown-ID operation cannot invent progress.
                if mutation_identity_gap(previous) is not None:
                    fail(identity_gap)
            if previous["unresolved"] is not None and (
                record["unresolved"] is None
                or any(
                    record[field] != previous[field] for field in RECORD_FIELDS - {"history", "mutations", "unresolved"}
                )
            ):
                # Requested checkpoints do not themselves verify. Clearing a
                # refusal or advancing still must re-earn historical proof.
                # Merely appending an original receipt at the same requested
                # checkpoint with unresolved retained needs no fresh proof.
                historical = [
                    name
                    for name in VERIFIED_CHECKPOINTS
                    if CHECKPOINTS.index(name) <= CHECKPOINTS.index(previous["checkpoint"])
                ]
                if historical:
                    verify_checkpoint({**record, "checkpoint": historical[-1]}, args.orca)
        verify_checkpoint(record, args.orca)
    changed = previous != record
    if changed:
        replace_text(target, json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    json.dump(
        {"path": str(target), "checkpoint": record["checkpoint"], "changed": changed},
        sys.stdout,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")


def spec_hash(args: argparse.Namespace) -> None:
    tasks = envelope_rows(read_json(args.tasks), "tasks", "Task-list")
    task = next((row for row in tasks if isinstance(row, dict) and row.get("id") == args.task), None)
    if task is None:
        fail(f"Task {args.task} is not in the task list.")
    spec = task.get("spec")
    if not isinstance(spec, str) or task.get("spec_truncated"):
        fail("Task spec is missing or truncated; read the full task-list before recording.")
    envelope, _prompt = decode_spec_text(spec)
    if envelope["dispatch_key"] != task.get("task_title"):
        fail("Launch envelope dispatch_key does not match the task title; inspect recovery state.")
    sys.stdout.write(hashlib.sha256(spec.encode("utf-8")).hexdigest() + "\n")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    decode_parser = commands.add_parser("decode")
    decode_parser.add_argument("--spec", required=True)
    decode_parser.set_defaults(func=decode)
    resume_parser = commands.add_parser("resume-spec")
    resume_parser.add_argument("--spec", required=True)
    resume_parser.add_argument("--checkpoint", required=True)
    resume_parser.add_argument("--answer", required=True)
    resume_parser.set_defaults(func=resume_spec)
    classify_parser = commands.add_parser("classify-restart")
    classify_parser.add_argument("--orca", default="orca")
    classify_parser.add_argument("--tasks", required=True)
    classify_parser.add_argument("--workers", required=True)
    classify_parser.add_argument("--recovery-record", action="append", default=[])
    classify_parser.add_argument("--checkpoint", action="append", default=[])
    classify_parser.add_argument("--dispatch-record", action="append", default=[])
    classify_parser.set_defaults(func=classify_restart)
    report_parser = commands.add_parser("classify-report")
    report_parser.add_argument("--message", required=True)
    report_parser.set_defaults(func=classify_report)
    cards_parser = commands.add_parser("attend-cards")
    cards_parser.add_argument("--tasks", required=True)
    cards_parser.add_argument("--classification", required=True)
    cards_parser.set_defaults(func=attend_cards)
    lifecycle_parser = commands.add_parser("classify-lifecycle")
    lifecycle_parser.add_argument("--op", required=True, choices=["release", "stop"])
    lifecycle_parser.add_argument("--result", required=True)
    lifecycle_parser.add_argument("--mode")
    lifecycle_parser.add_argument("--authority", choices=["park", "user-authorized", "exit-evidence", "none"])
    lifecycle_parser.set_defaults(func=classify_lifecycle)
    record_parser = commands.add_parser("record-write")
    record_parser.add_argument("--orca", default="orca")
    record_parser.add_argument("--path", required=True)
    record_parser.add_argument("--record", required=True)
    record_parser.set_defaults(func=write_record)
    hash_parser = commands.add_parser("spec-hash")
    hash_parser.add_argument("--tasks", required=True)
    hash_parser.add_argument("--task", required=True)
    hash_parser.set_defaults(func=spec_hash)
    return root


if __name__ == "__main__":
    namespace = parser().parse_args()
    namespace.func(namespace)
