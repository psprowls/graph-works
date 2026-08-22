"""The bounded loop, against a scripted orchestrator. No model call.

Every test here asserts the same shape of property: a failure produces a valid
output with a named status, not an exception. That is what makes run_query's
fallback an exception path rather than the normal path.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from graph_works_core.query import query_orchestrator as qo
from okf_io import load_bundle
from subagents_io import RoleBinding, RoleSpec


def _bundle(tmp_path: Path):
    root = tmp_path / "okf"
    (root / "concepts").mkdir(parents=True)
    (root / "concepts" / "auth.md").write_text(
        "---\ntitle: Auth\ndescription: token exchange\n---\n\n" + "Refresh rotates the token. " * 4,
        encoding="utf-8",
    )
    return load_bundle(root)


def _final_json(worker_plan: list[Any] | None = None) -> str:
    return json.dumps(
        {
            "answer_markdown": "Tokens rotate on refresh.",
            "citations": ["concepts/auth"],
            "evidence": [
                {
                    "id": "e1",
                    "source_type": "wiki",
                    "path": "concepts/auth",
                    "freshness": "fresh",
                    "staleness_reason": None,
                    "excerpt": "Refresh rotates the token.",
                    "line_refs": [],
                }
            ],
            "answer_evidence_map": [{"claim": "Tokens rotate on refresh.", "evidence_ids": ["e1"]}],
            "worker_plan": worker_plan or [],
            "worker_results": [],
            "gaps": [],
            "confidence": "high",
        }
    )


class ScriptedLoop:
    """Stands in for `run_tool_loop`. Yields queued results, then repeats."""

    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls = 0
        self._last = results[-1]

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.results:
            self._last = self.results.pop(0)
        return self._last


def _ok(text: str, error: str | None = None):
    from graph_works_core.agent_substrate.agent_loop import ToolLoopResult

    return ToolLoopResult(status="ok", final_text=text, error=error)


def _failed(error: str):
    from graph_works_core.agent_substrate.agent_loop import ToolLoopResult

    return ToolLoopResult(status="failed", final_text="", error=error)


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Neutralize the model factory; the loop itself is what these tests drive."""
    monkeypatch.setattr(qo, "make_llm", lambda *a, **k: object())
    return _bundle(tmp_path)


@pytest.fixture
def librarian_layout(tmp_path):
    """A real, schema-installed workspace -- `build_librarian_system` now
    needs `workspace.yaml` and `schema/` on disk, which the bare
    `_bundle(tmp_path)` fixture above never wrote."""
    from datetime import date

    from graph_works_core import apply_init, plan_init

    return apply_init(
        plan_init(tmp_path, today=date(2026, 8, 16), topic="Orchestrator tests", repo_root=tmp_path)
    ).layout


async def _run(bundle, tmp_path, **overrides):
    kwargs: dict[str, Any] = dict(
        query="how do tokens rotate?",
        bundle=bundle,
        repo_root=tmp_path,
        initial_candidates=[qo.InitialCandidate(path="concepts/auth", score=0.5, excerpt="x")],
        graph_tools=[],
        trace_dir=tmp_path / "traces",
    )
    kwargs.update(overrides)
    return await qo.run_query_orchestrator(**kwargs)


def test_the_planning_tools_are_the_three_plus_the_filtered_graph_tools(patched):
    tools = qo.build_orchestrator_tools(bundle=patched, graph_tools=[])
    assert [t.name for t in tools] == ["read_wiki_page", "search_wiki", "list_worker_capabilities"]


def test_read_wiki_page_is_bounded_and_bundle_keyed(patched):
    by_name = {t.name: t for t in qo.build_orchestrator_tools(bundle=patched, graph_tools=[])}
    assert "Auth" in by_name["read_wiki_page"].invoke({"path": "concepts/auth"})
    assert by_name["read_wiki_page"].invoke({"path": "concepts/ghost"}).startswith("ERROR:")


