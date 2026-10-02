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


@pytest.fixture(autouse=True)
def no_real_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The brief resolves its embedder in core; no test here may build a real Bedrock client.

    A test that wants an embedder patches `make_bedrock_embeddings` (or the CLI's own seams) itself.
    """
    from graph_works_core.query import commands as q

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise ProviderNotInstalled("bedrock is stubbed out in the CLI query tests")

    monkeypatch.setattr(q, "make_bedrock_embeddings", unavailable)


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

    def fake_plan_query_brief(query, layout, *, embedder, top_k, page):
        calls.append({"query": query, "layout": layout, "embedder": embedder, "top_k": top_k, "page": page})
        return brief

    def fail_run_query(*args: object, **kwargs: object) -> object:
        raise AssertionError("run_query must not be called under the claude_code default")

    def fail_default_embedder() -> object:
        raise AssertionError("the brief resolves its embedder through brief_embedder")

    monkeypatch.setattr(query_module, "brief_embedder", lambda: embedder)
    monkeypatch.setattr(query_module, "default_embedder", fail_default_embedder)
    monkeypatch.setattr(query_module, "plan_query_brief", fake_plan_query_brief)
    monkeypatch.setattr(query_module, "run_query", fail_run_query)

    result = runner.invoke(app, ["query", "--query", "why", "--json", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == {
        "query": "why",
        "top_pages": [{"path": "concepts/a", "excerpt": "…", "search_scores": {"bm25": 1.0, "embed": 0.5, "rrf": 0.2}}],
        "retrieval": "hybrid",
        "page": None,
        "refusal": None,
        "warnings": [],
    }
    assert calls == [
        {
            "query": "why",
            "layout": query_module.resolve_workspace(str(initialized_workspace)),
            "embedder": embedder,
            "top_k": 5,
            "page": None,
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


@pytest.mark.parametrize("backend", ["claude_code", "bedrock"])
def test_query_exhausted_embedding_limit_uses_shared_error(monkeypatch, initialized_workspace, backend):
    """Real retrieval overflow must reach the CLI error route on either backend."""
    from botocore.exceptions import ClientError
    from graph_works_core.query import commands as q

    class Provider:
        def embed_query(self, text):
            raise ClientError(
                {
                    "Error": {
                        "Code": "ValidationException",
                        "Message": ("Too many input tokens. Max input tokens: 8192, request input token count: 12273"),
                    }
                },
                "InvokeModel",
            )

    (initialized_workspace / "okf" / "large.md").write_text("---\ntitle: Large\n---\n" + "x" * 50_000, encoding="utf-8")
    monkeypatch.setattr(q, "make_bedrock_embeddings", lambda *a, **kw: Provider())
    result = runner.invoke(
        app, ["query", "--query", "why", "--backend", backend, "--workspace", str(initialized_workspace)]
    )
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert result.stdout == ""
    assert "Error: " in result.stderr
    assert "amazon.titan" in result.stderr
    assert "large" in result.stderr
    assert "Traceback" not in result.stderr


def test_query_large_page_returns_normal_brief_json(monkeypatch, initialized_workspace):
    from graph_works_core.query import commands as q

    class Provider:
        def embed_query(self, text):
            assert len(text) <= 32_000
            return [1.0, 0.5]

    (initialized_workspace / "okf" / "large.md").write_text(
        "---\ntitle: Large\n---\n" + "word " * 11_000 + " uniquetailterm", encoding="utf-8"
    )
    monkeypatch.setattr(q, "make_bedrock_embeddings", lambda *a, **kw: Provider())
    result = runner.invoke(
        app, ["query", "--query", "uniquetailterm", "--json", "--workspace", str(initialized_workspace)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload) == {"query", "top_pages", "retrieval", "page", "refusal", "warnings"}
    assert payload["retrieval"] == "hybrid"
    page = next(p for p in payload["top_pages"] if p["path"] == "large")
    assert set(page) == {"path", "excerpt", "search_scores"}
    assert page["search_scores"]["bm25"] > 0


@pytest.mark.parametrize("backend,persistent", [("claude_code", True), ("bedrock", True), ("claude_code", False)])
def test_query_real_bedrock_adapter_logs_rejections_without_uncaught_error(
    monkeypatch, initialized_workspace, backend, persistent
):
    """Provider logging may contain a traceback even when the CLI handles recovery."""
    import io
    import logging
    import sys

    from botocore.exceptions import ClientError
    from graph_works_core.query import commands as q
    from langchain_aws import BedrockEmbeddings

    calls = []

    class Transport:
        def invoke_model(self, **kwargs):
            text = json.loads(kwargs["body"])["inputText"]
            calls.append(text)
            if persistent or len(text) > 16_000:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ValidationException",
                            "Message": (
                                "Too many input tokens. Max input tokens: 8192, request input token count: 12273"
                            ),
                        }
                    },
                    "InvokeModel",
                )
            return {"body": io.BytesIO(b'{"embedding": [1.0, 0.5]}')}

    def make_provider(model_id, *, region):
        # Install stderr logging inside CliRunner's capture, with test-local state.
        provider_logger = logging.getLogger("langchain_aws.embeddings.bedrock")
        monkeypatch.setattr(provider_logger, "handlers", [logging.StreamHandler(sys.stderr)])
        monkeypatch.setattr(provider_logger, "propagate", False)
        return BedrockEmbeddings(client=Transport(), model_id=model_id, region_name=region)

    (initialized_workspace / "okf" / "large.md").write_text(
        "---\ntitle: Large\n---\n" + "word " * 11_000, encoding="utf-8"
    )
    monkeypatch.setattr(q, "make_bedrock_embeddings", make_provider)
    result = runner.invoke(
        app, ["query", "--query", "word", "--backend", backend, "--json", "--workspace", str(initialized_workspace)]
    )
    assert "Error raised by inference endpoint" in result.stderr
    assert "Traceback" in result.stderr  # Logged by the provider, not an uncaught CLI exception.
    assert len(calls[0]) == 32_000
    if persistent:
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert result.stdout == ""
        assert "Error: Page large: Embedding input token limit" in result.stderr
        assert len(calls) == 15 and len(calls[-1]) == 1
    else:
        assert result.exit_code == 0, result.output
        assert result.exception is None
        assert json.loads(result.stdout)["top_pages"][0]["path"] == "large"
        assert [len(t) for t in calls] == [32_000, 16_000, 4]


def _concepts(root: Path) -> None:
    concepts = root / "okf" / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "auth.md").write_text("---\ntitle: Auth\n---\n\nToken exchange and refresh.\n", encoding="utf-8")
    (concepts / "storage.md").write_text("---\ntitle: Storage\n---\n\nBlob token buckets.\n", encoding="utf-8")
    (concepts / "session.md").write_text(
        "---\ntitle: Session\n---\n\nCookies; see [Auth](/concepts/auth.md).\n", encoding="utf-8"
    )


def test_query_page_pins_the_page_and_runs_lexically_without_an_embedder(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """`--page` reaches the core brief; an unresolvable embedder is a lexical brief, not an error."""
    _concepts(initialized_workspace)
    monkeypatch.setattr(query_module, "brief_embedder", lambda: None)

    result = runner.invoke(
        app,
        [
            "query",
            "--query",
            "token",
            "--page",
            "concepts/session",
            "--json",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["retrieval"] == "lexical"
    assert payload["page"] == "concepts/session"
    assert [p["path"] for p in payload["top_pages"]][:2] == ["concepts/session", "concepts/auth"]


def test_query_embedding_failure_is_a_lexical_brief_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Credentials missing at call time: the brief still answers, and says why it went lexical."""
    from graph_works_core.query import commands as q

    class NoCredentials:
        def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("no credentials")

    _concepts(initialized_workspace)
    monkeypatch.setattr(q, "make_bedrock_embeddings", lambda *a, **kw: NoCredentials())

    result = runner.invoke(app, ["query", "--query", "token", "--json", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["retrieval"] == "lexical"
    assert payload["warnings"] == ["embedding unavailable: RuntimeError: no credentials"]
    assert "Warning: embedding unavailable" in result.stderr


def test_query_unknown_page_is_an_error_exit(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    _concepts(initialized_workspace)
    monkeypatch.setattr(query_module, "brief_embedder", lambda: None)

    result = runner.invoke(
        app, ["query", "--query", "token", "--page", "concepts/nope", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: unknown page 'concepts/nope'\n"


def test_query_page_on_a_non_brief_backend_is_an_error(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    def fail_run_query(*args: object, **kwargs: object) -> object:
        raise AssertionError("run_query must not run when --page is refused")

    monkeypatch.setattr(query_module, "run_query", fail_run_query)
    monkeypatch.setattr(query_module, "default_embedder", object)

    result = runner.invoke(
        app,
        [
            "query",
            "--query",
            "why",
            "--page",
            "concepts/a",
            "--backend",
            "bedrock",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == 1
    assert result.stderr == "Error: --page needs the brief backend (claude_code)\n"
