"""The separate OrcaPort adapter: exact argv and projected values."""

from __future__ import annotations

import json

import pytest
from orca_fakes import FakeRunner
from workflow_orca._cli import OrcaCliError, OrcaResult
from workflow_orca.port import OrcaCliPort


def port(routes: list[tuple[tuple[str, ...], str]]) -> tuple[OrcaCliPort, FakeRunner]:
    runner = FakeRunner(routes)
    return OrcaCliPort(run=runner), runner


def test_repo_list_projects_ids_and_paths():
    p, runner = port([(("repo", "list"), "repo_list")])
    assert p.repo_list() == [{"id": "repo_1", "path": "/repo"}]
    assert runner.calls == [("orca", "repo", "list", "--json")]


def test_worktree_show_strips_refs_heads():
    p, runner = port([(("worktree", "show"), "worktree_show_renamed")])
    row = p.worktree_show("id:x")
    assert row is not None
    assert row == {
        "id": "a5d7cb85-fc68-4596-bffd-ecf75466124a::/Users/pat/orca/workspaces/graph-works/child",
        "repo_id": "a5d7cb85-fc68-4596-bffd-ecf75466124a",
        "path": "/Users/pat/orca/workspaces/graph-works/child",
        "branch": "psprowls/child",
        "display_name": "bug/child",
        "is_main": False,
        "parent_id": None,
        "comment": None,
    }
    assert runner.calls[-1] == ("orca", "worktree", "show", "--worktree", "id:x", "--json")


def test_worktree_list_projects_every_row_with_its_comment():
    p, runner = port([(("worktree", "list"), "worktree_list")])
    rows = p.worktree_list("repo_1")
    assert runner.calls == [("orca", "worktree", "list", "--repo", "id:repo_1", "--json")]
    assert [(r["path"], r["branch"], r["is_main"], r["comment"]) for r in rows] == [
        ("/Users/pat/Personal/graph-works/gw", "main", True, ""),
        (
            "/Users/pat/orca/workspaces/gw/gw-reader-3c9f2e1d",
            None,
            False,
            "gw-reader:3c9f2e1d5b7a4c6e8d0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d",
        ),
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"truncated": True},
        {"totalCount": 3},
        {"hostScope": {"hostIds": [], "omittedHostIds": ["local"]}},
        {"hostScope": None},
        {"worktrees": None},
    ],
    ids=lambda change: next(iter(change)),
)
def test_worktree_list_refuses_an_incomplete_inventory(change):
    def incomplete(argv):
        result = {
            "worktrees": [{"id": "r::/a", "repoId": "r", "path": "/a", "comment": ""}],
            "totalCount": 1,
            "truncated": False,
            "hostScope": {"hostIds": ["local"], "omittedHostIds": []},
            **change,
        }
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")

    with pytest.raises(OrcaCliError, match="incomplete worktree inventory") as caught:
        OrcaCliPort(run=incomplete).worktree_list("r")
    assert caught.value.argv == ("orca", "worktree", "list", "--repo", "id:r", "--json")


def test_worktree_list_reads_a_commentless_row_through_show():
    def runner(argv):
        if argv[1:3] == ("worktree", "list"):
            result = {
                "worktrees": [{"id": "r::/a", "repoId": "r", "path": "/a"}],
                "totalCount": 1,
                "truncated": False,
                "hostScope": {"hostIds": ["local"], "omittedHostIds": []},
            }
        else:
            assert argv == ("orca", "worktree", "show", "--worktree", "path:/a", "--json")
            result = {"worktree": {"id": "r::/a", "repoId": "r", "path": "/a", "comment": "gw-reader:x"}}
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")

    [row] = OrcaCliPort(run=runner).worktree_list("r")
    assert row["comment"] == "gw-reader:x"


def test_worktree_list_show_fallback_that_finds_nothing_is_an_error():
    def runner(argv):
        if argv[1:3] == ("worktree", "list"):
            result = {
                "worktrees": [{"id": "r::/a", "repoId": "r", "path": "/a"}],
                "totalCount": 1,
                "truncated": False,
                "hostScope": {"hostIds": ["local"], "omittedHostIds": []},
            }
        else:
            result = {"worktree": None}
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")

    with pytest.raises(OrcaCliError, match="cannot inspect a listed worktree"):
        OrcaCliPort(run=runner).worktree_list("r")


