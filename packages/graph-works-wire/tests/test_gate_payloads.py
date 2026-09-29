"""The `gw work gate` projections and `advance`'s `gate_receipt` slot."""

from __future__ import annotations

from graph_works_wire import work
from samples_work import (
    GATE_CHECK_SATISFIED,
    GATE_CHECK_UNSATISFIED,
    GATE_RUN_REFUSED,
    GATE_RUN_STARTED,
    GATE_WAIT_FINISHED,
    GATE_WAIT_REFUSED,
    advance,
)

RID = "20260928T120000Z-0a1b2c3d"


def test_gate_run_payload() -> None:
    assert work.gate_run_payload(GATE_RUN_STARTED, "work/a") == {
        "path": "work/a",
        "status": "started",
        "run_id": RID,
        "command": "just check",
        "names": ["a"],
        "log_path": "/l",
        "match": None,
        "warnings": ["w"],
        "refusal": None,
    }
    refused = work.gate_run_payload(GATE_RUN_REFUSED, "work/a")
    assert refused["status"] is None and refused["refusal"] == {"reason": "dirty-tree", "detail": "d"}


def test_gate_wait_payload() -> None:
    assert work.gate_wait_payload(GATE_WAIT_FINISHED, "work/a") == {
        "path": "work/a",
        "status": "finished",
        "run_id": RID,
        "exit": 0,
        "recorded": True,
        "log_path": "/l",
        "log_tail": "tail",
        "receipt_path": "/r.md",
        "refusal": None,
    }
    assert work.gate_wait_payload(GATE_WAIT_REFUSED, "work/a")["refusal"] == {"reason": "no-run", "detail": "d"}


def test_gate_check_payload() -> None:
    assert work.gate_check_payload(GATE_CHECK_SATISFIED, "work/a") == {
        "path": "work/a",
        "status": "satisfied",
        "reason": None,
        "tree": "b" * 40,
        "match": {"owner": "work/other", "run_id": RID},
        "warnings": [],
        "refusal": None,
    }
    unsatisfied = work.gate_check_payload(GATE_CHECK_UNSATISFIED, "work/a")
    assert unsatisfied["reason"] == "no receipt" and unsatisfied["match"] is None and unsatisfied["refusal"] is None


def test_advance_payload_projects_the_gate_receipt() -> None:
    assert work.advance_payload(advance(applied=True), "work/a")["gate_receipt"] is None
    assert work.advance_payload(advance(applied=True, bypass=True), "work/a")["gate_receipt"] == {
        "owner": "work/other",
        "run_id": RID,
    }
