"""Timeout liveness rows: one per live dispatch, facts only, never a raise for one worker."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from orca_fakes import FakeRunner, fixture, fixture_result
from workflow_orca import LivenessRow, OrcaBackend, OrcaCliError
from workflow_orca._cli import OrcaResult
from workflow_orca._liveness import instant, iso

TARGET = "auto-drive:2026-08-14-epic-feature-orca-dispatch-backend"
FINISH = "2026-08-13-test-gap-code-wiki-smoke-script#finish"
LIVE_HANDLE = "ctx_320c498114b8"  # the one running row in worker_list.json

BASE = [
    (("run-list", "--cursor"), "run_list_page2"),
    (("run-list",), "run_list"),
    (("run-use",), "run_create"),
    (("task-list",), "task_list"),
    (("worker-list",), "worker_list"),
    (("worktree", "show"), "worktree_show_renamed"),
]

SHOW = fixture_result("worker_show_live_terminal")
HEARTBEAT = instant(SHOW["dispatch"]["lastHeartbeatAt"])
CREATED = instant(SHOW["dispatch"]["createdAt"])
NOW = HEARTBEAT + timedelta(seconds=90)


def _envelope(result: dict) -> dict:
    return {"id": "x", "ok": True, "result": result}


def _failure(code: str) -> dict:
    return {"id": "x", "ok": False, "error": {"code": code, "message": "nope"}}


class Runner(FakeRunner):
    """FakeRunner plus per-command replacement bodies (full envelopes)."""

    def __init__(self, routes, replies=None):
        super().__init__(routes)
        self.replies = replies or {}

    def __call__(self, argv):
        for token, body in self.replies.items():
            if token in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(body), stderr="")
        return super().__call__(argv)


def _show(worktree: Path | None = None, **patch) -> dict:
    shown = json.loads(fixture("worker_show_live_terminal"))["result"]
    if worktree is not None:
        shown["terminal"]["worktreePath"] = str(worktree)
    for dotted, value in patch.items():
        block, field = dotted.split("__")
        if value is None:
            shown[block].pop(field, None)
        else:
            shown[block][field] = value
    return _envelope(shown)


def session(*, show=None, read=None, extra=()):
    replies = {}
    if show is not None:
        replies["worker-show"] = show
    if read is not None:
        replies["worker-read"] = read
    routes = [*extra, (("worker-show",), "worker_show_live_terminal"), (("worker-read",), "worker_read_latest"), *BASE]
    runner = Runner(routes, replies)
    return OrcaBackend(run=runner).open_session(TARGET), runner


def _ledger(worktree: Path, text: str, when: datetime) -> Path:
    path = worktree / ".superpowers" / "sdd" / "plan" / "progress.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def test_all_three_activity_sources_and_progress(tmp_path):
    (tmp_path / "plan.md").write_text("### Task 1: a\n### Task 2: b\n", encoding="utf-8", newline="\n")
    ledger = _ledger(tmp_path, "# SDD ledger — plan: plan.md\nTask 1: complete\n", CREATED + timedelta(minutes=1))
    sess, _ = session(show=_show(tmp_path))
    [row] = sess.liveness(now=NOW)

    read = fixture_result("worker_read_latest")
    transcript = instant(read["transcript"]["messages"][-1]["timestamp"])
    output = instant(SHOW["terminal"]["lastOutputAt"])
    assert isinstance(row, LivenessRow)
    assert (row.key, row.handle, row.state) == (FINISH, LIVE_HANDLE, "running")
    assert (row.heartbeat_at, row.heartbeat_age_s) == (iso(HEARTBEAT), 90)
    assert row.transcript_at == iso(transcript)
    assert row.transcript_age_s == max(0, int((NOW - transcript).total_seconds()))
    assert row.output_at == iso(output)
    assert row.output_age_s == max(0, int((NOW - output).total_seconds()))
    assert row.worktree_path == str(tmp_path)
    assert row.progress is not None
    assert (row.progress.ledger, row.progress.completed, row.progress.total) == (str(ledger), 1, 2)
    assert row.notes == ()


def test_a_design_stage_worker_has_no_ledger(tmp_path):
    sess, _ = session(show=_show(tmp_path))
    [row] = sess.liveness(now=NOW)
    assert row.progress is None
    assert row.notes == ("no SDD ledger",)


def test_a_ledger_older_than_created_at_is_not_reported(tmp_path):
    _ledger(tmp_path, "# SDD ledger — plan: plan.md\nTask 1: complete\n", CREATED - timedelta(hours=1))
    sess, _ = session(show=_show(tmp_path))
    [row] = sess.liveness(now=NOW)
    assert row.progress is None
    assert row.notes == ("no SDD ledger",)


def test_ages_floor_at_zero_when_now_precedes_the_stamp():
    sess, _ = session(show=_show(Path("/nonexistent-worktree")))
    [row] = sess.liveness(now=HEARTBEAT - timedelta(days=1))
    assert row.heartbeat_age_s == 0
    assert row.output_age_s == 0
    assert row.transcript_age_s == 0


def test_naive_now_is_refused_before_any_call():
    sess, runner = session()
    before = len(runner.calls)
    with pytest.raises(ValueError):
        sess.liveness(now=datetime(2026, 9, 27, 12, 0))
    assert len(runner.calls) == before


def test_one_worker_show_per_worker_and_one_read_per_live_dispatch(tmp_path):
    sess, runner = session(show=_show(tmp_path))
    before = len(runner.calls)
    sess.liveness(now=NOW)
    calls = runner.calls[before:]
    shows = [c for c in calls if "worker-show" in c]
    reads = [c for c in calls if "worker-read" in c]
    rows = fixture_result("worker_list")["workers"]
    # workers() shows only the latest row per task, so one show per task with a row.
    assert len(shows) == len({r["taskId"] for r in rows})
    assert reads == [("orca", "orchestration", "worker-read", "--dispatch", LIVE_HANDLE, "--limit", "1", "--json")]
    assert not [c for c in calls if "worktree" in c]  # terminal.worktreePath answered it


def test_settled_workers_get_no_row():
    sess, _ = session()
    handles = [row.handle for row in sess.liveness(now=NOW)]
    assert handles == [LIVE_HANDLE]


def test_worker_show_failure_keeps_identity_and_notes_the_code():
    sess, _ = session(show=_failure("dispatch_not_found"))
    [row] = sess.liveness(now=NOW)
    assert (row.key, row.handle, row.state) == (FINISH, LIVE_HANDLE, "running")
    assert (row.heartbeat_at, row.output_at, row.worktree_path, row.progress) == (None, None, None, None)
    assert row.notes[0] == "worker-show failed: dispatch_not_found"
    assert "worker-show failed" not in " ".join(row.notes[1:])


def test_worker_read_failure_nulls_transcript_only(tmp_path):
    sess, _ = session(show=_show(tmp_path), read=_failure("worker_identity_changed"))
    [row] = sess.liveness(now=NOW)
    assert (row.transcript_at, row.transcript_age_s) == (None, None)
    assert row.heartbeat_at is not None
    assert "worker-read failed: worker_identity_changed" in row.notes


def test_terminal_source_never_stands_in_for_a_transcript(tmp_path):
    degraded = json.loads(fixture("worker_read_latest"))
    degraded["result"]["source"] = "terminal"
    sess, _ = session(show=_show(tmp_path), read=degraded)
    [row] = sess.liveness(now=NOW)
    assert row.transcript_at is None
    assert "transcript unavailable (terminal source)" in row.notes


def test_empty_transcript_and_missing_timestamp_are_notes(tmp_path):
    empty = json.loads(fixture("worker_read_latest"))
    empty["result"]["transcript"]["messages"] = []
    sess, _ = session(show=_show(tmp_path), read=empty)
    [row] = sess.liveness(now=NOW)
    assert row.transcript_at is None and "transcript empty" in row.notes

    stampless = json.loads(fixture("worker_read_latest"))
    stampless["result"]["transcript"]["messages"][-1].pop("timestamp")
    sess, _ = session(show=_show(tmp_path), read=stampless)
    [row] = sess.liveness(now=NOW)
    assert row.transcript_at is None and "transcript timestamp missing" in row.notes


def test_no_terminal_falls_back_to_worktree_show(tmp_path):
    shown = _show()
    del shown["result"]["terminal"]
    sess, runner = session(show=shown)
    [row] = sess.liveness(now=NOW)
    renamed = fixture_result("worktree_show_renamed")["worktree"]["path"]
    assert (row.output_at, row.output_age_s) == (None, None)
    assert row.worktree_path == renamed
    assert "no terminal" in row.notes
    [call] = runner.calls_matching("worktree", "show")
    assert runner.argv_after("--worktree", call) == f"id:{SHOW['worker']['worktreeId']}"


def test_no_terminal_and_no_worktree_id_is_worktree_unknown():
    shown = _show(worker__worktreeId=None)
    del shown["result"]["terminal"]
    sess, _ = session(show=shown)
    [row] = sess.liveness(now=NOW)
    assert (row.worktree_path, row.progress) == (None, None)
    assert row.notes == ("no terminal", "worktree unknown")


def test_a_failed_worktree_show_is_worktree_unknown():
    shown = _show()
    del shown["result"]["terminal"]
    sess, _ = session(show=shown, extra=())
    sess._run.replies["worktree"] = _failure("worktree_not_found")
    [row] = sess.liveness(now=NOW)
    assert row.worktree_path is None
    assert "worktree unknown" in row.notes


def test_unparseable_orca_timestamps_are_named(tmp_path):
    sess, _ = session(show=_show(tmp_path, dispatch__lastHeartbeatAt="soon", terminal__lastOutputAt="later"))
    [row] = sess.liveness(now=NOW)
    assert (row.heartbeat_at, row.output_at) == (None, None)
    assert "lastHeartbeatAt unparseable" in row.notes
    assert "lastOutputAt unparseable" in row.notes


def test_historical_show_without_created_at_reports_no_progress(tmp_path):
    # worker_show_live.json: snake-case keys, only `dispatched_at`, no terminal.
    historical = json.loads(fixture("worker_show_live"))
    historical["result"]["worker"]["worktreeId"] = "repo::" + str(tmp_path)
    _ledger(tmp_path, "# SDD ledger — plan: plan.md\n", NOW)
    sess, _ = session(show=historical)
    [row] = sess.liveness(now=NOW)
    assert row.heartbeat_at == "2026-08-13T18:38:36Z"  # snake-case historical key still read
    assert row.progress is None
    assert "createdAt unknown" in row.notes


def test_a_failed_enumeration_raises():
    sess, runner = session()
    runner.replies["worker-list"] = _failure("run_not_found")
    with pytest.raises(OrcaCliError):
        sess.liveness(now=NOW)


def test_liveness_does_not_nudge_or_mark_nudged(tmp_path):
    sess, runner = session(show=_show(tmp_path))
    sess.liveness(now=NOW)
    assert not runner.calls_matching("terminal", "send")
    assert sess._nudged == set()


@pytest.mark.parametrize("value", [None, True, 42, "bad", [1], {"x": 1}])
def test_malformed_dispatch_cannot_abort_enumeration(value, tmp_path):
    shown = _show(tmp_path)
    shown["result"]["dispatch"] = value
    sess, _ = session(show=shown)
    [row] = sess.liveness(now=NOW)
    assert (row.key, row.handle, row.state) == (FINISH, LIVE_HANDLE, "running")
    assert row.heartbeat_at is None
    assert "createdAt unknown" in row.notes


@pytest.mark.parametrize("value", [None, True, 42, "bad", {"x": 1}, [None], [True], [42], ["bad"]])
def test_malformed_transcript_messages_are_empty(value, tmp_path):
    read = json.loads(fixture("worker_read_latest"))
    read["result"]["transcript"]["messages"] = value
    sess, _ = session(show=_show(tmp_path), read=read)
    [row] = sess.liveness(now=NOW)
    assert row.transcript_at is None
    assert "transcript empty" in row.notes


@pytest.mark.parametrize("value", [None, True, 42, "", "   ", "bad\x00path", [], {}, ["/tmp"]])
@pytest.mark.parametrize("source", ["terminal", "worktree"])
def test_malformed_worktree_paths_are_unknown(value, source):
    shown = _show(worker__worktreeId=None)
    if source == "terminal":
        shown["result"]["terminal"]["worktreePath"] = value
    else:
        shown["result"].pop("terminal")
        shown["result"]["worker"]["worktreeId"] = "repo::path"
    sess, runner = session(show=shown)
    runner.replies["worktree"] = _envelope({"worktree": {"path": value}})
    [row] = sess.liveness(now=NOW)
    assert row.worktree_path is None and row.progress is None
    assert "worktree unknown" in row.notes


def test_caches_refresh_when_worker_show_disappears(tmp_path):
    sess, runner = session(show=_show(tmp_path))
    [first] = sess.liveness(now=NOW)
    assert first.heartbeat_at is not None
    runner.replies["worker-show"] = _failure("dispatch_not_found")
    [second] = sess.liveness(now=NOW)
    assert second.heartbeat_at is None
    assert second.notes[0] == "worker-show failed: dispatch_not_found"
    runner.replies["worker-show"] = _show(tmp_path)
    [third] = sess.liveness(now=NOW)
    assert third == first


@pytest.mark.parametrize("command", ["task-list", "worker-list"])
def test_each_enumeration_failure_raises(command):
    sess, runner = session()
    runner.replies[command] = _failure("run_not_found")
    with pytest.raises(OrcaCliError):
        sess.liveness(now=NOW)


AGENT_TERMINAL = "term_ff2faacf-db5b-43ad-b05b-c6dfee58eb53"


def test_a_proven_agent_terminal_is_on_the_row(tmp_path):
    sess, _ = session(show=_show(tmp_path, terminal__handle=AGENT_TERMINAL))
    [row] = sess.liveness(now=NOW)
    assert row.handle == LIVE_HANDLE and row.terminal == AGENT_TERMINAL


def test_an_unproven_terminal_is_null(tmp_path):
    sess, _ = session(show=_show(tmp_path))
    [row] = sess.liveness(now=NOW)
    assert row.terminal is None
