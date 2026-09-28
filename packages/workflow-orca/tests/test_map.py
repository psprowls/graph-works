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
    INBOX_LIMIT,
    WORKER_STATE_BY_ORCA,
    RunQuestion,
    event_from_message,
    inbox_truncated,
    join_pending_questions,
    parse_task_result,
    question_labels,
    reply_threads,
    run_questions,
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


RUN = "run_q00000000001"


def _q(message_id, *, dispatch_id="ctx_live", warning=None):
    return RunQuestion(
        message_id=message_id,
        dispatch_id=dispatch_id,
        task_id=None,
        question="?",
        options=(),
        ask_resource=None,
        asked_at="2026-09-27T15:00:00Z",
        warning=warning,
    )


def _fixture_join():
    questions = run_questions(RUN, fixture_result("inbox_run_questions"))
    replied = reply_threads(RUN, fixture_result("inbox_dispatch_replies"))
    states = {row["dispatchId"]: row["workerState"] for row in fixture_result("worker_list_questions")["workers"]}
    return join_pending_questions(questions, worker_states=states, replied=replied, truncated=False)


def test_run_questions_keeps_only_questions_oldest_first():
    questions = run_questions(RUN, fixture_result("inbox_run_questions"))
    assert [q.message_id for q in questions] == [
        "msg_0000000000a1",
        "msg_0000000000d4",
        "msg_0000000000b2",
        "msg_0000000000c3",
    ]


def test_run_questions_ignores_questions_addressed_elsewhere():
    inbox = {"messages": [{"id": "m1", "type": "question", "to_handle": "run:other", "payload": "{}"}], "count": 1}
    assert run_questions(RUN, inbox) == []


def test_run_questions_reads_dispatch_task_options_and_ask_resource():
    by_id = {q.message_id: q for q in run_questions(RUN, fixture_result("inbox_run_questions"))}
    typed = by_id["msg_0000000000c3"]
    assert typed.dispatch_id == "ctx_aaaaaaaaaaaa"
    assert typed.task_id == "task_aaaaaaaaaaaa"
    assert typed.options == ("approve", "changes")
    assert typed.ask_resource == "/work/feature-x/references/asks/spec-review-1.json"
    assert typed.asked_at == "2026-09-27T16:00:00Z"
    assert typed.warning is None
    assert by_id["msg_0000000000b2"].ask_resource is None


def test_no_reply_and_a_live_dispatch_is_pending():
    result = _fixture_join()
    assert [q["message_id"] for q in result["questions"]] == ["msg_0000000000b2", "msg_0000000000c3"]


def test_a_matching_reply_thread_closes_the_question():
    result = _fixture_join()
    assert "msg_0000000000a1" not in [q["message_id"] for q in result["questions"]]


def test_an_ended_dispatch_closes_the_question_with_a_warning():
    result = _fixture_join()
    assert "msg_0000000000d4" not in [q["message_id"] for q in result["questions"]]
    assert any("msg_0000000000d4" in w and "succeeded" in w for w in result["warnings"])


def test_a_dispatch_missing_from_worker_list_closes_the_question_with_a_warning():
    result = join_pending_questions(
        [_q("msg_x1", dispatch_id="ctx_gone")], worker_states={}, replied=set(), truncated=False
    )
    assert result["questions"] == []
    assert any("ctx_gone" in w and "worker-list" in w for w in result["warnings"])


def test_unknown_or_missing_state_keeps_the_question():
    result = join_pending_questions(
        [_q("msg_x1", dispatch_id="ctx_a"), _q("msg_x2", dispatch_id="ctx_b")],
        worker_states={"ctx_a": None, "ctx_b": "some-future-state"},
        replied=set(),
        truncated=False,
    )
    assert [q["message_id"] for q in result["questions"]] == ["msg_x1", "msg_x2"]


def test_a_null_or_foreign_thread_id_does_not_close_the_question():
    inbox = {
        "messages": [
            {"id": "r1", "from_handle": f"run:{RUN}", "thread_id": None},
            {"id": "r2", "from_handle": f"run:{RUN}", "thread_id": "msg_other"},
        ],
        "count": 2,
    }
    replied = reply_threads(RUN, inbox)
    result = join_pending_questions(
        [_q("msg_x1")], worker_states={"ctx_live": "running"}, replied=replied, truncated=False
    )
    assert [q["message_id"] for q in result["questions"]] == ["msg_x1"]


def test_a_thread_match_from_another_handle_is_not_a_reply():
    inbox = {"messages": [{"id": "r1", "from_handle": "dispatch:ctx_other", "thread_id": "msg_x1"}], "count": 1}
    assert reply_threads(RUN, inbox) == set()


