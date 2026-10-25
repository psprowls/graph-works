"""Placement notifications exercise the real port against synthetic CLI envelopes."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from workflow_orca import _cli
from workflow_orca._cli import OrcaResult
from workflow_orca.placement import OrcaPlacementNotifier
from workflow_orca.port import OrcaCliPort


@dataclass(frozen=True)
class Event:
    repo_root: Path = Path("/repo")
    worktree: Path = Path("/worktrees/repo/feature/placement-verb")
    parent_worktree: Path = Path("/repo")
    slug: str = "feature-placement-verb"
    worktrees_dir: Path = Path("/worktrees")


def success(**result):
    return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")


def failure(code="selector_not_found"):
    return OrcaResult(1, json.dumps({"ok": False, "error": {"code": code, "message": "unavailable"}}), "")


CHILD = success(worktree={"id": "child", "path": str(Event().worktree)})
PARENT = success(worktree={"id": "parent", "path": "/repo", "displayName": "main"})


class Runner:
    """Script each CLI operation independently; unspecified operations are errors."""

    def __init__(self, *, repo=None, child=None, parent=None, setting=None):
        self.responses = {
            "repo": repo if repo is not None else [success(repo={"worktreeBasePath": "/worktrees"})],
            "child": child if child is not None else [CHILD],
            "parent": parent if parent is not None else [PARENT],
            "set": setting if setting is not None else [success()],
        }
        self.calls = []

    def __call__(self, argv):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv[1:3] == ("repo", "show"):
            operation = "repo"
        elif argv[1:3] == ("worktree", "set"):
            operation = "set"
        else:
            assert argv[1:3] == ("worktree", "show")
            operation = "parent" if argv[4] == "path:/repo" else "child"
        response = self.responses[operation].pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_links_under_parent():
    runner = Runner()
    note = OrcaPlacementNotifier(OrcaCliPort(run=runner)).placed(Event())
    assert note == "orca: linked under main"
    assert runner.calls[-1] == (
        "orca",
        "worktree",
        "set",
        "--worktree",
        "path:/worktrees/repo/feature/placement-verb",
        "--parent-worktree",
        "path:/repo",
        "--display-name",
        "feature-placement-verb",
        "--json",
    )


@pytest.mark.parametrize("stalled", ["repo", "child", "parent", "set", None])
def test_default_notifier_bounds_every_subprocess(monkeypatch, stalled):
    responses = Runner()
    timeouts = []

    def run(argv, **kwargs):
        operation = (
            "repo"
            if argv[1] == "repo"
            else "set"
            if argv[2] == "set"
            else "parent"
            if argv[4] == "path:/repo"
            else "child"
        )
        timeouts.append(kwargs.get("timeout"))
        if operation == stalled:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        response = responses(argv)
        return subprocess.CompletedProcess(argv, response.returncode, response.stdout, response.stderr)

    monkeypatch.setattr(_cli.subprocess, "run", run)
    note = OrcaPlacementNotifier().placed(Event())

    assert timeouts and all(timeout == 5 for timeout in timeouts)
    if stalled is None:
        assert note == "orca: linked under main"
    else:
        assert note.startswith("orca: unavailable (") and "timed out" in note


def test_default_port_long_polling_has_no_notification_timeout(monkeypatch):
    calls = []

    # Deliberately retain the established subprocess seam: no timeout keyword.
    def run(argv, *, capture_output, text, check):
        calls.append(argv)
        response = success(messages=[])
        return subprocess.CompletedProcess(argv, response.returncode, response.stdout, response.stderr)

    monkeypatch.setattr(_cli.subprocess, "run", run)
    delivery = OrcaCliPort().check_wait("run", types="worker_done", timeout_ms=600000, ack=None)

    assert delivery == {"delivery_id": None, "messages": []}
    assert calls == [
        [
            "orca",
            "orchestration",
            "check",
            "--run",
            "run",
            "--wait",
            "--types",
            "worker_done",
            "--timeout-ms",
            "600000",
            "--json",
        ]
    ]


@pytest.mark.parametrize("missing", [failure(), success(worktree=None)])
def test_retries_selector_not_found(missing):
    runner = Runner(child=[missing, missing, CHILD])
    sleeps = []
    note = OrcaPlacementNotifier(OrcaCliPort(run=runner), sleep=sleeps.append).placed(Event())
    assert note == "orca: linked under main"
    child_shows = [
        call
        for call in runner.calls
        if call[1:3] == ("worktree", "show") and call[4] == "path:/worktrees/repo/feature/placement-verb"
    ]
    assert len(child_shows) == 3
    assert sleeps == [1.0, 1.0]


def test_not_found_after_retries():
    runner = Runner(child=[failure()] * 5)
    sleeps = []
    note = OrcaPlacementNotifier(OrcaCliPort(run=runner), sleep=sleeps.append).placed(Event())
    assert note == (
        "orca: not found after 5s; run: orca worktree set "
        "--worktree path:/worktrees/repo/feature/placement-verb "
        "--parent-worktree path:/repo --display-name feature-placement-verb"
    )
    assert len(runner.calls) == 6
    assert sleeps == [1.0] * 5
    assert all(call[1:3] != ("worktree", "set") for call in runner.calls)


def test_configurable_discovery_budget():
    runner = Runner(child=[failure()] * 2)
    sleeps = []
    note = OrcaPlacementNotifier(OrcaCliPort(run=runner), attempts=2, interval=0.25, sleep=sleeps.append).placed(
        Event()
    )
    assert note.startswith("orca: not found after 0.5s;")
    assert sleeps == [0.25, 0.25]


@pytest.mark.parametrize("missing", [failure(), success(worktree=None)])
def test_parent_fallback(missing):
    runner = Runner(parent=[missing])
    note = OrcaPlacementNotifier(OrcaCliPort(run=runner)).placed(Event())
    assert note == "orca: parent not found"
    assert runner.calls[-1] == (
        "orca",
        "worktree",
        "set",
        "--worktree",
        "path:/worktrees/repo/feature/placement-verb",
        "--display-name",
        "feature-placement-verb",
        "--json",
    )


def test_parent_without_display_name_uses_event_path():
    runner = Runner(parent=[success(worktree={"id": "parent"})])
    assert OrcaPlacementNotifier(OrcaCliPort(run=runner)).placed(Event()) == "orca: linked under /repo"


def test_unavailable_binary():
    runner = Runner(repo=[FileNotFoundError("orca missing")], child=[FileNotFoundError("orca missing")])
    assert OrcaPlacementNotifier(OrcaCliPort(run=runner)).placed(Event()) == "orca: unavailable (orca missing)"


@pytest.mark.parametrize("code", ["unknown_repository", "repo_not_found", "repository_not_found", "selector_not_found"])
def test_unknown_repository(code):
    runner = Runner(repo=[failure(code)])
    assert OrcaPlacementNotifier(OrcaCliPort(run=runner)).placed(Event()) == "orca: unknown repository"
    assert len(runner.calls) == 1


@pytest.mark.parametrize("operation", ["child", "parent", "setting"])
@pytest.mark.parametrize("error", [failure("permission_denied"), OSError("offline")])
def test_other_notification_failures_are_notes(operation, error):
    runner = Runner(**{operation: [error]})
    note = OrcaPlacementNotifier(OrcaCliPort(run=runner)).placed(Event())
    assert note.startswith("orca: unavailable (")
    assert "offline" in note if isinstance(error, OSError) else "permission_denied" in note


@pytest.mark.parametrize("operation", ["child", "parent"])
def test_malformed_vendor_worktree_is_a_note(operation):
    runner = Runner(**{operation: [success(worktree={"path": "/missing-id"})]})
    note = OrcaPlacementNotifier(OrcaCliPort(run=runner)).placed(Event())
    assert note.startswith("orca: unavailable (")
    assert "id" in note


def test_base_path_note_once_per_repo():
    runner = Runner(
        repo=[success(repo={"worktreeBasePath": "/elsewhere"})],
        child=[CHILD] * 2,
        parent=[PARENT] * 2,
        setting=[success()] * 2,
    )
    notifier = OrcaPlacementNotifier(OrcaCliPort(run=runner))
    assert notifier.placed(Event()) == (
        "orca: worktreeBasePath /elsewhere differs from layout worktrees_dir /worktrees; orca: linked under main"
    )
    assert notifier.placed(Event()) == "orca: linked under main"
    assert len([call for call in runner.calls if call[1:3] == ("repo", "show")]) == 1


@pytest.mark.parametrize("result", [failure("offline"), OSError("offline"), RuntimeError("offline"), success(repo={})])
def test_base_path_read_failure_is_silent_and_checked_once(result):
    runner = Runner(repo=[result], child=[CHILD] * 2, parent=[PARENT] * 2, setting=[success()] * 2)
    notifier = OrcaPlacementNotifier(OrcaCliPort(run=runner))
    assert notifier.placed(Event()) == "orca: linked under main"
    assert notifier.placed(Event()) == "orca: linked under main"


def test_base_path_check_uses_resolved_paths_and_tracks_each_repo():
    runner = Runner(
        repo=[success(repo={"worktreeBasePath": "/worktrees/../worktrees"})] * 2,
        child=[CHILD] * 2,
        parent=[PARENT] * 2,
        setting=[success()] * 2,
    )
    notifier = OrcaPlacementNotifier(OrcaCliPort(run=runner))
    assert notifier.placed(Event()) == "orca: linked under main"
    assert notifier.placed(replace(Event(), repo_root=Path("/another"))) == "orca: linked under main"
    assert len([call for call in runner.calls if call[1:3] == ("repo", "show")]) == 2
