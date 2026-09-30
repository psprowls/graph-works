"""`gw work obligation add`: dry run, apply, and JSON projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli.cli import app
from graph_works_cli.work_cli import obligation
from graph_works_core.workspace.errors import WorkspaceError
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def item(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    filed = runner.invoke(
        app,
        ["work", "file", "--title", "Feat", "--kind", "Feature", "--summary", "d", "--workspace", str(root), "--json"],
    )
    assert filed.exit_code == 0, filed.output
    return root, str(json.loads(filed.stdout)["path"])


def _page(root: Path, path: str) -> Path:
    return next(root.rglob(f"{path.rsplit('/', 1)[-1]}.md"))


def test_default_is_a_dry_run(item: tuple[Path, str]) -> None:
    root, path = item
    before = _page(root, path).read_bytes()
    result = runner.invoke(
        app, ["work", "obligation", "add", path, "--text", "Tag it", "--workspace", str(root), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["applied"] is False and payload["changed"] is True
    assert _page(root, path).read_bytes() == before


def test_apply_writes_and_reports_the_list(item: tuple[Path, str]) -> None:
    root, path = item
    result = runner.invoke(
        app, ["work", "obligation", "add", path, "--text", "Tag it", "--apply", "--workspace", str(root), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["applied"] is True
    assert payload["obligations"][0]["origin"] == "deferred"
    assert "finish_obligations:" in _page(root, path).read_text(encoding="utf-8")


def test_text_output_lists_the_obligations(item: tuple[Path, str]) -> None:
    root, path = item
    result = runner.invoke(app, ["work", "obligation", "add", path, "--text", "Tag it", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    assert "[deferred] Tag it" in result.stdout
    assert "dry run" in result.stdout


def test_blank_text_is_a_refusal_exit(item: tuple[Path, str]) -> None:
    root, path = item
    before = _page(root, path).read_bytes()
    result = runner.invoke(
        app, ["work", "obligation", "add", path, "--text", " ", "--apply", "--workspace", str(root), "--json"]
    )
    assert result.exit_code != 0
    assert _page(root, path).read_bytes() == before


@pytest.mark.parametrize("error", [WorkspaceError("bad workspace"), OSError("disk error")])
def test_runtime_errors_are_reported(item: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    root, path = item

    def fail(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(obligation, "run_obligation_add", fail)
    result = runner.invoke(app, ["work", "obligation", "add", path, "--text", "Tag it", "--workspace", str(root)])
    assert result.exit_code != 0
    assert str(error) in result.output


def test_incomplete_apply_reports_warning_and_fails(item: tuple[Path, str], monkeypatch: pytest.MonkeyPatch) -> None:
    root, path = item
    monkeypatch.setattr(obligation, "run_obligation_add", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        obligation.wire_work,
        "obligation_payload",
        lambda result: {
            "path": path,
            "obligations": [],
            "before": [],
            "changed": True,
            "applied": True,
            "rolled_back": True,
            "failures": ["write failed"],
            "commit": None,
            "warnings": ["rollback completed"],
            "refusal": None,
        },
    )
    result = runner.invoke(
        app, ["work", "obligation", "add", path, "--text", "Tag it", "--apply", "--workspace", str(root)]
    )
    assert result.exit_code != 0
    assert "rollback completed" in result.output
    assert "incomplete" in result.output