def test_labels_are_the_last_four_characters():
    assert question_labels(["msg_0000000000b2", "msg_00000000e862"]) == {
        "msg_0000000000b2": "q-00b2",
        "msg_00000000e862": "q-e862",
    }


def test_colliding_labels_extend_until_unique():
    labels = question_labels(["msg_000000a1e862", "msg_000000b1e862", "msg_0000000077c0"])
    assert labels == {
        "msg_000000a1e862": "q-a1e862",
        "msg_000000b1e862": "q-b1e862",
        "msg_0000000077c0": "q-77c0",
    }


def test_labels_are_set_on_the_join_result():
    result = _fixture_join()
    assert [q["label"] for q in result["questions"]] == ["q-00b2", "q-00c3"]


def test_count_at_the_limit_is_truncated():
    assert inbox_truncated({"messages": [], "count": INBOX_LIMIT}) is True
    assert inbox_truncated({"messages": [], "count": INBOX_LIMIT - 1}) is False
    assert inbox_truncated({"messages": [{}] * 3, "count": None}, limit=3) is True


def test_truncated_passes_through_the_join():
    result = join_pending_questions([], worker_states={}, replied=set(), truncated=True)
    assert result == {"questions": [], "truncated": True, "warnings": []}


def test_a_malformed_payload_is_kept_with_a_warning():
    inbox = {
        "messages": [
            {
                "id": "msg_bad1",
                "type": "question",
                "to_handle": f"run:{RUN}",
                "from_handle": "dispatch:ctx_live",
                "body": "raw body question",
                "payload": "{not json",
                "created_at": "2026-09-27T15:00:00Z",
            }
        ],
        "count": 1,
    }
    [question] = run_questions(RUN, inbox)
    assert question.dispatch_id == "ctx_live"
    assert question.task_id is None
    assert question.question == "raw body question"
    assert question.options == ()
    assert question.warning is not None and "msg_bad1" in question.warning
    result = join_pending_questions([question], worker_states={"ctx_live": "running"}, replied=set(), truncated=False)
    assert [q["message_id"] for q in result["questions"]] == ["msg_bad1"]
    assert result["warnings"] == [question.warning]


def test_a_non_object_or_null_payload_is_malformed_too():
    for payload in ("[1,2]", None, 7):
        inbox = {
            "messages": [{"id": "m", "type": "question", "to_handle": f"run:{RUN}", "body": "b", "payload": payload}],
            "count": 1,
        }
        [question] = run_questions(RUN, inbox)
        assert question.warning is not None
        assert question.dispatch_id is None


def test_a_mapping_payload_is_accepted_as_is():
    inbox = {
        "messages": [
            {
                "id": "m",
                "type": "question",
                "to_handle": f"run:{RUN}",
                "payload": {"dispatchId": "ctx_p", "taskId": "task_p", "question": "Q?", "options": ["a", 3]},
            }
        ],
        "count": 1,
    }
    [question] = run_questions(RUN, inbox)
    assert (question.dispatch_id, question.task_id, question.question, question.options) == (
        "ctx_p",
        "task_p",
        "Q?",
        ("a", "3"),
    )
    assert question.warning is None


def test_a_question_without_an_id_cannot_be_replied_to_and_is_skipped():
    inbox = {"messages": [{"type": "question", "to_handle": f"run:{RUN}", "payload": "{}"}], "count": 1}
    assert run_questions(RUN, inbox) == []


def test_empty_dispatch_inbox_has_no_replies():
    assert reply_threads(RUN, fixture_result("inbox_dispatch_empty")) == set()


def test_answering_a_collision_partner_does_not_shrink_a_label():
    questions = [_q("msg_000000a1e862"), _q("msg_000000b1e862")]
    before = join_pending_questions(questions, worker_states={"ctx_live": "running"}, replied=set(), truncated=False)
    after = join_pending_questions(
        questions,
        worker_states={"ctx_live": "running"},
        replied={"msg_000000b1e862"},
        truncated=False,
    )
    assert [q["label"] for q in before["questions"]] == ["q-a1e862", "q-b1e862"]
    assert [(q["message_id"], q["label"]) for q in after["questions"]] == [("msg_000000a1e862", "q-a1e862")]


def test_ending_a_collision_partner_does_not_shrink_a_label():
    questions = [_q("msg_000000a1e862"), _q("msg_000000b1e862", dispatch_id="ctx_ended")]
    for state in ("succeeded", "failed", "stopped"):
        result = join_pending_questions(
            questions,
            worker_states={"ctx_live": "running", "ctx_ended": state},
            replied=set(),
            truncated=False,
        )
        assert [(q["message_id"], q["label"]) for q in result["questions"]] == [("msg_000000a1e862", "q-a1e862")]
        assert any("msg_000000b1e862" in warning and state in warning for warning in result["warnings"])