def test_worktree_create_is_independent_setup_skipped_and_marked():
    p, runner = port([(("worktree", "create"), "worktree_create")])
    row = p.worktree_create(name="gw-reader-3c9f2e1d", repo_id="repo_1", base_branch="epic/x", comment="gw-reader:m")
    assert runner.calls == [
        (
            "orca",
            "worktree",
            "create",
            "--name",
            "gw-reader-3c9f2e1d",
            "--repo",
            "id:repo_1",
            "--base-branch",
            "epic/x",
            "--no-parent",
            "--setup",
            "skip",
            "--comment",
            "gw-reader:m",
            "--json",
        )
    ]
    assert row["path"] == "/Users/pat/orca/workspaces/gw/gw-reader-3c9f2e1d"
    assert row["branch"] == "psprowls/gw-reader-3c9f2e1d"
    assert row["is_main"] is False and row["parent_id"] is None
    assert row["comment"] == "gw-reader:3c9f2e1d5b7a4c6e8d0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d"


def test_worktree_create_without_a_worktree_row_is_an_error():
    def runner(argv):
        return OrcaResult(0, json.dumps({"ok": True, "result": {"worktree": {}}}), "")

    with pytest.raises(OrcaCliError, match="returned no worktree"):
        OrcaCliPort(run=runner).worktree_create(name="n", repo_id="r", base_branch="b", comment="c")


def test_worktree_set_methods_use_id_selectors():
    p, runner = port([(("worktree", "set"), "terminal_send")])
    p.worktree_set_parent("child", "parent")
    p.worktree_set_status("child", "done")
    assert runner.calls == [
        ("orca", "worktree", "set", "--worktree", "id:child", "--parent-worktree", "id:parent", "--json"),
        ("orca", "worktree", "set", "--worktree", "id:child", "--workspace-status", "done", "--json"),
    ]


def test_task_list_keeps_full_specs_and_marks_truncation():
    p, runner = port([(("task-list",), "task_list")])
    rows = p.task_list("run_1")
    assert len(rows) == 3
    assert rows[0]["spec"] == "Run /graph-wiki:next 2026-08-13-test-gap-code-wiki-smoke-script."
    assert rows[0]["title"] == "2026-08-13-test-gap-code-wiki-smoke-script#execute"
    assert runner.calls == [("orca", "orchestration", "task-list", "--run", "run_1", "--json")]


def test_task_list_truncated_spec_is_unknown():
    def truncated(argv):
        return OrcaResult(
            0,
            json.dumps(
                {
                    "ok": True,
                    "result": {
                        "tasks": [
                            {
                                "id": "task_1",
                                "task_title": "k",
                                "spec": "partial",
                                "spec_truncated": True,
                            }
                        ]
                    },
                }
            ),
            "",
        )

    assert OrcaCliPort(run=truncated).task_list("run_1")[0]["spec"] is None


def test_task_create_parses_result_task_id():
    p, runner = port([(("task-create",), "task_create")])
    assert p.task_create("run_1", spec="S\n", title="k", display_name="w · execute") == "task_new0000001"
    argv = runner.calls[-1]
    assert argv == (
        "orca",
        "orchestration",
        "task-create",
        "--run",
        "run_1",
        "--spec",
        "S\n",
        "--task-title",
        "k",
        "--display-name",
        "w · execute",
        "--json",
    )


def test_missing_task_create_id_raises_orca_cli_error():
    def missing(argv):
        return OrcaResult(0, json.dumps({"ok": True, "result": {"task": {}}}), "")

    with pytest.raises(OrcaCliError, match="no id"):
        OrcaCliPort(run=missing).task_create("run_1", spec="s", title="k", display_name="n")


def test_task_create_falls_back_to_top_level_id_when_task_is_empty():
    def top_level(argv):
        return OrcaResult(0, json.dumps({"ok": True, "result": {"task": {}, "id": "task_root"}}), "")

    assert OrcaCliPort(run=top_level).task_create("run_1", spec="s", title="k", display_name="n") == "task_root"


def test_task_update_uses_run_and_id():
    p, runner = port([(("task-update",), "terminal_send")])
    p.task_update("run_1", "task_1", "blocked")
    assert runner.calls[-1] == (
        "orca",
        "orchestration",
        "task-update",
        "--id",
        "task_1",
        "--status",
        "blocked",
        "--run",
        "run_1",
        "--json",
    )


