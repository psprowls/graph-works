"""Both pipelines, end to end, against scripted models. No network.

The fake LLM here is the same shape `test_agent_loop.py` uses: queued responses,
then the last one repeats. That is deliberate — one fake shape across the
package's async tests is one thing to learn.

A later task (12) appends the orchestrated-path tests to this same file — this
one owns the fixed-path tests plus `_read_file_bounded`, not the whole file forever.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from graph_works_core.query import commands as q
from graph_works_core.workspace.layout import layout_for
from okf_io import load_bundle


class Resp:
    def __init__(self, content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage_metadata = {"input_tokens": 10, "output_tokens": 20}


class FakeLLM:
    def __init__(self, responses: list[Resp]) -> None:
        self.responses = list(responses)
        self.seen: list[list[Any]] = []
        self._last = Resp("")

    def bind_tools(self, tools: list[Any]) -> FakeLLM:
        return self

    async def ainvoke(self, messages: list[Any]) -> Resp:
        self.seen.append(list(messages))
        if self.responses:
            self._last = self.responses.pop(0)
        return self._last


class FakeEmbedder:
    model_id = "fake-embed-v1"

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text) % 5 + 1), 1.0, 0.25]


class NeverEndingLLM:
    """Always emits another (unresolvable) tool call, forcing the loop's cap."""

    def bind_tools(self, tools: list[Any]) -> NeverEndingLLM:
        return self

    async def ainvoke(self, messages: list[Any]) -> Resp:
        return Resp("", tool_calls=[{"name": "whatever", "args": {}, "id": "c"}])


@pytest.fixture
def workspace(tmp_path):
    from datetime import date

    from graph_works_core import apply_init, plan_init

    layout = apply_init(plan_init(tmp_path, today=date(2026, 8, 16), topic="Query tests", repo_root=tmp_path)).layout
    root = layout.bundle_dir
    (root / "concepts").mkdir(parents=True)
    for slug, body in (("auth", "Refresh rotates the token."), ("storage", "Blobs expire on a lifecycle rule.")):
        (root / "concepts" / f"{slug}.md").write_text(
            f"---\ntitle: {slug.title()}\n---\n\n{body * 4}\n", encoding="utf-8"
        )
    return layout, load_bundle(root)


def _no_graph(monkeypatch):
    monkeypatch.setattr(q, "_load_query_graph_tools", lambda target: (None, []))


def _roles(monkeypatch, **llms):
    """Bind each role name to a fake."""
    monkeypatch.setattr(q, "make_llm", lambda role, **kw: llms[role])
    monkeypatch.setattr(q, "role_binding", lambda role, **kw: _binding(llms[role]))


def _binding(llm):
    from subagents_io import RoleBinding, RoleSpec

    return RoleBinding(
        spec=RoleSpec(model_id="fake", region="us-east-1", max_tokens=64, max_concurrency=2), make_llm=lambda: llm
    )


def test_read_file_bounded_refuses_a_path_outside_the_root(tmp_path):
    (tmp_path / "repo").mkdir()
    with pytest.raises(PermissionError, match="outside repo root"):
        q._read_file_bounded(tmp_path / "repo", "../secret.txt")


def test_read_file_bounded_refuses_a_path_under_exclude(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".works").mkdir(parents=True)
    (repo / ".works" / "notes.md").write_text("x", encoding="utf-8")
    with pytest.raises(PermissionError, match="inside"):
        q._read_file_bounded(repo, ".works/notes.md", exclude=repo / ".works")


def test_read_file_bounded_refuses_a_directory(tmp_path):
    (tmp_path / "repo" / "pkg").mkdir(parents=True)
    with pytest.raises(PermissionError, match="not a regular file"):
        q._read_file_bounded(tmp_path / "repo", "pkg")


def test_read_file_bounded_truncates_and_marks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "big.py").write_text("x" * 500, encoding="utf-8")
    out = q._read_file_bounded(repo, "big.py", max_bytes=100)
    assert out.endswith("[TRUNCATED]")
    assert len(out) == 100 + len("[TRUNCATED]")


def test_a_symlink_escaping_the_root_is_refused(tmp_path):
    # The named regression: both sides resolve BEFORE the containment check.
    # Drop the resolve() and this leaks files.
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    (repo / "link.txt").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(PermissionError, match="outside repo root"):
        q._read_file_bounded(repo, "link.txt")


