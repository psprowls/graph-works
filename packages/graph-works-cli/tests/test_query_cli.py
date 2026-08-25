"""`gw query` — one completed text query at the CLI boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import query as query_module
from graph_works_core.query.commands import QueryResult
from models_io import BedrockAccessDenied, ModelsIoError, ProviderNotInstalled
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    """Create the smallest real initialized workspace for CLI boundary tests."""
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0
    return root


def test_query_constructs_the_default_embedder_and_prints_citations(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A wrong top-k or omitted embedder would silently change retrieval quality."""
    embedder = object()
    calls: list[dict[str, object]] = []

    async def fake_run_query(*args: object, **kwargs: object) -> QueryResult:
        calls.append({"query": args[0], "layout": args[1], **kwargs})
        return QueryResult("answer", ["concepts/a", "concepts/b"], 2, {}, "orchestrated")

    monkeypatch.setattr(query_module, "default_embedder", lambda: embedder)
    monkeypatch.setattr(query_module, "run_query", fake_run_query)

    result = runner.invoke(
        app, ["query", "--query", "why", "--backend", "bedrock", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "query": "why",
            "layout": query_module.resolve_workspace(str(initialized_workspace)),
            "embedder": embedder,
            "top_k": 5,
        },
    ]
    assert result.stdout == "answer\n\nCitations:\n- concepts/a\n- concepts/b\n"
    assert result.stderr == ""


@pytest.mark.parametrize("limit", (2, 11))
def test_query_rejects_limits_outside_the_core_range(limit: int) -> None:
    """Out-of-range retrieval sizes must be rejected before resolving a workspace."""
    result = runner.invoke(app, ["query", "--query", "why", "--limit", str(limit)])

    assert result.exit_code == 2
    assert "Invalid value" in result.stderr


