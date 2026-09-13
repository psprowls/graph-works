#!/usr/bin/env python3
"""Encode and execute the auto-drive launch contract without resolving rules."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn

PREFIX = "GW_LAUNCH_V1 "
ENVELOPE_FIELDS = {
    "version",
    "dispatch_key",
    "agent",
    "model",
    "reasoning_effort",
    "placement_argv",
}


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
    if not isinstance(value, dict) or set(value) != ENVELOPE_FIELDS:
        fail("Missing or malformed launch envelope; inspect recovery state before retrying.")
    if type(value["version"]) is not int or value["version"] != 1:
        fail("Unsupported launch envelope version; inspect recovery state before retrying.")
    envelope = {
        "version": 1,
        "dispatch_key": nonblank(value["dispatch_key"], "dispatch_key"),
        "agent": nonblank(value["agent"], "agent"),
        "model": optional_nonblank(value["model"], "model"),
        "reasoning_effort": optional_nonblank(value["reasoning_effort"], "reasoning_effort"),
        "placement_argv": value["placement_argv"],
    }
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


def encode(args: argparse.Namespace) -> None:
    dispatch = read_json(args.dispatch)
    placement = read_json(args.placement)
    if not isinstance(dispatch, dict):
        fail("Dispatch must be a JSON object.")
    envelope = validate_envelope(
        {
            "version": 1,
            "dispatch_key": dispatch.get("key"),
            "agent": dispatch.get("agent"),
            "model": dispatch.get("model"),
            "reasoning_effort": dispatch.get("reasoning_effort"),
            "placement_argv": placement,
        }
    )
    prompt = dispatch.get("prompt")
    if not isinstance(prompt, str):
        fail("Dispatch prompt must be a string.")
    payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(f"{PREFIX}{payload}\n{prompt}")


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


def create(args: argparse.Namespace) -> None:
    envelope, _prompt = decode_spec(args.spec)
    if envelope["dispatch_key"] != args.task_title:
        fail("Launch envelope dispatch_key does not match the task title; inspect recovery state.")
    spec = read_text_exact(args.spec)
    argv = [
        args.orca,
        "orchestration",
        "task-create",
        "--run",
        args.run,
        "--spec",
        spec,
        "--task-title",
        args.task_title,
        "--display-name",
        args.display_name,
        "--json",
    ]
    completed = subprocess.run(argv, check=False)
    if completed.returncode != 0:
        fail(f"task-create exited {completed.returncode}; inspect the reserved task before starting.")


def launch(args: argparse.Namespace) -> None:
    envelope, _prompt = decode_spec(args.spec)
    if envelope["dispatch_key"] != args.dispatch_key:
        fail("Launch envelope dispatch_key does not match the task title; inspect recovery state.")
    placement = envelope["placement_argv"]
    if args.retry_of is not None:
        if args.recovery_placement is None:
            fail("Retry requires recovery-approved placement; inspect allocated resources first.")
        placement = read_json(args.recovery_placement)
        if not isinstance(placement, list) or not all(isinstance(arg, str) for arg in placement):
            fail("Recovery-approved placement must be a JSON list of argv strings.")
    elif args.recovery_placement is not None:
        fail("Recovery-approved placement is valid only with --retry-of.")
    argv = [
        args.orca,
        "orchestration",
        "worker-start",
        "--task",
        args.task,
        "--agent",
        str(envelope["agent"]),
        "--run",
        args.run,
        *placement,
    ]
    if envelope["model"] is not None:
        argv += ["--model", str(envelope["model"])]
    if envelope["reasoning_effort"] is not None:
        argv += ["--effort", str(envelope["reasoning_effort"])]
    if args.retry_of is not None:
        argv += ["--retry-of", args.retry_of]
    argv.append("--json")
    completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        fail(f"worker-start exited {completed.returncode}; inspect recovery before retrying.")
    try:
        payload = json.loads(completed.stdout)
        result = payload["result"]
        receipt = result["launch"]
    except (json.JSONDecodeError, KeyError, TypeError):
        fail("Launch receipt is missing; worker preferences are unverified.")
    check_receipt(envelope, receipt)


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
            check=False, capture_output=True, text=True,
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


def classify_restart(args: argparse.Namespace) -> None:
    task_payload = read_json(args.tasks)
    worker_payload = read_json(args.workers)
    tasks = envelope_rows(task_payload, "tasks", "Task-list")
    workers = envelope_rows(worker_payload, "workers", "Worker-list")
    latest_by_task: dict[str, dict[str, object]] = {}
    for worker in workers:
        if isinstance(worker, dict) and isinstance(worker.get("taskId"), str):
            latest_by_task[worker["taskId"]] = worker
    rows: list[dict[str, object]] = []
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            fail("Every task must carry an id.")
        task_id = task["id"]
        worker = latest_by_task.get(task_id)
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
        rows.append({"task_id": task_id, "dispatch_id": dispatch_id, "action": action})
    json.dump(rows, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def envelope_rows(payload: object, field: str, label: str) -> list[object]:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        fail(f"{label} payload must be a successful Orca JSON envelope.")
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get(field), list):
        fail(f"{label} payload result must contain {field}[].")
    return result[field]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    encode_parser = commands.add_parser("encode")
    encode_parser.add_argument("--dispatch", required=True)
    encode_parser.add_argument("--placement", required=True)
    encode_parser.set_defaults(func=encode)
    create_parser = commands.add_parser("create")
    create_parser.add_argument("--orca", default="orca")
    create_parser.add_argument("--spec", required=True)
    create_parser.add_argument("--run", required=True)
    create_parser.add_argument("--task-title", required=True)
    create_parser.add_argument("--display-name", required=True)
    create_parser.set_defaults(func=create)
    launch_parser = commands.add_parser("launch")
    launch_parser.add_argument("--orca", default="orca")
    launch_parser.add_argument("--spec", required=True)
    launch_parser.add_argument("--task", required=True)
    launch_parser.add_argument("--dispatch-key", required=True)
    launch_parser.add_argument("--run", required=True)
    launch_parser.add_argument("--retry-of")
    launch_parser.add_argument("--recovery-placement")
    launch_parser.set_defaults(func=launch)
    classify_parser = commands.add_parser("classify-restart")
    classify_parser.add_argument("--orca", default="orca")
    classify_parser.add_argument("--tasks", required=True)
    classify_parser.add_argument("--workers", required=True)
    classify_parser.set_defaults(func=classify_restart)
    return root


if __name__ == "__main__":
    namespace = parser().parse_args()
    namespace.func(namespace)