def test_worker_start_reports_terminal_worktree_and_receipt():
    p, runner = port([(("worker-start",), "worker_start_effects")])
    started = p.worker_start(
        "run_1",
        "task_1",
        request={"agent": "claude", "model": None, "reasoning_effort": None},
        placement_argv=["--worktree", "path:/w"],
    )
    assert started == {
        "dispatch_id": "ctx_new000000001",
        "terminal": "term_effect0000001",
        "worktree_id": "repo1::/w",
        "state": "ready",
        "receipt_problem": None,
    }
    assert runner.calls[-1] == (
        "orca",
        "orchestration",
        "worker-start",
        "--task",
        "task_1",
        "--agent",
        "claude",
        "--run",
        "run_1",
        "--worktree",
        "path:/w",
        "--json",
    )


def test_worker_start_uses_agent_terminal_handle_when_present():
    p, _ = port([(("worker-start",), "worker_start")])
    started = p.worker_start("run_1", "task_1", request={"agent": "claude"}, placement_argv=[])
    assert started["terminal"] == "term_new00000000001"


def test_receipt_mismatch_is_reported_not_raised():
    p, _ = port([(("worker-start",), "worker_start_effects")])
    started = p.worker_start(
        "run_1", "task_1", request={"agent": "codex", "model": None, "reasoning_effort": None}, placement_argv=[]
    )
    assert started["receipt_problem"] and "agent" in started["receipt_problem"]


def test_worker_start_adds_model_and_effort_together():
    p, runner = port([(("worker-start",), "worker_start_effects")])
    p.worker_start(
        "run_1", "task_1", request={"agent": "claude", "model": "m", "reasoning_effort": "high"}, placement_argv=[]
    )
    assert runner.calls[-1][-5:] == ("--model", "m", "--effort", "high", "--json")


def test_failed_start_preserves_receipt_and_known_ids():
    body = {
        "ok": False,
        "result": {"dispatchId": "ctx_known", "taskId": "task_1"},
        "error": {"code": "launch_failed", "message": "failed", "details": {"workerState": "unknown"}},
    }

    def failed(argv):
        return OrcaResult(1, json.dumps(body), "")

    with pytest.raises(OrcaCliError) as caught:
        OrcaCliPort(run=failed).worker_start("run_1", "task_1", request={"agent": "claude"}, placement_argv=[])
    assert caught.value.receipt == body
    assert caught.value.details["dispatchId"] == "ctx_known"
    assert caught.value.details["taskId"] == "task_1"
    assert caught.value.details["workerState"] == "unknown"


def test_worker_list_follows_page_cursor():
    calls = []

    def pages(argv):
        calls.append(tuple(argv))
        cursor = "--cursor" in argv
        result = {
            "workers": [
                {
                    "dispatchId": "ctx_2" if cursor else "ctx_1",
                    "taskId": "task_1",
                    "workerState": "running",
                    "dispatchStatus": "dispatched",
                    "resource": {"worktreeId": "w"},
                }
            ],
            "page": {"hasMore": not cursor, "nextCursor": None if cursor else "cursor_2"},
        }
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")

    rows = OrcaCliPort(run=pages).worker_list("run_1")
    assert [row["dispatch_id"] for row in rows] == ["ctx_1", "ctx_2"]
    assert rows[0]["worktree_id"] == "w"
    assert calls == [
        ("orca", "orchestration", "worker-list", "--run", "run_1", "--json"),
        ("orca", "orchestration", "worker-list", "--run", "run_1", "--cursor", "cursor_2", "--json"),
    ]


@pytest.mark.parametrize(
    "cursor_field", [{}, {"nextCursor": None}, {"nextCursor": 42}, {"nextCursor": ""}, {"nextCursor": "  "}]
)
def test_worker_list_rejects_incomplete_page_without_usable_cursor(cursor_field):
    calls = []

    def incomplete_page(argv):
        calls.append(tuple(argv))
        assert len(calls) == 1, "an unusable cursor must not request another page"
        result = {
            "workers": [{"dispatchId": "ctx_1", "taskId": "task_1", "workerState": "running"}],
            "page": {"hasMore": True, **cursor_field},
        }
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")

    with pytest.raises(OrcaCliError, match="nextCursor") as caught:
        OrcaCliPort(run=incomplete_page).worker_list("run_1")
    assert caught.value.argv == ("orca", "orchestration", "worker-list", "--run", "run_1", "--json")
    assert calls == [caught.value.argv]


def test_worker_show_reads_snake_case_heartbeat():
    p, runner = port([(("worker-show",), "worker_show_live")])
    assert p.worker_show("ctx_320c498114b8") == {
        "worktree_id": None,
        "terminal": "term_ff2faacf-db5b-43ad-b05b-c6dfee58eb53",
        "last_heartbeat_at": "2026-08-13T18:38:36Z",
    }
    assert runner.calls[-1] == ("orca", "orchestration", "worker-show", "--dispatch", "ctx_320c498114b8", "--json")


