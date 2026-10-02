"""`gw wiki section write`: plan by default, `--apply` writes, refusals exit non-zero."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import section as section_module
from graph_works_core.wiki_page import SectionWriteRun
from graph_works_core.workspace.errors import WorkspaceError
from typer.testing import CliRunner

runner = CliRunner()
PAGE = "docs/explanations/x"
TEXT = "---\ntype: Explanation\ntitle: X\ndescription: d\nupdated: 2026-01-01\n---\n\n## Context\n\nold\n"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    page = root / "okf" / f"{PAGE}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(TEXT, encoding="utf-8", newline="")
    return root


def _args(root: Path, *extra: str) -> list[str]:
    return ["wiki", "section", "write", PAGE, "--heading", "Context", "--workspace", str(root), *extra]


def test_plan_reads_stdin_and_writes_nothing(workspace: Path) -> None:
    page = workspace / "okf" / f"{PAGE}.md"
    result = runner.invoke(app, _args(workspace), input="new\n")
    assert result.exit_code == 0, result.output
    assert result.stdout == f"would write {PAGE}.md ## Context\n"
    assert page.read_text(encoding="utf-8") == TEXT


def test_apply_from_a_body_file_prints_the_projection(workspace: Path, tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("new\n", encoding="utf-8")
    result = runner.invoke(app, _args(workspace, "--body-file", str(body), "--apply", "--json"))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["applied"] is True and payload["written"] == [f"{PAGE}.md"]
    assert payload["before"] == "\nold\n" and payload["after"] == "\nnew\n\n"
    text = (workspace / "okf" / f"{PAGE}.md").read_text(encoding="utf-8")
    # The clock is the CLI's; the serve twin test pins the exact date.
    assert "updated: 2026-01-01\n" not in text and "## Context\n\nnew\n" in text


def test_apply_text_output(workspace: Path) -> None:
    result = runner.invoke(app, _args(workspace, "--apply"), input="new")
    assert result.exit_code == 0, result.output
    assert result.stdout == f"wrote {PAGE}.md ## Context\n"


@pytest.mark.parametrize(
    ("page", "heading", "reason", "refusal"),
    [
        ("docs/explanations/nope", "Context", "unresolved", "unknown-page"),
        (PAGE, "Missing", "refused", "missing-section"),
    ],
)
def test_refusals_emit_the_envelope(workspace: Path, page: str, heading: str, reason: str, refusal: str) -> None:
    args = ["wiki", "section", "write", page, "--heading", heading, "--workspace", str(workspace), "--json"]
    result = runner.invoke(app, args, input="x")
    assert result.exit_code != 0
    error = json.loads(result.stdout)["error"]
    assert error["reason"] == reason and error["command"] == "wiki section write"
    assert error["payload"]["refusal"] == refusal


def test_incomplete_apply_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = SectionWriteRun(PAGE, "Context", "\nold\n", "\nnew\n", None, applied=True, failures=("x.md: io -- denied",))
    monkeypatch.setattr(section_module, "run_section_write", lambda *_a, **_k: run)
    result = runner.invoke(app, _args(workspace, "--apply", "--json"), input="new")
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["reason"] == "incomplete-apply"


def test_workspace_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_a: object, **_k: object) -> SectionWriteRun:
        raise WorkspaceError("bad declarations")

    monkeypatch.setattr(section_module, "run_section_write", broken)
    result = runner.invoke(app, _args(workspace, "--json"), input="new")
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["reason"] == "workspace"


def test_missing_body_file_is_an_io_error(workspace: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, _args(workspace, "--body-file", str(tmp_path / "absent.md"), "--json"))
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["reason"] == "io"
