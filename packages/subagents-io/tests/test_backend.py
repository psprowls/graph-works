"""The seam's vocabularies are closed, and the union's discriminators agree with them."""

from __future__ import annotations

import dataclasses
from typing import Protocol, get_args, get_type_hints

import pytest
from subagents_io.backend import (
    EVENT_KINDS,
    WORKER_STATES,
    BackendError,
    DispatchBackend,
    DispatchSession,
    Escalation,
    Heartbeat,
    UnknownWorker,
    UnsupportedMode,
    WorkerDone,
    WorkerEvent,
    WorkerEventBase,
    WorkerQuestion,
    WorkerRecord,
    WorktreeNotProvisioned,
)

EVENT_TYPES = get_args(WorkerEvent)


def kind_literal(event_type: type) -> str:
    """The single string inside that member's `kind: Literal[...]` annotation."""
    return get_args(get_type_hints(event_type)["kind"])[0]


def test_worker_states_is_the_spec_s_six():
    # "unknown" is deliberate and is NOT a synonym for "failed": it is what a
    # backend reports when it holds a record but cannot corroborate the process
    # behind it. Collapsing the two makes a recoverable worker unrecoverable.
    assert {"pending", "running", "succeeded", "failed", "stopped", "unknown"} == WORKER_STATES


def test_the_union_has_four_members():
    # Guards the guard: an empty or single-member union would make the
    # exhaustiveness test below vacuous.
    assert len(EVENT_TYPES) == 4


def test_event_kinds_equals_the_union_s_kind_literals():
    # This is the exhaustiveness rule. Adding a member to WorkerEvent without
    # registering its kind in EVENT_KINDS fails HERE, before any coordinator
    # can drop the message on the floor.
    assert {kind_literal(t) for t in EVENT_TYPES} == EVENT_KINDS


@pytest.mark.parametrize("event_type", EVENT_TYPES, ids=lambda t: t.__name__)
def test_each_member_defaults_its_kind_to_its_own_literal(event_type):
    # The annotation and the runtime default are two places to write the same
    # string; this is what keeps them one fact.
    fields = {f.name: f for f in dataclasses.fields(event_type)}
    assert fields["kind"].default == kind_literal(event_type)


@pytest.mark.parametrize("event_type", EVENT_TYPES, ids=lambda t: t.__name__)
def test_every_member_carries_the_shared_base_fields(event_type):
    # key / handle / delivery_id are what a coordinator handles generically —
    # attribute, dedupe, ack. Inheritance is what stops them drifting apart
    # across four independently-edited classes.
    assert issubclass(event_type, WorkerEventBase)
    names = {f.name for f in dataclasses.fields(event_type)}
    assert {"key", "handle", "delivery_id"} <= names


@pytest.mark.parametrize("event_type", EVENT_TYPES, ids=lambda t: t.__name__)
def test_every_member_is_frozen(event_type):
    assert event_type.__dataclass_params__.frozen


def test_worker_done_carries_the_fields_a_coordinator_acts_on():
    ev = WorkerDone(
        key="s#plan",
        handle="h",
        delivery_id="h:0",
        outcome="succeeded",
        summary="did the thing",
        files_modified=("a.py", "b.py"),
        report_path=None,
    )
    assert ev.kind == "worker_done"
    assert ev.files_modified == ("a.py", "b.py")


def test_worker_question_carries_a_reply_token():
    ev = WorkerQuestion(
        key="s#plan", handle="h", delivery_id="h:2", question="which?", options=("a", "b"), reply_token="h:2"
    )
    assert ev.kind == "question"
    assert ev.reply_token == "h:2"


def test_escalation_and_heartbeat_shapes():
    esc = Escalation(key="s#plan", handle="h", delivery_id="h:1", subject="blocked", body="no worktree")
    beat = Heartbeat(key="s#plan", handle="h", delivery_id=None)
    assert (esc.kind, beat.kind) == ("escalation", "heartbeat")
    assert beat.phase is None


def test_worker_record_is_frozen_and_carries_five_fields():
    rec = WorkerRecord(key="s#plan", handle="h", state="running", last_heartbeat_at=None, detail=None)
    assert WorkerRecord.__dataclass_params__.frozen
    assert {f.name for f in dataclasses.fields(rec)} == {
        "key",
        "handle",
        "state",
        "last_heartbeat_at",
        "detail",
    }


@pytest.mark.parametrize("err", [UnsupportedMode, UnknownWorker, WorktreeNotProvisioned])
def test_every_backend_error_shares_one_catchable_base(err):
    # One base so a coordinator can catch the category; three subclasses
    # because each names a different caller mistake.
    assert issubclass(err, BackendError)
    assert issubclass(err, RuntimeError)


@pytest.mark.parametrize("proto", [DispatchBackend, DispatchSession])
def test_both_protocols_are_runtime_checkable(proto):
    assert issubclass(proto, Protocol)
    assert getattr(proto, "_is_runtime_protocol", False)


def test_the_session_protocol_names_the_eight_methods():
    # Enumerated rather than counted: a renamed method is the failure this
    # catches, and a count would not see it.
    expected = {"launch", "workers", "describe", "wait", "ack", "reply", "stop", "close"}
    assert expected <= set(DispatchSession.__protocol_attrs__)


def test_the_backend_protocol_is_the_session_factory():
    attrs = set(DispatchBackend.__protocol_attrs__)
    assert {"name", "supported_modes", "provisions_worktrees", "open_session"} <= attrs
