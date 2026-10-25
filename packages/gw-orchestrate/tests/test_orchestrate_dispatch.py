"""Dispatch against real journals, work-item writes, Git and the placement engine, with a scripted Orca seam."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from fake_orca_port import FakeOrcaPort, read
from gw_kernel import apply_init, plan_init
from gw_orchestrate import dispatch as d
from gw_orchestrate import dispatch_record as dr
from gw_orchestrate import placing
from gw_pipeline.placement import NullNotifier
from subagents_io.backend import BackendError

KEY = "gw-execute-x-1a2b"
TODAY = date(2026, 9, 26)
PAGE = (
    "---\ntype: {type}\ntitle: X\ndescription: d\nstatus: stable\n"
    "work_status: in-progress\nphase: {phase}\neffort: medium\n"
    "opened: 2026-09-26\nupdated: 2026-09-26\naffects: []\n---\n\n## Summary\nd\n"
)


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


def write_item(layout, path, *, type="Feature", phase="execute"):
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(PAGE.format(type=type, phase=phase), encoding="utf-8", newline="\n")
    return page


def computed(layout, path):
    resolved = placing.resolve(layout, path)
    assert not isinstance(resolved, placing.Refused), resolved
    assert resolved.placement is not None
    return resolved.placement


def planned(placement):
    """The orchestrate payload's `placement` block for *placement*."""
    return {
        "repo": {"name": placement.repo_name, "path": str(placement.repo_root)},
        "branch": placement.branch,
        "worktree": str(placement.worktree),
        "parent_branch": placement.parent_branch,
        "parent_worktree": str(placement.parent_worktree),
        "owner_path": placement.owner_path,
        "slug": placement.slug,
    }


def entry(layout, path, phase="execute", key=KEY):
    placement = computed(layout, path)
    return {
        "key": key,
        "path": path,
        "phase": phase,
        "mode": "autonomous",
        "agent": "claude",
        "model": "opus",
        "reasoning_effort": None,
        "prompt": f"Run /gw:workflow {path}.\n",
        "placement": planned(placement),
        "workspace": None,
        "repo": {"name": "graph-works", "path": str(placement.repo_root), "source": "manifest"},
    }


def row(path, branch, *, id="wt1"):
    return {
        "id": id,
        "repo_id": "repo1",
        "path": str(path),
        "branch": branch,
        "display_name": branch,
        "is_main": False,
        "parent_id": None,
        "comment": None,
    }


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "base")
    layout = apply_init(plan_init(tmp_path / ".works", today=TODAY, topic="Dispatch")).layout
    # Placement resolves declared repositories, independently of layout.repo_root.
    from ruamel.yaml import YAML

    yaml = YAML()
    manifest = yaml.load(layout.manifest_path.read_text(encoding="utf-8"))
    manifest["repositories"] = {"graph-works": {"path": str(repo)}}
    with layout.manifest_path.open("w", encoding="utf-8", newline="\n") as stream:
        yaml.dump(manifest, stream)
    write_item(layout, "work/x")
    plan = {"path": "work/x", "dispatches": [entry(layout, "work/x")]}
    wt = computed(layout, "work/x").worktree
    port = FakeOrcaPort(repos=[{"id": "repo1", "path": str(repo)}])
    port.worktrees["id:wt1"] = row(wt, plan["dispatches"][0]["placement"]["branch"])
    return layout, plan, port, repo, wt


def run(env, **kwargs):
    layout, plan, port, *_ = env
    return d.run_dispatch(
        layout,
        KEY,
        plan=kwargs.pop("plan", plan),
        run_id=kwargs.pop("run_id", "run_1"),
        port=port,
        notifier=kwargs.pop("notifier", NullNotifier()),
        today=TODAY,
        clock=lambda: datetime(2026, 9, 26, tzinfo=UTC),
        sleep=kwargs.pop("sleep", lambda _: None),
        **kwargs,
    )


def record(env):
    return dr.load_record(dr.record_file(env[0], "work/x", KEY))


def receipt_line(env, *, task="task_1", dispatch="ctx_1"):
    token = record(env).attempts[-1].steps["encode"].result["receipt"]["token"]
    return f"GW-RECEIPT v1 task={task} dispatch={dispatch} key={KEY} token={token}"


def receipt_sleep(env):
    def sleeping(_):
        env[2].reads = [read(messages=[{"role": "assistant", "text": receipt_line(env)}])]

    return sleeping


def test_first_reply_receipt_verifies_delivery(env):
    result = run(env, sleep=receipt_sleep(env))
    assert result.ok and result.probe == "submitted-transcript" and result.delivery == "verified"
    assert record(env).attempts[-1].steps["probe"].result == {"probe": "submitted-transcript", "delivery": "verified"}
    assert "terminal_send_enter" not in env[2].names()
    reads = [kwargs["limit"] for name, _, kwargs in env[2].calls if name == "worker_read"]
    assert reads == [5, 200]


def test_worker_asking_for_its_brief_is_submitted_but_unverified(env):
    env[2].reads = [read(messages=[{"role": "assistant", "text": "TASK block missing"}])]
    sleeps = []
    result = run(env, sleep=sleeps.append, settle_seconds=2)
    assert result.ok and result.probe == "submitted-transcript" and result.delivery == "unverified"
    assert "terminal_send_enter" not in env[2].names()
    reads = [kwargs["limit"] for name, _, kwargs in env[2].calls if name == "worker_read"]
    assert reads == [5, 200, 200, 200]
    assert sleeps == [2, 2, 2]


def test_delivery_receipt_can_arrive_on_last_bounded_read(env):
    def sleeping(_):
        if env[2].names().count("worker_read") == 3:
            env[2].reads = [read(messages=[{"role": "assistant", "text": receipt_line(env)}])]

    result = run(env, sleep=sleeping)
    assert result.delivery == "verified"
    assert env[2].names().count("worker_read") == 4


def test_heartbeat_only_worker_delivery_is_unverified_and_never_nudged(env):
    env[2].show["last_heartbeat_at"] = "now"
    env[2].reads = [read()]
    result = run(env)
    assert result.probe == "submitted-heartbeat" and result.delivery == "unverified"
    assert "terminal_send_enter" not in env[2].names()
    assert env[2].names().count("worker_read") == 3


@pytest.mark.parametrize("case", ["terminal", "clipped", "error"])
def test_untrustworthy_delivery_reads_are_inconclusive(env, monkeypatch, case):
    if case == "terminal":
        env[2].reads = [read(count=1), read(source="terminal")]
    elif case == "clipped":
        env[2].reads = [read(messages=[{"role": "assistant", "text": "hi"}], window_complete=False)]
    else:
        worker_read = env[2].worker_read

        def failing(dispatch_id, *, limit):
            if limit == 200:
                env[2].fail["worker_read"] = BackendError("unavailable")
            return worker_read(dispatch_id, limit=limit)

        monkeypatch.setattr(env[2], "worker_read", failing)
    result = run(env)
    assert result.ok and result.probe == "submitted-transcript" and result.delivery == "inconclusive"
    assert "terminal_send_enter" not in env[2].names()
    assert env[2].names().count("worker_read") == 2