async def test_the_fixed_path_synthesizes_from_librarian_excerpts(workspace, monkeypatch):
    layout, bundle = workspace
    _no_graph(monkeypatch)
    librarian = FakeLLM([Resp("Excerpt: refresh rotates the token.")])
    synthesizer = FakeLLM([Resp("Tokens rotate on refresh. See [Auth](/concepts/auth.md).")])
    _roles(monkeypatch, librarian=librarian, synthesizer=synthesizer, code_reader=FakeLLM([Resp("")]))

    result = await q.run_query("how do tokens rotate?", layout, bundle=bundle, embedder=FakeEmbedder(), use_legacy=True)
    assert result.path == "legacy"
    assert "Tokens rotate on refresh." in result.answer
    assert result.citations == ["concepts/auth"]
    assert result.pages_drilled >= 1


async def test_an_all_sentinel_fan_out_enters_the_code_fallback(workspace, monkeypatch):
    layout, bundle = workspace
    _no_graph(monkeypatch)
    (layout.repo_root / "src.py").write_text("def rotate():\n    return True\n", encoding="utf-8")
    librarian = FakeLLM([Resp("NO_RELEVANT_CONTENT")])
    code_reader = FakeLLM([Resp("`src.py:1` def rotate()")])
    synthesizer = FakeLLM([Resp("Rotation lives in `src.py:1`.")])
    _roles(monkeypatch, librarian=librarian, synthesizer=synthesizer, code_reader=code_reader)

    result = await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder(), use_legacy=True)
    assert result.answer.startswith(q.CODE_FALLBACK_MARKER)
    assert "src.py:1" in result.answer


async def test_both_pathways_empty_yields_the_disclaimer(workspace, monkeypatch):
    layout, bundle = workspace
    _no_graph(monkeypatch)
    sentinel = FakeLLM([Resp("NO_RELEVANT_CONTENT")])
    _roles(monkeypatch, librarian=sentinel, synthesizer=FakeLLM([Resp("")]), code_reader=sentinel)

    result = await q.run_query("nothing?", layout, bundle=bundle, embedder=FakeEmbedder(), use_legacy=True)
    assert result.answer == q.CODE_FALLBACK_DISCLAIMER


async def test_a_workspace_outside_a_repo_skips_the_code_fallback(tmp_path, monkeypatch):
    from datetime import date

    from graph_works_core import apply_init, plan_init

    # No repo_root= -- tmp_path has no .git ancestor, so plan_init's own
    # walk-up resolves it to None, same as the bare layout_for(tmp_path) this
    # replaces. build_librarian_system now needs a schema-installed layout
    # (_repositories.yaml, _schema/), which that bare layout never had.
    layout = apply_init(plan_init(tmp_path, today=date(2026, 8, 16), topic="Query tests")).layout
    root = layout.bundle_dir
    (root / "concepts").mkdir(parents=True)
    (root / "concepts" / "auth.md").write_text("---\ntitle: Auth\n---\n\nBody text here. " * 4, encoding="utf-8")
    bundle = load_bundle(root)
    _no_graph(monkeypatch)
    sentinel = FakeLLM([Resp("NO_RELEVANT_CONTENT")])
    _roles(monkeypatch, librarian=sentinel, synthesizer=FakeLLM([Resp("")]), code_reader=sentinel)

    result = await q.run_query("q?", layout, bundle=bundle, embedder=FakeEmbedder(), use_legacy=True)
    assert result.answer == q.CODE_FALLBACK_DISCLAIMER


def test_code_fallback_candidates_for_a_root_level_page_has_one_hint():
    assert q._code_fallback_candidates("readme") == ["readme"]


