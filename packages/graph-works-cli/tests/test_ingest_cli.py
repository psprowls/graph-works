"""`gw ingest` — one source at the CLI boundary."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import pytest
from code_wiki_okf.config import ConfigError
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import ingest as ingest_module
from graph_works_core.ingest.commands import IngestResult
from models_io import BedrockAccessDenied, ProviderNotInstalled
from ruamel.yaml import YAML
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    """Create the smallest real initialized workspace for CLI boundary tests."""
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0
    return root


def _configure_repositories(workspace: Path, *repos: Path) -> None:
    """Merge the configured repositories into `workspace.yaml`'s `repositories` block."""
    yaml = YAML()
    yaml.preserve_quotes = True
    manifest_path = workspace / "workspace.yaml"
    with manifest_path.open(encoding="utf-8") as handle:
        data = yaml.load(handle)
    data["repositories"] = {f"repo-{index}": {"path": str(repo)} for index, repo in enumerate(repos, start=1)}
    data["ignore"] = []
    with manifest_path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)


def test_ingest_runs_one_source_against_the_first_configured_repo(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, tmp_path: Path
) -> None:
    """A wrong repo, clock, or gate would make an ingest unsafe or non-reproducible."""
    first_repo = tmp_path / "first-repo"
    second_repo = tmp_path / "second-repo"
    _configure_repositories(initialized_workspace, first_repo, second_repo)
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")
    gate = object()
    calls: list[dict[str, object]] = []

    def fake_state_gate_adapter(config: object) -> object:
        assert config.repos[0].path == first_repo  # type: ignore[attr-defined]
        return gate

    async def fake_run_ingest_source(*args: object, **kwargs: object) -> IngestResult:
        calls.append({"source": args[0], **kwargs})
        return IngestResult(
            ok=True,
            page="sources/demo.md",
            copy="sources/references/demo.md",
            title="Demo",
            source_kind="reference",
            entity_uri="pkg:demo",
            entity_page="packages/demo.md",
            written=("sources/demo.md", "sources/references/demo.md"),
            indexes_updated=("index.md",),
            proposals=(
                {
                    "lane": "concepts",
                    "title": "Demo concept",
                    "target": "concepts/demo.md",
                    "proposal": "Add the concept",
                    "status": "filed",
                },
            ),
        )

    monkeypatch.setattr(ingest_module, "state_gate_adapter", fake_state_gate_adapter)
    monkeypatch.setattr(ingest_module, "run_ingest_source", fake_run_ingest_source)

    result = runner.invoke(
        app,
        ["ingest", "--source", str(source), "--json", "--workspace", str(initialized_workspace)],
    )

    assert result.exit_code == 0
    assert calls[0]["source"] == source
    assert calls[0]["repo"] == first_repo
    assert calls[0]["by"] == "agent:graph-works-cli"
    assert calls[0]["state_gate"] is gate
    assert calls[0]["at"].tzinfo is UTC  # type: ignore[union-attr]
    assert calls[0]["today"] == calls[0]["at"].date()  # type: ignore[union-attr]
    assert json.loads(result.stdout) == {
        "ok": True,
        "page": "sources/demo.md",
        "copy": "sources/references/demo.md",
        "title": "Demo",
        "source_kind": "reference",
        "entity_uri": "pkg:demo",
        "entity_page": "packages/demo.md",
        "frontmatter_parsed": True,
        "written": ["sources/demo.md", "sources/references/demo.md"],
        "indexes_updated": ["index.md"],
        "proposals": [
            {
                "lane": "concepts",
                "title": "Demo concept",
                "target": "concepts/demo.md",
                "proposal": "Add the concept",
                "status": "filed",
            },
        ],
        "warnings": [],
        "refusals": [],
    }