def test_previous_attempts_receipt_does_not_verify_delivery(env):
    assert run(env).ok
    env[2].reads = [read(messages=[{"role": "assistant", "text": receipt_line(env, dispatch="ctx_0")}])]
    before = record(env)
    start = len(env[2].calls)
    again = run(env)
    assert again.status == "existing" and again.delivery == "unverified"
    assert env[2].names()[start:] == ["task_list", "worker_read"]
    assert record(env) == before


@pytest.mark.parametrize("initial", ["unverified", "inconclusive"])
def test_rerun_upgrades_a_later_delivery_receipt_without_launching(env, initial):
    env[2].reads = [read(count=1, window_complete=initial == "unverified")]
    assert run(env).delivery == initial
    env[2].reads = [read(messages=[{"role": "assistant", "text": receipt_line(env)}])]
    start = len(env[2].calls)
    again = run(env)
    assert again.status == "existing" and again.delivery == "verified"
    assert env[2].names()[start:] == ["task_list", "worker_read"]
    assert env[2].names().count("worker_start") == 1
    assert record(env).attempts[-1].steps["probe"].result == {"probe": "submitted-transcript", "delivery": "verified"}


def test_rerun_never_downgrades_verified_delivery(env):
    assert run(env, sleep=receipt_sleep(env)).delivery == "verified"
    env[2].fail["worker_read"] = BackendError("unavailable")
    before = record(env)
    start = len(env[2].calls)
    assert run(env).delivery == "verified"
    assert env[2].names()[start:] == ["task_list"]
    assert record(env) == before


@pytest.mark.parametrize("previous", ["unverified", "verified"])
def test_disabled_existing_probe_preserves_delivery_without_reads(env, previous):
    first = run(env, sleep=receipt_sleep(env)) if previous == "verified" else run(env)
    assert first.delivery == previous
    env[2].reads = [read(messages=[{"role": "assistant", "text": receipt_line(env)}])]
    before = record(env)
    start = len(env[2].calls)
    again = run(env, probe=False)
    assert again.ok and again.status == "existing" and again.delivery == previous
    assert env[2].names()[start:] == ["task_list"]
    assert record(env) == before


def test_other_existing_task_never_inherits_verified_delivery(env):
    assert run(env, sleep=receipt_sleep(env)).delivery == "verified"
    env[2].tasks[0]["id"] = "another_task"
    before = record(env)
    start = len(env[2].calls)
    result = run(env)
    assert result.ok and result.status == "existing" and result.task_id == "another_task"
    assert result.delivery == "unverified" and result.dispatch_id is None
    assert env[2].names()[start:] == ["task_list"]
    assert record(env) == before


def test_delivery_recheck_requires_exact_dispatch_identity(env):
    result = run(env)
    saved = record(env)
    context = d._Dispatch(
        env[0],
        env[2],
        TODAY,
        lambda: datetime(2026, 9, 26, tzinfo=UTC),
        replace(result, dispatch_id="another_dispatch"),
        record=saved,
        persisted=saved,
    )
    start = len(env[2].calls)
    d._recheck_delivery(context)
    assert env[2].calls[start:] == []
    assert record(env) == saved and context.result.delivery == "unverified"


def test_delivery_recheck_does_not_downgrade_inconclusive(env):
    env[2].reads = [read(count=1, window_complete=False)]
    assert run(env).delivery == "inconclusive"
    env[2].reads = [read(count=1)]
    before = record(env)
    assert run(env).delivery == "inconclusive"
    assert record(env) == before


def test_no_terminal_still_verifies_delivery_from_transcript(env, monkeypatch):
    env[2].start["terminal"] = None
    # The no-terminal branch has no submission sleep; install the receipt at the seam.
    worker_read = env[2].worker_read

    def with_receipt(dispatch_id, *, limit):
        env[2].reads = [read(messages=[{"role": "assistant", "text": receipt_line(env)}])]
        return worker_read(dispatch_id, limit=limit)

    monkeypatch.setattr(env[2], "worker_read", with_receipt)
    result = run(env)
    assert result.ok and result.probe == "no-terminal" and result.delivery == "verified"
    assert "terminal_send_enter" not in env[2].names()


@pytest.mark.parametrize("legacy_probe", ["submitted-transcript", "inconclusive"])
@pytest.mark.parametrize("recovery", ["existing", "resume"])
def test_legacy_attempt_reports_unverified_delivery_without_reads(env, legacy_probe, recovery):
    assert run(env).ok
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    steps = payload["attempts"][-1]["steps"]
    steps["encode"].pop("result")
    steps["probe"]["result"] = {"probe": legacy_probe}
    if recovery == "resume":
        del steps["task-update"]
    dr.write_json_atomic(path, payload)
    before = path.read_bytes()
    start = len(env[2].calls)
    again = run(env)
    assert again.ok and again.status == ("existing" if recovery == "existing" else "resumed")
    assert again.delivery == "unverified"
    assert env[2].names()[start:] == (["task_list"] if recovery == "existing" else ["task_list", "task_update"])
    if recovery == "existing":
        assert path.read_bytes() == before


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
    assert result.recorded == "unchanged" and result.probe == "submitted-transcript"
    assert env[2].names() == [
        "task_list",
        "task_create",
        "worker_start",
        "worktree_show",
        "worker_list",
        "worker_show",
        "worker_read",
        "worker_read",
        "worker_read",
        "worker_read",
        "task_update",
    ]
    page = (env[0].bundle_dir / "work/x.md").read_text(encoding="utf-8")
    assert "worktree:" not in page and "branch:" not in page and "repo_stamps" not in page
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
        "placement_argv": ["--worktree", f"path:{env[4]}"],
        "mode": "autonomous",
        "worktree_path": str(env[4]),
    }
    token = "010afd8d2c29bcb9"
    assert prompt == (
        "Run /gw:workflow work/x.\n\n"
        "Delivery receipt: before any other action, your first reply must contain this line on its own, "
        "with the task ID and dispatch ID from your Orca instructions above filled in:\n"
        f"GW-RECEIPT v1 task=<task ID> dispatch=<dispatch ID> key={KEY} token={token}\n"
    )
    assert record(env).attempts[-1].steps["encode"].result == {"receipt": {"version": 1, "token": token}}


