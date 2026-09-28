"""Synthetic newest-first pages at the CLI boundary, through both observers."""

import json
from datetime import UTC, datetime

import pytest
from orca_fakes import fixture_result
from workflow_orca import OrcaCliError, OrcaCliPort, OrcaResult, OrcaSession

NOW = datetime(2026, 9, 27, tzinfo=UTC)
CURSOR = "opaque+/= cursor"


def worker(handle, task="task_live", state="running"):
    return {"dispatchId": handle, "taskId": task, "workerState": state}


def page(rows, cursor=None):
    return {"workers": rows, "page": {"hasMore": cursor is not None, "nextCursor": cursor}}


class Pages:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.index = 0

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        command = argv[2]
        if command == "task-list":
            result = {"tasks": [{"id": "task_live", "task_title": "live"}, {"id": "task_done", "task_title": "done"}]}
        elif command == "worker-list":
            # Bound the fake so a broken cursor loop fails instead of hanging.
            assert self.index < len(self.pages), "worker-list repeated a continuation"
            result = self.pages[self.index]
            self.index += 1
            if isinstance(result, str):
                return OrcaResult(1, json.dumps({"ok": False, "error": {"code": result, "message": "page failed"}}), "")
        elif command in ("worker-show", "worker-read"):
            result = fixture_result("worker_show_live_terminal" if command == "worker-show" else "worker_read_latest")
        else:
            raise AssertionError(argv)
        return OrcaResult(0, json.dumps({"ok": True, "result": result}), "")


def observe(transport, observer):
    if observer == "port":
        return [row["handle"] for row in OrcaCliPort(run=transport).liveness("run_test", now=NOW)]
    session = OrcaSession(name="test", run_id="run_test", run=transport, repo_selector=None)
    if observer == "workers":
        return [row.handle for row in session.workers() if row.state in {"pending", "running"}]
    return [row.handle for row in session.liveness(now=NOW)]


@pytest.mark.parametrize("observer", ["workers", "session", "port"])
@pytest.mark.parametrize(
    "pages",
    [
        [page([worker("ctx_done", "task_done", "succeeded")], CURSOR), page([worker("ctx_live")])],
        [page([worker("ctx_live"), worker("ctx_old", state="failed")])],
        [page([worker("ctx_live")], CURSOR), page([worker("ctx_old", state="failed")])],
    ],
    ids=["later-page-live", "newest-retry", "retry-across-pages"],
)
def test_complete_current_liveness(pages, observer):
    transport = Pages(pages)
    assert observe(transport, observer) == ["ctx_live"]
    lists = [call for call in transport.calls if "worker-list" in call]
    assert len(lists) == len(pages)
    assert lists[0] == ("orca", "orchestration", "worker-list", "--run", "run_test", "--json")
    if len(pages) > 1:
        assert lists[1] == ("orca", "orchestration", "worker-list", "--cursor", CURSOR, "--run", "run_test", "--json")
    assert all("ctx_old" not in call for call in transport.calls)
    assert {call[2] for call in transport.calls} <= {"task-list", "worker-list", "worker-show", "worker-read"}


@pytest.mark.parametrize("observer", ["session", "port"])
def test_later_page_failure_raises_without_partial_observation(observer):
    transport = Pages([page([worker("ctx_live")], CURSOR), "page_unavailable"])
    with pytest.raises(OrcaCliError) as caught:
        observe(transport, observer)
    assert caught.value.code == "page_unavailable"
    assert {call[2] for call in transport.calls} == {"task-list", "worker-list"}


@pytest.mark.parametrize("observer", ["session", "port"])
@pytest.mark.parametrize(
    "continuation",
    [
        None,
        [],
        {},
        {"hasMore": "false"},
        {"hasMore": True},
        *({"hasMore": True, "nextCursor": value} for value in [None, "", "  ", 1, [], {}]),
    ],
)
def test_malformed_continuation_raises(continuation, observer):
    transport = Pages([{"workers": [worker("ctx_live")], "page": continuation}])
    with pytest.raises(OrcaCliError, match=r"worker-list.*page"):
        observe(transport, observer)


@pytest.mark.parametrize("observer", ["session", "port"])
@pytest.mark.parametrize("cursors", [[CURSOR, CURSOR], [CURSOR, "second", CURSOR]])
def test_repeated_continuation_raises_before_looping(cursors, observer):
    transport = Pages([page([worker("ctx_live")], cursor) for cursor in cursors])
    with pytest.raises(OrcaCliError, match=r"worker-list.*page"):
        observe(transport, observer)
    assert transport.index == len(cursors)


@pytest.mark.parametrize("observer", ["session", "port"])
def test_continuation_cannot_drop_page_metadata(observer):
    transport = Pages([page([worker("ctx_live")], CURSOR), {"workers": []}])
    with pytest.raises(OrcaCliError, match=r"worker-list.*page"):
        observe(transport, observer)
