"""Synthetic launch receipts; shape verified against Orca 1.4.200 source.

These tests do not claim live worker or provider validation.
"""

import json
import runpy
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

import pytest
from fake_orca_cli import FakeOrcaCLI
from subagents_io.backend import BackendError
from subagents_io.dispatch import PlannedDispatch, WorktreeAction
from workflow_orca import OrcaBackend
from workflow_orca._cli import OrcaCliError, OrcaResult, unwrap
from workflow_orca._launch import (
    LAUNCH_SPEC_PREFIX,
    check_launch_receipt,
    decode_launch_spec,
    encode_launch_spec,
    launch_preferences,
)


@pytest.fixture
def dispatch():
    return PlannedDispatch(
        key="gw-execute-example",
        slug="work/feature-example",
        phase="execute",
        kind="Feature",
        effort="large",
        skill="superpowers:subagent-driven-development",
        mode="autonomous",
        agent="claude",
        model=None,
        reasoning_effort=None,
        worktree=WorktreeAction("reuse", "/tmp/example", "feature/example", None, True),
        merge_target="main",
        prompt="Perform the test task.",
    )


def test_effort_only_never_silently_drops_setting(dispatch):
    with pytest.raises(BackendError, match="Set a model or clear reasoning_effort"):
        launch_preferences(replace(dispatch, reasoning_effort="high"))


def test_codex_model_is_one_opaque_argument(dispatch):
    selected = replace(dispatch, agent="codex", model="provider model/id", reasoning_effort="high")
    assert launch_preferences(selected) == ["--agent", "codex", "--model", "provider model/id", "--effort", "high"]


def test_null_preferences_are_omitted(dispatch):
    assert launch_preferences(dispatch) == ["--agent", "claude"]


def test_envelope_freezes_request_and_exact_placement_before_prompt(dispatch):
    argv = ["--worktree", "new-top-level", "--repo", "path:/repo with spaces"]
    spec = encode_launch_spec(dispatch, placement_argv=argv)
    assert spec.startswith(LAUNCH_SPEC_PREFIX)
    assert spec.split("\n", 1)[1] == dispatch.prompt
    assert decode_launch_spec(spec) == {
        "version": 1,
        "dispatch_key": dispatch.key,
        "agent": "claude",
        "model": None,
        "reasoning_effort": None,
        "placement_argv": argv,
    }


@pytest.mark.parametrize(
    "spec",
    [
        "prompt",
        LAUNCH_SPEC_PREFIX + "{broken\nprompt",
        LAUNCH_SPEC_PREFIX + "[]\nprompt",
        LAUNCH_SPEC_PREFIX + '{"version":2}\nprompt',
    ],
)
def test_malformed_envelope_refuses_reconstruction(spec):
    with pytest.raises(BackendError, match="launch envelope"):
        decode_launch_spec(spec)


def receipt(agent="claude", model=None, effort=None):
    """Synthetic, mirroring runtime AM/jyn receipt constructors."""
    fields = {"agent": agent, "model": model, "effort": effort}
    return {"requested": dict(fields), "effective": dict(fields)}


def test_receipt_checks_only_explicit_preferences(dispatch):
    request = decode_launch_spec(encode_launch_spec(dispatch, placement_argv=[]))
    assert check_launch_receipt(request, receipt(model="configured default")) is None
    request.update(model="opaque id", reasoning_effort="max")
    assert check_launch_receipt(request, receipt(model="opaque id", effort="max")) is None


@pytest.mark.parametrize(
    "part,field", [(p, f) for p in ("requested", "effective") for f in ("agent", "model", "effort")]
)
def test_receipt_mismatch_identifies_exact_field(dispatch, part, field):
    request = {"agent": "claude", "model": "opaque", "reasoning_effort": "high"}
    proof = receipt(model="opaque", effort="high")
    proof[part][field] = "wrong"
    reason = check_launch_receipt(request, proof)
    assert f"launch.{part}.{field}" in reason
    assert "unverified" in reason