def test_receipt_token_is_stable_across_resume(env):
    env[2].fail["task_create"] = BackendError("down")
    failure(run(env), "create", "task-create-failed")
    first = record(env).attempts[-1].steps["encode"].result
    assert first == {"receipt": {"version": 1, "token": "010afd8d2c29bcb9"}}
    env[2].fail.pop("task_create")
    assert run(env).status == "resumed"
    assert record(env).attempts[-1].steps["encode"].result == first
    specs = [kwargs["spec"] for name, _, kwargs in env[2].calls if name == "task_create"]
    headers, briefs = zip(*(spec.split("\n", 1) for spec in specs), strict=True)
    assert json.loads(headers[0].removeprefix("GW_LAUNCH_V1 ")) == json.loads(headers[1].removeprefix("GW_LAUNCH_V1 "))
    assert briefs[0] == briefs[1]
    assert specs[1].endswith(f"key={KEY} token=010afd8d2c29bcb9\n")
    assert len(record(env).attempts) == 1


@pytest.mark.parametrize("changed", ["Run different instructions.\n", "Run /gw:workflow work/x.\n\n"])
def test_receipt_bound_brief_change_refuses_uncertain_create_retry(env, changed):
    env[2].fail["task_create"] = BackendError("down")
    failure(run(env), "create", "task-create-failed")
    env[2].fail.pop("task_create")
    encoded = record(env).attempts[-1].steps["encode"]
    env[1]["dispatches"][0]["prompt"] = changed
    result = run(env)
    failure(result, "create", "recovery-inspection")
    assert "planned brief changed" in result.failure.detail
    assert env[2].names().count("task_create") == 1
    assert "worker_start" not in env[2].names()
    assert record(env).attempts[-1].steps["encode"] == encoded
    assert len(record(env).attempts) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"receipt": {"version": 2, "token": "0" * 16}},
        {"receipt": {"version": True, "token": "0" * 16}},
        {"receipt": {"version": 1.0, "token": "0" * 16}},
        {"receipt": {"version": 1, "token": "XYZ"}},
        {"receipt": {"version": 1, "token": "A" * 16}},
        {"receipt": {"version": 1, "token": "0" * 16 + "\n"}},
        {"receipt": {"version": 1, "token": 7}},
        {"receipt": {"version": 1}},
        {"receipt": {"token": "0" * 16}},
        {"receipt": {"version": 1, "token": "0" * 16, "extra": 1}},
        {"receipt": None},
        {"receipt": []},
        {"other": 1},
    ],
)
def test_invalid_persisted_receipt_is_record_invalid(env, bad):
    env[2].fail["task_create"] = BackendError("down")
    failure(run(env), "create", "task-create-failed")
    env[2].fail.pop("task_create")
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["attempts"][-1]["steps"]["encode"]["result"] = bad
    dr.write_json_atomic(path, payload)
    before = path.read_bytes()
    start = len(env[2].calls)
    failure(run(env), "validate", "record-invalid")
    assert env[2].calls[start:] == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("encoded", [None, {}])
def test_legacy_receipt_free_attempt_resumes_without_appending_instruction(env, encoded):
    env[2].fail["task_create"] = BackendError("down")
    failure(run(env), "create", "task-create-failed")
    env[2].fail.pop("task_create")
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    step = payload["attempts"][-1]["steps"]["encode"]
    if encoded is None:
        step.pop("result", None)
    else:
        step["result"] = encoded
    dr.write_json_atomic(path, payload)
    assert run(env).status == "resumed"
    assert env[2].tasks[0]["spec"].split("\n", 1)[1] == "Run /gw:workflow work/x.\n"
    assert record(env).attempts[-1].steps["encode"].result == encoded


def test_the_record_never_stores_the_prompt(env):
    env[1]["dispatches"][0]["prompt"] = "Unique private instruction dcap_not_for_disk --dispatch-capability\n"
    assert run(env).ok
    text = dr.record_file(env[0], "work/x", KEY).read_text(encoding="utf-8")
    assert "Unique private instruction" not in text and "dcap_" not in text
    assert "GW-RECEIPT" not in text and "Delivery receipt:" not in text
    assert "Unique private instruction" in env[2].tasks[0]["spec"]


@pytest.mark.parametrize("kind", ["not-dict", "prompt", "run", "entry", "placement", "repo", "model"])
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
    assert not env[4].exists() and not env[2].calls


def test_reroute_override_is_applied_to_the_new_envelope(env):
    reroute(env, dr.Overrides(agent="codex"))
    result = run(env)
    assert result.ok and result.task_id == "task_2"
    assert record(env).attempts[-1].envelope["agent"] == "codex"
    assert record(env).attempts[-1].envelope["model"] is None


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
        "launchRequest": {"prompt": "carried-secret dcap_secret"},
        "receipt": {"preamble": "carried-secret"},
    }
    env[2].fail["worker_start"] = error
    result = run(env)
    failure(result, "launch", "launch-failed", "task_1", "ctx_failed")
    assert result.terminal == "term_failed"
    assert record(env).attempts[-1].dispatch_id == "ctx_failed"
    text = dr.record_file(env[0], "work/x", KEY).read_text(encoding="utf-8")
    assert (
        "carried-secret" not in text and "launchRequest" not in text and "preamble" not in text and "prompt" not in text
    )
    assert record(env).attempts[-1].steps["launch"].result == {
        "dispatch_id": "ctx_failed",
        "terminal": "term_failed",
        "worktree_id": None,
    }
    assert record(env).attempts[-1].steps["encode"].result == {"receipt": {"version": 1, "token": "010afd8d2c29bcb9"}}


@pytest.mark.parametrize("case", ["directory", "claimed", "row", "wid"])
def test_placement_mismatch(env, case):
    if case == "directory":
        on_start(env[2], lambda: git(env[3], "worktree", "remove", "--force", str(env[4])))
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


def test_two_dispatches_with_one_key_create_one_task(env):
    assert run(env).ok
    assert run(env).status == "existing"
    assert env[2].names().count("task_create") == 1


def returned_execute(env):
    """Model the active intent and reused checkout after a successful return."""
    layout = env[0]
    page = layout.bundle_dir / "work/x.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "affects: []\n",
            "affects: []\n"
            "execute_return:\n"
            "  id: ret-20260926-1a2b3c4d\n"
            "  recorded: 2026-09-26\n"
            "  state: active\n"
            "  coverage: /work/x/references/03-execute-coverage.md\n"
            "  scope:\n"
            "    - {id: R1, text: Implement the newly returned task}\n",
            1,
        ),
        encoding="utf-8",
        newline="\n",
    )