def test_worker_read_counts_messages():
    p, runner = port([(("worker-read",), "worker_read_empty")])
    assert p.worker_read("ctx", limit=5)["message_count"] == 0
    assert runner.calls[-1] == ("orca", "orchestration", "worker-read", "--dispatch", "ctx", "--limit", "5", "--json")
    p, _ = port([(("worker-read",), "worker_read_transcript")])
    assert p.worker_read("ctx", limit=5) == {"source": "transcript", "message_count": 1}


def test_terminal_send_is_a_bare_enter():
    p, runner = port([(("terminal", "send"), "terminal_send")])
    p.terminal_send_enter("term_1")
    assert runner.calls[-1] == ("orca", "terminal", "send", "--terminal", "term_1", "--text", "", "--enter", "--json")


def test_a_failed_call_raises_orca_cli_error():
    def refuse(argv):
        body = {"id": "x", "ok": False, "error": {"code": "task_not_found", "message": "no such task"}}
        return OrcaResult(returncode=1, stdout=json.dumps(body), stderr="")

    p = OrcaCliPort(run=refuse)
    with pytest.raises(OrcaCliError, match="task_not_found"):
        p.task_update("run_1", "task_missing", "blocked")


@pytest.mark.parametrize("field", ["lastHeartbeatAt", "last_heartbeat_at"])
@pytest.mark.parametrize("value", [None, "2026-09-26T00:00:00Z"])
def test_worker_show_preserves_explicit_heartbeat_evidence(field, value):
    def response(argv):
        return OrcaResult(0, json.dumps({"ok": True, "result": {"dispatch": {field: value}}}), "")

    assert OrcaCliPort(run=response).worker_show("ctx")["last_heartbeat_at"] == value


def test_degraded_worker_read_stays_inconclusive_without_transcript():
    def response(argv):
        return OrcaResult(0, json.dumps({"ok": True, "result": {"source": "terminal"}}), "")

    assert OrcaCliPort(run=response).worker_read("ctx", limit=5) == {"source": "terminal", "message_count": 0}


def test_check_wait_argv_without_ack():
    p, runner = port([(("check", "--wait"), "check_batch")])
    p.check_wait("run_1", types="worker_done,escalation,question", timeout_ms=600000, ack=None)
    assert runner.calls == [
        (
            "orca",
            "orchestration",
            "check",
            "--run",
            "run_1",
            "--wait",
            "--types",
            "worker_done,escalation,question",
            "--timeout-ms",
            "600000",
            "--json",
        )
    ]


def test_check_wait_argv_with_ack():
    p, runner = port([(("check", "--wait"), "check_batch")])
    p.check_wait("run_1", types="worker_done", timeout_ms=5000, ack="dlv_prev")
    assert runner.calls[-1] == (
        "orca",
        "orchestration",
        "check",
        "--run",
        "run_1",
        "--wait",
        "--types",
        "worker_done",
        "--timeout-ms",
        "5000",
        "--ack",
        "dlv_prev",
        "--json",
    )


def test_check_wait_projects_messages():
    p, _ = port([(("check", "--wait"), "check_batch")])
    delivery = p.check_wait("run_1", types="worker_done", timeout_ms=1, ack=None)
    assert delivery["delivery_id"] == "dlv_0000000000a1"
    assert [m["type"] for m in delivery["messages"]] == ["worker_done", "question", "escalation", "heartbeat"]
    first = delivery["messages"][0]
    assert first == {
        "id": "msg_b84fb919772e",
        "type": "worker_done",
        "subject": "wiki_smoke.py closes the coverage gap",
        "body": "Added scripts/wiki_smoke.py and its tests.",
        "from_": "term_10fb90d2-9735-42b5-84a0-dc73a7e1a66a",
        "created_at": "2026-08-13T18:31:58Z",
        "payload": {
            "taskId": "task_5ca8c19ffa5e",
            "dispatchId": "ctx_817ed5bf5986",
            "outcome": "succeeded",
            "filesModified": ["scripts/wiki_smoke.py", "scripts/tests/test_wiki_smoke.py"],
            "reportPath": None,
        },
        "payload_raw": None,
    }