def test_ingest_requires_a_configured_repository(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, tmp_path: Path
) -> None:
    """An ingest without a declared repository must fail rather than guess one."""
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")
    monkeypatch.setattr(
        ingest_module,
        "run_ingest_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("core must not run without a repository")),
    )

    result = runner.invoke(app, ["ingest", "--source", str(source), "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.NOT_IN_GIT_REPO
    assert "no repositories are configured" in result.stderr


def test_ingest_refusal_prints_its_payload_then_exits_one(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, tmp_path: Path
) -> None:
    """A primary-plan refusal must never be mistaken for optional degradation."""
    repo = tmp_path / "repo"
    _configure_repositories(initialized_workspace, repo)
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")

    async def fake_run_ingest_source(*args: object, **kwargs: object) -> IngestResult:
        return IngestResult(
            ok=False,
            page="sources/demo.md",
            copy="sources/references/demo.md",
            title="Demo",
            source_kind="doc",
            refusals=("sources/demo.md: target-exists: already ingested",),
        )

    monkeypatch.setattr(ingest_module, "run_ingest_source", fake_run_ingest_source)

    result = runner.invoke(
        app,
        ["ingest", "--source", str(source), "--json", "--workspace", str(initialized_workspace)],
    )

    assert result.exit_code == exit_codes.GENERIC
    assert json.loads(result.stdout)["refusals"] == ["sources/demo.md: target-exists: already ingested"]
    assert "ingest was refused" in result.stderr


def test_ingest_reports_optional_degradation_without_failing(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, tmp_path: Path
) -> None:
    """Frontmatter and suggestion fallbacks still leave a successful primary write."""
    repo = tmp_path / "repo"
    _configure_repositories(initialized_workspace, repo)
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")

    async def fake_run_ingest_source(*args: object, **kwargs: object) -> IngestResult:
        return IngestResult(
            ok=True,
            page="sources/demo.md",
            copy="sources/references/demo.md",
            title="Demo",
            source_kind="doc",
            frontmatter_parsed=False,
            proposal_status={"error": "planner unavailable"},
            proposals=({"target": "concepts/demo.md", "status": "filed"},),
        )

    monkeypatch.setattr(ingest_module, "run_ingest_source", fake_run_ingest_source)

    result = runner.invoke(app, ["ingest", "--source", str(source), "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert result.stdout == (
        "Page: sources/demo.md\nCopy: sources/references/demo.md\nProposal: filed concepts/demo.md\n"
    )
    assert "frontmatter was not parsed" in result.stderr
    assert "suggestion phase degraded: planner unavailable" in result.stderr


def test_ingest_reports_apply_time_suggestion_degradation_in_json_and_stderr(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, tmp_path: Path
) -> None:
    """Optional proposal write failures must be visible without failing ingest."""
    repo = tmp_path / "repo"
    _configure_repositories(initialized_workspace, repo)
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")

    async def fake_run_ingest_source(*args: object, **kwargs: object) -> IngestResult:
        return IngestResult(
            ok=True,
            page="sources/demo.md",
            copy="sources/references/demo.md",
            title="Demo",
            source_kind="doc",
            proposal_status={
                "failed": ["Keep: mkdir-error", "Merge: write-error"],
                "errored": ["Explain: RuntimeError"],
            },
        )

    monkeypatch.setattr(ingest_module, "run_ingest_source", fake_run_ingest_source)

    result = runner.invoke(
        app,
        ["ingest", "--source", str(source), "--json", "--workspace", str(initialized_workspace)],
    )

    expected = [
        "suggestion apply failed: Keep: mkdir-error",
        "suggestion apply failed: Merge: write-error",
        "suggestion apply errored: Explain: RuntimeError",
    ]
    assert result.exit_code == 0
    assert json.loads(result.stdout)["warnings"] == expected
    assert result.stderr == "".join(f"Warning: {warning}\n" for warning in expected)


@pytest.mark.parametrize(
    "provider_error",
    (
        ProviderNotInstalled(
            "The models-io[bedrock] extra is not installed.\n  Install it with: pip install 'models-io[bedrock]'"
        ),
        BedrockAccessDenied(
            "Bedrock access denied.\n"
            "  Model ARN attempted: arn:aws:bedrock:us-east-1::foundation-model/demo\n"
            "  IAM action required: bedrock:InvokeModel"
        ),
    ),
    ids=("provider-not-installed", "access-denied"),
)
def test_ingest_reports_provider_failures_without_stdout_or_traceback(
    monkeypatch: pytest.MonkeyPatch,
    initialized_workspace: Path,
    tmp_path: Path,
    provider_error: Exception,
) -> None:
    """A model provider failure must be one clean CLI diagnostic."""
    repo = tmp_path / "repo"
    _configure_repositories(initialized_workspace, repo)
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")

    async def fail(*args: object, **kwargs: object) -> IngestResult:
        raise provider_error

    monkeypatch.setattr(ingest_module, "run_ingest_source", fail)

    result = runner.invoke(app, ["ingest", "--source", str(source), "--workspace", str(initialized_workspace)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {provider_error}\n"
    assert "Traceback" not in result.stderr


def test_ingest_rejects_batch_flags() -> None:
    """The completed command accepts one source only, not deferred batch controls."""
    assert runner.invoke(app, ["ingest", "--source", "one.md", "--limit", "2"]).exit_code == 2
    assert runner.invoke(app, ["ingest", "--source", "one.md", "--all"]).exit_code == 2


def test_ingest_reports_unreadable_configuration_before_any_model_call(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, tmp_path: Path
) -> None:
    """Configuration is read before the LLM; its failure must not read as an ingest failure."""
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")

    def fail(
        _bundle_root: object,
        *,
        config_path: object = None,
        graph_dir: object = None,
        declarations_dir: object = None,
    ) -> object:
        raise ConfigError("config.yaml is malformed")

    monkeypatch.setattr(ingest_module, "load_config", fail)

    result = runner.invoke(app, ["ingest", "--source", str(source), "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert "Error: config.yaml is malformed" in result.stderr