def test_query_defaults_to_the_claude_code_brief_and_calls_no_role_llm(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """The system-wide claude_code default must reach the brief path, not run_query."""
    from graph_works_core.query.commands import QueryBrief, QueryPageBrief

    embedder = object()
    brief = QueryBrief(
        query="why",
        top_pages=(
            QueryPageBrief(path="concepts/a", excerpt="…", search_scores={"bm25": 1.0, "embed": 0.5, "rrf": 0.2}),
        ),
    )
    calls: list[dict[str, object]] = []

    def fake_plan_query_brief(query, layout, *, embedder, top_k):
        calls.append({"query": query, "layout": layout, "embedder": embedder, "top_k": top_k})
        return brief

    def fail_run_query(*args: object, **kwargs: object) -> object:
        raise AssertionError("run_query must not be called under the claude_code default")

    monkeypatch.setattr(query_module, "default_embedder", lambda: embedder)
    monkeypatch.setattr(query_module, "plan_query_brief", fake_plan_query_brief)
    monkeypatch.setattr(query_module, "run_query", fail_run_query)

    result = runner.invoke(app, ["query", "--query", "why", "--json", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == {
        "query": "why",
        "top_pages": [{"path": "concepts/a", "excerpt": "…", "search_scores": {"bm25": 1.0, "embed": 0.5, "rrf": 0.2}}],
    }
    assert calls == [
        {
            "query": "why",
            "layout": query_module.resolve_workspace(str(initialized_workspace)),
            "embedder": embedder,
            "top_k": 5,
        }
    ]


def test_query_backend_bedrock_still_runs_run_query_unchanged(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """`--backend bedrock` must be the regression check: today's pipeline, unaltered."""
    embedder = object()

    async def fake_run_query(*args: object, **kwargs: object) -> QueryResult:
        return QueryResult("answer", ["concepts/a"], 1, {}, "orchestrated")

    monkeypatch.setattr(query_module, "default_embedder", lambda: embedder)
    monkeypatch.setattr(query_module, "run_query", fake_run_query)

    result = runner.invoke(
        app, ["query", "--query", "why", "--backend", "bedrock", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 0, result.stdout
    assert result.stdout == "answer\n\nCitations:\n- concepts/a\n"


def test_query_defaults_to_the_claude_code_brief_text_output(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """The non-JSON rendering of a claude_code brief lists the query and its top pages."""
    from graph_works_core.query.commands import QueryBrief, QueryPageBrief

    brief = QueryBrief(
        query="why",
        top_pages=(
            QueryPageBrief(path="concepts/a", excerpt="…", search_scores={"bm25": 1.0}),
            QueryPageBrief(path="concepts/b", excerpt="…", search_scores={"bm25": 0.5}),
        ),
    )

    monkeypatch.setattr(query_module, "default_embedder", lambda: object())
    monkeypatch.setattr(query_module, "plan_query_brief", lambda *args, **kwargs: brief)

    result = runner.invoke(app, ["query", "--query", "why", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0, result.stdout
    assert result.stdout == "Query: why\n- concepts/a\n- concepts/b\n"


def test_query_backend_bedrock_json_output(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """`--backend bedrock --json` must render `query_payload`'s full completed-result shape."""

    async def fake_run_query(*args: object, **kwargs: object) -> QueryResult:
        return QueryResult("answer", ["concepts/a"], 1, {"concepts/a": {"bm25": 1.0}}, "orchestrated")

    monkeypatch.setattr(query_module, "default_embedder", lambda: object())
    monkeypatch.setattr(query_module, "run_query", fake_run_query)

    result = runner.invoke(
        app,
        ["query", "--query", "why", "--backend", "bedrock", "--json", "--workspace", str(initialized_workspace)],
    )

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == {
        "answer": "answer",
        "citations": ["concepts/a"],
        "pages_drilled": 1,
        "search_scores": {"concepts/a": {"bm25": 1.0}},
        "path": "orchestrated",
        "fallback_error": None,
    }


def test_query_reports_an_unknown_backend_via_role_spec(initialized_workspace: Path) -> None:
    """A bad `--backend` value must fail through `role_spec`'s `WorkspaceError`."""
    result = runner.invoke(
        app, ["query", "--query", "why", "--backend", "bogus", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "query_orchestrator" in combined
    assert "bogus" in combined


def test_query_reports_brief_planning_failures_without_stdout_or_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A brief-planning failure under the default `claude_code` backend is one clean diagnostic."""

    def fail(*args: object, **kwargs: object) -> object:
        raise ValueError("bad top_k")

    monkeypatch.setattr(query_module, "default_embedder", lambda: object())
    monkeypatch.setattr(query_module, "plan_query_brief", fail)

    result = runner.invoke(app, ["query", "--query", "why", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: bad top_k\n"
    assert "Traceback" not in result.stderr


def test_query_fallback_is_success_with_a_warning(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """An orchestrator fallback remains useful output and must not be an error exit."""

    async def fake_run_query(*args: object, **kwargs: object) -> QueryResult:
        return QueryResult("answer", ["concepts/a"], 1, {}, "fallback", "planner failed")

    monkeypatch.setattr(query_module, "run_query", fake_run_query)
    monkeypatch.setattr(query_module, "default_embedder", object)

    result = runner.invoke(
        app, ["query", "--query", "why", "--backend", "bedrock", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 0
    assert result.stdout == "answer\n\nCitations:\n- concepts/a\n"
    assert "planner failed" in result.stderr


def test_query_reports_a_missing_provider_without_stdout_or_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A default-provider construction failure must be one clean CLI diagnostic."""
    message = "The models-io[bedrock] extra is not installed.\n  Install it with: pip install 'models-io[bedrock]'"

    def missing_provider() -> object:
        raise ProviderNotInstalled(message)

    monkeypatch.setattr(query_module, "default_embedder", missing_provider)

    result = runner.invoke(
        app, ["query", "--query", "why", "--backend", "bedrock", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {message}\n"
    assert "Traceback" not in result.stderr


def test_query_reports_a_models_io_error_from_the_brief_path_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A Bedrock throttle/credential/access-denial error during `plan_query_brief`'s
    retrieval must produce the same clean diagnostic as the bedrock/vercel path,
    not a raw traceback -- `plan_query_brief` calls `refresh_index` and
    `embedder.embed_query` unconditionally, so a `ModelsIoError` there is real.
    """
    message = (
        "Bedrock access denied.\n"
        "  Model ARN attempted: arn:aws:bedrock:us-east-1::foundation-model/demo\n"
        "  IAM action required: bedrock:InvokeModel"
    )

    def fail(*args: object, **kwargs: object) -> object:
        raise ModelsIoError(message)

    monkeypatch.setattr(query_module, "default_embedder", lambda: object())
    monkeypatch.setattr(query_module, "plan_query_brief", fail)

    result = runner.invoke(app, ["query", "--query", "why", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {message}\n"
    assert "Traceback" not in result.stderr


def test_query_reports_provider_access_denial_without_stdout_or_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A provider invocation refusal must not escape Typer as a traceback."""
    message = (
        "Bedrock access denied.\n"
        "  Model ARN attempted: arn:aws:bedrock:us-east-1::foundation-model/demo\n"
        "  IAM action required: bedrock:InvokeModel"
    )

    async def denied(*args: object, **kwargs: object) -> QueryResult:
        raise BedrockAccessDenied(message)

    monkeypatch.setattr(query_module, "default_embedder", object)
    monkeypatch.setattr(query_module, "run_query", denied)

    result = runner.invoke(
        app, ["query", "--query", "why", "--backend", "bedrock", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {message}\n"
    assert "Traceback" not in result.stderr