def test_search_wiki_returns_json_rows(patched):
    by_name = {t.name: t for t in qo.build_orchestrator_tools(bundle=patched, graph_tools=[])}
    rows = json.loads(by_name["search_wiki"].invoke({"query": "token"}))
    assert any(row["slug"] == "auth" for row in rows)


async def test_an_empty_worker_plan_ends_the_loop(patched, tmp_path, monkeypatch):
    scripted = ScriptedLoop([_ok(_final_json())])
    monkeypatch.setattr(qo, "run_tool_loop", scripted)
    result = await _run(patched, tmp_path)
    assert scripted.calls == 1
    assert result.trace_metadata["status"] == "ok"
    assert result.output.confidence == "high"


async def test_layout_reaches_make_llm_for_the_orchestrator_role(patched, tmp_path, monkeypatch):
    # Reverting `make_llm("query_orchestrator", layout=layout)` to
    # `make_llm("query_orchestrator")` MUST fail this test — that is the whole
    # point. `patched` neutralizes `qo.make_llm` with a lambda that ignores its
    # kwargs; this test replaces that neutralization with a spy so the kwarg
    # itself is observed, not just tolerated.
    from graph_works_core.workspace.layout import layout_for

    layout = layout_for(tmp_path, repo_root=tmp_path)
    seen_layouts: list[Any] = []

    def _spy(role: str, **kwargs: Any) -> Any:
        seen_layouts.append(kwargs.get("layout"))
        return object()

    monkeypatch.setattr(qo, "make_llm", _spy)
    monkeypatch.setattr(qo, "run_tool_loop", ScriptedLoop([_ok(_final_json())]))
    await _run(patched, tmp_path, layout=layout)
    assert seen_layouts == [layout]