async def test_code_fallback_read_file_tool_succeeds_outside_the_workspace_root(tmp_path, monkeypatch):
    """The realistic layout: the workspace root sits *inside* the repo root
    (the standard `<repo>/.works` shape), not equal to it. A source file next
    to the workspace should be readable; nothing under the workspace root
    itself should be — that's what `exclude=layout.root` is for."""
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "auth.py").write_text("def rotate():\n    return True\n", encoding="utf-8")
    ws_root = repo_root / ".works"
    (ws_root / "okf" / "concepts").mkdir(parents=True)
    (ws_root / "okf" / "concepts" / "auth.md").write_text(
        "---\ntitle: Auth\n---\n\nRefresh rotates the token. " * 4, encoding="utf-8"
    )
    layout = layout_for(ws_root, repo_root=repo_root)
    bundle = load_bundle(ws_root / "okf")

    class OneRealReadLLM:
        def __init__(self) -> None:
            self._invocations = 0

        def bind_tools(self, tools: list[Any]) -> OneRealReadLLM:
            return self

        async def ainvoke(self, messages: list[Any]) -> Resp:
            self._invocations += 1
            if self._invocations == 1:
                return Resp("", tool_calls=[{"name": "read_file", "args": {"path": "src/auth.py"}, "id": "c1"}])
            last = messages[-1]
            return Resp(f"Saw: {last.content}")

    synthesizer = FakeLLM([Resp("ok")])
    _roles(monkeypatch, librarian=FakeLLM([Resp("")]), synthesizer=synthesizer, code_reader=OneRealReadLLM())

    pool = q.SubagentPool(layout.cache_dir / "traces")
    prepared = q._prepare_query_retrieval("rotation?", layout, bundle, top_k=3, embedder=FakeEmbedder())
    result = await q._run_code_fallback(prepared, query="rotation?", query_id="qid", pool=pool)
    assert result.startswith(q.CODE_FALLBACK_MARKER)
    # The real read reached the synthesizer as an excerpt — proof the tool
    # actually read the file rather than being refused by the exclude guard.
    sent = synthesizer.seen[0][-1].content
    assert "def rotate" in sent


async def test_run_query_closes_the_graph_reader_when_one_was_opened(workspace, monkeypatch):
    layout, bundle = workspace
    closed: list[bool] = []
    fake_reader = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(q, "_load_query_graph_tools", lambda target: (fake_reader, []))
    librarian = FakeLLM([Resp("Excerpt: refresh rotates the token.")])
    synthesizer = FakeLLM([Resp("Tokens rotate on refresh.")])
    _roles(monkeypatch, librarian=librarian, synthesizer=synthesizer, code_reader=FakeLLM([Resp("")]))

    await q.run_query("q?", layout, bundle=bundle, embedder=FakeEmbedder(), use_legacy=True)
    assert closed == [True]


async def test_librarian_drill_hits_its_cap_and_falls_through_to_the_code_fallback(workspace, monkeypatch):
    layout, bundle = workspace
    _no_graph(monkeypatch)
    (layout.repo_root / "src.py").write_text("def rotate():\n    return True\n", encoding="utf-8")
    code_reader = FakeLLM([Resp("`src.py:1` def rotate()")])
    synthesizer = FakeLLM([Resp("Rotation lives in `src.py:1`.")])
    _roles(monkeypatch, librarian=NeverEndingLLM(), synthesizer=synthesizer, code_reader=code_reader)

    result = await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder(), use_legacy=True)
    assert result.answer.startswith(q.CODE_FALLBACK_MARKER)


async def test_fixed_query_truncates_oversized_librarian_excerpts(workspace, monkeypatch):
    layout, bundle = workspace
    _no_graph(monkeypatch)
    monkeypatch.setattr(q, "_EXCERPTS_CHARS", 20)
    librarian = FakeLLM([Resp("x" * 100)])
    synthesizer = FakeLLM([Resp("ok")])
    _roles(monkeypatch, librarian=librarian, synthesizer=synthesizer, code_reader=FakeLLM([Resp("")]))

    await q.run_query("q?", layout, bundle=bundle, embedder=FakeEmbedder(), use_legacy=True)
    sent = synthesizer.seen[0][-1].content
    assert "x" * 100 not in sent


