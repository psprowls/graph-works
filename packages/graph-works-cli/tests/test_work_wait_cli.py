"""`gw work wait`: projection, human line, refusal envelope."""

from __future__ import annotations

import json
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.work_cli import main
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate.wait import WaitClock, WaitFailed, run_wait
from graph_works_wire.work import wait_payload
from subagents_io.backend import BackendError
from typer.testing import CliRunner
from workflow_orca._cli import OrcaResult
from workflow_orca.port import OrcaCliPort

REPO = Path(__file__).resolve().parents[3]
FakeOrcaPort = runpy.run_path(str(REPO / "packages/graph-works-core/tests/orchestrate/fake_orca_port.py"))[
    "FakeOrcaPort"
]
runner = CliRunner()


class Refused(BackendError):
    code = "run_not_found"


def message(mid: str, kind: str) -> dict:
    return {
        "id": mid,
        "type": kind,
        "subject": kind,
        "body": "b",
        "from_": "term_1",
        "created_at": "t",
        "payload": {"dispatchId": "ctx_1"},
        "payload_raw": None,
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    layout = apply_init(plan_init(tmp_path / ".works", today=main._today(), topic="Wait")).layout
    port = FakeOrcaPort()
    monkeypatch.setattr(main, "orca_port", lambda: port)
    return layout, port


def invoke(layout, *args: str):
    return runner.invoke(app, ["work", "wait", "--run", "run_1", "--workspace", str(layout.root), *args])


def test_event_json_is_the_wire_projection(env):
    layout, port = env
    port.deliveries = [
        {"delivery_id": "dlv_hb", "messages": [message("h", "heartbeat")]},
        {"delivery_id": "dlv_1", "messages": [message("e", "escalation")]},
    ]
    result = invoke(layout, "--ack", "dlv_prev", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "event" and payload["delivery_id"] == "dlv_1"
    assert [m["id"] for m in payload["messages"]] == ["e"]
    assert payload["self_acked"] == 1
    assert payload["pending_questions"] is None and payload["liveness"] is None
    acks = [kwargs["ack"] for name, _a, kwargs in port.calls if name == "check_wait"]
    assert acks[0] == "dlv_prev"


def test_timeout_human_line(env):
    layout, _port = env
    result = invoke(layout, "--timeout-s", "1")
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("timeout after ")


def test_event_human_line(env):
    layout, port = env
    port.deliveries = [{"delivery_id": "dlv_1", "messages": [message("e", "escalation"), message("q", "question")]}]
    result = invoke(layout)
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "event: 2 message(s) in dlv_1"


def test_rebound_is_named_in_the_human_line(env):
    layout, port = env

    class Fenced(BackendError):
        code = "consumer_fenced"

    port.wait_errors = [Fenced("f")]
    port.deliveries = [{"delivery_id": "dlv_1", "messages": [message("e", "escalation")]}]
    result = invoke(layout)
    assert result.stdout.strip() == "event: 1 message(s) in dlv_1; rebound"


def test_sleep_gap_is_named_in_the_human_line(env, monkeypatch: pytest.MonkeyPatch):
    layout, port = env
    port.deliveries = [{"delivery_id": "dlv_1", "messages": [message("e", "escalation")]}]
    start = datetime(2026, 1, 1, tzinfo=UTC)
    walls = iter((start, start + timedelta(seconds=3000)))
    monkeypatch.setattr(main, "_wait_clock", lambda: WaitClock(wall=lambda: next(walls), monotonic=lambda: 0.0))
    result = invoke(layout)
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().endswith("; sleep gap 3000s")


def test_orca_failure_is_a_refusal_envelope(env):
    layout, port = env
    port.wait_errors = [Refused("no such run")]
    result = invoke(layout, "--json")
    assert result.exit_code == exit_codes.GENERIC
    error = json.loads(result.stdout)["error"]
    assert error["reason"] == "refused"
    assert error["payload"] == {"run_id": "run_1", "code": "run_not_found"}
    assert "check_ack" not in port.names()


def test_timeout_below_one_is_usage_error(env):
    layout, port = env
    result = invoke(layout, "--timeout-s", "0")
    assert result.exit_code != 0
    assert "--timeout-s" in result.output and "1" in result.output
    assert "check_wait" not in port.names()


@pytest.mark.parametrize(
    "messages",
    [
        ["unrecognized message"],
        [{"id": "h", "type": "heartbeat"}, "unrecognized message"],
        [{"id": "q", "type": "question"}, "unrecognized message"],
    ],
    ids=["standalone", "heartbeat-mixed", "event-mixed"],
)
def test_real_adapter_refuses_malformed_delivery_without_ack(messages):
    calls = []
    tick = 0.0

    def monotonic():
        nonlocal tick
        tick += 0.25
        return tick

    def raw_check(argv):
        calls.append(tuple(argv))
        result = {"deliveryId": "bad_delivery", "messages": messages}
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")

    clock = WaitClock(wall=lambda: datetime(2026, 9, 27, tzinfo=UTC), monotonic=monotonic)
    with pytest.raises(WaitFailed, match=r"malformed.*messages"):
        run_wait(OrcaCliPort(run=raw_check), "run_1", ack=None, timeout_s=1, clock=clock)
    assert len(calls) == 1
    assert "--ack" not in calls[0]


@pytest.mark.parametrize(
    ("current", "historical", "expected"),
    [
        ("term_b47aac18-d4b5-4639-8f8e-5dd215e5c898", None, "term_b47aac18-d4b5-4639-8f8e-5dd215e5c898"),
        ("term_b47aac18-d4b5-4639-8f8e-5dd215e5c898", "term_old", "term_b47aac18-d4b5-4639-8f8e-5dd215e5c898"),
        (None, "term_old", "term_old"),
        ("", "term_old", "term_old"),
        ("  ", "term_old", "term_old"),
    ],
)
def test_captured_shape_sender_survives_adapter_core_and_wire(current, historical, expected):
    def raw_check(argv):
        result = {
            "deliveryId": "dlv_1",
            "messages": [
                {
                    "id": "msg_cfabb7bc9da9",
                    "type": "question",
                    "from_handle": current,
                    "from": historical,
                    "payload": '{"taskId":"task_8f4dd3955d92"}',
                }
            ],
        }
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")

    clock = WaitClock(wall=lambda: datetime(2026, 9, 27, tzinfo=UTC), monotonic=lambda: 0.0)
    result = run_wait(OrcaCliPort(run=raw_check), "run_1", ack=None, timeout_s=1, clock=clock)
    assert wait_payload(result)["messages"][0]["from"] == expected