def test_returned_execute_key_with_settled_task_is_existing_until_rerouted(env):
    from gw_orchestrate.reroute import run_reroute

    first = run(env)
    assert first.ok, first.failure
    port = env[2]
    port.tasks[0]["status"] = "completed"
    port.workers[0].update(state="succeeded", dispatch_status="completed")
    returned_execute(env)
    before = record(env)
    start = len(port.calls)

    existing = run(env)

    assert existing.ok and existing.status == "existing"
    assert (existing.task_id, existing.dispatch_id) == (first.task_id, first.dispatch_id)
    assert port.names()[start:] == ["task_list", "worker_read"]
    assert record(env) == before

    reason = "execute return ret-20260926-1a2b3c4d"
    rerouted = run_reroute(
        env[0], KEY, run_id="run_1", reason=reason, port=port, clock=lambda: datetime(2026, 9, 26, tzinfo=UTC)
    )
    assert rerouted.ok and rerouted.status == "rerouted"
    assert (rerouted.superseded_task_id, rerouted.superseded_dispatch_id) == (first.task_id, first.dispatch_id)
    assert rerouted.overrides == dr.Overrides()
    assert record(env).reroutes[-1].reason == reason
    assert port.tasks[0]["status"] == "blocked"
    port.start["dispatch_id"] = "ctx_2"

    fresh = run(env)

    assert fresh.ok and fresh.status == "dispatched", fresh.failure
    assert fresh.task_id != first.task_id and fresh.dispatch_id != first.dispatch_id
    saved = record(env)
    assert len(saved.attempts) == 2 and saved.attempts[-1].complete
    for field in ("agent", "model", "reasoning_effort"):
        assert saved.attempts[-1].envelope[field] == saved.attempts[0].envelope[field]
    assert port.names().count("task_create") == port.names().count("worker_start") == 2


def test_returned_execute_key_without_prior_task_dispatches_directly(env):
    returned_execute(env)
    assert not env[2].tasks and record(env) is None

    result = run(env)

    assert result.ok and result.status == "dispatched", result.failure
    saved = record(env)
    assert len(saved.attempts) == 1 and saved.attempts[-1].complete
    assert not saved.reroutes and not saved.superseded
    assert env[2].names().count("task_create") == env[2].names().count("worker_start") == 1


def test_reroute_refuses_live_attempt_for_returned_key(env):
    from gw_orchestrate.reroute import run_reroute
    from test_reroute import worker

    first = run(env)
    assert first.ok, first.failure
    env[2].workers = [worker(dispatch_id=first.dispatch_id, task_id=first.task_id, state="running")]
    returned_execute(env)
    before = record(env)
    start = len(env[2].calls)

    result = run_reroute(
        env[0],
        KEY,
        run_id="run_1",
        reason="execute return ret-20260926-1a2b3c4d",
        port=env[2],
        clock=lambda: datetime(2026, 9, 26, tzinfo=UTC),
    )

    assert not result.ok and result.status is None
    assert (result.failure.step, result.failure.reason) == ("reroute", "reroute-live")
    assert (result.failure.task_id, result.failure.dispatch_id) == (first.task_id, first.dispatch_id)
    assert record(env) == before
    assert env[2].names()[start:] == ["task_list", "task_list", "worker_list"]


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
    assert result.delivery == "unverified"
    assert env[2].names() == ["task_list"]


def at_plan(env):
    """Make work/x a plan-stage dispatch: placed, but recording no code stamp."""
    write_item(env[0], "work/x", phase="plan")
    env[1]["dispatches"][0]["phase"] = "plan"


def test_record_step_writes_no_stamp(env):
    """The `record` step stays in the journal for Runs that straddle the upgrade; it writes nothing."""
    page = env[0].bundle_dir / "work/x.md"
    result = run(env)
    assert result.ok, result.failure
    text = page.read_text(encoding="utf-8")
    assert not any(key in text for key in ("worktree:", "branch:", "repo_stamps"))
    assert record(env).attempts[-1].steps["record"].state == "done"
    assert result.recorded == "unchanged"


def test_a_design_or_plan_dispatch_records_no_stamp(env):
    at_plan(env)
    page = env[0].bundle_dir / "work/x.md"
    before = page.read_bytes()
    result = run(env)
    assert result.ok and result.recorded == "skipped:read-only-phase"
    assert page.read_bytes() == before
    assert record(env).attempts[-1].steps["record"] == dr.StepState(
        "skipped", at=record(env).attempts[-1].steps["record"].at, reason="read-only-phase"
    )


@pytest.mark.parametrize("skipped_step", ["record", "probe"])
def test_skipped_dispatch_can_be_called_again(env, skipped_step):
    if skipped_step == "record":
        at_plan(env)
    first = run(env, probe=skipped_step != "probe")
    assert first.ok
    path = dr.record_file(env[0], env[1]["dispatches"][0]["path"], KEY)
    assert dr.load_record(path).attempts[-1].steps[skipped_step].state == "skipped"
    start = len(env[2].calls)

    second = run(env, probe=skipped_step != "probe")

    assert second.ok and second.status == "existing"
    assert env[2].names()[start:] == (["task_list", "worker_read"] if skipped_step == "record" else ["task_list"])


