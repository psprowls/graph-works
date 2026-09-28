"""Dispatch against real journals, work-item writes and Git, with a scripted Orca seam."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime

import pytest
from fake_orca_port import FakeOrcaPort
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import dispatch as d
from graph_works_core.orchestrate import dispatch_record as dr
from subagents_io.backend import BackendError

KEY = "gw-execute-x-1a2b"
TODAY = date(2026, 9, 26)


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def env(tmp_path):
    repo, wt = tmp_path / "repo", tmp_path / "wt"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "base")
    git(repo, "worktree", "add", "-b", "feature/x", str(wt))
    layout = apply_init(plan_init(tmp_path / ".works", today=TODAY, topic="Dispatch")).layout
    # Placement resolves declared repositories, independently of layout.repo_root.
    from ruamel.yaml import YAML

    yaml = YAML()
    manifest = yaml.load(layout.manifest_path.read_text(encoding="utf-8"))
    manifest["repositories"] = {"graph-works": {"path": str(repo)}}
    with layout.manifest_path.open("w", encoding="utf-8", newline="\n") as stream:
        yaml.dump(manifest, stream)
    page = layout.bundle_dir / "work/x.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\ntitle: X\ndescription: d\nstatus: stable\n"
        "work_status: in-progress\nphase: execute\neffort: medium\n"
        "opened: 2026-09-26\nupdated: 2026-09-26\naffects: []\n---\n\n## Summary\nd\n",
        encoding="utf-8",
        newline="\n",
    )
    plan = {
        "path": "work/x",
        "dispatches": [
            {
                "key": KEY,
                "path": "work/x",
                "phase": "execute",
                "mode": "autonomous",
                "agent": "claude",
                "model": "opus",
                "reasoning_effort": None,
                "prompt": "Run /gw:workflow work/x.\n",
                "worktree": {
                    "action": "create-top-level",
                    "path": None,
                    "branch": "feature/x",
                    "base_branch": "main",
                    "exists": False,
                    "parent_path": None,
                },
                "repo": {"name": "graph-works", "path": str(repo), "source": "manifest"},
            }
        ],
    }
    port = FakeOrcaPort(repos=[{"id": "repo1", "path": str(repo)}])
    port.worktrees["id:wt1"] = {
        "id": "wt1",
        "repo_id": "repo1",
        "path": str(wt),
        "branch": "feature/x",
        "display_name": "feature/x",
        "is_main": False,
        "parent_id": None,
    }
    return layout, plan, port, repo, wt


def run(env, **kwargs):
    layout, plan, port, *_ = env
    return d.run_dispatch(
        layout,
        KEY,
        plan=kwargs.pop("plan", plan),
        run_id=kwargs.pop("run_id", "run_1"),
        port=port,
        today=TODAY,
        clock=lambda: datetime(2026, 9, 26, tzinfo=UTC),
        sleep=kwargs.pop("sleep", lambda _: None),
        **kwargs,
    )


def record(env):
    return dr.load_record(dr.record_file(env[0], "work/x", KEY))


def failure(result, step, reason, task=None, dispatch=None):
    assert not result.ok and result.status is None
    assert (result.failure.step, result.failure.reason) == (step, reason)
    assert (result.task_id, result.dispatch_id) == (task, dispatch)
    assert (result.failure.task_id, result.failure.dispatch_id) == (task, dispatch)


def test_happy_path_runs_every_step_in_order(env):
    result = run(env)
    assert result.ok, result.failure
    assert (result.status, result.task_id, result.task_title, result.display_name) == (
        "dispatched",
        "task_1",
        KEY,
        "work/x · execute",
    )
    assert result.recorded == "recorded" and result.probe == "submitted-transcript"
    assert env[2].names() == [
        "task_list",
        "repo_list",
        "task_create",
        "worker_start",
        "worktree_show",
        "worker_list",
        "worker_show",
        "worker_read",
        "task_update",
    ]
    assert f"worktree: {env[4]}" in (env[0].bundle_dir / "work/x.md").read_text(encoding="utf-8")
    assert record(env).attempts[-1].complete
    assert result.record_path == f"/work/x/references/orca-dispatch/{KEY}.json"


def test_the_spec_is_v2_and_carries_the_prompt(env):
    assert run(env).ok
    spec = next(k["spec"] for n, _, k in env[2].calls if n == "task_create")
    header, prompt = spec.split("\n", 1)
    assert header.startswith("GW_LAUNCH_V1 ")
    assert json.loads(header.removeprefix("GW_LAUNCH_V1 ")) == {
        "version": 2,
        "dispatch_key": KEY,
        "agent": "claude",
        "model": "opus",
        "reasoning_effort": None,
        "placement_argv": [
            "--worktree",
            "new-top-level",
            "--name",
            "feature/x",
            "--base-branch",
            "main",
            "--repo",
            "id:repo1",
        ],
        "mode": "autonomous",
        "worktree_path": None,
    }
    assert prompt == "Run /gw:workflow work/x.\n"


def test_the_record_never_stores_the_prompt(env):
    env[1]["dispatches"][0]["prompt"] = "Unique private instruction dcap_not_for_disk --dispatch-capability\n"
    assert run(env).ok
    text = dr.record_file(env[0], "work/x", KEY).read_text(encoding="utf-8")
    assert "Unique private instruction" not in text and "dcap_" not in text
    assert "Unique private instruction" in env[2].tasks[0]["spec"]


@pytest.mark.parametrize("kind", ["not-dict", "prompt", "run", "entry", "worktree", "repo", "model"])
def test_invalid_plan(env, kind):
    plan = env[1]
    kwargs = {}
    if kind == "not-dict":
        plan = []
    elif kind == "run":
        kwargs["run_id"] = "  "
    elif kind == "entry":
        plan["dispatches"] = [None]
    elif kind == "prompt":
        del plan["dispatches"][0]["prompt"]
    else:
        plan["dispatches"][0][kind] = 7
    failure(run(env, plan=plan, **kwargs), "validate", "plan-invalid")
    assert not env[2].calls


def test_key_not_in_plan(env):
    env[1]["dispatches"] = []
    failure(run(env), "validate", "key-not-in-plan")


def test_record_invalid(env):
    path = dr.record_file(env[0], "work/x", KEY)
    path.parent.mkdir(parents=True)
    path.write_text("garbage", encoding="utf-8", newline="\n")
    failure(run(env), "validate", "record-invalid")


def reroute(env, overrides):
    r = dr.DispatchRecord(KEY, "work/x", "execute", "run_1")
    r = r.with_reroute(dr.Reroute("t", "stall", "task_1", "ctx_old", overrides))
    dr.save_record(env[0], r)
    env[2].tasks = [{"id": "task_1", "title": KEY, "display_name": "old", "status": "blocked", "spec": "old"}]
    env[2].next_task = 2


def test_invalid_override_precedes_placement_writes(env):
    reroute(env, dr.Overrides(agent="codex", effort="high"))
    failure(run(env), "encode", "override-invalid")
    assert not (env[0].bundle_dir / "work/x/references/orca-placement").exists()
    assert "repo_list" not in env[2].names()


def test_reroute_override_is_applied_to_the_new_envelope(env):
    reroute(env, dr.Overrides(agent="codex"))
    result = run(env)
    assert result.ok and result.task_id == "task_2"
    assert record(env).attempts[-1].envelope["agent"] == "codex"
    assert record(env).attempts[-1].envelope["model"] is None


@pytest.mark.parametrize("case", ["no-repo", "duplicate", "no-path", "foreign-parent"])
def test_placement_refused(env, case):
    if case == "no-repo":
        env[2].repos = []
    elif case == "duplicate":
        env[2].repos *= 2
    elif case == "no-path":
        env[1]["dispatches"][0]["repo"] = None
    else:
        env[1]["dispatches"][0]["worktree"]["parent_path"] = "/parent"
        env[2].worktrees["path:/parent"] = {**env[2].worktrees["id:wt1"], "repo_id": "other"}
    failure(run(env), "place", "placement-refused")
    assert "task_create" not in env[2].names()


@pytest.mark.parametrize(
    "call,step,reason,task,dispatch",
    [
        ("task_create", "create", "task-create-failed", None, None),
        ("worker_start", "launch", "launch-failed", "task_1", None),
        ("task_update", "task-update", "task-update-failed", "task_1", "ctx_1"),
        ("worktree_show", "settle", "placement-mismatch", "task_1", "ctx_1"),
        ("worker_list", "settle", "placement-mismatch", "task_1", "ctx_1"),
    ],
)
def test_port_failure(env, call, step, reason, task, dispatch):
    env[2].fail[call] = BackendError("unavailable")
    failure(run(env), step, reason, task, dispatch)
    assert record(env).attempts[-1].steps[step].state == "attempted"


@pytest.mark.parametrize(
    "change,reason,dispatch",
    [
        ({"state": "outcome_unknown"}, "outcome-unknown", "ctx_1"),
        ({"dispatch_id": None}, "launch-failed", None),
        ({"receipt_problem": "wrong model"}, "receipt-mismatch", "ctx_1"),
    ],
)
def test_start_refusals(env, change, reason, dispatch):
    env[2].start.update(change)
    failure(run(env), "launch", reason, "task_1", dispatch)


def test_known_failed_start_ids_are_preserved_without_carriers(env):
    error = BackendError("start failed")
    error.details = {
        "dispatchId": "ctx_failed",
        "agentTerminalHandle": "term_failed",
        "launchRequest": {"prompt": "private dcap_secret"},
        "receipt": {"preamble": "private"},
    }
    env[2].fail["worker_start"] = error
    result = run(env)
    failure(result, "launch", "launch-failed", "task_1", "ctx_failed")
    assert result.terminal == "term_failed"
    assert record(env).attempts[-1].dispatch_id == "ctx_failed"
    text = dr.record_file(env[0], "work/x", KEY).read_text(encoding="utf-8")
    assert "private" not in text and "launchRequest" not in text and "receipt" not in text


@pytest.mark.parametrize("case", ["repo", "directory", "base", "branch", "main", "parent", "claimed", "row", "wid"])
def test_placement_mismatch(env, case):
    row = env[2].worktrees["id:wt1"]
    if case == "repo":
        row["repo_id"] = "wrong"
    elif case == "directory":
        row["path"] = "/unreachable/worktree"
    elif case == "base":
        git(env[4], "checkout", "--orphan", "orphan")
        git(env[4], "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "--allow-empty", "-m", "orphan")
        row["branch"] = "orphan"
    elif case == "branch":
        row["branch"] = "wrong"
    elif case == "main":
        row["is_main"] = True
    elif case == "parent":
        row["parent_id"] = "wrong"
    elif case == "claimed":
        env[2].workers = [
            {
                "dispatch_id": "ctx_other",
                "task_id": "other",
                "state": "running",
                "dispatch_status": "dispatched",
                "worktree_id": "wt1",
            }
        ]
    elif case == "row":
        env[2].worktrees["id:wt1"] = None
    else:
        env[2].start["worktree_id"] = env[2].show["worktree_id"] = None
    failure(run(env), "settle", "placement-mismatch", "task_1", "ctx_1")


def test_placement_unrecorded_preserves_refusal(env):
    page = env[0].bundle_dir / "work/x.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("phase: execute", "phase: finish"), encoding="utf-8", newline="\n"
    )
    result = run(env)
    failure(result, "record", "placement-unrecorded", "task_1", "ctx_1")
    assert result.failure.refusal == "phase-mismatch"


def test_two_dispatches_with_one_key_create_one_task(env):
    assert run(env).ok
    assert run(env).status == "existing"
    assert env[2].names().count("task_create") == 1


def crash(env, monkeypatch, step):
    save = dr.compare_and_swap_record

    def interrupt(layout, value, **kwargs):
        if value.attempts[-1].steps.get(step, dr.StepState("attempted")).state == "done":
            raise KeyboardInterrupt
        return save(layout, value, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(dr, "compare_and_swap_record", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(env)


def test_crash_after_task_create_adopts_by_title(env, monkeypatch):
    crash(env, monkeypatch, "create")
    result = run(env)
    assert result.ok and result.status == "resumed"
    assert env[2].names().count("task_create") == 1
    assert record(env).attempts[-1].steps["create"].result["adopted"] is True


@pytest.mark.parametrize("step", ["launch", "settle", "record", "attend", "probe", "task-update"])
def test_crash_matrix_halts_on_every_other_attempted_step(env, monkeypatch, step):
    env[1]["dispatches"][0]["mode"] = "attend"
    crash(env, monkeypatch, step)
    start = len(env[2].calls)
    result = run(env)
    assert result.failure.reason == "recovery-inspection" and result.failure.step == step
    assert env[2].names()[start:] == ["task_list"]


def test_ambiguous_title_lookup_is_inspection(env, monkeypatch):
    crash(env, monkeypatch, "create")
    env[2].tasks.append({**env[2].tasks[0], "id": "task_other"})
    failure(run(env), "create", "recovery-inspection")


def test_a_task_from_the_old_primitives_counts_as_existing(env):
    env[2].tasks = [{"id": "old", "title": KEY, "display_name": "old name", "status": "dispatched", "spec": "old"}]
    result = run(env)
    assert result.status == "existing" and result.task_id == "old"
    assert env[2].names() == ["task_list"]


def test_read_only_descendant_is_not_recorded(env):
    env[1]["dispatches"][0].update(path="work/x/children/feature-y", phase="plan")
    page = env[0].bundle_dir / "work/x/children/feature-y.md"
    page.parent.mkdir(parents=True)
    page.write_bytes((env[0].bundle_dir / "work/x.md").read_bytes())
    before = page.read_bytes()
    result = run(env)
    assert result.ok and result.recorded == "skipped:read-only-descendant"
    assert page.read_bytes() == before


@pytest.mark.parametrize("skipped_step", ["record", "probe"])
def test_skipped_dispatch_can_be_called_again(env, skipped_step):
    if skipped_step == "record":
        env[1]["dispatches"][0].update(path="work/x/children/feature-y", phase="plan")
        page = env[0].bundle_dir / "work/x/children/feature-y.md"
        page.parent.mkdir(parents=True)
        page.write_bytes((env[0].bundle_dir / "work/x.md").read_bytes())
    first = run(env, probe=skipped_step != "probe")
    assert first.ok
    path = dr.record_file(env[0], env[1]["dispatches"][0]["path"], KEY)
    assert dr.load_record(path).attempts[-1].steps[skipped_step].state == "skipped"
    start = len(env[2].calls)

    second = run(env, probe=skipped_step != "probe")

    assert second.ok and second.status == "existing"
    assert env[2].names()[start:] == ["task_list"]


@pytest.mark.parametrize("recovery", ["existing", "resume"])
@pytest.mark.parametrize("skipped_step", ["record", "probe"])
def test_persisted_skipped_step_without_result_is_valid(env, skipped_step, recovery):
    if skipped_step == "record":
        env[1]["dispatches"][0].update(path="work/x/children/feature-y", phase="plan")
        page = env[0].bundle_dir / "work/x/children/feature-y.md"
        page.parent.mkdir(parents=True)
        page.write_bytes((env[0].bundle_dir / "work/x.md").read_bytes())
    assert run(env, probe=skipped_step != "probe").ok
    path = dr.record_file(env[0], env[1]["dispatches"][0]["path"], KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    attempt = payload["attempts"][-1]
    assert attempt["steps"][skipped_step]["state"] == "skipped"
    del attempt["steps"][skipped_step]["result"]
    if recovery == "resume":
        del attempt["steps"]["task-update"]
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    start = len(env[2].calls)

    result = run(env, probe=skipped_step != "probe")

    assert result.ok and result.status == ("existing" if recovery == "existing" else "resumed")
    if skipped_step == "record":
        assert result.recorded == "skipped:read-only-descendant"
    else:
        assert result.probe == "skipped"
    assert env[2].names()[start:] == (["task_list"] if recovery == "existing" else ["task_list", "task_update"])
    assert env[2].names().count("task_create") == 1
    assert env[2].names().count("worker_start") == 1


def test_attend_sets_in_review(env):
    env[1]["dispatches"][0]["mode"] = "attend"
    assert run(env).ok
    assert [(a, k) for n, a, k in env[2].calls if n == "worktree_set_status"] == [(("wt1", "in-review"), {})]


def test_attend_error_is_a_warning(env):
    env[1]["dispatches"][0]["mode"] = "attend"
    env[2].fail["worktree_set_status"] = BackendError("unavailable")
    result = run(env)
    assert result.ok and "in-review not set: unavailable" in result.placement.notes
    assert record(env).attempts[-1].steps["attend"].result == {"warning": "unavailable"}


@pytest.mark.parametrize("case", ["degraded", "failed", "reread-degraded", "reread-failed"])
def test_probe_never_nudges_a_degraded_or_unreadable_read(env, case):
    if case == "degraded":
        env[2].reads = [{"source": "terminal", "message_count": 0}]
    elif case == "failed":
        env[2].fail["worker_read"] = BackendError("unreadable")
    else:
        env[2].reads = [{"source": "transcript", "message_count": 0}, {"source": "terminal", "message_count": 0}]
        if case == "reread-failed":

            def sleeping(_):
                if "terminal_send_enter" in env[2].names():
                    env[2].fail["worker_read"] = BackendError("x")

            result = run(env, sleep=sleeping)
    if case != "reread-failed":
        result = run(env)
    assert result.ok and result.probe == "inconclusive"
    assert env[2].names().count("terminal_send_enter") == (1 if case.startswith("reread") else 0)


@pytest.mark.parametrize("after_read", [0, 1, 2])
def test_heartbeat_vetoes_every_nudge(env, monkeypatch, after_read):
    env[2].reads = [{"source": "transcript", "message_count": 0}]
    show = env[2].worker_show

    def heartbeat(dispatch):
        value = show(dispatch)
        if env[2].names().count("worker_read") >= after_read:
            value["last_heartbeat_at"] = "now"
        return value

    monkeypatch.setattr(env[2], "worker_show", heartbeat)
    result = run(env)
    assert result.ok and result.probe == "submitted-heartbeat"
    assert env[2].names().count("terminal_send_enter") == max(0, after_read - 1)


def test_unsent_after_exactly_two_nudges(env):
    env[2].reads = [{"source": "transcript", "message_count": 0}]
    failure(run(env), "probe", "unsent", "task_1", "ctx_1")
    assert env[2].names().count("terminal_send_enter") == 2


def test_recovered_submission_is_nudged(env):
    env[2].reads = [{"source": "transcript", "message_count": 0}, {"source": "transcript", "message_count": 1}]
    assert run(env).probe == "nudged"


@pytest.mark.parametrize("case", ["disabled", "terminal"])
def test_no_probe_and_no_terminal(env, case):
    if case == "terminal":
        env[2].start["terminal"] = None
    result = run(env, probe=case != "disabled")
    assert result.ok and result.probe == ("skipped" if case == "disabled" else "no-terminal")
    assert "worker_read" not in env[2].names()


def test_reuse_places_by_path_without_an_orca_call(env):
    env[1]["dispatches"][0]["worktree"].update(action="reuse", path=str(env[4]))
    assert run(env).ok
    names = env[2].names()
    assert names[: names.index("task_create")] == ["task_list"]
    assert record(env).attempts[-1].envelope["placement_argv"] == ["--worktree", f"path:{env[4]}"]


@pytest.mark.parametrize(
    "field,value", [("run_id", "other"), ("phase", "finish"), ("work_path", "work/y"), ("key", "other")]
)
def test_record_identity_is_not_adopted(env, field, value):
    from dataclasses import replace

    r = replace(dr.DispatchRecord(KEY, "work/x", "execute", "run_1"), **{field: value})
    dr.write_json_atomic(dr.record_file(env[0], "work/x", KEY), dr.to_json(r))
    failure(run(env), "validate", "record-invalid")
    assert not env[2].calls


def test_uncertain_create_with_no_task_retries_same_attempt(env):
    env[2].fail["task_create"] = BackendError("offline")
    failure(run(env), "create", "task-create-failed")
    del env[2].fail["task_create"]
    assert run(env).status == "resumed"
    assert len(record(env).attempts) == 1 and len(env[2].tasks) == 1


@pytest.mark.parametrize("step", ["launch", "settle", "record", "attend", "probe", "task-update"])
def test_crash_before_attempted_write_resumes_without_repeating_done_steps(env, monkeypatch, step):
    save = dr.compare_and_swap_record

    def interrupt(layout, value, **kwargs):
        if value.attempts[-1].steps.get(step, dr.StepState("done")).state == "attempted":
            raise KeyboardInterrupt
        return save(layout, value, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(dr, "compare_and_swap_record", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(env)
    result = run(env)
    assert result.ok and result.status == "resumed"
    assert env[2].names().count("task_create") == 1
    assert env[2].names().count("worker_start") == 1
    assert result.recorded == "recorded" and result.probe == "submitted-transcript"
    assert result.placement.path == str(env[4])


def test_incomplete_journal_with_missing_task_is_inspection(env, monkeypatch):
    crash(env, monkeypatch, "launch")
    env[2].tasks.clear()
    before = len(env[2].calls)
    failure(run(env), "launch", "recovery-inspection", "task_1")
    assert env[2].names()[before:] == ["task_list"]


def test_ambiguous_old_tasks_refuse(env):
    env[2].tasks = [{"id": n, "title": KEY, "display_name": n, "status": "pending", "spec": "old"} for n in ("a", "b")]
    failure(run(env), "create", "recovery-inspection")


@pytest.mark.parametrize("case", ["unknown", "error", "main", "child"])
def test_parent_resolution_is_explicit_and_safe(env, case, monkeypatch):
    wt = env[1]["dispatches"][0]["worktree"]
    wt.update(action="fork-child", parent_path=str(env[3]))
    if case in ("main", "child"):
        env[2].worktrees[f"path:{env[3]}"] = {**env[2].worktrees["id:wt1"], "id": "parent", "is_main": case == "main"}
    elif case == "error":
        show = env[2].worktree_show

        def parent_error(selector):
            if selector.startswith("path:"):
                raise BackendError("unknown parent")
            return show(selector)

        monkeypatch.setattr(env[2], "worktree_show", parent_error)
    result = run(env)
    assert result.ok, result.failure
    assert env[2].names().count("worktree_set_parent") == (1 if case == "child" else 0)
    assert result.placement.lineage_set is (case == "child")
    assert result.placement.parent_worktree_id == ("parent" if case == "child" else None)
    assert bool(result.placement.notes) is (case != "child")
    placed = json.loads(
        (env[0].bundle_dir / f"work/x/references/orca-placement/{KEY}.json").read_text(encoding="utf-8")
    )
    assert placed == {
        "action": "fork-child",
        "placement_argv": [
            "--worktree",
            "new-top-level",
            "--name",
            "feature/x",
            "--base-branch",
            "main",
            "--repo",
            "id:repo1",
        ],
        "repo_id": "repo1",
        "parent_worktree_id": "parent" if case == "child" else None,
        "start_sha": git(env[3], "rev-parse", "main").stdout.strip(),
    }


def test_parent_repair_is_verified(env, monkeypatch):
    env[1]["dispatches"][0]["worktree"]["parent_path"] = "/parent"
    env[2].worktrees["path:/parent"] = {**env[2].worktrees["id:wt1"], "id": "parent"}
    monkeypatch.setattr(env[2], "worktree_set_parent", lambda *_: None)
    failure(run(env), "settle", "placement-mismatch", "task_1", "ctx_1")


def test_uniquified_name_and_worker_show_fallback(env):
    env[2].start["worktree_id"] = None
    env[2].worktrees["id:wt1"]["display_name"] = "feature/x-2"
    result = run(env)
    assert result.ok and "uniquified" in result.placement.notes[0]
    assert record(env).attempts[-1].placement["worktree_id"] == "wt1"


@pytest.mark.parametrize("state", ["succeeded", "failed", "stopped"])
def test_settled_workers_do_not_claim_worktree(env, state):
    env[2].workers = [
        {"dispatch_id": "old", "task_id": "old", "worktree_id": "wt1", "state": state, "dispatch_status": "settled"}
    ]
    assert run(env).ok


def test_reuse_path_disagreement_refuses(env):
    env[1]["dispatches"][0]["worktree"].update(action="main", path=str(env[3]))
    failure(run(env), "settle", "placement-mismatch", "task_1", "ctx_1")


@pytest.mark.parametrize("field", ["path", "branch", "base_branch", "parent_path"])
def test_missing_placement_fields_refuse_before_create(env, field):
    wt = env[1]["dispatches"][0]["worktree"]
    if field == "path":
        wt["action"] = "reuse"
    wt[field] = " "
    failure(run(env), "place", "placement-refused")
    assert "task_create" not in env[2].names()


def test_placement_file_cannot_escape_owner(env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    refs = env[0].bundle_dir / "work/x/references"
    refs.mkdir(parents=True)
    (refs / "orca-placement").symlink_to(outside, target_is_directory=True)
    failure(run(env), "place", "placement-refused")
    assert not list(outside.iterdir()) and "task_create" not in env[2].names()


def test_record_unchanged_is_verified(env):
    assert run(env).ok
    env[2].workers[0]["state"] = "stopped"
    reroute(env, dr.Overrides())
    assert run(env).recorded == "unchanged"


@pytest.mark.parametrize("case", ["exception", "application", "verify"])
def test_record_write_and_verification_failures(env, monkeypatch, case):
    from types import SimpleNamespace

    original = d.run_record_placement

    def write(*args, **kwargs):
        if case == "exception":
            raise OSError("disk failed")
        if case == "application":
            plan = SimpleNamespace(refusal=None, changed=False, start_after=None)
            return SimpleNamespace(plan=plan, application=SimpleNamespace(ok=False))
        result = original(*args, **kwargs)
        if kwargs["dry_run"]:
            return SimpleNamespace(plan=SimpleNamespace(refusal=None, changed=True))
        return result

    monkeypatch.setattr(d, "run_record_placement", write)
    failure(run(env), "record", "placement-unrecorded", "task_1", "ctx_1")
    assert "task_update" not in env[2].names()


@pytest.mark.parametrize("call", ["worker_show", "terminal_send_enter"])
def test_probe_errors_are_inconclusive(env, call):
    env[2].reads = [{"source": "transcript", "message_count": 0}]
    env[2].fail[call] = BackendError("unavailable")
    result = run(env)
    assert result.ok and result.probe == "inconclusive"
    assert env[2].names().count("terminal_send_enter") <= 1


def test_branch_probe_failure_cannot_be_mistaken_for_verified_output(env, monkeypatch):
    from graph_works_core.workspace.provenance import GitOutcome

    original = d.probe_git

    def probe(path, *args):
        if args == ("branch", "--show-current"):
            return GitOutcome(1, "feature/x\n", "ok", "failed")
        return original(path, *args)

    monkeypatch.setattr(d, "probe_git", probe)
    failure(run(env), "settle", "placement-mismatch", "task_1", "ctx_1")


def test_failed_start_keeps_terminal_effect_without_persisting_receipt(env):
    error = BackendError("start failed")
    error.details = {
        "dispatchId": "ctx_failed",
        "effects": [
            {"kind": "worktree", "id": "wt_failed"},
            {"kind": "terminal", "role": "agent", "id": "term_failed"},
        ],
        "launchRequest": {"prompt": "private"},
    }
    env[2].fail["worker_start"] = error
    result = run(env)
    failure(result, "launch", "launch-failed", "task_1", "ctx_failed")
    assert result.terminal == "term_failed"
    assert record(env).attempts[-1].steps["launch"].result == {
        "dispatch_id": "ctx_failed",
        "terminal": "term_failed",
        "worktree_id": "wt_failed",
    }


def test_title_lookup_failure_is_inspection_not_permission_to_create(env):
    env[2].fail["task_list"] = BackendError("unavailable")
    failure(run(env), "create", "recovery-inspection")
    assert env[2].names() == ["task_list"]


def test_failed_recovery_lookup_retains_journal_ids(env, monkeypatch):
    crash(env, monkeypatch, "settle")
    env[2].fail["task_list"] = BackendError("unavailable")
    failure(run(env), "create", "recovery-inspection", "task_1", "ctx_1")


def test_resume_preserves_attend_warning(env, monkeypatch):
    env[1]["dispatches"][0]["mode"] = "attend"
    env[2].fail["worktree_set_status"] = BackendError("offline")
    save = dr.compare_and_swap_record

    def interrupt(layout, value, **kwargs):
        if "probe" in value.attempts[-1].steps:
            raise KeyboardInterrupt
        return save(layout, value, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(dr, "compare_and_swap_record", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(env)
    result = run(env)
    assert result.ok and result.status == "resumed"
    assert result.placement.notes == ("in-review not set: offline",)
    assert env[2].names().count("worktree_set_status") == 1


@pytest.mark.parametrize("recovery", ["existing", "resume", "lookup-failure"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("settle.result", {}),
        ("settle.result.extra", "unexpected"),
        ("settle.result.notes", None),
        ("settle.result.notes", "not-an-array"),
        ("settle.result.notes", [7]),
        ("settle.result.path", []),
        ("settle.result.branch", None),
        ("settle.result.start_sha", 7),
        ("settle.result.lineage_set", 1),
        ("envelope", {}),
        ("envelope.extra", "unexpected"),
        ("envelope.version", True),
        ("envelope.placement_argv", None),
        ("envelope.placement_argv", [7]),
        ("envelope.model", {}),
        ("envelope.worktree_path", []),
        ("placement", None),
        ("placement", {}),
        ("placement.repo_id", []),
        ("placement.parent_worktree_id", 7),
        ("placement.worktree_id", None),
        ("place.result.notes", None),
        ("place.result.notes", [False]),
        ("create.result", {}),
        ("create.result.adopted", "yes"),
        ("launch.result", {}),
        ("launch.result.terminal", []),
        ("launch.result.worktree_id", {}),
        ("record.result.recorded", []),
        ("record.result.recorded", "unknown"),
        ("attend.result", {"warning": []}),
        ("probe.result.probe", []),
        ("probe.result.probe", "unknown"),
    ],
)
def test_malformed_persisted_dispatch_shapes(env, recovery, field, value):
    assert run(env).ok
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    attempt = payload["attempts"][-1]
    if recovery == "resume":
        del attempt["steps"]["task-update"]
    elif recovery == "lookup-failure":
        env[2].fail["task_list"] = BackendError("offline")
    parts = field.split(".")
    target = attempt if parts[0] in ("envelope", "placement") else attempt["steps"]
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    before = path.read_bytes()
    start = len(env[2].calls)
    result = run(env)
    failure(result, "validate", "record-invalid", "task_1", "ctx_1")
    assert result.terminal == (None if field in ("launch.result", "launch.result.terminal") else "term_1")
    assert result.record_path == "/" + dr.record_ref("work/x", KEY)
    assert result.failure.detail
    assert env[2].calls[start:] == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("step", ["place", "create", "launch", "settle", "record", "probe"])
def test_missing_persisted_done_result_is_record_invalid(env, step):
    assert run(env).ok
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["attempts"][-1]["steps"][step]["result"]
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    failure(run(env), "validate", "record-invalid", "task_1", "ctx_1")


def test_invalid_persisted_attempt_retains_ids_from_step_results(env):
    assert run(env).ok
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    attempt = payload["attempts"][-1]
    attempt.update(task_id=None, dispatch_id=None)
    attempt["steps"]["settle"]["result"] = {}
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    result = run(env)
    failure(result, "validate", "record-invalid", "task_1", "ctx_1")
    assert result.terminal == "term_1"


def test_invalid_persisted_envelope_blocks_uncertain_create_retry(env, monkeypatch):
    crash(env, monkeypatch, "create")
    env[2].tasks.clear()
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["attempts"][-1]["envelope"]["placement_argv"]
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    start = len(env[2].calls)
    failure(run(env), "validate", "record-invalid")
    assert env[2].calls[start:] == []


def test_restore_programming_errors_are_not_record_invalid(env, monkeypatch):
    assert run(env).ok

    def broken_restore(_context):
        raise TypeError("programming error")

    monkeypatch.setattr(d, "_restore", broken_restore)
    with pytest.raises(TypeError, match="programming error"):
        run(env)


@pytest.mark.parametrize("boundary", ["task_list", "task_create", "worker_start", "task_update"])
def test_overlapping_dispatch_is_refused_before_any_duplicate_effect(env, monkeypatch, boundary):
    port = env[2]
    original = getattr(port, boundary)
    contenders = []

    def overlap(*args, **kwargs):
        # A snapshot may already have been captured when the contender runs.
        if boundary == "task_list":
            value = original(*args, **kwargs)
        monkeypatch.setattr(port, boundary, original)
        contenders.append(run(env))
        return value if boundary == "task_list" else original(*args, **kwargs)

    monkeypatch.setattr(port, boundary, overlap)
    winner = run(env)
    assert winner.ok, winner.failure
    assert contenders[0].failure.reason == "recovery-inspection"
    assert len(port.tasks) == port.names().count("worker_start") == 1
    assert len(record(env).attempts) == 1
    if boundary == "task_update":
        assert (contenders[0].failure.task_id, contenders[0].failure.dispatch_id) == ("task_1", "ctx_1")


def test_reroute_cannot_supersede_an_active_launch(env, monkeypatch):
    from graph_works_core.orchestrate.reroute import run_reroute

    original = env[2].worker_start
    contenders = []

    def overlap(*args, **kwargs):
        contenders.append(
            run_reroute(
                env[0],
                KEY,
                run_id="run_1",
                reason="switch provider",
                agent="codex",
                port=env[2],
                clock=lambda: datetime.now(UTC),
            )
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(env[2], "worker_start", overlap)
    assert run(env).ok
    assert contenders[0].failure.reason == "recovery-inspection"
    assert contenders[0].failure.task_id == "task_1"
    assert not record(env).reroutes
    assert env[2].names().count("task_update") == 1


@pytest.mark.parametrize("boundary", ["worker_list", "task_update"])
def test_dispatch_cannot_resume_or_overwrite_an_active_reroute(env, monkeypatch, boundary):
    from graph_works_core.orchestrate.reroute import run_reroute

    assert run(env).ok
    env[2].workers[0]["state"] = "failed"
    original = getattr(env[2], boundary)
    contender = []

    def overlap(*args, **kwargs):
        monkeypatch.setattr(env[2], boundary, original)
        contender.append(run(env))
        return original(*args, **kwargs)

    monkeypatch.setattr(env[2], boundary, overlap)
    result = run_reroute(
        env[0],
        KEY,
        run_id="run_1",
        reason="switch provider",
        agent="codex",
        port=env[2],
        clock=lambda: datetime.now(UTC),
    )
    assert result.ok, result.failure
    failure(contender[0], "validate", "recovery-inspection", "task_1", "ctx_1")
    assert record(env).reroutes[-1].reason == "switch provider"
    assert env[2].names().count("worker_start") == 1
    env[2].next_task = 2
    assert run(env).ok
    assert record(env).reroutes[-1].reason == "switch provider"
    assert record(env).attempts[-1].envelope["agent"] == "codex"


def test_stale_dispatch_snapshot_cannot_erase_completed_reroute(env, monkeypatch):
    from contextlib import contextmanager

    from graph_works_core.orchestrate.reroute import run_reroute

    assert run(env).ok
    env[2].workers[0]["state"] = "failed"
    original = dr.execution_owner
    durable = []

    @contextmanager
    def interleave(*args):
        monkeypatch.setattr(dr, "execution_owner", original)
        result = run_reroute(
            env[0], KEY, run_id="run_1", reason="winner", agent="codex", port=env[2], clock=lambda: datetime.now(UTC)
        )
        assert result.ok
        durable.append(record(env))
        with original(*args):
            yield

    monkeypatch.setattr(dr, "execution_owner", interleave)
    failure(run(env), "validate", "recovery-inspection", "task_1", "ctx_1")
    assert record(env) == durable[0]
    assert env[2].names().count("worker_start") == 1


def test_dispatch_completion_cas_preserves_a_changed_journal(env, monkeypatch):
    from dataclasses import replace

    original = env[2].task_update
    durable = []

    def change(*args):
        original(*args)
        saved = replace(record(env), run_id="out-of-band-recovery")
        dr.save_record(env[0], saved)
        durable.append(saved)

    monkeypatch.setattr(env[2], "task_update", change)
    failure(run(env), "task-update", "recovery-inspection", "task_1", "ctx_1")
    assert record(env) == durable[0]


def test_execution_ownership_does_not_hold_item_lock_across_record_placement(env, monkeypatch):
    from contextlib import contextmanager

    original_lock = dr.locked_decision_owner
    original_record = d.run_record_placement
    held = False

    @contextmanager
    def tracked(*args):
        nonlocal held
        assert not held
        with original_lock(*args) as context:
            held = True
            try:
                yield context
            finally:
                held = False

    def placement(*args, **kwargs):
        assert not held
        with pytest.raises(dr.DispatchRecordConflict), dr.execution_owner(env[0], KEY):
            pytest.fail("execution ownership released before placement")
        return original_record(*args, **kwargs)

    monkeypatch.setattr(dr, "locked_decision_owner", tracked)
    monkeypatch.setattr(d, "run_record_placement", placement)
    assert run(env).ok


def test_different_keys_have_independent_execution_ownership(env):
    with dr.execution_owner(env[0], "other-key"):
        assert run(env).ok


@pytest.mark.parametrize(
    "boundary,step,dispatch", [("task_create", "create", None), ("worker_start", "launch", "ctx_1")]
)
def test_completion_conflict_keeps_ids_returned_by_effect(env, monkeypatch, boundary, step, dispatch):
    from dataclasses import replace

    original = getattr(env[2], boundary)
    durable = []

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        saved = replace(record(env), run_id="out-of-band-recovery")
        dr.save_record(env[0], saved)
        durable.append(saved)
        return result

    monkeypatch.setattr(env[2], boundary, changed)
    result = run(env)
    failure(result, step, "recovery-inspection", "task_1", dispatch)
    if dispatch:
        assert result.terminal == "term_1"
    assert record(env) == durable[0]


# --- pin-detached readers ---------------------------------------------------


def _reader_row(port, path, *, repo_id="repo1", comment, is_main=False, parent_id=None):
    row = {
        "id": "wt1",
        "repo_id": repo_id,
        "path": str(path),
        "branch": None,
        "display_name": "gw-reader",
        "is_main": is_main,
        "parent_id": parent_id,
        "comment": comment,
    }
    port.worktrees["id:wt1"] = row
    return row


@pytest.fixture
def reader_env(env, tmp_path):
    """A design-stage descendant whose planner action is `pin-detached` at the repo tip."""
    layout, plan, port, repo, _wt = env
    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    entry = plan["dispatches"][0]
    entry.update(path="work/x/children/feature-y", phase="design", mode="attend")
    entry["worktree"] = {
        "action": "pin-detached",
        "path": None,
        "branch": None,
        "base_branch": "main",
        "exists": None,
        "parent_path": None,
        "start_sha": sha,
    }
    page = layout.bundle_dir / "work/x/children/feature-y.md"
    page.parent.mkdir(parents=True)
    page.write_bytes((layout.bundle_dir / "work/x.md").read_bytes().replace(b"phase: execute", b"phase: design"))
    reader = tmp_path / "reader"

    def create(*, name, repo_id, base_branch, comment):
        git(repo, "worktree", "add", "-b", f"psprowls/{name[:20]}", str(reader), base_branch)
        return _reader_row(port, reader, comment=comment)

    port.create = create
    return layout, plan, port, repo, reader, sha


def test_reader_is_prepared_detached_verified_and_receipted(reader_env):
    layout, plan, port, _repo, reader, sha = reader_env
    result = run(reader_env)
    assert result.ok, result.failure
    placement = result.placement
    assert (placement.action, placement.branch, placement.start_sha, placement.parent_worktree_id) == (
        "pin-detached",
        None,
        sha,
        None,
    )
    assert placement.path == str(reader.resolve())
    assert result.recorded == "reader-receipt"
    assert git(reader, "rev-parse", "HEAD").stdout.strip() == sha
    assert subprocess.run(["git", "-C", str(reader), "symbolic-ref", "-q", "HEAD"], capture_output=True).returncode == 1
    names = port.names()
    assert names.index("worktree_list") < names.index("worktree_create") < names.index("task_create")
    [(_name, _args, kwargs)] = [call for call in port.calls if call[0] == "worktree_create"]
    assert (
        kwargs["comment"].startswith("gw-reader:") and kwargs["base_branch"] == "main" and kwargs["repo_id"] == "repo1"
    )
    attempt = dr.load_record(dr.record_file(layout, "work/x/children/feature-y", KEY)).attempts[-1]
    assert attempt.placement["marker"] == kwargs["comment"] and attempt.placement["reused"] is False
    assert attempt.placement["attempt_id"] == "run_1-1" and attempt.placement["start_sha"] == sha
    assert attempt.envelope["placement_argv"] == ["--worktree", f"path:{reader.resolve()}"]
    saved = layout.bundle_dir / "work/x/children/feature-y/references/orca-placement"
    assert json.loads((saved / f"{KEY}.dispatch.json").read_text(encoding="utf-8")) == plan["dispatches"][0]
    assert json.loads((saved / f"{KEY}.json").read_text(encoding="utf-8"))["marker"] == kwargs["comment"]
    from graph_works_core.orchestrate.placement import read_reader_receipt

    receipt = read_reader_receipt(layout, "work/x/children/feature-y", "ctx_1")
    assert receipt is not None and receipt["start_sha"] == sha and receipt["task_id"] == "task_1"
    assert receipt["worktree"] == str(reader.resolve()) and receipt["dispatch_key"] == KEY
    # The item page carries no placement stamp: the receipt is the only record.
    page = (layout.bundle_dir / "work/x/children/feature-y.md").read_text(encoding="utf-8")
    assert "worktree:" not in page
    assert port.names()[-1] == "task_update"


def test_reader_placement_is_the_reader_line_not_a_branch(reader_env):
    result = run(reader_env)
    assert result.ok and result.placement.branch is None and result.placement.start_sha


def test_root_reader_also_records_a_receipt(reader_env):
    layout, plan, *_ = reader_env
    plan["dispatches"][0]["path"] = "work/x"
    page = layout.bundle_dir / "work/x.md"
    page.write_bytes(page.read_bytes().replace(b"phase: execute", b"phase: design"))
    result = run(reader_env)
    assert result.ok, result.failure
    assert result.recorded == "reader-receipt"


def test_reader_reuses_its_own_marked_checkout_without_creating(reader_env, tmp_path):
    layout, _plan, port, _repo, reader, _sha = reader_env
    # A crash after `worktree create` left a marked, detached, clean checkout behind.
    first = run(reader_env)
    assert first.ok
    marker = port.calls[[c[0] for c in port.calls].index("worktree_create")][2]["comment"]
    port.listed = [_reader_row(port, reader, comment=marker)]
    port.create = None
    record_path = dr.record_file(layout, "work/x/children/feature-y", KEY)
    record_path.unlink()
    for path in (layout.bundle_dir / "work/x/children/feature-y/references/orca-placement").glob("*"):
        path.unlink()
    from graph_works_core.orchestrate.placement import reader_receipt_path

    reader_receipt_path(layout, "work/x/children/feature-y", "ctx_1").unlink()
    port.calls.clear()
    port.tasks.clear()
    port.workers.clear()
    second = run(reader_env)
    assert second.ok, second.failure
    assert "worktree_create" not in port.names()
    assert dr.load_record(record_path).attempts[-1].placement["reused"] is True


def test_reader_never_shares_a_checkout_across_attempts(reader_env):
    """A superseded attempt's ordinal changes the marker, so its checkout is not found."""
    layout, _plan, port, _repo, reader, _sha = reader_env
    assert run(reader_env).ok
    marker = port.calls[[c[0] for c in port.calls].index("worktree_create")][2]["comment"]
    record_path = dr.record_file(layout, "work/x/children/feature-y", KEY)
    superseded = dr.load_record(record_path).with_reroute(dr.Reroute("t", "stall", "task_1", "ctx_1", dr.Overrides()))
    dr.save_record(layout, superseded)
    port.tasks[0]["status"] = "blocked"
    port.listed = [_reader_row(port, reader, comment=marker)]
    port.create = lambda **kwargs: (_ for _ in ()).throw(BackendError("create refused: second attempt must allocate"))
    result = run(reader_env)
    assert result.failure.reason == "placement-refused" and "second attempt" in result.failure.detail


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"start_sha": "f" * 40}, "is not a commit"),
        ({"base_branch": "nope"}, "source branch nope is not a commit"),
        ({"parent_path": None, "base_branch": "main", "start_sha": None}, "reader dispatch needs"),
    ],
)
def test_reader_plan_refusals(reader_env, change, detail):
    plan = reader_env[1]
    plan["dispatches"][0]["worktree"].update(change)
    result = run(reader_env)
    assert not result.ok and detail in result.failure.detail
    assert result.failure.reason == ("plan-invalid" if "needs" in detail else "placement-refused")
    assert "worktree_create" not in reader_env[2].names()