async def test_code_fallback_read_file_tool_reports_a_permission_error(workspace, monkeypatch):
    layout, bundle = workspace
    (layout.repo_root / "src.py").write_text("def rotate(): pass\n", encoding="utf-8")

    class EscapingLLM:
        def __init__(self) -> None:
            self._invocations = 0

        def bind_tools(self, tools: list[Any]) -> EscapingLLM:
            return self

        async def ainvoke(self, messages: list[Any]) -> Resp:
            self._invocations += 1
            if self._invocations == 1:
                return Resp("", tool_calls=[{"name": "read_file", "args": {"path": "../escape.py"}, "id": "c1"}])
            return Resp("Rotation lives in src.py.")

    synthesizer = FakeLLM([Resp("Rotation lives in src.py.")])
    _roles(monkeypatch, librarian=FakeLLM([Resp("")]), synthesizer=synthesizer, code_reader=EscapingLLM())

    pool = q.SubagentPool(layout.cache_dir / "traces")
    prepared = q._prepare_query_retrieval("rotation?", layout, bundle, top_k=3, embedder=FakeEmbedder())
    result = await q._run_code_fallback(prepared, query="rotation?", query_id="qid", pool=pool)
    assert result.startswith(q.CODE_FALLBACK_MARKER)


async def test_code_fallback_read_file_tool_wraps_a_generic_os_error(workspace, monkeypatch):
    layout, bundle = workspace
    (layout.repo_root / "src.py").write_text("def rotate(): pass\n", encoding="utf-8")

    class OneToolCallLLM:
        def __init__(self) -> None:
            self._invocations = 0

        def bind_tools(self, tools: list[Any]) -> OneToolCallLLM:
            return self

        async def ainvoke(self, messages: list[Any]) -> Resp:
            self._invocations += 1
            if self._invocations == 1:
                return Resp("", tool_calls=[{"name": "read_file", "args": {"path": "src.py"}, "id": "c1"}])
            return Resp("Rotation lives in src.py.")

    def _boom(*args: Any, **kwargs: Any) -> str:
        raise OSError("disk gremlins")

    monkeypatch.setattr(q, "_read_file_bounded", _boom)
    synthesizer = FakeLLM([Resp("Rotation lives in src.py.")])
    _roles(monkeypatch, librarian=FakeLLM([Resp("")]), synthesizer=synthesizer, code_reader=OneToolCallLLM())

    pool = q.SubagentPool(layout.cache_dir / "traces")
    prepared = q._prepare_query_retrieval("rotation?", layout, bundle, top_k=3, embedder=FakeEmbedder())
    result = await q._run_code_fallback(prepared, query="rotation?", query_id="qid", pool=pool)
    assert result.startswith(q.CODE_FALLBACK_MARKER)


async def test_code_drill_hits_its_cap_and_yields_the_disclaimer(workspace, monkeypatch):
    layout, bundle = workspace
    (layout.repo_root / "src.py").write_text("def rotate(): pass\n", encoding="utf-8")
    _roles(monkeypatch, librarian=FakeLLM([Resp("")]), synthesizer=FakeLLM([Resp("")]), code_reader=NeverEndingLLM())

    pool = q.SubagentPool(layout.cache_dir / "traces")
    prepared = q._prepare_query_retrieval("rotation?", layout, bundle, top_k=3, embedder=FakeEmbedder())
    result = await q._run_code_fallback(prepared, query="rotation?", query_id="qid", pool=pool)
    assert result == q.CODE_FALLBACK_DISCLAIMER


async def test_code_fallback_truncates_oversized_excerpts(workspace, monkeypatch):
    layout, bundle = workspace
    (layout.repo_root / "src.py").write_text("def rotate(): pass\n", encoding="utf-8")
    monkeypatch.setattr(q, "_EXCERPTS_CHARS", 20)
    code_reader = FakeLLM([Resp("y" * 100)])
    synthesizer = FakeLLM([Resp("ok")])
    _roles(monkeypatch, librarian=FakeLLM([Resp("")]), synthesizer=synthesizer, code_reader=code_reader)

    pool = q.SubagentPool(layout.cache_dir / "traces")
    prepared = q._prepare_query_retrieval("rotation?", layout, bundle, top_k=3, embedder=FakeEmbedder())
    await q._run_code_fallback(prepared, query="rotation?", query_id="qid", pool=pool)
    sent = synthesizer.seen[0][-1].content
    assert "y" * 100 not in sent


