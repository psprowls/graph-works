#!/usr/bin/env python3
"""Encode and execute the auto-drive launch contract and its settlement recovery without resolving rules."""

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
    """The frozen envelope re-placed, plus the composed `## Resume after park` text.

    Two consumers, and they take different halves. `launch --retry-of` reads
    only the *envelope*: `worker-start`'s `--task` and `--spec` are mutually
    exclusive and a retry passes `--task`, so the original Task's frozen prompt
    is what the resumed worker actually starts on and the prompt written here
    never reaches it. The resume text is delivered separately, by the
    coordinator's `send --to dispatch:<new id>` follow-up (auto-drive §2.6
    step 7), which slices this file from its `## Resume after park` heading --
    which is why that text is composed here, once, rather than in two places.
    """
    envelope, prompt = decode_spec(args.spec)
    checkpoint_text = read_text_exact(args.checkpoint)
    question = _checkpoint_question(checkpoint_text)
    placement = read_json(args.placement) if os.path.exists(args.placement) else json.loads(args.placement)
    if not isinstance(placement, list) or not all(isinstance(item, str) for item in placement):
        fail("--placement must be a JSON list of argv strings.")
    resumed_prompt = (
        f"{prompt}\n\n## Resume after park\n\n"
        f"This Dispatch was parked mid-stage after a question went unanswered past its grace "
        f"period. The question was:\n\n{question}\n\nThe human's answer, now recorded:\n\n"
        f"{args.answer}\n\nContinue from the checkpoint's recorded remaining actions using this "
        f"answer; do not re-ask it."
    )
    new_envelope = dict(envelope)
    new_envelope["placement_argv"] = placement
    payload = json.dumps(new_envelope, ensure_ascii=False, separators=(",", ":"))
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
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or not isinstance(payload.get("result"), dict)
    ):
        fail(f"orca {argv[0]} returned an unsuccessful envelope; checkpoint not verified.")
    return payload["result"]


EXISTING_ACTIONS = {"reuse", "main"}
CREATION_ACTIONS = {"fork-child", "create-top-level"}
REF_PREFIX = "refs/heads/"


def orca_top_json(orca: str, argv: list[str], *, label: str) -> dict[str, Any]:
    """One `orca <argv> --json` call, unwrapped; any failure fails with *label*."""
    name = " ".join(argv[:2])
    try:
        completed = subprocess.run([orca, *argv, "--json"], check=False, capture_output=True, text=True)
    except OSError as error:
        fail(f"{label}: orca {name} could not run ({error}).")
    if completed.returncode != 0:
        fail(f"{label}: orca {name} exited {completed.returncode}: {completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        fail(f"{label}: orca {name} returned invalid JSON.")
    if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("result"), dict):
        fail(f"{label}: orca {name} returned an unsuccessful envelope.")
    return payload["result"]


def placement_dispatch(path: str, verb: str) -> tuple[str, str, dict[str, Any]]:
    """`(key, label, worktree)` from a saved `dispatches[]` entry."""
    dispatch = read_json(path)
    if not isinstance(dispatch, dict) or not isinstance(dispatch.get("key"), str) or not dispatch["key"].strip():
        fail(f"PLACEMENT {verb}: the dispatch JSON has no key.")
    key = dispatch["key"]
    label = f"PLACEMENT {verb} {key}"
    worktree = dispatch.get("worktree")
    if not isinstance(worktree, dict) or worktree.get("action") not in EXISTING_ACTIONS | CREATION_ACTIONS:
        fail(f"{label}: the dispatch worktree action is missing or unknown.")
    return key, label, worktree


def text_field(value: object, label: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label}: {what} is missing.")
    return value