def test_reader_with_ambiguous_markers_refuses_before_creating(reader_env):
    import hashlib
    import os

    _layout, _plan, port, repo, _reader, sha = reader_env
    # The marker formula is pinned here: key, SHA, real repo path, attempt ordinal.
    digest = hashlib.sha256(
        json.dumps([KEY, sha, os.path.realpath(repo), "run_1-1"], separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    marker = f"gw-reader:{digest}"
    port.listed = [
        {"id": "a", "repo_id": "repo1", "path": "/a", "comment": marker, "is_main": False, "parent_id": None},
        {"id": "b", "repo_id": "repo1", "path": "/b", "comment": marker, "is_main": False, "parent_id": None},
    ]
    result = run(reader_env)
    assert result.failure.reason == "placement-refused" and "ambiguous reader markers" in result.failure.detail
    assert "worktree_create" not in port.names()


@pytest.mark.parametrize(
    ("row_change", "detail"),
    [
        ({"repo_id": "other"}, "wrong observed repository"),
        ({"path": "relative"}, "not absolute"),
        ({"is_main": True}, "integration checkout"),
    ],
)
def test_reader_refuses_a_bad_created_row(reader_env, row_change, detail):
    _layout, _plan, port, _repo, _reader, _sha = reader_env
    inner = port.create

    def create(**kwargs):
        row = inner(**kwargs)
        row.update(row_change)
        return row

    port.create = create
    result = run(reader_env)
    assert result.failure.reason == "placement-refused" and detail in result.failure.detail


def test_reader_refuses_the_repository_checkout_or_a_known_path(reader_env):
    _layout, _plan, port, repo, reader, _sha = reader_env
    port.create = lambda **kwargs: _reader_row(port, repo, comment=kwargs["comment"])
    result = run(reader_env)
    assert result.failure.reason == "placement-refused" and "integration checkout" in result.failure.detail
    existing = reader.parent / "wt"  # the fixture's pre-existing feature worktree
    port.create = lambda **kwargs: _reader_row(port, existing, comment=kwargs["comment"])
    result = run(reader_env)
    assert result.failure.reason == "placement-refused" and "existing checkout" in result.failure.detail


def test_reader_refuses_a_dirty_or_moved_new_checkout(reader_env):
    _layout, _plan, port, _repo, reader, sha = reader_env
    inner = port.create

    def dirty(**kwargs):
        row = inner(**kwargs)
        (reader / "junk").write_text("x", encoding="utf-8")
        return row

    port.create = dirty
    result = run(reader_env)
    assert result.failure.reason == "placement-refused" and "checkout is dirty" in result.failure.detail
    assert git(reader, "rev-parse", "HEAD").stdout.strip() == sha  # never detached, never reset


def test_reader_is_never_reset_on_recovery(reader_env):
    layout, _plan, port, _repo, reader, _sha = reader_env
    assert run(reader_env).ok
    marker = port.calls[[c[0] for c in port.calls].index("worktree_create")][2]["comment"]
    git(reader, "checkout", "--quiet", "-b", "stray")
    port.listed = [_reader_row(port, reader, comment=marker)]
    port.create = None
    dr.record_file(layout, "work/x/children/feature-y", KEY).unlink()
    port.tasks.clear()
    port.workers.clear()
    result = run(reader_env)
    assert result.failure.reason == "placement-refused" and "never reset" in result.failure.detail
    assert git(reader, "branch", "--show-current").stdout.strip() == "stray"


def test_reader_settlement_verifies_the_detached_commit(reader_env):
    _layout, _plan, port, repo, reader, _sha = reader_env
    inner = port.worker_start

    def start_then_move(*args, **kwargs):
        git(repo, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "--allow-empty", "-m", "next")
        git(reader, "checkout", "--quiet", "--detach", "main")
        return inner(*args, **kwargs)

    port.worker_start = start_then_move
    result = run(reader_env)
    failure(result, "settle", "placement-mismatch", "task_1", "ctx_1")
    assert "reader checkout is not verified" in result.failure.detail


@pytest.mark.parametrize(
    ("row_change", "detail"),
    [
        ({"parent_id": "parent"}, "unexpected parent"),
        ({"path": "/elsewhere"}, "differs from the prepared path"),
    ],
)
def test_reader_settlement_refuses_a_moved_or_linked_row(reader_env, row_change, detail):
    _layout, _plan, port, _repo, _reader, _sha = reader_env
    inner = port.create

    def create(**kwargs):
        row = inner(**kwargs)
        port.worktrees["id:wt1"] = {**row, **row_change}
        return row

    port.create = create
    result = run(reader_env)
    failure(result, "settle", "placement-mismatch", "task_1", "ctx_1")
    assert detail in result.failure.detail


@pytest.mark.parametrize(
    ("name", "detail"), [(None, "no declared repository"), ("nope", "names no declared repository")]
)
def test_reader_receipt_refusals_are_placement_unrecorded(reader_env, name, detail):
    _layout, plan, _port, _repo, _reader, _sha = reader_env
    plan["dispatches"][0]["repo"]["name"] = name
    result = run(reader_env)
    failure(result, "record", "placement-unrecorded", "task_1", "ctx_1")
    assert detail in result.failure.detail


def test_reader_receipt_phase_refusal_carries_the_refusal(reader_env):
    _layout, plan, _port, _repo, _reader, _sha = reader_env
    plan["dispatches"][0]["phase"] = "plan"  # the item is at design
    result = run(reader_env)
    failure(result, "record", "placement-unrecorded", "task_1", "ctx_1")
    assert result.failure.refusal == "phase-mismatch"


def test_reader_receipt_replay_after_a_crash_is_reported(reader_env, monkeypatch):
    from graph_works_core.orchestrate.placement import read_reader_receipt

    layout, _plan, _port, _repo, _reader, _sha = reader_env
    crash(reader_env, monkeypatch, "record")
    assert read_reader_receipt(layout, "work/x/children/feature-y", "ctx_1") is not None
    # The journal never saw the record step finish, so the resumed attempt is inspection (as every other step).
    result = run(reader_env)
    assert result.failure.reason == "recovery-inspection" and result.failure.step == "record"


def test_reader_receipt_conflict_is_attempt_mismatch(reader_env):
    from graph_works_core.orchestrate.placement import reader_receipt_path

    layout, _plan, _port, _repo, _reader, _sha = reader_env
    target = reader_receipt_path(layout, "work/x/children/feature-y", "ctx_1")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"observation": {"start_sha": "0" * 40}}), encoding="utf-8", newline="\n")
    result = run(reader_env)
    failure(result, "record", "placement-unrecorded", "task_1", "ctx_1")
    assert result.failure.refusal == "attempt-mismatch"