@pytest.mark.parametrize("proof", [{}, {"requested": {}, "effective": None}])
def test_missing_receipt_is_unverified(proof):
    assert "unverified" in check_launch_receipt({"agent": "claude"}, proof)


@pytest.mark.parametrize("ok", [True, False])
def test_nonzero_json_retains_entire_receipt(ok):
    payload = {
        "dispatchId": "ctx_allocated",
        "failedStage": "prompt",
        "residualResources": [{"id": "resource"}],
        "recovery": "inspect",
    }
    body = {"ok": ok, "result": payload, "error": {"code": "start_failed", "details": payload}}
    with pytest.raises(OrcaCliError) as caught:
        unwrap(["orca", "orchestration", "worker-start"], OrcaResult(1, json.dumps(body), "stderr"))
    assert caught.value.receipt == body
    assert caught.value.details["dispatchId"] == "ctx_allocated"
    assert caught.value.details["residualResources"] == payload["residualResources"]


def test_effort_only_refused_before_task_create(dispatch):
    cli = FakeOrcaCLI()
    session = OrcaBackend(run=cli).open_session("test")
    with pytest.raises(BackendError, match="Set a model"):
        session.launch(replace(dispatch, reasoning_effort="high"))
    assert not any("task-create" in c for c in cli.calls)


def test_alternating_agents_uses_each_frozen_dispatch(dispatch):
    cli = FakeOrcaCLI()
    session = OrcaBackend(run=cli).open_session("test")
    for index, agent in enumerate(("claude", "codex", "claude")):
        selected = replace(
            dispatch, key=f"key-{index}", agent=agent, model="opaque provider/id", reasoning_effort="high"
        )
        record = session.launch(selected)
        assert "unverified" not in (record.detail or "")
        start = [c for c in cli.calls if "worker-start" in c][-1]
        assert start[start.index("--agent") + 1] == agent
        assert start[start.index("--model") + 1] == "opaque provider/id"
        stored = next(t for t in cli._tasks.values() if t["task_title"] == selected.key)
        frozen = decode_launch_spec(stored["spec"])
        assert frozen["agent"] == agent
        assert frozen["placement_argv"] == ["--worktree", "path:/tmp/example"]


class SyntheticStartCLI(FakeOrcaCLI):
    def __init__(self, *, proof="matching", failed=False, missing_id=False):
        super().__init__()
        self.proof, self.failed, self.missing_id = proof, failed, missing_id

    def _cmd_worker_start(self, argv):
        original = super()._cmd_worker_start(argv)
        body = json.loads(original.stdout)
        payload = body["result"]
        worker = self._workers[payload["dispatchId"]]
        worker["terminal"] = None  # An orchestration worker without a terminal.
        payload.pop("agentTerminalHandle")
        if self.proof == "missing":
            payload.pop("launch")
            worker["launch"] = None
        elif self.proof == "mismatch":
            payload["launch"]["effective"]["agent"] = "wrong"
            worker["launch"] = payload["launch"]
        if self.failed:
            payload.update(
                failedStage="prompt", residualResources=[{"id": "allocation"}], recovery="inspect worker-show"
            )
        if self.missing_id:
            payload.pop("dispatchId")
        return OrcaResult(1 if self.failed else 0, json.dumps(body), "")


@pytest.mark.parametrize("proof", ["missing", "mismatch"])
def test_ambiguous_receipt_keeps_ids_and_prevents_duplicate_across_restart(dispatch, proof):
    cli = SyntheticStartCLI(proof=proof)
    session = OrcaBackend(run=cli).open_session("test")
    record = session.launch(dispatch)
    task_id = next(iter(cli._tasks))
    assert record.handle and record.handle in record.detail
    assert task_id in record.detail and "unverified" in record.detail
    reopened = OrcaBackend(run=cli).open_session("test")
    restored = reopened.describe(dispatch.key)
    assert restored.handle == record.handle
    assert task_id in restored.detail and "unverified" in restored.detail
    with pytest.raises(BackendError, match="already"):
        reopened.launch(replace(dispatch, agent="codex"))
    assert len([c for c in cli.calls if "worker-start" in c]) == 1
    assert len(cli._tasks) == 1