def _orchestrated(answer: str = "Tokens rotate. See [Auth](/concepts/auth.md)."):
    from graph_works_core.query.query_orchestrator import (
        AnswerEvidenceMap,
        OrchestratorEvidence,
        OrchestratorOutput,
        QueryOrchestratorResult,
    )

    output = OrchestratorOutput(
        answer_markdown=answer,
        citations=["concepts/auth"],
        evidence=[
            OrchestratorEvidence(
                id="e1",
                source_type="wiki",
                path="concepts/auth",
                freshness="fresh",
                staleness_reason=None,
                excerpt="Refresh rotates the token.",
                line_refs=[],
            )
        ],
        answer_evidence_map=[AnswerEvidenceMap(claim=answer, evidence_ids=["e1"])],
        worker_plan=(),
        worker_results=(),
        gaps=[],
        confidence="high",
    )
    return QueryOrchestratorResult(output=output, trace_metadata={"status": "ok", "worker_batches": 0})


async def test_the_default_path_is_orchestrated(workspace, monkeypatch):
    layout, bundle = workspace
    _no_graph(monkeypatch)
    seen_layouts: list[Any] = []

    async def scripted(**kwargs):
        # Reverting `layout=layout` at this call site in `run_query` MUST make
        # `seen_layouts` come back `[None]` instead of `[layout]` — that is the
        # whole point of recording it rather than just asserting the path.
        seen_layouts.append(kwargs.get("layout"))
        return _orchestrated()

    monkeypatch.setattr(q, "run_query_orchestrator", scripted)
    result = await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())
    assert result.path == "orchestrated"
    assert result.fallback_error is None
    assert result.citations == ["concepts/auth"]
    assert seen_layouts == [layout]


async def test_an_orchestrator_exception_falls_back_and_records_why(workspace, monkeypatch):
    layout, bundle = workspace
    _no_graph(monkeypatch)

    async def boom(**kwargs):
        raise RuntimeError("orchestrator is dead")

    monkeypatch.setattr(q, "run_query_orchestrator", boom)
    _roles(
        monkeypatch,
        librarian=FakeLLM([Resp("Excerpt: refresh rotates the token.")]),
        synthesizer=FakeLLM([Resp("Tokens rotate on refresh.")]),
        code_reader=FakeLLM([Resp("")]),
    )

    result = await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())
    assert result.path == "fallback"
    assert result.fallback_error == "RuntimeError: orchestrator is dead"
    assert "Tokens rotate on refresh." in result.answer


@pytest.mark.parametrize("status", ["invalid_json", "validation_error", "capped"])
async def test_a_returned_orchestrator_result_does_not_trigger_the_fallback(workspace, monkeypatch, status):
    # A planner that broke its own output contract still RETURNED. Re-running a
    # different pipeline over the same corpus does not diagnose that, so these
    # stay orchestrated even though the answer is the degraded one.
    layout, bundle = workspace
    _no_graph(monkeypatch)
    from graph_works_core.query.query_orchestrator import QueryOrchestratorResult, degraded_output

    async def degraded(**kwargs):
        return QueryOrchestratorResult(
            output=degraded_output("rotation?", reason="something went wrong"),
            trace_metadata={"status": status, "worker_batches": 0, "error": "something went wrong"},
        )

    monkeypatch.setattr(q, "run_query_orchestrator", degraded)

    def fail(*a, **k):
        raise AssertionError("the fixed pipeline must not run for a returned result")

    monkeypatch.setattr(q, "role_binding", fail)
    result = await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())
    assert result.path == "orchestrated"
    assert result.fallback_error is None


@pytest.mark.parametrize("status", ["tool_loop_error", "tool_loop_failed", "worker_batch_error"])
async def test_an_infrastructure_failure_status_falls_back(workspace, monkeypatch, status):
    # These three mean the orchestrator itself broke, not that the model did.
    layout, bundle = workspace
    _no_graph(monkeypatch)
    from graph_works_core.query.query_orchestrator import QueryOrchestratorResult, degraded_output

    async def degraded(**kwargs):
        return QueryOrchestratorResult(
            output=degraded_output("rotation?", reason="the loop died"),
            trace_metadata={"status": status, "worker_batches": 0, "error": "the loop died"},
        )

    monkeypatch.setattr(q, "run_query_orchestrator", degraded)
    _roles(
        monkeypatch,
        librarian=FakeLLM([Resp("Excerpt: refresh rotates the token.")]),
        synthesizer=FakeLLM([Resp("Tokens rotate on refresh.")]),
        code_reader=FakeLLM([Resp("")]),
    )

    result = await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())
    assert result.path == "fallback"
    assert result.fallback_error == f"{status}: the loop died"
    assert "Tokens rotate on refresh." in result.answer


