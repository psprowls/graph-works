#!/usr/bin/env python3
"""Settlement recovery for rejected worker_done reports (auto-drive §4.1.1).

Fixtures are controls shaped like the Orca lifecycle spike's W7 sequence
(transport receipts 331-354). None reproduces a live rejected worker_done; the
historical caller-identity cause remains unverified.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HELPER_PATH = Path(__file__).resolve().parents[1] / "skills/auto-drive/references/launch-worker.py"
HELPER = runpy.run_path(str(HELPER_PATH))

RUN = "run_fixture"
TASK = "task_fixture"
DISPATCH = "ctx_fixture"
KEY = "gw-execute-example-0123abcd"
MARKER = {"code": "caller_not_dispatch_pane", "reason": "The caller is not the Dispatch pane."}
COMMIT = "0123456789abcdef0123456789abcdef01234567"
ENVELOPE = {
    "version": 1,
    "dispatch_key": KEY,
    "agent": "claude",
    "model": "opus",
    "reasoning_effort": None,
    "placement_argv": ["--worktree", "path:/tmp/example"],
}
SPEC = "GW_LAUNCH_V1 " + json.dumps(ENVELOPE, separators=(",", ":")) + "\nRun the execute stage.\n"
RECEIPT = {
    side: {"agent": "claude", "model": "opus", "effort": None}
    for side in ("requested", "effective")
}
ARTIFACT_TEXT = "# Execute evidence\n"
LIFECYCLE_MUTATIONS = {"worker-stop", "worker-release", "task-update", "worker-start", "abandon"}
RETRY_ROW = {
    "dispatchId": "ctx_retry",
    "taskId": TASK,
    "runId": RUN,
    "workerState": "running",
    "dispatchStatus": "dispatched",
    "terminalState": "active",
    "resource": {"releaseState": "active"},
}


def worker_done(payload: object, subject: str = "Execute stage complete") -> dict[str, object]:
    return {
        "id": "msg_fixture",
        "run_id": RUN,
        "type": "worker_done",
        "subject": subject,
        "body": "report",
        "payload": json.dumps(payload) if isinstance(payload, dict) else payload,
    }


class ReportClassificationTests(unittest.TestCase):
    def classify(self, message: object) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "message.json"
            path.write_text(json.dumps(message), encoding="utf-8", newline="\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                HELPER["classify_report"](argparse.Namespace(message=str(path)))
        return json.loads(output.getvalue())

    def test_rejection_marker_is_read_before_the_outcome(self):
        for outcome in ("succeeded", "failed"):
            with self.subTest(outcome=outcome):
                row = self.classify(worker_done(
                    {"taskId": TASK, "dispatchId": DISPATCH, "outcome": outcome,
                     "_orcaLifecycleRejection": MARKER},
                    subject="Rejected worker_done: Execute stage complete",
                ))
                self.assertEqual(row, {
                    "message_id": "msg_fixture", "task_id": TASK, "dispatch_id": DISPATCH,
                    "outcome": outcome, "rejection": MARKER,
                    "branch": "claimed-unconfirmed", "reason": "rejection-marker",
                })

    def test_unprovable_reports_enter_inspection(self):
        identity = {"taskId": TASK, "dispatchId": DISPATCH, "outcome": "succeeded"}
        cases = {
            "malformed-rejection-marker": worker_done({**identity, "_orcaLifecycleRejection": "rejected"}),
            "legacy-rejection-wrapper": worker_done(None, subject="Rejected worker_done: Execute stage complete"),
            "payload-unreadable": worker_done("{not json"),
            "identity-missing": worker_done({"outcome": "succeeded"}),
            "outcome-missing": worker_done({"taskId": TASK, "dispatchId": DISPATCH}),
        }
        for reason, message in cases.items():
            with self.subTest(reason=reason):
                row = self.classify(message)
                self.assertEqual((row["branch"], row["reason"]), ("claimed-unconfirmed", reason))

    def test_accepted_outcomes_keep_their_existing_branches(self):
        for outcome, branch in (("succeeded", "accepted-success"), ("failed", "accepted-failure")):
            with self.subTest(outcome=outcome):
                row = self.classify(worker_done({"taskId": TASK, "dispatchId": DISPATCH, "outcome": outcome}))
                self.assertEqual(row["branch"], branch)
                self.assertIsNone(row["rejection"])

    def test_only_worker_done_messages_are_classified(self):
        with self.assertRaises(SystemExit):
            self.classify({"id": "msg_fixture", "type": "status", "payload": None})


class DecodeSpecCommandTests(unittest.TestCase):
    def _write_spec(
        self,
        *,
        dispatch_key: str,
        agent: str,
        model: str | None,
        reasoning_effort: str | None,
        placement_argv: list[str],
        prompt: str,
    ) -> Path:
        dispatch = {
            "key": dispatch_key,
            "agent": agent,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "prompt": prompt,
        }
        with tempfile.TemporaryDirectory() as directory:
            dispatch_path = Path(directory) / "dispatch.json"
            placement_path = Path(directory) / "placement.json"
            dispatch_path.write_text(json.dumps(dispatch), encoding="utf-8", newline="\n")
            placement_path.write_text(json.dumps(placement_argv), encoding="utf-8", newline="\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                HELPER["encode"](
                    argparse.Namespace(dispatch=str(dispatch_path), placement=str(placement_path))
                )
            spec_text = output.getvalue()
        return self._write_raw_spec(spec_text)

    def _write_raw_spec(self, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "spec.txt"
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def test_decode_prints_envelope_and_prompt_as_json(self):
        spec_path = self._write_spec(
            dispatch_key="gw-finish-item",
            agent="claude",
            model="opus",
            reasoning_effort=None,
            placement_argv=["--worktree", "path:/repo/item"],
            prompt="Run gw work next item.\nSend worker_done when done.",
        )
        args = argparse.Namespace(spec=str(spec_path))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            HELPER["decode"](args)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["envelope"]["dispatch_key"], "gw-finish-item")
        self.assertEqual(payload["envelope"]["agent"], "claude")
        self.assertEqual(payload["envelope"]["placement_argv"], ["--worktree", "path:/repo/item"])
        self.assertEqual(payload["prompt"], "Run gw work next item.\nSend worker_done when done.")

    def test_decode_fails_loudly_on_a_malformed_spec(self):
        spec_path = self._write_raw_spec("not a launch envelope at all")
        args = argparse.Namespace(spec=str(spec_path))
        with self.assertRaises(SystemExit):
            HELPER["decode"](args)


class ResumeSpecCommandTests(unittest.TestCase):
    def _write_text(self, name: str, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def _write_spec(
        self,
        *,
        dispatch_key: str,
        agent: str,
        model: str | None,
        reasoning_effort: str | None,
        placement_argv: list[str],
        prompt: str,
    ) -> Path:
        dispatch = {
            "key": dispatch_key,
            "agent": agent,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "prompt": prompt,
        }
        with tempfile.TemporaryDirectory() as directory:
            dispatch_path = Path(directory) / "dispatch.json"
            placement_path = Path(directory) / "placement.json"
            dispatch_path.write_text(json.dumps(dispatch), encoding="utf-8", newline="\n")
            placement_path.write_text(json.dumps(placement_argv), encoding="utf-8", newline="\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                HELPER["encode"](
                    argparse.Namespace(dispatch=str(dispatch_path), placement=str(placement_path))
                )
            spec_text = output.getvalue()
        return self._write_text("spec.txt", spec_text)

    def test_resume_spec_appends_question_and_answer_to_the_original_prompt(self):
        original_spec = self._write_spec(
            dispatch_key="gw-finish-item",
            agent="claude",
            model="opus",
            reasoning_effort=None,
            placement_argv=["--worktree", "path:/repo/item-old"],
            prompt="Run gw work next item.\nSend worker_done when done.",
        )
        checkpoint_path = self._write_text(
            "checkpoint.md",
            "---\n"
            "title: 'Checkpoint: Item (finish)'\n"
            "item: item\n"
            "decision: D-014\n"
            "phase: finish\n"
            "dispatch_key: gw-finish-item\n"
            "branch: item-branch\n"
            "worktree: /repo/item-old\n"
            "base: main\n"
            "head: abc123\n"
            "created: 2026-09-15T14:30:00Z\n"
            "---\n\n"
            "## Completed work\n\ntests verified\n\n"
            "## Remaining actions\n\nsettle\n\n"
            "## Question\n\nmerge, pr, hold, or discard?\n\n"
            "## Placement\n\nclean\n\n"
            "## Validation evidence\n\npytest: pass\n",
        )
        args = argparse.Namespace(
            spec=str(original_spec),
            checkpoint=str(checkpoint_path),
            answer="merge",
            placement='["--worktree", "path:/repo/item-old"]',
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            HELPER["resume_spec"](args)
        envelope, prompt = HELPER["decode_spec_text"](buf.getvalue())
        self.assertEqual(envelope["dispatch_key"], "gw-finish-item")
        self.assertEqual(envelope["agent"], "claude")
        self.assertEqual(envelope["placement_argv"], ["--worktree", "path:/repo/item-old"])
        self.assertIn("Run gw work next item.", prompt)
        self.assertIn("## Resume after park", prompt)
        self.assertIn("merge, pr, hold, or discard?", prompt)
        self.assertIn("merge", prompt.rsplit("## Resume after park", 1)[1])

    def test_resume_spec_fails_loudly_when_the_checkpoint_has_no_question_section(self):
        original_spec = self._write_spec(
            dispatch_key="gw-finish-item", agent="claude", model="opus",
            reasoning_effort=None, placement_argv=[], prompt="x",
        )
        checkpoint_path = self._write_text("checkpoint.md", "---\nitem: item\n---\n\n## Completed work\n\nx\n")
        args = argparse.Namespace(
            spec=str(original_spec), checkpoint=str(checkpoint_path), answer="merge", placement="[]",
        )
        with self.assertRaises(SystemExit):
            HELPER["resume_spec"](args)


class LaunchRetryOfTests(unittest.TestCase):
    def _write_text(self, name: str, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def _write_spec(
        self,
        *,
        dispatch_key: str,
        agent: str,
        model: str | None,
        reasoning_effort: str | None,
        placement_argv: list[str],
        prompt: str,
    ) -> Path:
        dispatch = {
            "key": dispatch_key,
            "agent": agent,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "prompt": prompt,
        }
        with tempfile.TemporaryDirectory() as directory:
            dispatch_path = Path(directory) / "dispatch.json"
            placement_path = Path(directory) / "placement.json"
            dispatch_path.write_text(json.dumps(dispatch), encoding="utf-8", newline="\n")
            placement_path.write_text(json.dumps(placement_argv), encoding="utf-8", newline="\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                HELPER["encode"](
                    argparse.Namespace(dispatch=str(dispatch_path), placement=str(placement_path))
                )
            spec_text = output.getvalue()
        return self._write_text("spec.txt", spec_text)

    def test_retry_of_requires_recovery_placement(self):
        spec_path = self._write_spec(
            dispatch_key="gw-finish-item", agent="claude", model="opus",
            reasoning_effort=None, placement_argv=["--worktree", "path:/repo/item"], prompt="x",
        )
        args = argparse.Namespace(
            orca="orca", spec=str(spec_path), task="task-1", dispatch_key="gw-finish-item",
            run="run-1", retry_of="dispatch-old", recovery_placement=None,
        )
        with self.assertRaises(SystemExit):
            HELPER["launch"](args)

    def test_recovery_placement_without_retry_of_is_refused(self):
        spec_path = self._write_spec(
            dispatch_key="gw-finish-item", agent="claude", model="opus",
            reasoning_effort=None, placement_argv=["--worktree", "path:/repo/item"], prompt="x",
        )
        args = argparse.Namespace(
            orca="orca", spec=str(spec_path), task="task-1", dispatch_key="gw-finish-item",
            run="run-1", retry_of=None, recovery_placement='["--worktree", "path:/repo/item"]',
        )
        with self.assertRaises(SystemExit):
            HELPER["launch"](args)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FakeOrca:
    """Read-only Orca rows for one Task; states follow spike W7's readbacks."""

    def __init__(self) -> None:
        self.task_status = "dispatched"
        self.task_title = KEY
        self.display_name = "work/example · execute"
        self.spec = SPEC
        self.spec_truncated = False
        self.worker_run = RUN
        self.worker_state = "ready"
        self.liveness: object = {"verdict": "live", "source": "agent_status"}
        self.dispatch_status = "dispatched"
        self.terminal_state = "active"
        self.release_state = "active"
        self.extra_workers: list[dict[str, object]] = []
        self.worker_page_size: int | None = None
        self.worker_scope: object = {"run": RUN, "source": "flag"}
        self.worker_cursors: dict[int, object] = {}
        self.worker_page_rows: dict[int, list[dict[str, object]]] = {}
        self.worker_page_has_more: dict[int, bool] = {}
        self.worker_page_totals: dict[int, object] = {}
        self.show_ok = True
        self.dispatch_meta: dict[str, dict[str, object]] = {}
        self.commits: set[str] = {COMMIT}
        self.calls: list[list[str]] = []

    def tasks(self) -> dict[str, object]:
        return {
            "id": "tasks",
            "ok": True,
            "result": {
                "runId": RUN,
                "tasks": [
                    {
                        "id": TASK,
                        "run_id": RUN,
                        "task_title": self.task_title,
                        "display_name": self.display_name,
                        "status": self.task_status,
                        "spec": self.spec,
                        "spec_truncated": self.spec_truncated,
                    }
                ],
            },
        }

    def worker_rows(self) -> list[dict[str, object]]:
        row = {
            "dispatchId": DISPATCH,
            "taskId": TASK,
            "runId": self.worker_run,
            "workerState": self.worker_state,
            "dispatchStatus": self.dispatch_status,
            "terminalState": self.terminal_state,
            "resource": {"releaseState": self.release_state},
            "projection": {"liveness": self.liveness},
        }
        # Real worker-list is newest first; `extra_workers` is appended oldest
        # to newest, so each later entry is a newer attempt of the same Task.
        return [*reversed(self.extra_workers), row]

    def dispatch(self, dispatch_id: str) -> dict[str, object]:
        """worker-show's `result.dispatch`: creation order and the retry chain."""
        order = [DISPATCH, *(str(row["dispatchId"]) for row in self.extra_workers)]
        position = order.index(dispatch_id) if dispatch_id in order else 0
        value: dict[str, object] = {
            "id": dispatch_id,
            "taskId": TASK,
            "runId": RUN,
            "createdAt": f"2026-09-13 20:{position:02d}:00",
            "retryOfDispatchId": order[position - 1] if position else None,
        }
        value.update(self.dispatch_meta.get(dispatch_id, {}))
        return value

    def workers(self, cursor: str | None = None) -> dict[str, object]:
        rows = self.worker_rows()
        size = self.worker_page_size or max(1, len(rows))
        page_index = int(cursor.removeprefix("cursor-")) if cursor is not None else 0
        start = page_index * size
        page_rows = rows[start : start + size]
        page_rows = self.worker_page_rows.get(page_index, page_rows)
        has_more = start + len(page_rows) < len(rows)
        has_more = self.worker_page_has_more.get(page_index, has_more)
        next_cursor: object = f"cursor-{page_index + 1}" if has_more else None
        next_cursor = self.worker_cursors.get(page_index, next_cursor)
        return {
            "id": "workers",
            "ok": True,
            "result": {
                "scope": self.worker_scope,
                "page": {
                    "limit": size,
                    "total": self.worker_page_totals.get(page_index, len(rows)),
                    "hasMore": has_more,
                    "nextCursor": next_cursor,
                },
                "workers": page_rows,
            },
        }

    def mutations(self) -> list[list[str]]:
        return [argv for argv in self.calls if argv[0] != "git" and argv[2] in LIFECYCLE_MUTATIONS]

    def __call__(self, argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if argv[0] == "git":
            found = argv[-1].removesuffix("^{commit}") in self.commits
            return subprocess.CompletedProcess(argv, 0 if found else 1, "", "")
        verb = argv[2]
        if verb == "task-list":
            payload: dict[str, object] = self.tasks()
        elif verb == "worker-list":
            cursor = argv[argv.index("--cursor") + 1] if "--cursor" in argv else None
            payload = self.workers(cursor)
        elif verb == "worker-show":
            payload = {
                "id": "show",
                "ok": self.show_ok,
                "result": {
                    "dispatch": self.dispatch(argv[argv.index("--dispatch") + 1]),
                    "worker": {
                        "state": self.worker_state,
                        "startOptions": {"launch": RECEIPT},
                    }
                },
            }
        else:
            raise AssertionError(f"helper issued an unexpected Orca call: {argv}")
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")


class RecoveryFixture(unittest.TestCase):
    CHECKPOINTS = HELPER["CHECKPOINTS"]

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artifact = self.root / "03-execute.md"
        self.target = self.root / "references" / "orca-settlement" / f"{DISPATCH}.json"
        self.reset()

    def reset(self) -> None:
        self.artifact.write_text(ARTIFACT_TEXT, encoding="utf-8", newline="\n")
        self.target.unlink(missing_ok=True)
        self.orca = FakeOrca()

    def record(self, checkpoint: str = "inspection", **changes: object) -> dict[str, object]:
        """Receipt-available control; interruption tests build null-ID intent separately."""
        index = self.CHECKPOINTS.index(checkpoint)
        gates = (
            ("stop-requested", "worker-stop"),
            ("release-requested", "worker-release"),
            ("completion-requested", "task-update"),
        )
        value: dict[str, object] = {
            "schema": "gw-orca-settlement",
            "version": 1,
            "run_id": RUN,
            "task_id": TASK,
            "dispatch_id": DISPATCH,
            "dispatch_key": KEY,
            "work_path": "work/example",
            "phase": "execute",
            "spec_sha256": sha256_text(SPEC),
            "placement": {"worktree": "/tmp/example", "branch": "feature/example"},
            "report": {
                "message_id": "msg_fixture",
                "outcome": "succeeded",
                "rejection": dict(MARKER),
                "reason": "rejection-marker",
            },
            "evidence": [
                {
                    "kind": "file",
                    "path": str(self.artifact),
                    "sha256": hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
                },
                {"kind": "commit", "repo": "/tmp/example", "sha": COMMIT},
                {
                    "kind": "validation",
                    "command": "just test-plugin",
                    "exit": 0,
                    "receipt": "execute log, gate section",
                },
            ],
            "judgment": "succeeded",
            "stop_authority": {
                "kind": "exit-evidence",
                "source": "worker-show liveness exited",
                "at": "2026-09-13T20:03:41Z",
            },
            "checkpoint": checkpoint,
            "mutations": [
                {
                    "action": action,
                    "request_id": f"req-{action}",
                    "receipt": f"receipt {action}",
                    "at": "2026-09-13T20:03:42Z",
                }
                for gate, action in gates
                if index >= self.CHECKPOINTS.index(gate)
            ],
            "history": [
                {
                    "checkpoint": name,
                    "at": f"2026-09-13T20:04:{position:02d}Z",
                    "note": "fixture",
                }
                for position, name in enumerate(self.CHECKPOINTS[: index + 1])
            ],
            "unresolved": None,
        }
        value.update(changes)
        return value

    def call(self, name: str, **namespace: object) -> str:
        output = io.StringIO()
        with patch("subprocess.run", side_effect=self.orca), contextlib.redirect_stdout(output):
            HELPER[name](argparse.Namespace(**namespace))
        return output.getvalue()

    def write(self, value: dict[str, object]) -> dict[str, object]:
        source = self.root / "record-input.json"
        source.write_text(json.dumps(value), encoding="utf-8", newline="\n")
        return json.loads(
            self.call("write_record", orca="fake-orca", path=str(self.target), record=str(source))
        )

    def refused(self, value: dict[str, object], message: str) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.write(value)
        self.assertIn(message, str(caught.exception))

    def settle(self, level: int) -> None:
        """Move Orca rows to what W7 read back after stop (1), release (2), Task completion (3)."""
        if level >= 1:
            self.orca.liveness = {"verdict": "exited", "source": "resource_release"}
            self.orca.worker_state, self.orca.dispatch_status, self.orca.task_status = (
                "stopped",
                "failed",
                "blocked",
            )
        if level >= 2:
            self.orca.terminal_state = self.orca.release_state = "released"
        if level >= 3:
            self.orca.task_status = "completed"

    def progress(self, through: str) -> None:
        levels = {"stopped-verified": 1, "released-verified": 2, "completed-verified": 3}
        for name in self.CHECKPOINTS[: self.CHECKPOINTS.index(through) + 1]:
            self.settle(levels.get(name, 0))
            self.write(self.record(name))

    def place(self, value: dict[str, object], target: Path | None = None) -> Path:
        """A record already on disk, as a crash would leave it."""
        path = target or self.target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8", newline="\n")
        return path

    def classify(
        self, records: list[str] | None = None, checkpoints: list[str] | None = None
    ) -> dict[str, dict[str, object]]:
        tasks = self.root / "tasks.json"
        workers = self.root / "workers.json"
        tasks.write_text(json.dumps(self.orca.tasks()), encoding="utf-8", newline="\n")
        workers.write_text(json.dumps(self.orca.workers()), encoding="utf-8", newline="\n")
        paths = [str(self.target)] if records is None else records
        rows = json.loads(
            self.call(
                "classify_restart",
                orca="fake-orca",
                tasks=str(tasks),
                workers=str(workers),
                recovery_record=paths,
                checkpoint=checkpoints or [],
            )
        )
        return {row["task_id"]: row for row in rows}

    def checkpoint(self, item: str = "work/example", phase: str = "execute") -> str:
        """A park checkpoint on disk, as the worker's `--hold park` call leaves it."""
        path = self.root / f"03-{phase}-checkpoint-D-001.md"
        path.write_text(
            "---\n"
            f"title: 'Checkpoint: Example ({phase})'\n"
            f"item: {item}\n"
            "decision: D-001\n"
            f"phase: {phase}\n"
            f"dispatch_key: {KEY}\n"
            "branch: feature/example\n"
            "worktree: /tmp/example\n"
            "base: main\n"
            "head: none\n"
            "created: 2026-09-15T14:30:00Z\n"
            "---\n\n"
            "## Completed work\n\nx\n\n## Remaining actions\n\ny\n\n"
            "## Question\n\nmerge or hold?\n\n## Placement\n\nclean\n\n"
            "## Validation evidence\n\nnone run\n",
            encoding="utf-8",
            newline="\n",
        )
        return str(path)


class RecordWriteTests(RecoveryFixture):
    def test_new_record_starts_at_inspection_and_is_replaced_atomically(self):
        self.refused(self.record("stop-requested"), "must start at inspection")
        self.assertEqual(
            self.write(self.record()),
            {"path": str(self.target), "checkpoint": "inspection", "changed": True},
        )
        data = self.target.read_bytes()
        self.assertTrue(data.endswith(b"\n"))
        self.assertNotIn(b"\r", data)
        self.assertEqual(json.loads(data.decode("utf-8")), self.record())
        self.assertEqual([path.name for path in self.target.parent.iterdir()], [f"{DISPATCH}.json"])
        self.assertEqual(self.orca.calls, [], "inspection needs no Orca readback")

    def test_identical_rewrite_is_a_no_op(self):
        self.write(self.record())
        before = self.target.read_bytes()
        self.assertFalse(self.write(self.record())["changed"])
        self.assertEqual(self.target.read_bytes(), before)

    def test_file_name_is_the_dispatch_id(self):
        self.target = self.target.with_name("other.json")
        self.refused(self.record(), "<dispatch_id>.json")

    def test_checkpoints_past_inspection_require_judgment_evidence_and_authority(self):
        self.write(self.record())
        passing_files = self.record()["evidence"][:2]
        cases = {
            "require judgment succeeded": {"judgment": "unestablished"},
            "require file or commit evidence": {
                "evidence": [
                    {
                        "kind": "validation",
                        "command": "just test-plugin",
                        "exit": 0,
                        "receipt": "log",
                    }
                ]
            },
            "require stop_authority": {"stop_authority": None},
            "require passing validation evidence": {
                "evidence": [
                    *passing_files,
                    {
                        "kind": "validation",
                        "command": "just test-plugin",
                        "exit": 1,
                        "receipt": "log",
                    },
                ]
            },
        }
        for message, change in cases.items():
            with self.subTest(message=message):
                self.refused(self.record("stop-requested", **change), message)
        self.assertEqual(self.write(self.record("stop-requested"))["checkpoint"], "stop-requested")

    def test_identity_is_immutable_and_progress_is_append_only(self):
        self.write(self.record())
        self.write(self.record("stop-requested"))
        changed_report = dict(self.record()["report"], message_id="msg_other")
        for field, value in (
            ("run_id", "run_other"),
            ("task_id", "task_other"),
            ("dispatch_key", "gw-execute-other-0123abcd"),
            ("spec_sha256", "0" * 64),
            ("work_path", "work/other"),
            ("report", changed_report),
        ):
            with self.subTest(field=field):
                self.refused(
                    self.record("stop-requested", **{field: value}),
                    f"{field} is immutable",
                )
        self.refused(self.record(), "cannot move backwards")
        rewritten = self.record("release-requested")
        rewritten["history"][0]["note"] = "rewritten"
        self.refused(rewritten, "history is append-only")
        dropped = self.record("release-requested")
        dropped["mutations"] = dropped["mutations"][1:]
        self.refused(dropped, "mutations is append-only")

    def test_evidence_changes_only_with_a_reattestation_note(self):
        self.write(self.record())
        self.artifact.write_text(
            "# Execute evidence, revised by a later stage\n", encoding="utf-8", newline="\n"
        )
        revised = self.record()
        self.refused(revised, "reattested:")
        revised["history"].append(
            {
                "checkpoint": "inspection",
                "at": "2026-09-13T21:00:00Z",
                "note": "reattested: artifact revised by a later stage",
            }
        )
        self.assertTrue(self.write(revised)["changed"])

    def test_dispatch_capabilities_are_never_stored(self):
        for leaked in ("dcap_example", "orca orchestration send --dispatch-capability example"):
            with self.subTest(leaked=leaked):
                value = self.record()
                value["report"]["reason"] = leaked
                self.refused(value, "must not store Dispatch capabilities")
        self.assertFalse(self.target.exists())

    def test_spec_hash_reads_the_full_untruncated_task_spec(self):
        tasks = self.root / "tasks.json"
        tasks.write_text(json.dumps(self.orca.tasks()), encoding="utf-8", newline="\n")
        self.assertEqual(
            self.call("spec_hash", tasks=str(tasks), task=TASK).strip(), sha256_text(SPEC)
        )
        for attribute, value in (("spec_truncated", True), ("task_title", "gw-execute-other-0123abcd")):
            with self.subTest(attribute=attribute):
                self.orca = FakeOrca()
                setattr(self.orca, attribute, value)
                tasks.write_text(json.dumps(self.orca.tasks()), encoding="utf-8", newline="\n")
                with self.assertRaises(SystemExit):
                    self.call("spec_hash", tasks=str(tasks), task=TASK)

    def test_new_inspection_requires_history_ending_at_its_checkpoint(self):
        missing = self.record()
        missing["history"] = []
        self.refused(missing, "history must end at the current checkpoint")
        mismatched = self.record()
        mismatched["history"][-1]["checkpoint"] = "stop-requested"
        self.refused(mismatched, "history must end at the current checkpoint")

    def test_changed_record_requires_new_history(self):
        self.write(self.record())
        changed = self.record(unresolved="awaiting readback")
        self.refused(changed, "changed record must append history")

    def test_changed_record_history_ends_at_its_checkpoint(self):
        self.write(self.record())
        changed = self.record(unresolved="awaiting readback")
        changed["history"].append(
            {
                "checkpoint": "stop-requested",
                "at": "2026-09-13T21:00:00Z",
                "note": "inspection updated",
            }
        )
        self.refused(changed, "history must end at the current checkpoint")


class VerifiedCheckpointTests(RecoveryFixture):
    def test_stopped_verified_needs_a_settled_latest_worker(self):
        self.progress("stop-requested")
        self.refused(self.record("stopped-verified"), "worker-not-settled")

    def test_failed_stop_never_substitutes_for_a_settlement_readback(self):
        self.progress("stop-requested")
        failed_stop = self.record(
            "stop-requested",
            unresolved="worker-stop refused: Dispatch is not stopping.",
        )
        failed_history = {
            "checkpoint": "stop-requested",
            "at": "2026-09-13T20:05:00Z",
            "note": "worker-stop refused",
        }
        failed_stop["history"].append(failed_history)
        self.write(failed_stop)
        verified = self.record("stopped-verified")
        verified["history"].insert(-1, failed_history)
        self.refused(verified, "worker-not-settled")
        self.settle(1)
        self.assertEqual(
            self.write(verified)["checkpoint"],
            "stopped-verified",
        )

    def test_release_and_completion_need_their_own_readbacks(self):
        cases = (
            (
                "released-verified",
                {"terminal_state": "retained", "release_state": "retained"},
                "terminal-not-released",
            ),
            (
                "released-verified",
                {"terminal_state": "released", "release_state": "release_unknown"},
                "terminal-not-released",
            ),
            ("completed-verified", {"task_status": "blocked"}, "task-not-completed"),
            ("completed-verified", {"show_ok": False}, "launch-proof-unverified"),
        )
        for checkpoint, state, reason in cases:
            with self.subTest(checkpoint=checkpoint, reason=reason):
                self.reset()
                self.progress(self.CHECKPOINTS[self.CHECKPOINTS.index(checkpoint) - 1])
                self.settle(HELPER["VERIFIED_CHECKPOINTS"][checkpoint])
                vars(self.orca).update(state)
                self.refused(self.record(checkpoint), reason)

    def test_completion_reverifies_file_hashes_and_commits(self):
        mutations = {
            "file changed": lambda: self.artifact.write_text(
                "# edited\n", encoding="utf-8", newline="\n"
            ),
            "commit missing": lambda: self.orca.commits.clear(),
        }
        for reason, mutate in mutations.items():
            with self.subTest(reason=reason):
                self.reset()
                self.progress("completion-requested")
                self.settle(3)
                value = self.record("completed-verified")
                mutate()
                self.refused(value, reason)

    def test_newer_attempt_and_unknown_outcome_block_verification(self):
        self.progress("stop-requested")
        self.settle(1)
        self.orca.extra_workers.append(dict(RETRY_ROW))
        self.refused(self.record("stopped-verified"), "newer-attempt")
        self.reset()
        self.progress("stop-requested")
        self.orca.worker_state, self.orca.dispatch_status = "stopped", "outcome_unknown"
        self.refused(self.record("stopped-verified"), "outcome-unknown")

    def test_readback_identity_must_match_the_record(self):
        cases = (
            ("spec", SPEC.replace("execute stage", "plan stage"), "spec-mismatch"),
            ("spec_truncated", True, "spec-mismatch"),
            ("task_title", "gw-execute-other-0123abcd", "dispatch-key-mismatch"),
            ("worker_run", "run_other", "run-mismatch"),
        )
        for attribute, value, reason in cases:
            with self.subTest(attribute=attribute):
                self.reset()
                self.progress("stop-requested")
                self.settle(1)
                setattr(self.orca, attribute, value)
                self.refused(self.record("stopped-verified"), reason)

    def test_worker_list_reads_every_page_before_choosing_the_latest_attempt(self):
        self.progress("stop-requested")
        self.settle(1)
        self.orca.extra_workers.append(dict(RETRY_ROW))
        self.orca.worker_page_size = 1
        self.refused(self.record("stopped-verified"), "newer-attempt")
        worker_calls = [call for call in self.orca.calls if call[2] == "worker-list"]
        self.assertEqual(len(worker_calls), 2)
        self.assertEqual(worker_calls[1][-3:], ["--cursor", "cursor-1", "--json"])

    def test_worker_list_rejects_untrustworthy_pagination_and_scope(self):
        cases = (
            ("missing cursor", {"worker_page_size": 1, "worker_cursors": {0: None}}),
            ("malformed cursor", {"worker_page_size": 1, "worker_cursors": {0: 17}}),
            (
                "repeated cursor",
                {
                    "worker_page_size": 1,
                    "extra_workers": [dict(RETRY_ROW), dict(RETRY_ROW, dispatchId="ctx_retry_2")],
                    "worker_cursors": {1: "cursor-1"},
                },
            ),
            ("run scope", {"worker_scope": {"run": "run_other", "source": "flag"}}),
        )
        for reason, changes in cases:
            with self.subTest(reason=reason):
                self.reset()
                self.progress("stop-requested")
                self.settle(1)
                self.orca.extra_workers.append(dict(RETRY_ROW))
                vars(self.orca).update(changes)
                self.refused(self.record("stopped-verified"), reason)

    def test_worker_list_rejects_a_premature_final_page(self):
        self.progress("stop-requested")
        self.settle(1)
        self.orca.worker_page_totals[0] = 2
        self.refused(self.record("stopped-verified"), "incomplete pagination")

    def test_worker_list_rejects_an_empty_advancing_page(self):
        self.progress("stop-requested")
        self.settle(1)
        self.orca.worker_page_rows[0] = []
        self.orca.worker_page_has_more[0] = True
        self.refused(self.record("stopped-verified"), "empty nonterminal page")

    def test_worker_list_rejects_total_drift(self):
        self.progress("stop-requested")
        self.settle(1)
        self.orca.extra_workers.append(dict(RETRY_ROW))
        self.orca.worker_page_size = 1
        self.orca.worker_page_totals[1] = 3
        self.refused(self.record("stopped-verified"), "total changed")

    def test_worker_list_rejects_invalid_totals(self):
        for total in (None, -1, True, 1.0, "1"):
            with self.subTest(total=total):
                self.reset()
                self.progress("stop-requested")
                self.settle(1)
                self.orca.worker_page_totals[0] = total
                self.refused(self.record("stopped-verified"), "invalid total")


class W7ControlSequenceTests(RecoveryFixture):
    """Control fixture shaped like spike W7 (receipts 331-354); not a live rejection reproduction."""

    def test_verified_stop_release_and_completion_record_every_checkpoint(self):
        self.progress("completed-verified")
        saved = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(saved["checkpoint"], "completed-verified")
        self.assertEqual(
            [entry["checkpoint"] for entry in saved["history"]],
            list(self.CHECKPOINTS),
        )
        self.assertEqual(
            [mutation["action"] for mutation in saved["mutations"]],
            ["worker-stop", "worker-release", "task-update"],
        )
        self.assertEqual(self.orca.mutations(), [], "the helper only reads; the coordinator mutates")


class RestartTests(RecoveryFixture):
    def test_restart_at_each_checkpoint_resumes_recovery(self):
        expected = {
            "inspection": (0, "live", "current-evidence-wins"),
            "stop-requested": (0, "live", "current-evidence-wins"),
            "stopped-verified": (1, "recovery-inspection", "recovery-incomplete"),
            "release-requested": (1, "recovery-inspection", "recovery-incomplete"),
            "released-verified": (2, "recovery-inspection", "recovery-incomplete"),
            "completion-requested": (2, "recovery-inspection", "recovery-incomplete"),
        }
        for checkpoint, (level, action, reason) in expected.items():
            with self.subTest(checkpoint=checkpoint):
                self.reset()
                self.settle(level)
                self.place(self.record(checkpoint))
                self.assertEqual(
                    self.classify()[TASK],
                    {
                        "task_id": TASK,
                        "dispatch_id": DISPATCH,
                        "action": action,
                        "recovery": {"checkpoint": checkpoint, "reason": reason},
                    },
                )

    def retried_twice(self) -> None:
        """The run_7de888bf105a incident: abandoned root, failed retry, live retry-of-retry."""
        self.orca.worker_state, self.orca.dispatch_status = "abandoned", "failed"
        self.orca.liveness = {"verdict": "exited", "source": "resource_release"}
        self.orca.extra_workers.append(
            dict(RETRY_ROW, dispatchId="ctx_second", workerState="failed", dispatchStatus="failed")
        )
        self.orca.extra_workers.append(dict(RETRY_ROW))

    def test_a_retried_task_classifies_on_its_newest_dispatch(self):
        # worker-list is newest first; the last row is the oldest attempt, so
        # taking it routes a running retry into recovery inspection.
        self.retried_twice()
        self.assertEqual(
            self.classify(records=[])[TASK],
            {"task_id": TASK, "dispatch_id": "ctx_retry", "action": "live"},
        )

    def test_the_retry_chain_breaks_a_created_at_tie(self):
        self.retried_twice()
        for dispatch_id in (DISPATCH, "ctx_second", "ctx_retry"):
            self.orca.dispatch_meta[dispatch_id] = {"createdAt": "2026-09-13 20:00:00"}
        self.assertEqual(self.classify(records=[])[TASK]["dispatch_id"], "ctx_retry")

    def test_list_order_that_disagrees_with_creation_order_is_ambiguous(self):
        self.retried_twice()
        self.orca.dispatch_meta[DISPATCH] = {"createdAt": "2026-09-13 21:00:00"}
        self.assertEqual(
            self.classify(records=[])[TASK],
            {
                "task_id": TASK,
                "dispatch_id": "ctx_retry",
                "action": "recovery-inspection",
                "recovery": {"checkpoint": None, "reason": "latest-attempt-ambiguous"},
            },
        )

    def test_a_retried_row_that_is_itself_retried_is_ambiguous(self):
        self.retried_twice()
        self.orca.dispatch_meta[DISPATCH] = {"retryOfDispatchId": "ctx_retry"}
        self.assertEqual(
            self.classify(records=[])[TASK]["recovery"]["reason"], "latest-attempt-ambiguous"
        )

    def test_an_unreadable_attempt_on_a_retried_task_is_ambiguous(self):
        self.retried_twice()
        self.orca.show_ok = False
        self.assertEqual(
            self.classify(records=[])[TASK]["recovery"]["reason"], "latest-attempt-ambiguous"
        )

    def test_a_single_attempt_task_needs_no_ordering_read(self):
        self.classify(records=[])
        self.assertEqual([call for call in self.orca.calls if call[2] == "worker-show"], [])

    def test_a_parked_task_is_not_reported_as_a_deliberate_skip(self):
        # §2.5.2's worker-stop leaves exactly the deliberate-skip signature --
        # blocked Task, stopped worker -- and writes no marker of its own. The
        # checkpoint on disk is the only thing that tells them apart, so a
        # restarted coordinator without this join reports parked work as
        # "skipped by a human" and never resumes it.
        self.settle(1)
        self.assertEqual(self.classify(records=[])[TASK]["action"], "deliberate-skip")
        self.assertEqual(
            self.classify(records=[], checkpoints=[self.checkpoint()])[TASK]["action"],
            "parked",
        )

    def test_a_checkpoint_for_another_item_or_phase_leaves_the_skip_alone(self):
        self.settle(1)
        for item, phase in (("work/other", "execute"), ("work/example", "finish")):
            with self.subTest(item=item, phase=phase):
                rows = self.classify(records=[], checkpoints=[self.checkpoint(item, phase)])
                self.assertEqual(rows[TASK]["action"], "deliberate-skip")

    def test_a_task_with_no_display_name_can_never_be_called_parked(self):
        # A dispatch key is not reversible to a path; the display name is the
        # only durable carrier. Without one there is nothing to join on, and
        # guessing would resume the wrong item.
        self.settle(1)
        self.orca.display_name = None
        self.assertEqual(
            self.classify(records=[], checkpoints=[self.checkpoint()])[TASK]["action"],
            "deliberate-skip",
        )

    def test_a_live_or_settled_task_is_never_reclassified_as_parked(self):
        checkpoints = [self.checkpoint()]
        self.assertEqual(self.classify(records=[], checkpoints=checkpoints)[TASK]["action"], "live")
        self.settle(3)
        self.orca.worker_state = "succeeded"
        self.assertEqual(
            self.classify(records=[], checkpoints=checkpoints)[TASK]["action"], "settled"
        )

    def test_a_malformed_checkpoint_is_refused_rather_than_ignored(self):
        self.settle(1)
        broken = self.root / "03-execute-checkpoint-D-002.md"
        broken.write_text("no frontmatter here\n", encoding="utf-8", newline="\n")
        with self.assertRaises(SystemExit):
            self.classify(records=[], checkpoints=[str(broken)])

    def test_stopped_blocked_pending_recovery_is_not_a_deliberate_skip(self):
        self.settle(1)
        self.assertEqual(self.classify(records=[])[TASK]["action"], "deliberate-skip")
        self.place(self.record("stopped-verified"))
        self.assertEqual(self.classify()[TASK]["action"], "recovery-inspection")

    def test_unresolved_reason_is_reported_with_its_checkpoint(self):
        self.settle(1)
        self.place(
            self.record(
                "release-requested", unresolved="worker-release retained: identity_unproven"
            )
        )
        self.assertEqual(
            self.classify()[TASK]["recovery"],
            {
                "checkpoint": "release-requested",
                "reason": "worker-release retained: identity_unproven",
            },
        )

    def test_completed_task_with_stopped_worker_and_no_record_is_not_success(self):
        self.settle(3)
        self.assertEqual(
            self.classify(records=[])[TASK],
            {
                "task_id": TASK,
                "dispatch_id": DISPATCH,
                "action": "recovery-inspection",
            },
        )

    def test_verified_final_record_survives_restart_as_recovered_settled(self):
        self.progress("completed-verified")
        self.assertEqual(
            self.classify()[TASK],
            {
                "task_id": TASK,
                "dispatch_id": DISPATCH,
                "action": "recovered-settled",
                "recovery": {"checkpoint": "completed-verified", "reason": "verified"},
            },
        )
        self.assertEqual(self.orca.mutations(), [])

    def test_recovered_settled_refuses_any_disagreeing_evidence(self):
        cases = {
            "spec-mismatch": lambda: setattr(
                self.orca, "spec", SPEC.replace("execute stage", "plan stage")
            ),
            "dispatch-key-mismatch": lambda: setattr(
                self.orca, "task_title", "gw-execute-other-0123abcd"
            ),
            "run-mismatch": lambda: setattr(self.orca, "worker_run", "run_other"),
            "terminal-not-released": lambda: setattr(self.orca, "release_state", "retained"),
            "task-not-completed": lambda: setattr(self.orca, "task_status", "blocked"),
            "file changed": lambda: self.artifact.write_text(
                "# edited\n", encoding="utf-8", newline="\n"
            ),
            "commit missing": lambda: self.orca.commits.clear(),
            "launch-proof-unverified": lambda: setattr(self.orca, "show_ok", False),
        }
        for reason, mutate in cases.items():
            with self.subTest(reason=reason):
                self.reset()
                self.progress("completed-verified")
                mutate()
                row = self.classify()[TASK]
                self.assertEqual(row["action"], "recovery-inspection")
                self.assertIn(reason, row["recovery"]["reason"])

    def test_live_or_unknown_current_evidence_wins_over_a_completed_record(self):
        self.progress("completed-verified")
        self.orca.extra_workers.append(dict(RETRY_ROW))
        self.assertEqual(
            self.classify()[TASK],
            {
                "task_id": TASK,
                "dispatch_id": "ctx_retry",
                "action": "live",
                "recovery": {"checkpoint": None, "reason": "current-evidence-wins"},
            },
        )
        self.orca.extra_workers[-1]["dispatchStatus"] = "outcome_unknown"
        self.assertEqual(
            self.classify()[TASK]["recovery"],
            {"checkpoint": None, "reason": "record-not-latest-attempt"},
        )
        self.assertEqual(self.classify()[TASK]["action"], "recovery-inspection")

    def test_invalid_duplicate_and_orphan_records_stay_visible(self):
        self.settle(3)
        invalid = self.record("completed-verified")
        invalid["checkpoint"] = "bogus"
        self.place(invalid)
        self.assertIn("Invalid recovery record", self.classify()[TASK]["recovery"]["reason"])
        self.reset()
        self.progress("completed-verified")
        copy = self.place(
            self.record("completed-verified"), self.root / "second" / f"{DISPATCH}.json"
        )
        self.assertEqual(
            self.classify(records=[str(self.target), str(copy)])[TASK]["recovery"],
            {"checkpoint": None, "reason": "duplicate-records"},
        )
        orphan = self.place(
            self.record(task_id="task_gone"), self.root / "orphan" / f"{DISPATCH}.json"
        )
        elsewhere = self.place(
            self.record(task_id="task_elsewhere", run_id="run_other"),
            self.root / "elsewhere" / f"{DISPATCH}.json",
        )
        rows = self.classify(records=[str(self.target), str(orphan), str(elsewhere)])
        self.assertEqual(set(rows), {TASK, "task_gone"})
        self.assertEqual(
            rows["task_gone"],
            {
                "task_id": "task_gone",
                "dispatch_id": DISPATCH,
                "action": "recovery-inspection",
                "recovery": {"checkpoint": None, "reason": "record-task-not-in-run"},
            },
        )

    def test_unjoinable_record_files_stop_the_restart(self):
        misnamed = self.place(self.record(), self.root / "misnamed.json")
        with self.assertRaises(SystemExit):
            self.classify(records=[str(misnamed)])
        untasked = self.record()
        del untasked["task_id"]
        with self.assertRaises(SystemExit):
            self.classify(records=[str(self.place(untasked))])

    def test_record_bearing_restart_rejects_an_advertised_partial_snapshot(self):
        self.progress("completed-verified")
        self.orca.worker_page_has_more[0] = True
        self.orca.worker_cursors[0] = "cursor-1"
        self.orca.worker_page_totals[0] = 2
        self.orca.calls.clear()
        with self.assertRaises(SystemExit) as caught:
            self.classify()
        self.assertIn("incomplete", str(caught.exception))
        self.assertEqual(self.orca.calls, [], "restart must not fetch a replacement worker list")

    def test_legacy_restart_ignores_advertised_partial_snapshot_without_records(self):
        self.settle(3)
        self.orca.worker_page_has_more[0] = True
        self.orca.worker_cursors[0] = "cursor-1"
        self.orca.worker_page_totals[0] = 2
        self.assertEqual(
            self.classify(records=[])[TASK],
            {
                "task_id": TASK,
                "dispatch_id": DISPATCH,
                "action": "recovery-inspection",
            },
        )

    def test_record_bearing_restart_rejects_invalid_totals_and_count_disagreements(self):
        cases = (None, -1, True, 1.0, "1", 2)
        for total in cases:
            with self.subTest(total=total):
                self.reset()
                self.progress("completed-verified")
                self.orca.worker_page_totals[0] = total
                with self.assertRaises(SystemExit):
                    self.classify()

    def test_record_bearing_restart_rejects_a_cursor_on_a_final_snapshot(self):
        self.progress("completed-verified")
        self.orca.worker_cursors[0] = "cursor-1"
        with self.assertRaises(SystemExit) as caught:
            self.classify()
        self.assertIn("cursor", str(caught.exception))

    def test_complete_assembled_snapshot_supports_recovered_settled(self):
        self.progress("completed-verified")
        self.orca.extra_workers.append(
            {
                **RETRY_ROW,
                "dispatchId": "ctx_elsewhere",
                "taskId": "task_elsewhere",
            }
        )
        self.assertEqual(
            self.classify()[TASK]["action"],
            "recovered-settled",
        )


class ReplayTests(RecoveryFixture):
    def test_replayed_writes_and_restarts_repeat_no_lifecycle_action(self):
        self.progress("completed-verified")
        saved = self.target.read_bytes()
        self.assertFalse(self.write(self.record("completed-verified"))["changed"])
        first, second = self.classify(), self.classify()
        self.assertEqual(first, second)
        self.assertEqual(first[TASK]["action"], "recovered-settled")
        self.assertEqual(self.target.read_bytes(), saved)
        self.assertEqual(self.orca.mutations(), [])


class FinalReviewTests(RecoveryFixture):
    def annotate(self, record, reason):
        value = json.loads(json.dumps(record))
        value["unresolved"] = reason
        value["history"].append({
            "checkpoint": value["checkpoint"], "at": "2026-09-13T22:00:00Z",
            "note": "refused: " + reason if reason else "reverified: fresh proof",
        })
        return value

    def test_fleet_liveness_guards_every_verified_checkpoint(self):
        for checkpoint in ("stopped-verified", "released-verified", "completed-verified"):
            for liveness in (None, [], {}, {"verdict": "live"}, {"verdict": "unverifiable"},
                             {"verdict": "future-verdict"}):
                with self.subTest(checkpoint=checkpoint, liveness=liveness):
                    self.reset()
                    self.progress(checkpoint)
                    self.orca.liveness = liveness
                    self.refused(self.record(checkpoint), "liveness")

    def test_recovery_liveness_wins_even_over_legacy_success_shortcut(self):
        for state in ("stopped", "succeeded"):
            for verdict, action in (("live", "live"), ("unverifiable", "recovery-inspection"),
                                    (None, "recovery-inspection"), ("exited", "recovered-settled")):
                with self.subTest(state=state, verdict=verdict):
                    self.reset()
                    self.progress("completed-verified")
                    self.orca.worker_state = state
                    # Dispatch failed: a succeeded workerState alone is not accepted completion.
                    self.orca.liveness = {"verdict": verdict}
                    self.assertEqual(self.classify()[TASK]["action"], action)
                    if state == "succeeded":
                        self.assertEqual(self.classify(records=[])[TASK]["action"], "settled")

    def test_current_accepted_success_duplicate_keeps_normal_cleanup(self):
        self.progress("completed-verified")
        self.orca.worker_state = "succeeded"
        self.orca.dispatch_status = "completed"
        self.orca.terminal_state = self.orca.release_state = "reclaimable"
        self.orca.liveness = {"verdict": "live", "source": "agent_status"}
        self.place(self.annotate(self.record("completed-verified"), "old refusal"))
        # Fresh Task + Dispatch completion and launch proof, independent of the saved claim.
        self.assertEqual(self.classify()[TASK]["action"], "settled")
        self.orca.task_status = "blocked"
        self.assertEqual(self.classify()[TASK]["action"], "live")
        self.orca.task_status = "completed"
        self.orca.show_ok = False
        self.assertEqual(self.classify()[TASK]["action"], "live")

    def test_later_refusals_persist_without_reverifying_historical_progress(self):
        for checkpoint in ("stopped-verified", "released-verified", "completed-verified"):
            for failure in ("readback unavailable", "artifact drift"):
                with self.subTest(checkpoint=checkpoint, failure=failure):
                    self.reset()
                    self.progress(checkpoint)
                    saved = json.loads(self.target.read_text(encoding="utf-8"))
                    if failure == "artifact drift":
                        self.artifact.write_text("changed", encoding="utf-8", newline="\n")
                    else:
                        self.orca.liveness = {"verdict": "unverifiable"}
                    refused = self.annotate(saved, failure)
                    with patch("subprocess.run", side_effect=OSError("readback unavailable")):
                        # write() normally installs its own process control; call the real entry directly.
                        source = self.root / "refusal.json"
                        source.write_text(json.dumps(refused), encoding="utf-8", newline="\n")
                        with contextlib.redirect_stdout(io.StringIO()):
                            HELPER["write_record"](argparse.Namespace(
                                path=str(self.target), record=str(source), orca="fake-orca"))
                    self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), refused)
                    self.assertEqual(self.classify()[TASK]["action"], "recovery-inspection")
                    cleared = self.annotate(refused, None)
                    self.refused(cleared, "not verified")
                    self.artifact.write_text(ARTIFACT_TEXT, encoding="utf-8", newline="\n")
                    self.orca.liveness = {"verdict": "exited"}
                    self.assertEqual(self.classify()[TASK]["action"], "recovery-inspection")
                    self.assertTrue(self.write(cleared)["changed"])
                    self.assertEqual(self.classify()[TASK]["action"],
                                     "recovered-settled" if checkpoint == "completed-verified"
                                     else "recovery-inspection")

    def test_refusal_exception_cannot_change_protected_evidence_or_authority(self):
        for checkpoint in ("stopped-verified", "released-verified", "completed-verified"):
            for field in ("evidence", "judgment", "stop_authority", "placement", "spec_sha256"):
                with self.subTest(checkpoint=checkpoint, field=field):
                    self.reset()
                    self.progress(checkpoint)
                    before = self.target.read_bytes()
                    refused = self.annotate(json.loads(before), "readback unavailable")
                    if field == "evidence":
                        refused[field][0]["sha256"] = "0" * 64
                        refused["history"][-1]["note"] = "reattested: cannot bypass verification"
                    elif field == "judgment":
                        refused[field] = "unestablished"
                    elif field == "stop_authority":
                        refused[field]["source"] = "different authority"
                    elif field == "placement":
                        refused[field]["worktree"] = "/tmp/different"
                    else:
                        refused[field] = "0" * 64
                    self.orca.liveness = {"verdict": "unverifiable"}
                    with self.assertRaises(SystemExit):
                        self.write(refused)
                    self.assertEqual(self.target.read_bytes(), before)

    def test_unresolved_verified_checkpoint_cannot_advance_without_fresh_proof(self):
        for checkpoint, next_checkpoint in (("stopped-verified", "release-requested"),
                                             ("released-verified", "completion-requested")):
            with self.subTest(checkpoint=checkpoint):
                self.reset()
                self.progress(checkpoint)
                refused = self.annotate(json.loads(self.target.read_text(encoding="utf-8")), "drift")
                self.write(refused)
                self.orca.liveness = {"verdict": "unverifiable"}
                advanced = self.annotate(refused, None)
                advanced["checkpoint"] = advanced["history"][-1]["checkpoint"] = next_checkpoint
                self.refused(advanced, "not verified")

    def test_unknown_request_identity_survives_interruption_for_each_operation(self):
        cases = (("inspection", "stop-requested", "stopped-verified", "worker-stop", 1),
                 ("stopped-verified", "release-requested", "released-verified", "worker-release", 2),
                 ("released-verified", "completion-requested", "completed-verified", "task-update", 3))
        for previous, requested, verified, action, level in cases:
            with self.subTest(action=action):
                self.reset()
                self.progress(previous)
                intent = self.annotate(json.loads(self.target.read_text(encoding="utf-8")),
                                       "original request identity unknown: " + action)
                intent["checkpoint"] = intent["history"][-1]["checkpoint"] = requested
                intent["mutations"].append({"action": action, "request_id": None,
                    "receipt": "sanitized operation output reference", "at": "2026-09-13T22:00:01Z"})
                self.write(intent)  # Durable intent before the coordinator's operation.
                self.settle(level)  # Control: operation landed, response was lost before persistence.
                self.orca.calls.clear()
                for _ in range(2):
                    self.assertEqual(self.classify()[TASK]["action"], "recovery-inspection")
                self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), intent)
                cleared = self.annotate(intent, None)
                self.refused(cleared, "request identity")
                advanced = self.annotate(intent, "still unknown")
                advanced["checkpoint"] = advanced["history"][-1]["checkpoint"] = verified
                self.refused(advanced, "request identity")
                # Coordinator recovered the ORIGINAL ID from durable captured output, not a new retry ID.
                recovered = self.annotate(intent, "original identity recovered; readback unavailable")
                recovered["mutations"].append({"action": action, "request_id": "original-" + action,
                    "receipt": "sanitized recovered original receipt", "at": "2026-09-13T22:00:02Z"})
                # Saving a receipt must survive unavailable state readback too.
                self.orca.liveness = {"verdict": "unverifiable"}
                self.write(recovered)
                self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), recovered)
                self.orca.liveness = {"verdict": "exited"}
                recovered = self.annotate(recovered, None)
                recovered["checkpoint"] = recovered["history"][-1]["checkpoint"] = verified
                self.write(recovered)
                self.assertEqual(self.orca.mutations(), [])

    def test_restart_rejects_malformed_page_and_nonboolean_has_more(self):
        self.progress("completed-verified")
        for page in (None, [], {"hasMore": "false", "total": 1},
                     {"hasMore": 0, "total": 1}, {"total": 1}):
            with self.subTest(page=page):
                payload = self.orca.workers()
                payload["result"]["page"] = page
                with patch.object(self.orca, "workers", return_value=payload):
                    with self.assertRaisesRegex(SystemExit, "malformed pagination"):
                        self.classify()


if __name__ == "__main__":
    unittest.main()