@pytest.mark.parametrize("missing_id", [False, True])
def test_nonzero_start_retains_recovery_and_duplicate_refusal(dispatch, missing_id):
    cli = SyntheticStartCLI(failed=True, missing_id=missing_id)
    session = OrcaBackend(run=cli).open_session("test")
    with pytest.raises(OrcaCliError) as caught:
        session.launch(dispatch)
    error = caught.value
    assert error.details["taskId"] == next(iter(cli._tasks))
    assert error.details["failedStage"] == "prompt"
    assert error.details["residualResources"] == [{"id": "allocation"}]
    assert error.details["launchRequest"]["agent"] == "claude"
    if not missing_id:
        assert error.details["dispatchId"] == next(iter(cli._workers))
    reopened = OrcaBackend(run=cli).open_session("test")
    assert reopened.describe(dispatch.key).handle == next(iter(cli._workers))
    with pytest.raises(BackendError, match="already"):
        reopened.launch(dispatch)
    assert len([c for c in cli.calls if "worker-start" in c]) == 1


def test_success_receipt_without_dispatch_id_keeps_task_recovery(dispatch):
    cli = SyntheticStartCLI(missing_id=True)
    session = OrcaBackend(run=cli).open_session("test")
    with pytest.raises(OrcaCliError) as caught:
        session.launch(dispatch)
    assert caught.value.details["taskId"] == next(iter(cli._tasks))
    assert "recovery inspection" in str(caught.value)
    with pytest.raises(BackendError, match="already"):
        OrcaBackend(run=cli).open_session("test").launch(dispatch)


def test_restart_reconstructs_durable_request_and_flags_corrupt_envelope(dispatch):
    cli = SyntheticStartCLI()
    session = OrcaBackend(run=cli).open_session("test")
    record = session.launch(dispatch)
    reopened = OrcaBackend(run=cli).open_session("test")
    assert "unverified" not in (reopened.describe(dispatch.key).detail or "")
    task = next(iter(cli._tasks.values()))
    task["spec"] = LAUNCH_SPEC_PREFIX + "{broken\nprompt"
    restored = reopened.describe(dispatch.key)
    assert restored.handle == record.handle
    assert "launch envelope" in restored.detail
    assert "unverified" in restored.detail
    assert not any("--brief" in c for c in cli.calls)


def test_terminal_free_questions_heartbeat_and_completion(dispatch):
    cli = SyntheticStartCLI()
    selected = replace(dispatch, prompt="heartbeat:planning question:ready? done:succeeded")
    session = OrcaBackend(run=cli).open_session("test")
    session.launch(selected)
    heartbeat = session.wait(timeout_s=0)[0]
    assert heartbeat.kind == "heartbeat"
    session.ack(heartbeat)
    question = session.wait(timeout_s=0)[0]
    assert question.kind == "question"
    session.reply(question.reply_token, "yes")
    session.ack(question)
    done = session.wait(timeout_s=0)[0]
    assert done.kind == "worker_done"
    session.ack(done)
    assert OrcaBackend(run=cli).open_session("test").describe(dispatch.key).state == "succeeded"
    assert not any("terminal" in c for c in cli.calls)


def test_unsupported_preferences_capability_refused_before_task_create(dispatch):
    class NoCapability(FakeOrcaCLI):
        def __call__(self, argv):
            if argv[1] == "status":
                return self._ok({"runtime": {"capabilities": []}})
            return super().__call__(argv)

    cli = NoCapability()
    with pytest.raises(BackendError, match="launch preferences"):
        OrcaBackend(run=cli).open_session("test").launch(replace(dispatch, model="opaque"))
    assert not cli._tasks