async def test_retrieval_runs_once_even_on_the_fallback(workspace, monkeypatch):
    layout, bundle = workspace
    _no_graph(monkeypatch)
    calls: list[str] = []
    original = q._prepare_query_retrieval

    def counting(*args, **kwargs):
        calls.append("retrieved")
        return original(*args, **kwargs)

    monkeypatch.setattr(q, "_prepare_query_retrieval", counting)

    async def boom(**kwargs):
        raise RuntimeError("dead")

    monkeypatch.setattr(q, "run_query_orchestrator", boom)
    _roles(
        monkeypatch,
        librarian=FakeLLM([Resp("Excerpt.")]),
        synthesizer=FakeLLM([Resp("Answer.")]),
        code_reader=FakeLLM([Resp("")]),
    )
    await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())
    assert calls == ["retrieved"]


async def test_the_trace_summary_records_the_path_and_the_error(workspace, monkeypatch):
    layout, bundle = workspace
    _no_graph(monkeypatch)

    async def boom(**kwargs):
        raise ValueError("nope")

    monkeypatch.setattr(q, "run_query_orchestrator", boom)
    _roles(
        monkeypatch,
        librarian=FakeLLM([Resp("Excerpt.")]),
        synthesizer=FakeLLM([Resp("Answer.")]),
        code_reader=FakeLLM([Resp("")]),
    )
    await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())

    summaries = sorted((layout.cache_dir / "traces").glob("query_*.jsonl"))
    assert summaries
    record = json.loads(summaries[-1].read_text(encoding="utf-8").splitlines()[0])
    assert record["path"] == "fallback"
    assert record["fallback_error"] == "ValueError: nope"
    assert record["kind"] == "query_summary"


async def test_the_trace_summary_records_worker_batches_from_a_degraded_fallback(workspace, monkeypatch):
    # A worker_batch_error on the third batch legitimately carries
    # worker_batches=2 — the fallback's trace summary must report that count,
    # not hardcode 0 as if no orchestrator run had ever happened.
    layout, bundle = workspace
    _no_graph(monkeypatch)
    from graph_works_core.query.query_orchestrator import QueryOrchestratorResult, degraded_output

    async def degraded(**kwargs):
        return QueryOrchestratorResult(
            output=degraded_output("rotation?", reason="boom"),
            trace_metadata={"status": "worker_batch_error", "worker_batches": 2, "error": "boom"},
        )

    monkeypatch.setattr(q, "run_query_orchestrator", degraded)
    _roles(
        monkeypatch,
        librarian=FakeLLM([Resp("Excerpt: refresh rotates the token.")]),
        synthesizer=FakeLLM([Resp("Tokens rotate on refresh.")]),
        code_reader=FakeLLM([Resp("")]),
    )

    await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())

    summaries = sorted((layout.cache_dir / "traces").glob("query_*.jsonl"))
    assert summaries
    record = json.loads(summaries[-1].read_text(encoding="utf-8").splitlines()[0])
    assert record["orchestrator_batch_iterations"] == 2
    assert record["orchestrator_status"] == "worker_batch_error"


async def test_the_graph_reader_is_closed_on_every_path(workspace, monkeypatch):
    layout, bundle = workspace

    class Reader:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    reader = Reader()
    monkeypatch.setattr(q, "_load_query_graph_tools", lambda target: (reader, []))

    async def boom(**kwargs):
        raise RuntimeError("dead")

    monkeypatch.setattr(q, "run_query_orchestrator", boom)
    _roles(
        monkeypatch,
        librarian=FakeLLM([Resp("Excerpt.")]),
        synthesizer=FakeLLM([Resp("Answer.")]),
        code_reader=FakeLLM([Resp("")]),
    )
    await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())
    assert reader.closed is True