@pytest.mark.parametrize("recovery", ["existing", "resume"])
@pytest.mark.parametrize("skipped_step", ["record", "probe"])
def test_persisted_skipped_step_without_result_is_valid(env, skipped_step, recovery):
    if skipped_step == "record":
        at_plan(env)
    assert run(env, probe=skipped_step != "probe").ok
    path = dr.record_file(env[0], env[1]["dispatches"][0]["path"], KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    attempt = payload["attempts"][-1]
    assert attempt["steps"][skipped_step]["state"] == "skipped"
    attempt["steps"][skipped_step].pop("result", None)
    if recovery == "resume":
        del attempt["steps"]["task-update"]
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    start = len(env[2].calls)

    result = run(env, probe=skipped_step != "probe")

    assert result.ok and result.status == ("existing" if recovery == "existing" else "resumed")
    if skipped_step == "record":
        assert result.recorded == "skipped:read-only-phase"
    else:
        assert result.probe == "skipped"
    expected = ["task_list"]
    if recovery == "resume":
        expected.append("task_update")
    elif skipped_step == "record":
        expected.append("worker_read")
    assert env[2].names()[start:] == expected
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
        env[2].reads = [read("terminal")]
    elif case == "failed":
        env[2].fail["worker_read"] = BackendError("unreadable")
    else:
        env[2].reads = [read(), read("terminal")]
        if case == "reread-failed":

            def sleeping(_):
                if "terminal_send_enter" in env[2].names():
                    env[2].fail["worker_read"] = BackendError("x")

            result = run(env, sleep=sleeping)
    if case != "reread-failed":
        result = run(env)
    assert result.ok and result.probe == "inconclusive"
    assert result.delivery == "inconclusive"
    assert env[2].names().count("worker_read") == (2 if case.startswith("reread") else 1)
    assert env[2].names().count("terminal_send_enter") == (1 if case.startswith("reread") else 0)


@pytest.mark.parametrize("after_read", [0, 1, 2])
def test_heartbeat_vetoes_every_nudge(env, monkeypatch, after_read):
    env[2].reads = [read()]
    show = env[2].worker_show

    def heartbeat(dispatch):
        value = show(dispatch)
        if env[2].names().count("worker_read") >= after_read:
            value["last_heartbeat_at"] = "now"
        return value

    monkeypatch.setattr(env[2], "worker_show", heartbeat)
    result = run(env)
    assert result.ok and result.probe == "submitted-heartbeat"
    assert result.delivery == "unverified"
    assert env[2].names().count("terminal_send_enter") == max(0, after_read - 1)


def test_unsent_after_exactly_two_nudges(env):
    env[2].reads = [read()]
    result = run(env)
    failure(result, "probe", "unsent", "task_1", "ctx_1")
    assert result.delivery is None
    assert env[2].names().count("worker_read") == 3
    assert env[2].names().count("terminal_send_enter") == 2


def test_recovered_submission_is_nudged(env):
    env[2].reads = [read(), read(count=1)]
    result = run(env)
    assert result.probe == "nudged" and result.delivery == "unverified"
    assert env[2].names().count("terminal_send_enter") == 1


@pytest.mark.parametrize("case", ["disabled", "terminal"])
def test_no_probe_and_no_terminal(env, case):
    if case == "terminal":
        env[2].start["terminal"] = None
    result = run(env, probe=case != "disabled")
    assert result.ok and result.probe == ("skipped" if case == "disabled" else "no-terminal")
    assert result.delivery == ("skipped" if case == "disabled" else "unverified")
    assert env[2].names().count("worker_read") == (0 if case == "disabled" else 3)


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
    assert result.recorded == "unchanged" and result.probe == "submitted-transcript"
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


def test_worker_show_supplies_a_missing_worktree_id(env):
    env[2].start["worktree_id"] = None
    result = run(env)
    assert result.ok, result.failure
    assert "worker_show" in env[2].names()
    assert record(env).attempts[-1].placement["worktree_id"] == "wt1"


@pytest.mark.parametrize("state", ["succeeded", "failed", "stopped"])
def test_settled_workers_do_not_claim_worktree(env, state):
    env[2].workers = [
        {"dispatch_id": "old", "task_id": "old", "worktree_id": "wt1", "state": state, "dispatch_status": "settled"}
    ]
    assert run(env).ok


@pytest.mark.parametrize("field", ["branch", "worktree", "parent_branch", "parent_worktree", "repo"])
def test_missing_placement_fields_are_plan_invalid(env, field):
    env[1]["dispatches"][0]["placement"][field] = None if field == "repo" else ""
    failure(run(env), "validate", "plan-invalid")
    assert not env[2].calls and not env[4].exists()


def test_record_unchanged_is_verified(env):
    assert run(env).ok
    env[2].workers[0]["state"] = "stopped"
    reroute(env, dr.Overrides())
    assert run(env).recorded == "unchanged"


@pytest.mark.parametrize("call", ["worker_show", "terminal_send_enter"])
def test_probe_errors_are_inconclusive(env, call):
    env[2].reads = [read()]
    env[2].fail[call] = BackendError("unavailable")
    result = run(env)
    assert result.ok and result.probe == "inconclusive"
    assert env[2].names().count("terminal_send_enter") <= 1


def test_branch_probe_failure_cannot_be_mistaken_for_verified_output(env, monkeypatch):
    from gw_kernel.workspace.provenance import GitOutcome

    original = d.probe_git

    def probe(path, *args):
        if args == ("symbolic-ref", "--short", "HEAD"):
            return GitOutcome(1, env[1]["dispatches"][0]["placement"]["branch"] + "\n", "ok", "failed")
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
    assert result.placement.notes[-1:] == ("in-review not set: offline",)
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
        ("settle.result.branch", ""),
        ("settle.result.start_sha", 7),
        ("settle.result.action", "reuse"),
        ("envelope", {}),
        ("envelope.extra", "unexpected"),
        ("envelope.version", True),
        ("envelope.placement_argv", None),
        ("envelope.placement_argv", [7]),
        ("envelope.model", {}),
        ("envelope.worktree_path", []),
        ("placement", None),
        ("placement", {}),
        ("placement.extra", "unexpected"),
        ("placement.path", ""),
        ("placement.branch", 7),
        ("placement.chain", None),
        ("placement.chain", [{"path": "/w", "action": "created"}]),
        ("placement.orca", 7),
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
    from gw_orchestrate.reroute import run_reroute

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
    from gw_orchestrate.reroute import run_reroute

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

    from gw_orchestrate.reroute import run_reroute

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


def test_execute_records_the_engine_baseline_not_the_worker_head(env):
    _, _, port, _, wt = env
    on_start(port, lambda: commit(wt))
    result = run(env)
    assert result.ok, result.failure
    before = git(wt, "rev-parse", "HEAD~1").stdout.strip()
    text = item_text(env)
    assert f"start_sha: {before}" in text and result.placement.start_sha == before
    assert head(wt) not in text
    assert run(env).ok
    assert item_text(env) == text


def test_an_existing_checkout_keeps_its_recorded_baseline(env):
    layout, plan, _, repo, wt = env
    branch = plan["dispatches"][0]["placement"]["branch"]
    git(repo, "worktree", "add", "-b", branch, str(wt))
    old = head(wt)
    page = layout.bundle_dir / "work/x.md"
    page.write_text(
        item_text(env).replace(
            "affects: []\n", f"affects: []\nworktree: {wt}\nbranch: {branch}\nstart_sha: {old}\n", 1
        ),
        encoding="utf-8",
        newline="\n",
    )
    commit(wt)
    result = run(env)
    assert result.ok, result.failure
    assert f"start_sha: {old}" in item_text(env)
    assert result.recorded == "unchanged"


# --- computed placement through the engine ----------------------------------


def fake_realize(monkeypatch, outcome):
    """Replace the engine seam; record each call's keywords and answer *outcome*."""
    calls = []

    def realize(layout, path, **kwargs):
        calls.append({"path": path, **kwargs})
        return outcome(layout, path, **kwargs) if callable(outcome) else outcome

    monkeypatch.setattr(d, "realize", realize)
    return calls


def test_place_runs_before_any_port_call(env, monkeypatch):
    fake_realize(monkeypatch, placing.Refused("refresh-conflict", "merging main into x conflicted in a.py"))
    result = run(env)
    failure(result, "place", "placement-refused")
    assert result.failure.refusal == "refresh-conflict"
    assert result.failure.detail == "merging main into x conflicted in a.py"
    assert env[2].calls == []
    assert record(env) is None


def test_execute_starts_other_phases_ensure(env, monkeypatch):
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "held"))
    run(env)
    write_item(env[0], "work/x", phase="design")
    env[1]["dispatches"][0]["phase"] = "design"
    run(env)
    assert [(c["phase"], c["verify_only"]) for c in calls] == [("execute", False), ("design", False)]
    assert all(isinstance(c["notifier"], NullNotifier) for c in calls)


def test_planned_placement_differs_from_computed_stops_before_engine(env, monkeypatch):
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    # The item was reparented since the plan: its computed worktree moved.
    env[1]["dispatches"][0]["placement"]["worktree"] = str(env[4].parent / "elsewhere")
    result = run(env)
    failure(result, "place", "plan-invalid")
    assert "worktree" in result.failure.detail
    assert calls == [] and env[2].calls == [] and record(env) is None