@pytest.mark.parametrize("proof", ["missing", "mismatch"])
def test_successful_done_without_proof_keeps_delivery_for_recovery(dispatch, proof):
    cli = SyntheticStartCLI(proof=proof)
    selected = replace(dispatch, prompt="done:succeeded")
    session = OrcaBackend(run=cli).open_session("test")
    record = session.launch(selected)
    done = session.wait(timeout_s=0)[0]
    before = len(cli.calls)
    with pytest.raises(BackendError, match="unverified"):
        session.ack(done)
    assert done.handle == record.handle
    assert not any("--ack" in c or "task-update" in c or "worker-release" in c for c in cli.calls[before:])
    assert session.wait(timeout_s=0)[0].handle == done.handle


def test_done_ack_respects_runtime_owned_settlement(dispatch):
    cli = SyntheticStartCLI()
    session = OrcaBackend(run=cli).open_session("test")
    session.launch(replace(dispatch, prompt="done:succeeded"))
    session.ack(session.wait(timeout_s=0)[0])
    assert not any("task-update" in c for c in cli.calls)


def test_current_runtime_camel_case_heartbeat_survives_terminal_free_read(dispatch):
    class CurrentContractCLI(SyntheticStartCLI):
        def _cmd_worker_show(self, argv):
            raw = super()._cmd_worker_show(argv)
            body = json.loads(raw.stdout)
            shown = body["result"]["dispatch"]
            shown["lastHeartbeatAt"] = shown.pop("last_heartbeat_at")
            return OrcaResult(0, json.dumps(body), "")

    cli = CurrentContractCLI()
    session = OrcaBackend(run=cli).open_session("test")
    session.launch(dispatch)
    assert session.describe(dispatch.key).last_heartbeat_at == "2026-08-14T00:00:00Z"


def test_current_runtime_worktree_id_readback(dispatch):
    class CurrentContractCLI(SyntheticStartCLI):
        def _cmd_worker_show(self, argv):
            raw = super()._cmd_worker_show(argv)
            body = json.loads(raw.stdout)
            body["result"]["worker"]["worktreeId"] = "repo::/actual/path"
            return OrcaResult(0, json.dumps(body), "")

        def __call__(self, argv):
            if list(argv[1:3]) == ["worktree", "show"]:
                self.calls.append(tuple(argv))
                return self._ok({"worktree": {"path": "/actual/path", "branch": "refs/heads/actual"}})
            return super().__call__(argv)

    cli = CurrentContractCLI()
    session = OrcaBackend(run=cli).open_session("test")
    action = WorktreeAction("fork-child", None, "planned", "main", None)
    record = session.launch(replace(dispatch, worktree=action))
    assert record.worktree_path == "/actual/path"
    assert record.worktree_branch == "actual"


def test_another_event_cannot_ack_a_batch_with_unverified_success(dispatch):
    from subagents_io.backend import Heartbeat

    cli = SyntheticStartCLI(proof="missing")
    session = OrcaBackend(run=cli).open_session("test")
    session.launch(replace(dispatch, prompt="done:succeeded"))
    done = session.wait(timeout_s=0)[0]
    sibling = Heartbeat(key=done.key, handle=done.handle, delivery_id=done.delivery_id, phase="last")
    with pytest.raises(BackendError, match="unverified"):
        session.ack(sibling)
    assert not any("--ack" in c for c in cli.calls)


@pytest.mark.parametrize(
    "updates",
    [
        {"extra": 1},
        {"agent": None},
        {"model": 123},
        {"model": None, "reasoning_effort": "high"},
        {"placement_argv": [123]},
    ],
)
def test_invalid_frozen_fields_are_not_reinterpreted(dispatch, updates):
    frozen = decode_launch_spec(encode_launch_spec(dispatch, placement_argv=[]))
    frozen.update(updates)
    with pytest.raises(BackendError, match="launch envelope"):
        decode_launch_spec(LAUNCH_SPEC_PREFIX + json.dumps(frozen) + "\nprompt")