async def test_the_orchestrator_seam_delegates_to_the_real_orchestrator(workspace, monkeypatch):
    # The other tests here all monkeypatch `q.run_query_orchestrator` itself, so
    # the seam's own body — the lazy import plus delegation — never runs. This
    # one leaves the seam alone and instead scripts the orchestrator's own
    # model call, one level down, so the seam's real import-and-call path
    # actually executes.
    layout, bundle = workspace
    _no_graph(monkeypatch)
    from graph_works_core.agent_substrate.agent_loop import ToolLoopResult
    from graph_works_core.query import query_orchestrator as qo

    final_json = json.dumps(
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
            "worker_plan": [],
            "worker_results": [],
            "gaps": [],
            "confidence": "high",
        }
    )
    monkeypatch.setattr(qo, "make_llm", lambda *a, **k: object())

    async def scripted_loop(**kwargs):
        return ToolLoopResult(status="ok", final_text=final_json, error=None)

    monkeypatch.setattr(qo, "run_tool_loop", scripted_loop)

    result = await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())
    assert result.path == "orchestrated"
    assert "Tokens rotate on refresh." in result.answer


async def test_a_trace_write_failure_does_not_mask_the_answer(workspace, monkeypatch):
    layout, bundle = workspace
    _no_graph(monkeypatch)

    async def scripted(**kwargs):
        return _orchestrated()

    monkeypatch.setattr(q, "run_query_orchestrator", scripted)

    original_write_text = Path.write_text

    def boom(self: Path, *args: Any, **kwargs: Any) -> int:
        if self.name.startswith("query_") and self.suffix == ".jsonl":
            raise OSError("disk full")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", boom)

    result = await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())
    assert result.path == "orchestrated"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/concepts/auth.md", "concepts/auth"),
        ("concepts/auth.md", "concepts/auth"),
        ("  /concepts/auth.md  ", "concepts/auth"),
        ("src/x.py:42", None),
        ("concepts/auth", None),
        ("", None),
        (".md", None),
    ],
)
def test_normalize_citation_accepts_both_shown_forms_and_rejects_the_rest(raw, expected):
    assert q._normalize_citation(raw) == expected


async def test_the_orchestrated_path_yields_concept_ids_only(workspace, monkeypatch):
    # The finding: the fixed path returned concept ids and the orchestrated path
    # returned a mix, so a consumer resolving against bundle.concepts got
    # different results depending on which pipeline answered.
    layout, bundle = workspace
    _no_graph(monkeypatch)
    from graph_works_core.query.query_orchestrator import (
        AnswerEvidenceMap,
        OrchestratorEvidence,
        OrchestratorOutput,
        QueryOrchestratorResult,
    )

    # citations lists storage before auth, and links only auth in the body —
    # so the assertion below only passes if the merge is body-links-first;
    # a merge order of [*listed, *_extract_links(...)] would yield
    # ["concepts/storage", "concepts/auth"] instead.
    answer = "Rotation is described in [auth](/concepts/auth.md)."
    output = OrchestratorOutput(
        answer_markdown=answer,
        citations=["/concepts/storage.md", "/concepts/auth.md"],
        evidence=[
            OrchestratorEvidence(
                id="e1",
                source_type="wiki",
                path="concepts/auth",
                freshness="unknown",
                staleness_reason=None,
                excerpt="Refresh rotates the token.",
                line_refs=[],
            )
        ],
        answer_evidence_map=[AnswerEvidenceMap(claim=answer, evidence_ids=["e1"])],
        worker_plan=(),
        worker_results=(),
        gaps=[],
        confidence="high",
    )

    async def scripted(**kwargs):
        return QueryOrchestratorResult(output=output, trace_metadata={"status": "ok", "worker_batches": 0})

    monkeypatch.setattr(q, "run_query_orchestrator", scripted)
    result = await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())

    assert result.citations == ["concepts/auth", "concepts/storage"]