def test_a_planned_placement_for_an_item_with_no_code_repository_is_plan_invalid(env, monkeypatch):
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    page = env[0].bundle_dir / "work/x.md"
    page.write_text(item_text(env).replace("affects: []", "affects: [gw:workspace]"), encoding="utf-8", newline="\n")
    failure(run(env), "place", "plan-invalid")
    assert calls == [] and env[2].calls == []


def test_an_unresolvable_item_is_refused_with_the_engine_kind(env, monkeypatch):
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    (env[0].bundle_dir / "work/x.md").unlink()
    result = run(env)
    failure(result, "place", "placement-refused")
    assert result.failure.refusal == "unknown-item" and calls == []


def test_placement_argv_is_always_path(env):
    result = run(env)
    assert result.ok, result.failure
    envelope = record(env).attempts[-1].envelope
    assert envelope["placement_argv"] == ["--worktree", f"path:{env[4]}"]
    assert envelope["worktree_path"] == str(env[4])
    started = next(k for n, _, k in env[2].calls if n == "worker_start")
    assert started["placement_argv"] == ["--worktree", f"path:{env[4]}"]
    placed = record(env).attempts[-1].placement
    assert placed == {
        "path": str(env[4]),
        "branch": env[1]["dispatches"][0]["placement"]["branch"],
        "parent_path": str(env[3]),
        "start_sha": head(env[3], "main"),
        "placement_argv": ["--worktree", f"path:{env[4]}"],
        "chain": [{"path": str(env[4]), "action": "created", "refresh": "none"}],
        "orca": "orca: not configured",
        "worktree_id": "wt1",
    }


@pytest.mark.parametrize("stale_block", [False, True])
def test_null_placement_launches_in_workspace_root_without_engine(env, monkeypatch, tmp_path, stale_block):
    """A plan saved before workspace placement was deleted may carry a `workspace` block; it is ignored."""
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    write_item(env[0], "work/x", phase="design")
    planned_entry = env[1]["dispatches"][0]
    planned_entry.update(phase="design", placement=None)
    target, branch = str(env[0].root), None
    if stale_block:
        planned_entry["workspace"] = {"worktree": str(tmp_path / "content"), "branch": "ws/x"}
    env[2].worktrees["id:wt1"] = row(target, branch)
    result = run(env)
    assert result.ok, result.failure
    assert calls == []
    envelope = record(env).attempts[-1].envelope
    assert envelope["placement_argv"] == ["--worktree", f"path:{target}"] and envelope["worktree_path"] == target
    assert (result.placement.path, result.placement.branch) == (target, branch)
    assert result.recorded == "skipped:read-only-phase"


def test_settle_passes_on_computed_path_and_branch(env):
    result = run(env)
    assert result.ok, result.failure
    branch = env[1]["dispatches"][0]["placement"]["branch"]
    assert (result.placement.path, result.placement.branch) == (str(env[4]), branch)
    assert result.placement.display_name == branch
    settled = record(env).attempts[-1].steps["settle"].result
    assert set(settled) == {"path", "branch", "display_name", "notes", "start_sha"}


def test_settle_fails_on_path_mismatch(env, tmp_path):
    env[2].worktrees["id:wt1"]["path"] = str(tmp_path)
    result = run(env)
    failure(result, "settle", "placement-mismatch", "task_1", "ctx_1")
    assert "differs from the computed" in result.failure.detail


def test_settle_fails_on_branch_mismatch(env):
    on_start(env[2], lambda: git(env[4], "checkout", "-q", "-b", "elsewhere"))
    result = run(env)
    failure(result, "settle", "placement-mismatch", "task_1", "ctx_1")
    assert "elsewhere" in result.failure.detail


def test_resumed_settle_does_not_place_again(env, monkeypatch):
    save = dr.compare_and_swap_record

    def interrupt(layout, value, **kwargs):
        if "settle" in value.attempts[-1].steps:
            raise KeyboardInterrupt
        return save(layout, value, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(dr, "compare_and_swap_record", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(env)
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    result = run(env)
    assert result.ok and result.status == "resumed", result.failure
    assert calls == [] and result.recorded == "unchanged"
    assert env[2].names().count("worker_start") == 1


def test_attempted_settle_halts_without_placing(env, monkeypatch):
    crash(env, monkeypatch, "settle")
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    result = run(env)
    failure(result, "settle", "recovery-inspection", "task_1", "ctx_1")
    assert calls == []


def test_occupied_answers_from_run_journal_and_orca(env, monkeypatch, tmp_path):
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "stop"))
    held, orca_held, free = (str(tmp_path / name) for name in ("held", "orca", "free"))
    for path in ("work/other", "work/done", "work/far", "work/bad"):
        write_item(env[0], path)
    other = dr.DispatchRecord("gw-execute-other-9f9f", "work/other", "execute", "run_1")
    other = other.with_attempt(
        dr.Attempt(
            None, None, "work/other · execute", {"version": 2}, {"path": held}, {"create": dr.StepState("attempted")}
        )
    )
    dr.save_record(env[0], other)
    finished = dr.DispatchRecord("gw-execute-done-8e8e", "work/done", "execute", "run_1")
    done = {name: dr.StepState("done") for name in dr.STEPS}
    finished = finished.with_attempt(dr.Attempt("t", "c", "done", {"version": 2}, {"path": free}, done))
    dr.save_record(env[0], finished)
    elsewhere = dr.DispatchRecord("gw-execute-far-7d7d", "work/far", "execute", "run_2")
    elsewhere = elsewhere.with_attempt(
        dr.Attempt(None, None, "far", {"version": 2}, {"path": free}, {"create": dr.StepState("attempted")})
    )
    dr.save_record(env[0], elsewhere)
    env[2].worktrees[f"path:{orca_held}"] = row(orca_held, "b", id="wt_orca")
    env[2].workers = [{"dispatch_id": "ctx_orca", "task_id": "t", "state": "running", "worktree_id": "wt_orca"}]
    run(env)
    occupied = calls[0]["occupied"]
    assert "gw-execute-other-9f9f" in occupied(held)
    assert "ctx_orca" in occupied(orca_held)
    assert occupied(free) is None
    env[2].workers[0]["state"] = "succeeded"
    assert occupied(orca_held) is None
    env[2].fail["worker_list"] = BackendError("offline")
    assert "offline" in occupied(orca_held)
    bad = dr.record_file(env[0], "work/bad", "gw-execute-bad-6c6c")
    bad.parent.mkdir(parents=True)
    bad.write_text("garbage", encoding="utf-8", newline="\n")
    assert "unreadable" in occupied(free)