@pytest.mark.parametrize("corruption", ["missing", "truncated", "wrong-key"])
def test_restart_requires_full_spec_and_matching_dispatch_key(dispatch, corruption):
    cli = SyntheticStartCLI()
    session = OrcaBackend(run=cli).open_session("test")
    session.launch(dispatch)
    task = next(iter(cli._tasks.values()))
    if corruption == "missing":
        task["spec"] = None
    elif corruption == "truncated":
        task["spec"] = LAUNCH_SPEC_PREFIX + '{"version":1'
    else:
        task["spec"] = encode_launch_spec(replace(dispatch, key="another-key"), placement_argv=[])
    assert "unverified" in OrcaBackend(run=cli).open_session("test").describe(dispatch.key).detail


@pytest.mark.parametrize("verified", [False, True])
def test_close_release_uses_verification_result_not_diagnostic_words(dispatch, monkeypatch, verified):
    cli = SyntheticStartCLI(proof="matching" if verified else "missing")
    session = OrcaBackend(run=cli).open_session("test")
    session.launch(replace(dispatch, prompt="done:succeeded"))
    session.wait(timeout_s=0)
    # Diagnostics are presentation: a wording change cannot grant release,
    # and a vendor status containing this word cannot revoke proven release.
    if verified:
        original_list = cli._cmd_worker_list

        def reworded_list(argv):
            body = json.loads(original_list(argv).stdout)
            body["result"]["workers"][0]["dispatchStatus"] = "previously unverified, now complete"
            return OrcaResult(0, json.dumps(body), "")

        monkeypatch.setattr(cli, "_cmd_worker_list", reworded_list)
    else:
        real_verification = session._launch_verification

        def reworded(task, shown):
            reason = real_verification(task, shown)
            assert reason is not None
            return "Launch receipt missing; inspect recovery."

        monkeypatch.setattr(session, "_launch_verification", reworded)
    before = len(cli.calls)
    session.close()
    releases = [c for c in cli.calls[before:] if "worker-release" in c]
    assert bool(releases) is verified


class EmbeddedMessageCLI(SyntheticStartCLI):
    """Current 1.4.200 check payloads captured during Task 7 live smoke."""

    def _cmd_check(self, argv):
        raw = super()._cmd_check(argv)
        body = json.loads(raw.stdout)
        for message in body["result"].get("messages", []):
            message["payload"] = json.dumps(message["payload"])
        return OrcaResult(raw.returncode, json.dumps(body), raw.stderr)


def test_embedded_json_messages_survive_restart_question_reply_and_completion(dispatch):
    cli = EmbeddedMessageCLI()
    session = OrcaBackend(run=cli).open_session("test")
    record = session.launch(replace(dispatch, prompt="question:smoke-confirm done:succeeded"))
    reopened = OrcaBackend(run=cli).open_session("test")
    question = reopened.wait(timeout_s=0)[0]
    assert question.key == dispatch.key and question.handle == record.handle
    assert question.question == "smoke-confirm"
    reopened.reply(question.reply_token, "yes")
    reopened.ack(question)
    done = reopened.wait(timeout_s=0)[0]
    assert done.key == dispatch.key and done.outcome == "succeeded"
    reopened.ack(done)
    assert any("worker-release" in call for call in cli.calls)


def test_embedded_success_proof_still_gates_entire_ack_batch(dispatch):
    from subagents_io.backend import Heartbeat

    cli = EmbeddedMessageCLI(proof="missing")
    session = OrcaBackend(run=cli).open_session("test")
    session.launch(replace(dispatch, prompt="done:succeeded"))
    done = session.wait(timeout_s=0)[0]
    sibling = Heartbeat(key=done.key, handle=done.handle, delivery_id=done.delivery_id)
    with pytest.raises(BackendError, match="unverified"):
        session.ack(sibling)
    assert not any("--ack" in call or "worker-release" in call for call in cli.calls)


