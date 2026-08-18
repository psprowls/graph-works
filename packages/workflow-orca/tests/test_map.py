"""Translation only. Nothing here touches a process."""

from __future__ import annotations

from orca_fakes import fixture_result
from subagents_io.backend import (
    EVENT_KINDS,
    WORKER_STATES,
    Escalation,
    Heartbeat,
    WorkerDone,
    WorkerQuestion,
)
from workflow_orca._map import (
    WORKER_STATE_BY_ORCA,
    event_from_message,
    parse_task_result,
    worker_state,
)


def test_every_mapped_state_is_a_protocol_state():
    assert set(WORKER_STATE_BY_ORCA.values()) <= WORKER_STATES


def test_ready_becomes_pending():
    # The one rename. Orca's `ready` means launched-not-yet-working, which is
    # the protocol's `pending`.
    assert worker_state("ready") == "pending"


def test_the_four_states_that_pass_through_unchanged():
    for name in ("running", "succeeded", "failed", "stopped"):
        assert worker_state(name) == name


def test_an_unrecognised_state_is_unknown_not_failed():
    # `unknown` exists precisely so a recoverable key stays recoverable.
    # Collapsing it to `failed` would make it not.
    assert worker_state("some-future-state") == "unknown"
    assert worker_state(None) == "unknown"


def test_parse_task_result_tolerates_every_empty_shape():
    assert parse_task_result(None) == {}
    assert parse_task_result("") == {}
    assert parse_task_result("not json") == {}


def test_parse_task_result_tolerates_valid_json_that_is_not_an_object():
    # Valid JSON, just not the dict this function always hands back — a
    # settled task's `result` must reconstruct even if Orca ever stored a
    # bare list or scalar there.
    assert parse_task_result("[1,2]") == {}


def test_event_from_message_tolerates_non_list_options():
    # Defensive: if a field that should be a list isn't, tolerate it.
    msg = {
        "id": "msg_x",
        "type": "question",
        "payload": {
            "taskId": "task_123",
            "dispatchId": "ctx_123",
            "question": "What?",
            "options": "not-a-list",  # Should be a list, but we tolerate it
        },
    }
    event = event_from_message(msg, {"task_123": "a#task"}, delivery_id=None)
    assert isinstance(event, WorkerQuestion)
    assert event.options == ()  # Treated as empty tuple


def test_parse_task_result_opens_the_embedded_json():
    tasks = fixture_result("task_list")["tasks"]
    settled = next(t for t in tasks if t["task_title"].endswith("#execute"))
    payload = parse_task_result(settled["result"])
    assert payload["outcome"] == "succeeded"
    assert payload["filesModified"] == ["scripts/wiki_smoke.py", "scripts/tests/test_wiki_smoke.py"]
    assert payload["reportPath"] is None


def _events():
    messages = fixture_result("check_batch")["messages"]
    keys = {"task_5ca8c19ffa5e": "a#execute", "task_338800a1fa14": "a#finish"}
    return [event_from_message(m, keys, delivery_id="dlv_0000000000a1") for m in messages]


def test_the_batch_produces_one_of_each_event_class():
    done, question, escalation, heartbeat = _events()
    assert isinstance(done, WorkerDone)
    assert isinstance(question, WorkerQuestion)
    assert isinstance(escalation, Escalation)
    assert isinstance(heartbeat, Heartbeat)


def test_the_worker_done_event_carries_the_payload_fields():
    done = _events()[0]
    assert done.key == "a#execute"
    assert done.handle == "ctx_817ed5bf5986"
    assert done.outcome == "succeeded"
    assert done.summary == "Added scripts/wiki_smoke.py and its tests."
    assert done.files_modified == ("scripts/wiki_smoke.py", "scripts/tests/test_wiki_smoke.py")
    assert done.report_path is None
    assert done.delivery_id == "dlv_0000000000a1"


def test_the_question_carries_its_own_message_id_as_the_reply_token():
    # `reply --id <msg_id>` is the actual CLI, so the token is the message's
    # own id and there is no second correlation scheme to get wrong.
    question = _events()[1]
    assert question.reply_token == "msg_1111111111aa"
    assert question.options == ("main", "epic/graph-works-core")


def test_an_unrecognised_message_type_is_dropped_not_raised():
    # A new Orca message type must not blind the coordinator to the rest of
    # the batch it arrived in.
    msg = {"id": "msg_x", "type": "some_future_type", "payload": {"taskId": "task_338800a1fa14"}}
    assert event_from_message(msg, {"task_338800a1fa14": "a#finish"}, delivery_id=None) is None


def test_the_emitted_kinds_are_exactly_the_protocol_vocabulary():
    kinds = {e.kind for e in _events()}
    assert kinds == EVENT_KINDS