def test_reader_resume_restores_the_detached_placement(reader_env, monkeypatch):
    _layout, _plan, _port, _repo, _reader, sha = reader_env
    crash(reader_env, monkeypatch, "task-update")
    result = run(reader_env)
    assert result.failure.reason == "recovery-inspection"
    assert result.placement is not None and result.placement.start_sha == sha and result.placement.branch is None
    assert result.recorded == "reader-receipt"


def head(path, ref="HEAD"):
    return git(path, "rev-parse", ref).stdout.strip()


def commit(path, message="work"):
    git(path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", message)


def item_text(env):
    return (env[0].bundle_dir / "work/x.md").read_text(encoding="utf-8")


def on_start(port, action):
    original = port.worker_start

    def start(*args, **kwargs):
        action()
        return original(*args, **kwargs)

    port.worker_start = start


def test_reuse_dispatch_records_the_pre_launch_head_not_the_worker_head(env):
    _, plan, port, _, wt = env
    plan["dispatches"][0]["worktree"].update(action="reuse", path=str(wt))
    before = head(wt)
    on_start(port, lambda: commit(wt))
    assert head(wt) == before
    result = run(env)
    assert result.ok, result.failure
    assert head(wt) != before
    text = item_text(env)
    assert f"start_sha: {before}" in text
    assert head(wt) not in text
    assert run(env).ok
    assert item_text(env) == text


def test_creation_dispatch_records_the_branch_creation_commit(env):
    _, _, port, repo, wt = env
    git(repo, "worktree", "remove", "--force", str(wt))
    git(repo, "branch", "-D", "feature/x")
    base = head(repo, "main")
    new = wt.parent / "wt-new"
    port.worktrees["id:wt1"]["path"] = str(new)

    def create():
        git(repo, "worktree", "add", "-b", "feature/x", str(new), "main")
        commit(new)

    on_start(port, create)
    result = run(env)
    assert result.ok, result.failure
    assert f"start_sha: {base}" in item_text(env)


def test_creation_dispatch_whose_base_moved_is_a_placement_mismatch(env):
    _, _, port, repo, wt = env
    git(repo, "worktree", "remove", "--force", str(wt))
    git(repo, "branch", "-D", "feature/x")
    new = wt.parent / "wt-new"
    port.worktrees["id:wt1"]["path"] = str(new)

    def create():
        commit(repo, "moved")
        git(repo, "worktree", "add", "-b", "feature/x", str(new), "main")
        commit(new)
        git(repo, "reflog", "expire", "--expire=now", "--all")

    on_start(port, create)
    failure(run(env), "settle", "placement-mismatch", "task_1", "ctx_1")


def test_a_reuse_dispatch_keeps_an_existing_baseline(env):
    layout, plan, _, _, wt = env
    plan["dispatches"][0]["worktree"].update(action="reuse", path=str(wt))
    old = head(wt)
    page = layout.bundle_dir / "work/x.md"
    page.write_text(
        item_text(env).replace(
            "affects: []\n", f"affects: []\nworktree: {wt}\nbranch: feature/x\nstart_sha: {old}\n", 1
        ),
        encoding="utf-8",
        newline="\n",
    )
    commit(wt)
    result = run(env)
    assert result.ok, result.failure
    assert f"start_sha: {old}" in item_text(env)


def test_a_code_phase_record_without_a_provable_baseline_is_unrecorded(env, monkeypatch):
    _, _, port, _, _ = env
    monkeypatch.setattr(d, "strict_commit", lambda *a, **k: d.GitFailure("nonzero", "boom"))
    failure(run(env), "place", "placement-refused")
    assert "worker_start" not in port.names()