def test_legacy_journal_placement_loads_and_resumes(env, monkeypatch):
    save = dr.compare_and_swap_record

    def interrupt(layout, value, **kwargs):
        if "record" in value.attempts[-1].steps:
            raise KeyboardInterrupt
        return save(layout, value, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(dr, "compare_and_swap_record", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(env)
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    attempt = payload["attempts"][-1]
    sha = attempt["placement"]["start_sha"]
    branch = attempt["placement"]["branch"]
    # Exactly what the dispatcher before computed placement journaled for a creation.
    argv = ["--worktree", "new-top-level", "--name", branch, "--base-branch", "main", "--repo", "id:repo1"]
    attempt["envelope"].update(placement_argv=argv, worktree_path=None)
    attempt["placement"] = {
        "action": "create-top-level",
        "placement_argv": argv,
        "repo_id": "repo1",
        "parent_worktree_id": None,
        "start_sha": sha,
        "worktree_id": "wt1",
        "marker": "gw-reader:0",
        "reused": False,
    }
    attempt["steps"]["settle"]["result"] = {
        "action": "create-top-level",
        "path": str(env[4]),
        "branch": branch,
        "display_name": branch,
        "repo_id": "repo1",
        "parent_worktree_id": None,
        "lineage_set": False,
        "notes": [],
        "start_sha": sha,
    }
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    result = run(env)
    assert result.ok and result.status == "resumed", result.failure
    assert calls == [] and result.recorded == "unchanged"
    assert (result.placement.path, result.placement.branch, result.placement.start_sha) == (str(env[4]), branch, sha)
    assert "worktree:" not in item_text(env)


def test_a_legacy_settle_is_resumed_from_the_launch_path(env, monkeypatch):
    save = dr.compare_and_swap_record

    def interrupt(layout, value, **kwargs):
        if "settle" in value.attempts[-1].steps:
            raise KeyboardInterrupt
        return save(layout, value, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(dr, "compare_and_swap_record", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(env)
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    attempt = payload["attempts"][-1]
    attempt["placement"] = {
        "action": "reuse",
        "placement_argv": ["--worktree", f"path:{env[4]}"],
        "repo_id": None,
        "parent_worktree_id": None,
        "start_sha": attempt["placement"]["start_sha"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    result = run(env)
    assert result.ok and result.status == "resumed", result.failure
    assert result.placement.path == str(env[4])


def test_a_legacy_creation_without_a_path_cannot_settle(env, monkeypatch):
    save = dr.compare_and_swap_record

    def interrupt(layout, value, **kwargs):
        if "settle" in value.attempts[-1].steps:
            raise KeyboardInterrupt
        return save(layout, value, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(dr, "compare_and_swap_record", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(env)
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    argv = ["--worktree", "new-top-level", "--name", "b", "--base-branch", "main", "--repo", "id:repo1"]
    attempt = payload["attempts"][-1]
    attempt["envelope"]["placement_argv"] = argv
    attempt["placement"] = {"action": "fork-child", "placement_argv": argv, "repo_id": "r", "parent_worktree_id": None}
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    failure(run(env), "settle", "placement-mismatch", "task_1", "ctx_1")


def test_end_to_end_dispatch_lands_in_computed_worktree(env):
    _, plan, port, repo, wt = env
    branch = plan["dispatches"][0]["placement"]["branch"]
    assert not wt.exists()
    result = run(env)
    assert result.ok, result.failure
    assert git(wt, "symbolic-ref", "--short", "HEAD").stdout.strip() == branch
    assert os.path.realpath(git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()) == (
        os.path.realpath(repo / ".git")
    )
    text = item_text(env)
    assert "worktree:" not in text and "branch:" not in text and "repo_stamps" not in text
    assert f"start_sha: {head(repo, 'main')}" in text
    assert [name for name in port.names() if name.startswith("worktree_")] == ["worktree_show"]


# --- retry and park-resume: gw work dispatch --retry-of ----------------------

_MUTATIONS = {"task_create", "task_update", "worker_start", "worktree_set_status", "terminal_send_enter"}


def dispatched(env):
    """A completed first attempt on task_1 / ctx_1; the next worker start returns ctx_2."""
    result = run(env)
    assert result.ok, result.failure
    env[2].start["dispatch_id"] = "ctx_2"
    return result


def rewrite(env, change):
    """Apply *change* to the persisted journal's JSON payload."""
    path = dr.record_file(env[0], "work/x", KEY)
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")


def supersede(env):
    reroute = dr.Reroute("2026-09-26T00:00:00+00:00", "why", "task_1", "ctx_1", dr.Overrides())
    dr.save_record(env[0], record(env).with_reroute(reroute))


def later_attempt(env):
    rewrite(env, lambda p: p["attempts"].append({**p["attempts"][-1], "dispatch_id": "ctx_2"}))


def absent_task(env):
    env[2].tasks = []


@pytest.mark.parametrize(
    "case,retry_of,detail",
    [
        (supersede, "ctx_1", "superseded"),
        (lambda env: None, "ctx_other", "ctx_other"),
        (later_attempt, "ctx_1", "already retried"),
        (absent_task, "ctx_1", "task_list"),
    ],
    ids=["superseded", "wrong-dispatch", "later-attempt", "absent-task"],
)
def test_retry_preconditions_stop_recovery_inspection(env, monkeypatch, case, retry_of, detail):
    dispatched(env)
    case(env)
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    before, start = record(env), len(env[2].calls)
    result = run(env, retry_of=retry_of)
    assert result.failure is not None and result.failure.reason == "recovery-inspection", result.failure
    assert detail in result.failure.detail
    assert not _MUTATIONS & set(env[2].names()[start:])
    assert calls == [] and record(env) == before


def test_retry_of_a_fresh_key_has_no_attempt_to_retry(env):
    result = run(env, retry_of="ctx_1")
    assert result.failure is not None and result.failure.reason == "recovery-inspection"
    assert "no attempt" in result.failure.detail
    assert env[2].calls == [] and record(env) is None


def test_good_retry_journals_second_attempt_on_same_task(env, monkeypatch):
    first = dispatched(env)
    calls = fake_realize(monkeypatch, placing.realize)
    start = len(env[2].calls)
    result = run(env, retry_of="ctx_1")
    assert result.ok, result.failure
    assert (result.status, result.task_id, result.dispatch_id) == ("dispatched", "task_1", "ctx_2")
    assert [c["verify_only"] for c in calls] == [True]
    journal = record(env)
    assert [(a.task_id, a.dispatch_id) for a in journal.attempts] == [("task_1", "ctx_1"), ("task_1", "ctx_2")]
    retried = journal.attempts[-1]
    assert retried.complete
    assert retried.steps["create"].state == "skipped"
    assert retried.steps["create"].result == {"task_id": "task_1", "retry_of": "ctx_1"}
    assert retried.envelope == journal.attempts[0].envelope
    assert retried.placement["start_sha"] == first.placement.start_sha
    assert retried.steps["encode"].result == journal.attempts[0].steps["encode"].result
    names = env[2].names()[start:]
    assert "task_create" not in names
    started = [k for n, _, k in env[2].calls[start:] if n == "worker_start"]
    assert started == [
        {"request": dict(retried.envelope), "placement_argv": ["--worktree", f"path:{env[4]}"], "retry_of": "ctx_1"}
    ]
    assert result.recorded == "unchanged"
    # The retry is now the latest attempt: a second retry of the first dispatch is "already retried".
    again = run(env, retry_of="ctx_1")
    assert again.failure is not None and again.failure.reason == "recovery-inspection"


def test_a_plain_dispatch_never_passes_retry_of(env):
    assert run(env).ok
    started = next(k for n, _, k in env[2].calls if n == "worker_start")
    assert started["retry_of"] is None


def test_an_interrupted_retry_resumes_its_launch_with_retry_of(env, monkeypatch):
    dispatched(env)
    save = dr.compare_and_swap_record

    def interrupt(layout, value, **kwargs):
        if len(value.attempts) == 2 and "launch" in value.attempts[-1].steps:
            raise KeyboardInterrupt
        return save(layout, value, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(dr, "compare_and_swap_record", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(env, retry_of="ctx_1")
    start = len(env[2].calls)
    result = run(env)
    assert result.ok and result.status == "resumed", result.failure
    started = [k for n, _, k in env[2].calls[start:] if n == "worker_start"]
    assert [k["retry_of"] for k in started] == ["ctx_1"]


def test_retry_with_journaled_path_differing_from_computed_stops(env, monkeypatch, tmp_path):
    dispatched(env)
    elsewhere = str(tmp_path / "orca-named")

    def move(payload):
        payload["attempts"][-1]["placement"]["path"] = elsewhere

    rewrite(env, move)
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    before, start = record(env), len(env[2].calls)
    result = run(env, retry_of="ctx_1")
    failure(result, "place", "recovery-inspection", "task_1", None)
    assert elsewhere in result.failure.detail and str(env[4]) in result.failure.detail
    assert calls == []
    assert not _MUTATIONS & set(env[2].names()[start:])
    assert record(env) == before


def test_retry_with_journaled_branch_differing_from_computed_stops(env, monkeypatch):
    dispatched(env)
    rewrite(env, lambda p: p["attempts"][-1]["placement"].update(branch="orca/x"))
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    result = run(env, retry_of="ctx_1")
    failure(result, "place", "recovery-inspection", "task_1", None)
    assert "orca/x" in result.failure.detail and calls == []


def test_a_refused_verify_only_retry_journals_nothing(env, monkeypatch):
    dispatched(env)
    calls = fake_realize(monkeypatch, placing.Refused("foreign-checkout", "someone else's"))
    before = record(env)
    result = run(env, retry_of="ctx_1")
    failure(result, "place", "placement-refused", "task_1", None)
    assert result.failure.refusal == "foreign-checkout"
    assert [c["verify_only"] for c in calls] == [True]
    assert record(env) == before


def legacy(payload, *, path_in_placement):
    """Rewrite the latest attempt into exactly what the dispatcher before computed placement journaled."""
    attempt = payload["attempts"][-1]
    placed = attempt["placement"]
    argv = ["--worktree", "new-top-level", "--name", placed["branch"], "--base-branch", "main", "--repo", "id:repo1"]
    attempt["envelope"].update(placement_argv=argv, worktree_path=None)
    attempt["placement"] = {
        "action": "create-top-level",
        "placement_argv": argv,
        "repo_id": "repo1",
        "parent_worktree_id": None,
        "start_sha": placed["start_sha"],
        "worktree_id": "wt1",
        "marker": "gw-reader:0",
        "reused": False,
    }
    if path_in_placement:
        attempt["placement"]["path"] = placed["path"]
    attempt["steps"]["settle"]["result"] = {
        "action": "create-top-level",
        "path": placed["path"],
        "branch": placed["branch"],
        "display_name": placed["branch"],
        "repo_id": "repo1",
        "parent_worktree_id": None,
        "lineage_set": False,
        "notes": [],
        "start_sha": placed["start_sha"],
    }


@pytest.mark.parametrize("path_in_placement", [True, False])
def test_retry_of_reads_path_branch_start_sha_from_legacy_attempt(env, monkeypatch, path_in_placement):
    first = dispatched(env)
    rewrite(env, lambda p: legacy(p, path_in_placement=path_in_placement))
    calls = fake_realize(monkeypatch, placing.realize)
    result = run(env, retry_of="ctx_1")
    assert result.ok, result.failure
    assert [c["verify_only"] for c in calls] == [True]
    retried = record(env).attempts[-1]
    assert retried.placement["path"] == str(env[4])
    assert retried.placement["start_sha"] == first.placement.start_sha
    assert retried.envelope["placement_argv"] == ["--worktree", f"path:{env[4]}"]
    assert retried.envelope["worktree_path"] == str(env[4])
    assert (result.placement.path, result.placement.branch) == (str(env[4]), first.placement.branch)


def test_retry_of_a_legacy_attempt_on_another_branch_stops(env, monkeypatch):
    dispatched(env)

    def change(payload):
        legacy(payload, path_in_placement=True)
        payload["attempts"][-1]["steps"]["settle"]["result"]["branch"] = "orca/x"

    rewrite(env, change)
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    failure(run(env, retry_of="ctx_1"), "place", "recovery-inspection", "task_1", None)
    assert calls == []


def test_retry_of_a_legacy_attempt_without_any_path_stops(env, monkeypatch):
    dispatched(env)

    def change(payload):
        # A launched legacy creation that never settled: only its creation argv names where it went.
        legacy(payload, path_in_placement=False)
        steps = payload["attempts"][-1]["steps"]
        for name in ("settle", "record", "attend", "probe", "task-update"):
            del steps[name]

    rewrite(env, change)
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    result = run(env, retry_of="ctx_1")
    failure(result, "place", "recovery-inspection", "task_1", None)
    assert "no launch path" in result.failure.detail
    assert calls == []


@pytest.mark.parametrize("stale_block", [False, True])
def test_retry_of_a_null_placement_relaunches_in_the_same_root(env, monkeypatch, tmp_path, stale_block):
    write_item(env[0], "work/x", phase="design")
    planned_entry = env[1]["dispatches"][0]
    planned_entry.update(phase="design", placement=None)
    target, branch = str(env[0].root), None
    if stale_block:
        planned_entry["workspace"] = {"worktree": str(tmp_path / "content"), "branch": "ws/x"}
    env[2].worktrees["id:wt1"] = row(target, branch)
    dispatched(env)
    calls = fake_realize(monkeypatch, placing.Refused("occupied", "never reached"))
    result = run(env, retry_of="ctx_1")
    assert result.ok, result.failure
    assert calls == [] and result.dispatch_id == "ctx_2"
    assert record(env).attempts[-1].placement["path"] == target