def test_check_wait_decodes_a_string_payload():
    p, _ = port([(("check", "--wait"), "check_heartbeat_only")])
    [message] = p.check_wait("run_1", types="worker_done", timeout_ms=1, ack=None)["messages"]
    assert message["payload"] == {
        "taskId": "task_338800a1fa14",
        "dispatchId": "ctx_320c498114b8",
        "phase": "implementing",
    }
    assert message["payload_raw"] is None


def test_check_wait_empty_batch_has_no_delivery_id():
    p, _ = port([(("check", "--wait"), "check_timeout")])
    assert p.check_wait("run_1", types="worker_done", timeout_ms=1, ack=None) == {
        "delivery_id": None,
        "messages": [],
    }


@pytest.mark.parametrize(
    ("raw", "payload", "payload_raw"),
    [
        ("{not json", None, "{not json"),
        ("[1, 2]", None, "[1, 2]"),
        (7, None, "7"),
        (None, {}, None),
        ({"a": 1}, {"a": 1}, None),
    ],
    ids=["undecodable-string", "string-non-object", "non-string-scalar", "null", "historical-mapping"],
)
def test_check_wait_payload_decoding(raw, payload, payload_raw):
    def batch(argv):
        message = {"id": "m1", "type": "worker_done", "payload": raw}
        result = {"deliveryId": "dlv_1", "messages": [message]}
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")

    [message] = OrcaCliPort(run=batch).check_wait("r", types="t", timeout_ms=1, ack=None)["messages"]
    assert (message["payload"], message["payload_raw"]) == (payload, payload_raw)
    assert (message["subject"], message["body"], message["from_"], message["created_at"]) == (None, None, None, None)


@pytest.mark.parametrize("messages", [None, "bad", {}, ["junk"], [{"id": "h", "type": "heartbeat"}, "junk"]])
def test_check_wait_refuses_unreadable_message_container_or_row(messages):
    def batch(argv):
        result = {"deliveryId": "dlv_1", "messages": messages}
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")

    with pytest.raises(OrcaCliError, match=r"malformed.*messages"):
        OrcaCliPort(run=batch).check_wait("r", types="t", timeout_ms=1, ack=None)


def test_check_wait_refuses_missing_messages_even_with_delivery_id():
    def batch(argv):
        return OrcaResult(0, json.dumps({"ok": True, "result": {"deliveryId": "dlv_1"}}), "")

    with pytest.raises(OrcaCliError, match=r"malformed.*messages"):
        OrcaCliPort(run=batch).check_wait("r", types="t", timeout_ms=1, ack=None)


def test_check_ack_argv():
    p, runner = port([(("check", "--ack"), "terminal_send")])
    p.check_ack("run_1", "dlv_1")
    assert runner.calls == [("orca", "orchestration", "check", "--run", "run_1", "--ack", "dlv_1", "--json")]


def test_run_use_argv():
    p, runner = port([(("run-use",), "terminal_send")])
    p.run_use("run_1")
    assert runner.calls == [("orca", "orchestration", "run-use", "--id", "run_1", "--json")]


def test_consumer_fenced_surfaces_as_the_error_code():
    p, _ = port([(("check", "--wait"), "check_consumer_fenced")])
    with pytest.raises(OrcaCliError) as caught:
        p.check_wait("run_43b63f0bdf2b", types="worker_done", timeout_ms=1, ack=None)
    assert caught.value.code == "consumer_fenced"


def test_worker_list_projects_release_state_and_terminal():
    p, _ = port([(("worker-list",), "worker_list")])
    rows = {row["dispatch_id"]: row for row in p.worker_list("run_1")}
    assert rows["ctx_000000000001"]["release_state"] == "released"
    assert rows["ctx_000000000001"]["terminal"] == "term_0000000000000001"
    assert rows["ctx_817ed5bf5986"]["release_state"] == "retained"
    assert rows["ctx_320c498114b8"]["release_state"] == "active"


def test_task_list_decodes_result():
    p, _ = port([(("task-list",), "task_list")])
    rows = {row["id"]: row for row in p.task_list("run_1")}
    assert rows["task_5ca8c19ffa5e"]["result"] is not None
    assert rows["task_5ca8c19ffa5e"]["result"]["provenance"] == "worker_report"
    assert rows["task_338800a1fa14"]["result"] is None


@pytest.mark.parametrize("raw", ["{bad", "[1]", 3, None], ids=["undecodable", "array", "number", "null"])
def test_task_list_unusable_result_is_none(raw):
    def listing(argv):
        result = {"tasks": [{"id": "task_1", "result": raw}]}
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")

    assert OrcaCliPort(run=listing).task_list("run_1")[0]["result"] is None