async def test_a_tool_loop_exception_degrades(patched, tmp_path, monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("bedrock said no")

    monkeypatch.setattr(qo, "run_tool_loop", boom)
    result = await _run(patched, tmp_path)
    assert result.trace_metadata["status"] == "tool_loop_error"
    assert "RuntimeError: bedrock said no" in result.trace_metadata["error"]
    assert result.output.confidence == "low"
    qo.validate_orchestrator_output(result.output)  # the degraded output is still valid


async def test_a_failed_tool_loop_degrades(patched, tmp_path, monkeypatch):
    monkeypatch.setattr(qo, "run_tool_loop", ScriptedLoop([_failed("hit iteration cap (5)")]))
    result = await _run(patched, tmp_path)
    assert result.trace_metadata["status"] == "tool_loop_failed"
    assert result.output.confidence == "low"


async def test_invalid_json_degrades_under_its_own_status(patched, tmp_path, monkeypatch):
    monkeypatch.setattr(qo, "run_tool_loop", ScriptedLoop([_ok("not json at all")]))
    result = await _run(patched, tmp_path)
    assert result.trace_metadata["status"] == "invalid_json"


async def test_fenced_json_reaches_ok_not_invalid_json(patched, tmp_path, monkeypatch):
    fenced = f"```json\n{_final_json()}\n```"
    monkeypatch.setattr(qo, "run_tool_loop", ScriptedLoop([_ok(fenced)]))
    result = await _run(patched, tmp_path)
    assert result.trace_metadata["status"] == "ok"
    assert result.output.confidence == "high"


async def test_a_schema_violation_degrades_as_validation_error(patched, tmp_path, monkeypatch):
    monkeypatch.setattr(qo, "run_tool_loop", ScriptedLoop([_ok(json.dumps({"answer_markdown": "x"}))]))
    result = await _run(patched, tmp_path)
    assert result.trace_metadata["status"] == "validation_error"


_ONE_WORKER_RESULT_ROW = {"task_id": "t1", "worker": "librarian", "status": "complete", "result": "x"}


async def test_invalid_json_after_a_completed_batch_still_carries_its_worker_results(patched, tmp_path, monkeypatch):
    plan = [
        {
            "worker": "librarian",
            "task_id": "t1",
            "query_focus": "rotation",
            "expected_evidence": "the refresh rule",
            "page_path": "concepts/auth",
        }
    ]
    monkeypatch.setattr(qo, "run_tool_loop", ScriptedLoop([_ok(_final_json(plan)), _ok("not json at all")]))

    async def one_row(*a, **k):
        return (_ONE_WORKER_RESULT_ROW,)

    monkeypatch.setattr(qo, "run_worker_batch", one_row)
    result = await _run(patched, tmp_path)
    assert result.trace_metadata["status"] == "invalid_json"
    assert result.output.worker_results == (_ONE_WORKER_RESULT_ROW,)


async def test_a_schema_violation_after_a_completed_batch_still_carries_its_worker_results(
    patched, tmp_path, monkeypatch
):
    plan = [
        {
            "worker": "librarian",
            "task_id": "t1",
            "query_focus": "rotation",
            "expected_evidence": "the refresh rule",
            "page_path": "concepts/auth",
        }
    ]
    monkeypatch.setattr(
        qo,
        "run_tool_loop",
        ScriptedLoop([_ok(_final_json(plan)), _ok(json.dumps({"answer_markdown": "x"}))]),
    )

    async def one_row(*a, **k):
        return (_ONE_WORKER_RESULT_ROW,)

    monkeypatch.setattr(qo, "run_worker_batch", one_row)
    result = await _run(patched, tmp_path)
    assert result.trace_metadata["status"] == "validation_error"
    assert result.output.worker_results == (_ONE_WORKER_RESULT_ROW,)


async def test_a_worker_batch_exception_degrades(patched, tmp_path, monkeypatch):
    plan = [
        {
            "worker": "librarian",
            "task_id": "t1",
            "query_focus": "rotation",
            "expected_evidence": "the refresh rule",
            "page_path": "concepts/auth",
        }
    ]
    monkeypatch.setattr(qo, "run_tool_loop", ScriptedLoop([_ok(_final_json(plan))]))

    async def boom(*a, **k):
        raise RuntimeError("pool died")

    monkeypatch.setattr(qo, "run_worker_batch", boom)
    result = await _run(patched, tmp_path)
    assert result.trace_metadata["status"] == "worker_batch_error"
    assert result.output.confidence == "low"


async def test_the_worker_batch_cap_produces_capped_not_an_exception(patched, tmp_path, monkeypatch):
    plan = [
        {
            "worker": "librarian",
            "task_id": "t1",
            "query_focus": "rotation",
            "expected_evidence": "the refresh rule",
            "page_path": "concepts/auth",
        }
    ]
    # Every turn asks for more workers, so the cap is the only exit.
    monkeypatch.setattr(qo, "run_tool_loop", ScriptedLoop([_ok(_final_json(plan))]))

    async def one_row(*a, **k):
        return ({"task_id": "t1", "worker": "librarian", "status": "complete", "result": "x"},)

    monkeypatch.setattr(qo, "run_worker_batch", one_row)
    result = await _run(patched, tmp_path)
    assert result.trace_metadata["status"] == "capped"
    assert result.trace_metadata["worker_batches"] == qo.MAX_ORCHESTRATOR_WORKER_BATCHES
    assert result.output.worker_plan == ()
    assert result.output.confidence == "low"
    qo.validate_orchestrator_output(result.output)


def test_a_worker_plan_row_naming_an_unknown_worker_is_rejected():
    with pytest.raises(qo.OrchestratorValidationError, match="worker must be one of"):
        qo.parse_worker_tasks([{"worker": "scanner", "task_id": "t", "query_focus": "q", "expected_evidence": "e"}])


def test_a_code_reader_row_needs_non_empty_hints():
    with pytest.raises(qo.OrchestratorValidationError, match="target_paths_or_hints"):
        qo.parse_worker_tasks(
            [
                {
                    "worker": "code_reader",
                    "task_id": "t",
                    "query_focus": "q",
                    "expected_evidence": "e",
                    "target_paths_or_hints": [],
                }
            ]
        )


async def test_a_code_reader_task_without_a_repo_root_becomes_an_error_row(tmp_path, monkeypatch):
    monkeypatch.setattr(qo, "make_llm", lambda *a, **k: object())
    task = qo.WorkerTask(
        worker="code_reader",
        task_id="t1",
        query_focus="q",
        expected_evidence="e",
        target_paths_or_hints=("src/",),
    )
    rows = await qo.run_worker_batch([task], query="q", bundle=None, repo_root=None, trace_dir=tmp_path)
    assert rows[0]["status"] == "error"
    assert "repo_root is required" in rows[0]["error"]


# ---------------------------------------------------------------------------
# The file-reading path: `_read_file_bounded`, its two guards, and the
# librarian/code-reader runners that sit on top of them.
# ---------------------------------------------------------------------------


def test_read_file_bounded_reads_a_normal_file(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "foo.txt").write_text("hello world", encoding="utf-8")
    assert qo._read_file_bounded(repo_root, "foo.txt") == "hello world"


def test_read_file_bounded_rejects_a_path_escaping_the_repo_root(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    with pytest.raises(PermissionError, match="resolves outside repo root"):
        qo._read_file_bounded(repo_root, "../secret.txt")


def test_read_file_bounded_rejects_a_non_regular_file(tmp_path):
    repo_root = tmp_path / "repo"
    (repo_root / "subdir").mkdir(parents=True)
    with pytest.raises(PermissionError, match="not a regular file"):
        qo._read_file_bounded(repo_root, "subdir")


def test_read_file_bounded_truncates_at_the_byte_cap(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "big.txt").write_text("0123456789", encoding="utf-8")
    result = qo._read_file_bounded(repo_root, "big.txt", max_bytes=4)
    assert result == "0123[TRUNCATED]"


def test_path_allowed_by_worker_hints_matches_an_exact_and_a_directory_hint(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    assert qo._path_allowed_by_worker_hints(repo_root, "src/auth.py", ("src/auth.py",))
    assert qo._path_allowed_by_worker_hints(repo_root, "src/auth.py", ("src/",))


def test_path_allowed_by_worker_hints_rejects_an_unrelated_path(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    assert not qo._path_allowed_by_worker_hints(repo_root, "other/thing.py", ("src/auth.py",))


def test_path_allowed_by_worker_hints_rejects_an_unresolvable_requested_path(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    assert not qo._path_allowed_by_worker_hints(repo_root, "", ("src/",))


def test_path_allowed_by_worker_hints_skips_a_hint_that_escapes_the_repo_root(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # The first hint resolves outside repo_root and must be skipped, not raise;
    # the second, valid hint still matches.
    assert qo._path_allowed_by_worker_hints(repo_root, "src/auth.py", ("../escape.py", "src/auth.py"))


def test_read_worker_scoped_file_rejects_a_path_outside_its_hints(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    result = qo._read_worker_scoped_file(repo_root, "other/thing.py", ("src/",))
    assert result.startswith("ERROR: refusing to read")


def test_read_worker_scoped_file_wraps_a_permission_error(tmp_path):
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    # "src" is allowed by the "src/" hint but is a directory, not a regular file.
    result = qo._read_worker_scoped_file(repo_root, "src", ("src/",))
    assert result.startswith("ERROR:")
    assert "not a regular file" in result


def test_read_worker_scoped_file_wraps_a_generic_os_error(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    def _boom(*args: Any, **kwargs: Any) -> str:
        raise OSError("disk gremlins")

    monkeypatch.setattr(qo, "_read_file_bounded", _boom)
    result = qo._read_worker_scoped_file(repo_root, "src/auth.py", ("src/",))
    assert "disk gremlins" in result


def test_read_worker_scoped_file_excludes_the_workspace_directory(tmp_path):
    # The seam-level gap this closes: an orchestrator worker task whose hints
    # happen to name the workspace directory (e.g. `.works`) must not be able
    # to read the workspace's own derived state (traces, the search index,
    # config) back into an answer — the same exclusion
    # `commands.query._run_code_fallback`'s `read_file` tool already applies.
    repo_root = tmp_path / "repo"
    workspace_root = repo_root / ".works"
    (workspace_root / "traces").mkdir(parents=True)
    (workspace_root / "traces" / "query_abc.jsonl").write_text('{"answer": "secret"}\n', encoding="utf-8")

    result = qo._read_worker_scoped_file(
        repo_root, ".works/traces/query_abc.jsonl", (".works/",), exclude=workspace_root
    )
    assert result.startswith("ERROR:")
    assert "secret" not in result


def test_read_worker_scoped_file_with_no_exclude_still_reads_normally(tmp_path):
    # exclude defaults to None: a caller with no workspace concept (or the
    # unit tests above) keeps working exactly as before this change.
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "auth.py").write_text("def rotate(): ...\n", encoding="utf-8")
    result = qo._read_worker_scoped_file(repo_root, "src/auth.py", ("src/",))
    assert result == "def rotate(): ...\n"


class _RaisingLibrarianLLM:
    async def ainvoke(self, messages: list[Any]) -> Any:
        raise RuntimeError("model unavailable")


async def test_a_worker_task_failure_inside_the_pool_becomes_a_named_error_row(
    patched, tmp_path, monkeypatch, librarian_layout
):
    """Drives a real WorkerTask through the pool to a genuine `PerItemError`, so
    `_worker_failure_from_error`'s WorkerTask branch runs for real."""

    def _raising_role_binding(role: str, **_kwargs: Any) -> RoleBinding:
        return RoleBinding(spec=RoleSpec(model_id="stub", max_concurrency=1), make_llm=_RaisingLibrarianLLM)

    monkeypatch.setattr(qo, "role_binding", _raising_role_binding)
    task = qo.WorkerTask(
        worker="librarian",
        task_id="lib-fail",
        query_focus="q",
        expected_evidence="e",
        page_path="concepts/auth",
    )
    rows = await qo.run_worker_batch(
        [task], query="q", bundle=patched, repo_root=None, trace_dir=tmp_path / "traces", layout=librarian_layout
    )
    assert rows[0]["task_id"] == "lib-fail"
    assert rows[0]["status"] == "error"
    assert "model unavailable" in rows[0]["error"]


def test_worker_failure_from_error_falls_back_when_the_item_is_not_a_worker_task():
    from subagents_io import PerItemError

    row = qo._worker_failure_from_error(PerItemError(item="not-a-task", exception=RuntimeError("boom")))
    assert row["task_id"] == ""
    assert row["worker"] == ""
    assert row["status"] == "error"
    assert "boom" in row["error"]


def test_repo_relative_posix_resolves_a_normal_path(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    assert qo._repo_relative_posix(repo_root, "src/auth.py") == "src/auth.py"


def test_repo_relative_posix_rejects_an_empty_or_escaping_path(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    assert qo._repo_relative_posix(repo_root, "") is None
    assert qo._repo_relative_posix(repo_root, "../outside.py") is None


class _StubLibrarianLLM:
    """Fakes a bound chat model's `.ainvoke`, standing in for `make_llm(...)`."""

    async def ainvoke(self, messages: list[Any]) -> Any:
        return SimpleNamespace(content="Refresh rotates the token, per the vault page.")


class _StubCodeReaderLLM:
    """Fakes one tool-call round then a final answer, exercising the loop body."""

    def __init__(self) -> None:
        self._invocations = 0

    def bind_tools(self, tools: list[Any]) -> _StubCodeReaderLLM:
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        self._invocations += 1
        if self._invocations == 1:
            return SimpleNamespace(
                content="",
                tool_calls=[{"name": "read_file", "args": {"path": "src/auth.py"}, "id": "call-1"}],
            )
        return SimpleNamespace(content="def rotate(): ...  # src/auth.py:1", tool_calls=[])


def _stub_role_binding(role: str, **_kwargs: Any) -> RoleBinding:
    if role == "librarian":
        return RoleBinding(spec=RoleSpec(model_id="stub-librarian", max_concurrency=1), make_llm=_StubLibrarianLLM)
    return RoleBinding(spec=RoleSpec(model_id="stub-code-reader", max_concurrency=1), make_llm=_StubCodeReaderLLM)


async def test_run_worker_batch_drives_the_librarian_and_code_reader_runners_for_real(
    patched, tmp_path, monkeypatch, librarian_layout
):
    """Exercises `_build_librarian_task_runner` and `_build_code_reader_task_runner`
    through the real `run_worker_batch` dispatch path, with `role_binding` stubbed
    so no network call happens but the runner closures actually run."""
    monkeypatch.setattr(qo, "role_binding", _stub_role_binding)
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "auth.py").write_text("def rotate():\n    return True\n", encoding="utf-8")

    librarian_task = qo.WorkerTask(
        worker="librarian",
        task_id="lib-1",
        query_focus="rotation",
        expected_evidence="the refresh rule",
        page_path="concepts/auth",
    )
    code_task = qo.WorkerTask(
        worker="code_reader",
        task_id="code-1",
        query_focus="rotation",
        expected_evidence="the refresh implementation",
        target_paths_or_hints=("src/auth.py",),
    )

    rows = await qo.run_worker_batch(
        [librarian_task, code_task],
        query="how do tokens rotate?",
        bundle=patched,
        repo_root=repo_root,
        trace_dir=tmp_path / "traces",
        layout=librarian_layout,
    )

    by_task = {row["task_id"]: row for row in rows}
    assert by_task["lib-1"]["status"] == "complete"
    assert "Refresh rotates" in by_task["lib-1"]["result"]
    assert by_task["code-1"]["status"] == "complete"
    assert "rotate" in by_task["code-1"]["result"]


class _CapturingCodeReaderLLM:
    """Records the bound tools and returns immediately with no tool calls."""

    def __init__(self) -> None:
        self.tools: list[Any] | None = None

    def bind_tools(self, tools: list[Any]) -> _CapturingCodeReaderLLM:
        self.tools = tools
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        return SimpleNamespace(content="no tool needed", tool_calls=[])


async def test_the_code_reader_runners_read_file_tool_delegates_to_the_scoped_reader(tmp_path):
    """Drives `_build_code_reader_task_runner` directly so its nested `read_file`
    tool (built and bound, but never invoked by the loop itself since the loop
    calls `_read_worker_scoped_file` for each tool_call) is exercised too."""
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "auth.py").write_text("def rotate(): pass\n", encoding="utf-8")

    task = qo.WorkerTask(
        worker="code_reader",
        task_id="t1",
        query_focus="q",
        expected_evidence="e",
        target_paths_or_hints=("src/auth.py",),
    )
    stub_llm = _CapturingCodeReaderLLM()
    runner = qo._build_code_reader_task_runner(stub_llm, query="q", repo_root=repo_root)
    result = await runner(task)

    assert result.value == "no tool needed"
    assert stub_llm.tools is not None
    read_file_tool = stub_llm.tools[0]
    assert "def rotate" in read_file_tool.invoke({"path": "src/auth.py"})


class _NeverEndingCodeReaderLLM:
    """Always emits another tool call, so the runner must hit its iteration cap."""

    def bind_tools(self, tools: list[Any]) -> _NeverEndingCodeReaderLLM:
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        return SimpleNamespace(
            content="",
            tool_calls=[{"name": "read_file", "args": {"path": "src/auth.py"}, "id": "c"}],
        )


async def test_the_code_reader_runner_hits_its_iteration_cap(tmp_path):
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "auth.py").write_text("def rotate(): pass\n", encoding="utf-8")

    task = qo.WorkerTask(
        worker="code_reader",
        task_id="t1",
        query_focus="q",
        expected_evidence="e",
        target_paths_or_hints=("src/auth.py",),
    )
    runner = qo._build_code_reader_task_runner(_NeverEndingCodeReaderLLM(), query="q", repo_root=repo_root)
    result = await runner(task)

    assert result.value == "NO_RELEVANT_CONTENT"


async def test_the_orchestrator_resolves_roles_through_the_workspace_layout(tmp_path, monkeypatch):
    # The finding: make_llm("query_orchestrator") resolved packaged-only, so a
    # workspace override changed the model on the fixed path and was ignored here.
    from graph_works_core.agent_substrate import roles
    from graph_works_core.workspace.layout import layout_for

    layout = layout_for(tmp_path, repo_root=tmp_path)
    layout.manifest_path.write_text(
        "version: 1\nroles:\n  query_orchestrator:\n    model_id: zai.glm-5\n", encoding="utf-8"
    )

    seen: list[Any] = []

    def _capture(role, **kwargs):
        seen.append(kwargs.get("layout"))
        raise RuntimeError("stop here — the layout is all this test needs")

    monkeypatch.setattr(qo, "make_llm", _capture)

    with pytest.raises(RuntimeError, match="stop here"):
        await qo.run_query_orchestrator(
            query="rotation?",
            bundle=_bundle(tmp_path),
            repo_root=tmp_path,
            initial_candidates=[qo.InitialCandidate(path="concepts/auth", score=0.5, excerpt="x")],
            graph_tools=[],
            trace_dir=tmp_path / "traces",
            layout=layout,
        )

    assert seen == [layout]
    # And the layout that arrived is one that actually carries the override.
    assert roles.role_spec("query_orchestrator", layout=seen[0]).model_id == "zai.glm-5"


async def test_run_worker_batch_receives_layout_in_role_binding_call(patched, tmp_path, monkeypatch):
    # Direct test of run_worker_batch to ensure layout flows to role_binding,
    # not just to make_llm. This guards against regressions to the role_binding call.
    from datetime import date

    from graph_works_core import apply_init, plan_init

    layout = apply_init(
        plan_init(tmp_path, today=date(2026, 8, 16), topic="Orchestrator tests", repo_root=tmp_path)
    ).layout
    captured_layouts: list[Any] = []

    def _capturing_role_binding(role: str, **_kwargs: Any) -> RoleBinding:
        captured_layouts.append(_kwargs.get("layout"))
        return _stub_role_binding(role, **_kwargs)

    monkeypatch.setattr(qo, "role_binding", _capturing_role_binding)

    task = qo.WorkerTask(
        worker="librarian",
        task_id="lib-1",
        query_focus="rotation",
        expected_evidence="the refresh rule",
        page_path="concepts/auth",
    )

    await qo.run_worker_batch(
        [task],
        query="how do tokens rotate?",
        bundle=patched,
        repo_root=tmp_path,
        trace_dir=tmp_path / "traces",
        layout=layout,
    )

    assert captured_layouts == [layout]


async def test_run_worker_batch_reports_a_named_error_when_layout_is_missing_for_a_librarian_task(patched, tmp_path):
    # SubagentPool requires a real Path for trace_dir, so this can't pass None
    # there -- the missing-layout guard fires before layout is ever touched,
    # so an ordinary traces dir is enough to reach it.
    task = qo.WorkerTask(
        worker="librarian",
        task_id="lib-no-layout",
        query_focus="q",
        expected_evidence="e",
        page_path="concepts/auth",
    )
    rows = await qo.run_worker_batch(
        [task], query="q", bundle=patched, repo_root=None, trace_dir=tmp_path / "traces", layout=None
    )
    assert rows[0]["status"] == "error"
    assert "layout is required for librarian workers" in rows[0]["error"]