def place(args: argparse.Namespace) -> None:
    """Resolve where a dispatch goes before anything is created.

    Nothing here reads the caller's cwd, terminal or Orca worktree: the
    repository comes from the plan's `repo.path`, the parent from the plan's
    `parent_path`, and both are matched by explicit selectors. Every creation
    launches top-level; Orca's child mode would take both from the caller.

    A cross-repo parent refuses outright: plan and reality genuinely
    contradict each other, and the repository is load-bearing (creation
    argv names it directly via `--repo`). An Orca-unknown parent, or a
    parent that is the repository's own checkout, instead *degrades* to a
    parentless top-level creation with a note on stderr: lineage is
    presentational (nothing outside `settle_placement`'s own assertion
    reads `parent_worktree_id`), D2 already accepts a temporarily parentless
    child, and `_adopted_action`'s parent source is `git worktree list`,
    which includes hand-made worktrees Orca never created -- refusing over
    a cosmetic link would block otherwise-correct work.
    """
    _key, label, worktree = placement_dispatch(args.dispatch, "REFUSED")
    action = worktree["action"]
    repo_id: str | None = None
    parent_id: str | None = None
    if action in EXISTING_ACTIONS:
        path = text_field(worktree.get("path"), label, f"a {action} dispatch's worktree path")
        argv = ["--worktree", f"path:{path}"]
    else:
        branch = text_field(worktree.get("branch"), label, "the worktree branch")
        base = text_field(worktree.get("base_branch"), label, "the worktree base_branch")
        if args.repo_path is None:
            fail(f"{label}: the plan names no code repository, and a new worktree is never placed by location.")
        wanted = os.path.realpath(args.repo_path)
        repos = orca_top_json(args.orca, ["repo", "list"], label=label).get("repos")
        if not isinstance(repos, list):
            fail(f"{label}: orca repo list returned no repos array.")
        matches = [
            row for row in repos
            if isinstance(row, dict) and isinstance(row.get("path"), str)
            and os.path.realpath(row["path"]) == wanted
        ]
        if len(matches) != 1:
            fail(f"{label}: {len(matches)} Orca repositories match {wanted}; exactly one must be registered.")
        repo_id = text_field(matches[0].get("id"), label, "the matched Orca repository id")
        parent_path = worktree.get("parent_path")
        if parent_path is not None:
            parent_path = text_field(parent_path, label, "parent_path")
            try:
                shown = orca_top_json(
                    args.orca, ["worktree", "show", "--worktree", f"path:{parent_path}"], label=label
                ).get("worktree")
            except SystemExit:
                # Degrade, don't refuse: an Orca-unknown parent is not a
                # plan/reality contradiction the way a cross-repo parent is.
                shown = None
            if not isinstance(shown, dict):
                print(
                    f"{label}: Orca does not know the planned parent {parent_path}; "
                    "creating a parentless top-level worktree instead.",
                    file=sys.stderr,
                )
            elif shown.get("repoId") != repo_id:
                fail(f"{label}: parent {parent_path} is in Orca repository {shown.get('repoId')}, not {repo_id}.")
            elif shown.get("isMainWorktree") is not False:
                print(
                    f"{label}: parent {parent_path} is the repository's own checkout; "
                    "creating a parentless top-level worktree instead.",
                    file=sys.stderr,
                )
            else:
                parent_id = text_field(shown.get("id"), label, "the parent worktree id")
        argv = ["--worktree", "new-top-level", "--name", branch, "--base-branch", base, "--repo", f"id:{repo_id}"]
    with Path(args.out_placement).open("w", encoding="utf-8", newline="") as stream:
        json.dump(argv, stream)
    sys.stdout.write(json.dumps(
        {"action": action, "placement_argv": argv, "repo_id": repo_id, "parent_worktree_id": parent_id}
    ))


def started_worktree_id(start: object, orca: str, label: str) -> str:
    """The created or selected worktree's `<repoId>::<path>` id."""
    result = start.get("result") if isinstance(start, dict) else None
    if not isinstance(result, dict):
        fail(f"{label}: the worker-start JSON has no result.")
    for effect in result.get("effects") or []:
        if isinstance(effect, dict) and effect.get("kind") == "worktree" and isinstance(effect.get("id"), str):
            return effect["id"]
    dispatch_id = text_field(result.get("dispatchId"), label, "the worker-start dispatchId")
    worker = orca_top_json(orca, ["orchestration", "worker-show", "--dispatch", dispatch_id], label=label).get("worker")
    found = (worker.get("worktreeId") or worker.get("worktree_id")) if isinstance(worker, dict) else None
    return text_field(found, label, f"the worktree of dispatch {dispatch_id}")


