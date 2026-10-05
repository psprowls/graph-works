"""Reroute supersedes one settled Orca Task and preserves recovery evidence."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from graph_works_core.orchestrate import dispatch as d
from graph_works_core.orchestrate import dispatch_record as dr
from graph_works_core.orchestrate import reroute
from subagents_io.backend import BackendError
from test_orchestrate_dispatch import KEY
from test_orchestrate_dispatch import env as base_env  # noqa: F401 - registered pytest fixture
from test_orchestrate_dispatch import run as dispatch_run


@pytest.fixture(name="env")
def reroute_env(request: pytest.FixtureRequest):
    layout, plan, port, *_ = request.getfixturevalue("base_env")
    port.tasks = [
        {"id": "task_1", "title": KEY, "display_name": "work/x · execute", "status": "failed", "spec": spec()}
    ]
    port.workers = [worker()]
    return layout, plan, port


def spec():
    return d.encode_launch_envelope(
        key=KEY,
        agent="claude",
        model="opus",
        reasoning_effort=None,
        placement_argv=["--worktree", "path:/old"],
        mode="autonomous",
        worktree_path=None,
        prompt="private\n",
    )


def worker(**changes):
    return {
        "dispatch_id": "ctx_old",
        "task_id": "task_1",
        "state": "failed",
        "dispatch_status": "failed",
        "worktree_id": "wt1",
        **changes,
    }


def run(env, **kwargs):
    return reroute.run_reroute(
        env[0],
        KEY,
        run_id=kwargs.pop("run_id", "run_1"),
        reason=kwargs.pop("reason", "worker stalled"),
        port=env[2],
        clock=lambda: datetime(2026, 9, 26, tzinfo=UTC),
        **kwargs,
    )


def record(env):
    return dr.load_record(dr.record_file(env[0], "work/x", KEY))


def test_reroute_blocks_the_old_task_and_records_why(env):
    result = run(env, agent="codex")
    assert result.ok and result.status == "rerouted"
    assert (result.superseded_task_id, result.superseded_dispatch_id) == ("task_1", "ctx_old")
    assert result.record_path == f"/work/x/references/orca-dispatch/{KEY}.json"
    assert [call[1] for call in env[2].calls if call[0] == "task_update"] == [("run_1", "task_1", "blocked")]
    saved = record(env)
    assert saved.reroutes[-1].reason == "worker stalled"
    assert saved.reroutes[-1].overrides == dr.Overrides(agent="codex")
    assert saved.attempts[-1].steps["reroute"].state == "done"


def test_reroute_live_is_refused(env):
    env[2].workers = [worker(state="running")]
    result = run(env)
    assert (result.failure.step, result.failure.reason) == ("reroute", "reroute-live")
    assert "task_update" not in env[2].names() and record(env) is None


def test_no_task(env):
    env[2].tasks = []
    result = run(env)
    assert result.failure.reason == "no-task"
    assert record(env) is None


@pytest.mark.parametrize("reason", ["", "  \t"])
def test_reason_missing(env, reason):
    result = run(env, reason=reason)
    assert result.failure.reason == "reason-missing"
    assert env[2].calls == []


def test_override_invalid(env):
    result = run(env, agent="codex", effort="high")
    assert result.failure.reason == "override-invalid"
    assert "task_update" not in env[2].names() and record(env) is None


def test_rerun_after_success_is_a_no_op_reporting_the_same_entry(env):
    first = run(env, agent="codex")
    count = env[2].names().count("task_update")
    second = run(env, reason="new reason", agent="claude")
    assert second.ok and second.status == "rerouted"
    assert (second.reason, second.overrides, second.superseded_task_id) == (
        first.reason,
        first.overrides,
        first.superseded_task_id,
    )
    assert env[2].names().count("task_update") == count
    assert len(record(env).reroutes) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "outcome_unknown"},
        {"dispatch_status": "outcome_unknown"},
        {"state": "running", "dispatch_status": "outcome_unknown"},
    ],
)
def test_outcome_unknown_is_inspection_before_mutation(env, changes):
    env[2].workers = [worker(**changes)]
    result = run(env)
    assert result.failure.reason == "recovery-inspection"
    assert "task_update" not in env[2].names() and record(env) is None


def test_task_update_uncertainty_halts_retry(env):
    env[2].fail["task_update"] = BackendError("connection lost")
    first = run(env)
    assert first.failure.reason == "task-update-failed"
    assert record(env).attempts[-1].steps["reroute"].state == "attempted"
    env[2].fail.clear()
    second = run(env)
    assert second.failure.reason == "recovery-inspection"
    assert env[2].names().count("task_update") == 1


def test_record_identity_mismatch_is_refused(env):
    dr.save_record(env[0], dr.DispatchRecord(KEY, "work/x", "execute", "other_run"))
    result = run(env)
    assert result.failure.reason == "record-invalid"
    assert "task_update" not in env[2].names()


def test_next_dispatch_after_reroute_creates_a_new_task_with_overrides(env):
    assert run(env, agent="codex").ok
    env[2].next_task = 2
    result = dispatch_run((env[0], env[1], env[2]), probe=False)
    assert result.ok and result.task_id == "task_2"
    assert record(env).attempts[-1].envelope["agent"] == "codex"
    assert record(env).attempts[-1].envelope["model"] is None


@pytest.mark.parametrize("existing", [False, True])
def test_overlapping_stale_decision_cannot_repeat_update_or_overwrite_winner(env, monkeypatch, existing):
    if existing:
        dr.save_record(env[0], dr.DispatchRecord(KEY, "work/x", "execute", "run_1"))
    port = env[2]
    worker_list = port.worker_list
    winner = []

    def overlap(run_id):
        # The outer call owns execution before worker inspection. A contender
        # must refuse without taking over the in-flight operation.
        monkeypatch.setattr(port, "worker_list", worker_list)
        winner.append(run(env, reason="winner", agent="codex"))
        return worker_list(run_id)

    monkeypatch.setattr(port, "worker_list", overlap)
    stale = run(env, reason="stale", agent="claude")
    assert stale.ok
    assert port.names().count("task_update") == 1
    assert winner[0].failure.reason == "recovery-inspection"
    saved = record(env)
    assert len(saved.reroutes) == 1
    assert (saved.reroutes[0].reason, saved.reroutes[0].overrides) == ("stale", dr.Overrides(agent="claude"))


def test_contender_during_task_update_cannot_repeat_effect(env, monkeypatch):
    port = env[2]
    task_update = port.task_update
    contender = []
    lock = dr.locked_decision_owner
    held = False

    @contextmanager
    def tracked_lock(*args):
        nonlocal held
        assert not held, "nested owner lock"
        with lock(*args) as context:
            held = True
            try:
                yield context
            finally:
                held = False

    def overlap(*args):
        assert not held, "owner lock held across Orca call"
        contender.append(run(env, reason="contender", agent="claude"))
        task_update(*args)

    monkeypatch.setattr(dr, "locked_decision_owner", tracked_lock)
    monkeypatch.setattr(port, "task_update", overlap)
    winner = run(env, reason="winner", agent="codex")
    assert winner.ok
    assert contender[0].failure.reason == "recovery-inspection"
    assert port.names().count("task_update") == 1
    assert record(env).reroutes[0].reason == "winner"


def test_completion_cannot_overwrite_a_changed_claim(env, monkeypatch):
    task_update = env[2].task_update
    durable = []

    def changed_claim(*args):
        task_update(*args)
        saved = record(env)
        entry = dr.Reroute("later", "recovered winner", "task_1", "ctx_old", dr.Overrides(agent="codex"))
        saved = saved.with_step(0, "reroute", dr.StepState("done", at="later")).with_reroute(entry)
        dr.save_record(env[0], saved)
        durable.append(saved)

    monkeypatch.setattr(env[2], "task_update", changed_claim)
    stale = run(env, reason="stale", agent="claude")
    assert record(env) == durable[0]
    assert stale.failure is not None and stale.failure.reason == "recovery-inspection"
    retry = run(env, reason="retry")
    assert retry.ok and retry.reason == "recovered winner"
    assert retry.overrides == dr.Overrides(agent="codex")
    assert env[2].names().count("task_update") == 1


@pytest.mark.parametrize(
    "state,after_write,updates,expected",
    [
        ("attempted", False, 0, "rerouted"),
        ("attempted", True, 0, "recovery-inspection"),
        ("done", False, 1, "recovery-inspection"),
        ("done", True, 1, "rerouted"),
    ],
)
def test_crash_at_reroute_writes_preserves_recovery(env, monkeypatch, state, after_write, updates, expected):
    write = dr.write_json_atomic

    def crash(path, payload):
        if payload["attempts"][-1]["steps"]["reroute"]["state"] == state:
            if after_write:
                write(path, payload)
            raise KeyboardInterrupt("simulated crash")
        write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(dr, "write_json_atomic", crash)
        with pytest.raises(KeyboardInterrupt, match="simulated crash"):
            run(env, reason="original", agent="codex")
    assert env[2].names().count("task_update") == updates
    retried = run(env, reason="retry", agent="claude")
    assert (retried.status if retried.ok else retried.failure.reason) == expected
    assert env[2].names().count("task_update") == (1 if expected == "rerouted" else updates)
    if state == "done" and after_write:
        assert retried.reason == "original" and retried.overrides == dr.Overrides(agent="codex")


@pytest.mark.parametrize("existing", [False, True])
def test_reroute_rejects_snapshot_superseded_before_execution_ownership(env, monkeypatch, existing):
    if existing:
        dr.save_record(env[0], dr.DispatchRecord(KEY, "work/x", "execute", "run_1"))
    original = dr.execution_owner
    durable = []

    @contextmanager
    def interleave(*args):
        monkeypatch.setattr(dr, "execution_owner", original)
        assert run(env, reason="winner", agent="codex").ok
        durable.append(record(env))
        with original(*args):
            yield

    monkeypatch.setattr(dr, "execution_owner", interleave)
    result = run(env, reason="stale", agent="claude")
    assert result.failure.reason == "recovery-inspection"
    assert record(env) == durable[0]
    assert env[2].names().count("task_update") == 1


class CodedRefusal(BackendError):
    def __init__(self, code):
        self.code = code
        super().__init__("task_not_startable: private diagnostic")


def test_definitive_refusal_is_durable_and_retry_uses_requested_overrides(env):
    env[2].fail["task_update"] = CodedRefusal("task_not_startable")
    first = run(env, agent="codex")
    assert first.failure.reason == "task-update-failed"
    saved = record(env)
    assert saved.attempts[-1].steps["reroute"] == dr.StepState(
        "failed", at="2026-09-26T00:00:00+00:00", reason="task_not_startable"
    )
    assert not saved.reroutes and not saved.superseded
    assert env[2].tasks[0]["status"] == "failed"
    env[2].fail.clear()
    second = run(env, reason="cause cleared", agent="codex", model="gpt", effort="high")
    assert second.ok
    assert env[2].names().count("task_update") == 2
    assert record(env).reroutes[-1].overrides == dr.Overrides("codex", "gpt", "high")
    env[2].next_task = 2
    assert dispatch_run((env[0], env[1], env[2]), probe=False).ok
    assert record(env).attempts[-1].envelope["model"] == "gpt"
    assert record(env).attempts[-1].envelope["reasoning_effort"] == "high"


@pytest.mark.parametrize("code", [None, "other", " task_not_startable", 1, [], {}])
def test_unproven_refusal_retains_inspection_only_evidence(env, code):
    env[2].fail["task_update"] = CodedRefusal(code)
    assert run(env).failure.reason == "task-update-failed"
    assert record(env).attempts[-1].steps["reroute"].state == "attempted"
    env[2].fail.clear()
    assert run(env).failure.reason == "recovery-inspection"
    assert env[2].names().count("task_update") == 1


@pytest.mark.parametrize("status", ["blocked", "failed", "unknown", None])
def test_stale_attempted_never_uses_task_status_as_effect_proof(env, status):
    env[2].fail["task_update"] = BackendError("task_not_startable")
    assert run(env).failure.reason == "task-update-failed"
    env[2].fail.clear()
    env[2].tasks[0]["status"] = status
    assert run(env).failure.reason == "recovery-inspection"
    assert env[2].names().count("task_update") == 1


@pytest.mark.parametrize("guard", ["live", "unknown", "identity", "override"])
def test_failed_retry_still_checks_worker_identity_and_overrides(env, guard):
    env[2].fail["task_update"] = CodedRefusal("task_not_startable")
    assert run(env).failure.reason == "task-update-failed"
    env[2].fail.clear()
    kwargs = {}
    expected = "recovery-inspection"
    if guard == "live":
        env[2].workers = [worker(state="running")]
        expected = "reroute-live"
    elif guard == "unknown":
        env[2].workers = [worker(state="outcome_unknown")]
    elif guard == "identity":
        kwargs["run_id"] = "other_run"
        expected = "record-invalid"
    else:
        kwargs.update(agent="codex", effort="high")
        expected = "override-invalid"
    assert run(env, **kwargs).failure.reason == expected
    assert record(env).attempts[-1].steps["reroute"].state == "failed"
    assert env[2].names().count("task_update") == 1


def test_refusal_cannot_overwrite_a_changed_claim(env, monkeypatch):
    durable = []
    original = env[2].task_update

    def changed_claim(*args):
        original(*args)
        saved = record(env).with_step(0, "reroute", dr.StepState("attempted", at="winner"))
        dr.save_record(env[0], saved)
        durable.append(saved)
        raise CodedRefusal("task_not_startable")

    monkeypatch.setattr(env[2], "task_update", changed_claim)
    result = run(env)
    assert result.failure.reason == "recovery-inspection"
    assert record(env) == durable[0]
    assert run(env).failure.reason == "recovery-inspection"
    assert env[2].names().count("task_update") == 1


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
@pytest.mark.parametrize("after_write", [False, True])
def test_failed_marker_write_failure_preserves_durable_evidence(env, monkeypatch, error, after_write):
    env[2].fail["task_update"] = CodedRefusal("task_not_startable")
    write = dr.write_json_atomic

    def fail_write(path, payload):
        if payload["attempts"][-1]["steps"]["reroute"]["state"] == "failed":
            if after_write:
                write(path, payload)
            raise error("failed marker write interrupted")
        write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(dr, "write_json_atomic", fail_write)
        if error is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                run(env)
        else:
            assert run(env).failure.reason == "recovery-inspection"
    assert env[2].names().count("task_update") == 1
    assert record(env).attempts[-1].steps["reroute"].state == ("failed" if after_write else "attempted")
    env[2].fail.clear()
    retry = run(env)
    assert retry.ok if after_write else retry.failure.reason == "recovery-inspection"
    assert env[2].names().count("task_update") == (2 if after_write else 1)


def test_dispatch_does_not_repeat_effects_after_failed_reroute(env):
    # Start with a genuinely completed dispatch rather than a synthetic
    # reroute-only attempt, then refuse a reroute at the backend boundary.
    env[2].tasks.clear()
    env[2].workers.clear()
    assert dispatch_run((env[0], env[1], env[2]), probe=False).ok
    env[2].workers = [worker()]
    env[2].tasks[0]["status"] = "failed"
    env[2].fail["task_update"] = CodedRefusal("task_not_startable")
    assert run(env).failure.reason == "task-update-failed"
    env[2].fail.clear()
    before = record(env)
    calls = env[2].names().copy()
    result = dispatch_run((env[0], env[1], env[2]), probe=False)
    assert result.ok
    assert record(env) == before
    for name in ("task_create", "worker_start", "task_update"):
        assert env[2].names().count(name) == calls.count(name)