@pytest.mark.parametrize("payload", ["{broken", "[]", "null", "42", ["invalid"]])
def test_malformed_embedded_payload_keeps_delivery_for_recovery(dispatch, payload):
    class MalformedMessageCLI(EmbeddedMessageCLI):
        def _cmd_check(self, argv):
            raw = super()._cmd_check(argv)
            body = json.loads(raw.stdout)
            for message in body["result"].get("messages", []):
                message["payload"] = payload
            return OrcaResult(raw.returncode, json.dumps(body), raw.stderr)

    cli = MalformedMessageCLI()
    session = OrcaBackend(run=cli).open_session("test")
    session.launch(replace(dispatch, prompt="done:succeeded"))
    with pytest.raises(BackendError, match=r"message payload.*recovery"):
        session.wait(timeout_s=0)
    assert not any("--ack" in call or "worker-release" in call for call in cli.calls)


@pytest.fixture
def plugin_recipe():
    root = Path(__file__).resolve().parents[3]
    return runpy.run_path(str(root / "plugins/gw/skills/auto-drive/references/launch-worker.py"))


@pytest.mark.parametrize(
    "agent,model,effort",
    [
        ("claude", None, None),
        ("codex", "provider model/é", "opaque-effort"),
    ],
)
def test_plugin_and_backend_share_exact_envelope(dispatch, plugin_recipe, tmp_path, capsys, agent, model, effort):
    selected = replace(dispatch, agent=agent, model=model, reasoning_effort=effort, prompt="Prompt é\r\n\r\n")
    placement = ["--worktree", "new-top-level", "--repo", "path:/repo with spaces", "--name", "feature/x"]
    dispatch_path, placement_path = tmp_path / "dispatch.json", tmp_path / "placement.json"
    dispatch_path.write_text(
        json.dumps(
            {
                "key": selected.key,
                "agent": agent,
                "model": model,
                "reasoning_effort": effort,
                "prompt": selected.prompt,
            }
        ),
        encoding="utf-8",
    )
    placement_path.write_text(json.dumps(placement), encoding="utf-8")
    plugin_recipe["encode"](Namespace(dispatch=str(dispatch_path), placement=str(placement_path)))
    plugin_spec = capsys.readouterr().out
    backend_spec = encode_launch_spec(selected, placement_argv=placement)
    assert plugin_spec == backend_spec
    request = decode_launch_spec(plugin_spec)
    assert request["placement_argv"] == placement
    spec_path = tmp_path / "task.txt"
    spec_path.write_bytes(backend_spec.encode())
    plugin_request, prompt = plugin_recipe["decode_spec"](str(spec_path))
    assert plugin_request == request and prompt == selected.prompt


@pytest.mark.parametrize(
    "side,field", [(s, f) for s in ("requested", "effective") for f in ("agent", "model", "effort")]
)
@pytest.mark.parametrize("explicit", [False, True])
def test_plugin_and_backend_agree_on_effective_receipt_semantics(plugin_recipe, side, field, explicit):
    request = {
        "agent": "codex",
        "model": "opaque" if explicit else None,
        "reasoning_effort": "custom" if explicit else None,
    }
    proof = receipt(agent="codex", model="opaque", effort="custom")
    proof[side][field] = "different"
    reason = check_launch_receipt(request, proof)
    if field == "agent" or explicit:
        assert reason is not None
        with pytest.raises(SystemExit):
            plugin_recipe["check_receipt"](request, proof)
    else:
        assert reason is None
        plugin_recipe["check_receipt"](request, proof)


@pytest.mark.parametrize(
    "updates",
    [
        {"version": True},
        {"extra": "field"},
        {"dispatch_key": ""},
        {"model": None, "reasoning_effort": "high"},
        {"placement_argv": [3]},
    ],
)
def test_plugin_and_backend_reject_same_corrupt_envelopes(dispatch, plugin_recipe, tmp_path, updates):
    request = decode_launch_spec(encode_launch_spec(dispatch, placement_argv=[]))
    request.update(updates)
    spec = LAUNCH_SPEC_PREFIX + json.dumps(request) + "\nprompt"
    path = tmp_path / "task.txt"
    path.write_text(spec, encoding="utf-8")
    with pytest.raises(BackendError):
        decode_launch_spec(spec)
    with pytest.raises(SystemExit):
        plugin_recipe["decode_spec"](str(path))