def show_worktree(orca: str, worktree_id: str, label: str) -> dict[str, Any]:
    row = orca_top_json(orca, ["worktree", "show", "--worktree", f"id:{worktree_id}"], label=label).get("worktree")
    if not isinstance(row, dict):
        fail(f"{label}: Orca shows no worktree {worktree_id}.")
    return row


def settle_placement(args: argparse.Namespace) -> None:
    """Read back where a start landed, link its planned lineage once, and assert it.

    Idempotent: a re-run after a crash between start and `worktree set`
    repairs the missing parent and never launches anything.
    """
    _key, label, worktree = placement_dispatch(args.dispatch, "MISMATCH")
    placed = read_json(args.placement_result)
    if not isinstance(placed, dict):
        fail(f"{label}: the place result is not a JSON object.")
    worktree_id = started_worktree_id(read_json(args.start), args.orca, label)
    row = show_worktree(args.orca, worktree_id, label)
    lineage_set = False
    if worktree["action"] in EXISTING_ACTIONS:
        planned = text_field(worktree.get("path"), label, "the planned worktree path")
        if os.path.realpath(str(row.get("path") or "")) != os.path.realpath(planned):
            fail(f"{label}: planned {planned}, actual {row.get('path')}.")
    else:
        if row.get("repoId") != placed.get("repo_id"):
            fail(f"{label}: landed in Orca repository {row.get('repoId')}, planned {placed.get('repo_id')}.")
        if row.get("isMainWorktree") is not False:
            fail(f"{label}: landed in the repository's own checkout {row.get('path')}.")
        # Orca 1.4.203 (verified live 2026-09-15, on a bare `orca worktree
        # create` call -- not the `worker-start --worktree new-top-level`
        # call auto-drive actually issues, which names no parent for Orca to
        # infer at all): a cross-repo parent inferred there is echoed but
        # never persisted, so the read-back is null. This guard triggers the
        # one explicit set; if Orca ever persisted such a parent, the value
        # would be non-null and wrong, and the assertion below would fail
        # loudly rather than place silently.
        expected = placed.get("parent_worktree_id")
        if expected is not None and row.get("parentWorktreeId") is None:
            orca_top_json(
                args.orca,
                ["worktree", "set", "--worktree", f"id:{worktree_id}", "--parent-worktree", f"id:{expected}"],
                label=label,
            )
            lineage_set = True
            row = show_worktree(args.orca, worktree_id, label)
        if row.get("parentWorktreeId") != expected:
            fail(f"{label}: parent is {row.get('parentWorktreeId')}, planned {expected}.")
    branch = row.get("branch")
    short = branch[len(REF_PREFIX):] if isinstance(branch, str) and branch.startswith(REF_PREFIX) else branch
    sys.stdout.write(json.dumps({
        "worktree_id": worktree_id,
        "path": row.get("path"),
        "branch": short,
        "display_name": row.get("displayName"),
        "repo_id": row.get("repoId"),
        "parent_worktree_id": row.get("parentWorktreeId"),
        "lineage_set": lineage_set,
    }))


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
        if (
            not isinstance(scope, dict)
            or scope.get("source") != "flag"
            or scope.get("run") != run_id
        ):
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
    record: dict[str, Any], task: object, workers: list[object], level: int
) -> str | None:
    """Why fresh Task/worker rows do not prove a verified checkpoint level, or None."""
    attempts = [
        row
        for row in workers
        if isinstance(row, dict) and row.get("taskId") == record["task_id"]
    ]
    if not attempts:
        return "worker-missing"
    worker = attempts[-1]
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
    if worker.get("workerState") == "outcome_unknown" or worker.get("dispatchStatus") == (
        "outcome_unknown"
    ):
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
    if level >= 3 and task.get("status") != "completed":
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
        (
            row
            for row in tasks
            if isinstance(row, dict) and row.get("id") == record["task_id"]
        ),
        None,
    )
    gap = (
        mutation_identity_gap(record)
        or settlement_gap(record, task, workers, level)
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
            fail(
                f"Recovery record {path} is not named <dispatch_id>.json; "
                "inspect it before restarting."
            )
        by_task.setdefault(task_id, []).append(raw)
    return by_task


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
    workers: list[object],
    candidates: list[dict[str, Any]],
    *,
    base: str,
    orca: str,
) -> tuple[str, dict[str, object]]:
    """Let a saved recovery record refine, never override, fresh Orca evidence."""
    attempts = [
        row for row in workers if isinstance(row, dict) and row.get("taskId") == task["id"]
    ]
    latest = attempts[-1].get("dispatchId") if attempts else None
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
    worker = attempts[-1] if attempts else None
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
    gap = settlement_gap(record, task, workers, 3) or evidence_gap(record)
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
    if records:
        validate_recovery_snapshot(worker_payload, workers)
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
        # A parked Task and a deliberately skipped one are bit-for-bit
        # identical in Orca (`blocked`, latest attempt `stopped`/`failed`):
        # §2.5.2 writes no marker of its own. The checkpoint on disk is the
        # distinguishing signal, joined by the Task's display name because a
        # dispatch key is not reversible to a path.
        if action == "deliberate-skip" and display_stage(task) in parked_stages:
            action = "parked"
        row: dict[str, object] = {
            "task_id": task_id,
            "dispatch_id": dispatch_id,
            "action": action,
        }
        if task_id in records:
            row["action"], row["recovery"] = reconcile_records(
                task,
                workers,
                records[task_id],
                base=action,
                orca=args.orca,
            )
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
            or mutation["action"] not in {"worker-stop", "worker-release", "task-update"}
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
        and all(
            record[field] == previous[field]
            for field in RECORD_FIELDS - {"history", "unresolved"}
        )
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
                    record[field] != previous[field]
                    for field in RECORD_FIELDS - {"history", "mutations", "unresolved"}
                )
            ):
                # Requested checkpoints do not themselves verify. Clearing a
                # refusal or advancing still must re-earn historical proof.
                # Merely appending an original receipt at the same requested
                # checkpoint with unresolved retained needs no fresh proof.
                historical = [
                    name for name in VERIFIED_CHECKPOINTS
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
    encode_parser = commands.add_parser("encode")
    encode_parser.add_argument("--dispatch", required=True)
    encode_parser.add_argument("--placement", required=True)
    encode_parser.set_defaults(func=encode)
    decode_parser = commands.add_parser("decode")
    decode_parser.add_argument("--spec", required=True)
    decode_parser.set_defaults(func=decode)
    resume_parser = commands.add_parser("resume-spec")
    resume_parser.add_argument("--spec", required=True)
    resume_parser.add_argument("--checkpoint", required=True)
    resume_parser.add_argument("--answer", required=True)
    resume_parser.add_argument("--placement", required=True)
    resume_parser.set_defaults(func=resume_spec)
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
    classify_parser.add_argument("--recovery-record", action="append", default=[])
    classify_parser.add_argument("--checkpoint", action="append", default=[])
    classify_parser.set_defaults(func=classify_restart)
    report_parser = commands.add_parser("classify-report")
    report_parser.add_argument("--message", required=True)
    report_parser.set_defaults(func=classify_report)
    record_parser = commands.add_parser("record-write")
    record_parser.add_argument("--orca", default="orca")
    record_parser.add_argument("--path", required=True)
    record_parser.add_argument("--record", required=True)
    record_parser.set_defaults(func=write_record)
    hash_parser = commands.add_parser("spec-hash")
    hash_parser.add_argument("--tasks", required=True)
    hash_parser.add_argument("--task", required=True)
    hash_parser.set_defaults(func=spec_hash)
    place_parser = commands.add_parser("place")
    place_parser.add_argument("--orca", default="orca")
    place_parser.add_argument("--dispatch", required=True)
    place_parser.add_argument("--repo-path")
    place_parser.add_argument("--out-placement", required=True)
    place_parser.set_defaults(func=place)
    settle_parser = commands.add_parser("settle-placement")
    settle_parser.add_argument("--orca", default="orca")
    settle_parser.add_argument("--dispatch", required=True)
    settle_parser.add_argument("--placement-result", required=True)
    settle_parser.add_argument("--start", required=True)
    settle_parser.set_defaults(func=settle_placement)
    return root


if __name__ == "__main__":
    namespace = parser().parse_args()
    namespace.func(namespace)
