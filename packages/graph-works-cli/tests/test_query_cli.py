"""`gw query` — one completed text query at the CLI boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import query as query_module
from graph_works_core.query.commands import QueryResult
from models_io import BedrockAccessDenied, ProviderNotInstalled
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

    result = runner.invoke(app, ["query", "--query", "why", "--workspace", str(initialized_workspace)])

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


def test_query_has_no_json_mode() -> None:
    """Text and citations are the entire completed query surface."""
    result = runner.invoke(app, ["query", "--query", "why", "--json"])

    assert result.exit_code == 2
    assert "No such option: --json" in result.stderr


def test_query_fallback_is_success_with_a_warning(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """An orchestrator fallback remains useful output and must not be an error exit."""

    async def fake_run_query(*args: object, **kwargs: object) -> QueryResult:
        return QueryResult("answer", ["concepts/a"], 1, {}, "fallback", "planner failed")

    monkeypatch.setattr(query_module, "run_query", fake_run_query)
    monkeypatch.setattr(query_module, "default_embedder", object)

    result = runner.invoke(app, ["query", "--query", "why", "--workspace", str(initialized_workspace)])

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

    result = runner.invoke(app, ["query", "--query", "why", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {message}\n"
    assert "Traceback" not in result.stderr