def test_candidates_carry_a_classified_freshness(tmp_path):
    # The finding: nothing set InitialCandidate.freshness, so every candidate
    # reached the planner as "unknown" and the stale-claim guardrail was keyed
    # off values the model invented.
    root = tmp_path / "okf"
    (root / "concepts").mkdir(parents=True)
    # Bodies are padded well past MIN_MEANINGFUL_BODY_CHARS so this test isolates
    # the last_updated_commit signal rather than tripping the unrelated
    # placeholder-content rule.
    (root / "concepts" / "aged.md").write_text(
        "---\ntitle: Aged\nlast_updated_commit: oldsha\n---\n\n"
        "Rotation and refresh and tokens rotate through the whole cycle here.\n",
        encoding="utf-8",
    )
    (root / "concepts" / "plain.md").write_text(
        "---\ntitle: Plain\n---\n\nRotation and refresh and tokens rotate through the whole cycle here.\n",
        encoding="utf-8",
    )
    layout = layout_for(tmp_path, repo_root=tmp_path)
    bundle = load_bundle(root)
    prepared = q._prepare_query_retrieval("rotation refresh", layout, bundle, top_k=3, embedder=FakeEmbedder())

    candidates = {c.path: c for c in q._initial_candidates(prepared, repo_head="newsha")}
    assert candidates["concepts/aged"].freshness == "stale"
    assert candidates["concepts/aged"].staleness_reason == "last_updated_commit mismatch"
    assert candidates["concepts/plain"].freshness == "unknown"
    assert candidates["concepts/plain"].staleness_reason is None


async def test_an_empty_fan_out_drills_no_pages(workspace, monkeypatch):
    # pages_drilled meant "fan-out successes" here and "evidence rows" on the
    # orchestrated path. Five pages that each said NO_RELEVANT_CONTENT read as 5.
    layout, bundle = workspace
    _no_graph(monkeypatch)

    async def boom(**kwargs):
        raise RuntimeError("dead")

    monkeypatch.setattr(q, "run_query_orchestrator", boom)
    _roles(
        monkeypatch,
        librarian=FakeLLM([Resp("NO_RELEVANT_CONTENT")]),
        synthesizer=FakeLLM([Resp("unused")]),
        code_reader=FakeLLM([Resp("Nothing in the code either.")]),
    )
    result = await q.run_query("rotation?", layout, bundle=bundle, embedder=FakeEmbedder())
    assert result.pages_drilled == 0


def test_g4_names_the_evidence_the_path_actually_used(tmp_path):
    from okf_io import load_bundle
    from subagents_io import FanOutResult

    root = tmp_path / "okf"
    (root / "concepts").mkdir(parents=True)
    (root / "concepts" / "auth.md").write_text("---\ntitle: Auth\n---\n\nRotation.\n", encoding="utf-8")
    bundle = load_bundle(root)
    result = q.QueryResult(
        answer="An answer citing [auth](/concepts/auth.md).",
        citations=["concepts/auth"],
        pages_drilled=0,
        search_scores={},
        path="orchestrated",
    )

    fixed = q.apply_guardrails(result, bundle, FanOutResult(successes=[], errors=[]))
    assert "no librarian excerpts" in fixed.answer

    orchestrated = q.apply_guardrails(
        result, bundle, FanOutResult(successes=[], errors=[]), evidence_noun="orchestrator evidence"
    )
    assert "no orchestrator evidence" in orchestrated.answer


async def test_run_worker_batch_threads_its_layout_into_role_resolution(tmp_path, monkeypatch):
    # Reverting `role_binding(role, layout=layout)` to `role_binding(role)`
    # MUST fail this test — that is the whole point. The first version of this
    # test called `make_llm` directly and passed either way, proving only that
    # `roles.make_llm` honours a layout it is handed, which it already did.
    from graph_works_core.query import query_orchestrator as qo
    from graph_works_core.workspace.layout import layout_for

    (tmp_path / "workspace.yaml").write_text(
        "version: 1\nroles:\n  librarian:\n    model_id: zai.glm-5\n",
        encoding="utf-8",
    )
    layout = layout_for(tmp_path)

    resolved: list[str] = []
    real_role_binding = qo.role_binding

    def _spy(role, **kwargs):
        binding = real_role_binding(role, **kwargs)
        resolved.append(binding.spec.model_id)
        return binding

    monkeypatch.setattr(qo, "role_binding", _spy)

    task = qo.WorkerTask(
        worker="librarian",
        task_id="t1",
        query_focus="q",
        expected_evidence="e",
        page_path="concepts/auth",
    )

    await qo.run_worker_batch([task], query="q", bundle=None, repo_root=None, trace_dir=tmp_path, layout=layout)
    await qo.run_worker_batch([task], query="q", bundle=None, repo_root=None, trace_dir=tmp_path)

    assert resolved == ["zai.glm-5", "moonshotai.kimi-k2.5"]
