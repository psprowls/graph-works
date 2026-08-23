"""Path-native CLI acceptance over filing, routing, advancing, and linting."""

import json
from pathlib import Path

from typer.testing import CliRunner
from work_tracker_okf.cli import app

runner = CliRunner()
TODAY = "2026-08-22"
PATH = "work/tech-debt-compose-the-cli"
REFERENCES = f"{PATH}/references"


def _run(*args: str) -> None:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, f"{args}\n{result.output}\n{result.exception!r}"


def test_the_path_native_surface_ends_at_zero_errors(tmp_path: Path) -> None:
    root = str(tmp_path)
    _run("init", root, "--today", TODAY)
    _run(
        "file",
        root,
        "--type",
        "TechDebt",
        "--title",
        "Compose the CLI",
        "--description",
        "Wire the commands over canonical paths.",
        "--affects",
        "packages/work-tracker-okf",
        "--today",
        TODAY,
    )
    page = tmp_path / f"{PATH}.md"
    assert page.is_file()

    result = runner.invoke(app, ["next", root, PATH, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path"] == PATH
    assert payload["dispatch"] == {"stage": "design", "variant": "exploration"}

    _run("advance", root, PATH, "--today", TODAY)
    artifact = tmp_path / REFERENCES / "01-design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Design — compose the CLI\n", encoding="utf-8")
    _run("advance", root, PATH, "--today", TODAY, "--effort", "small")
    text = page.read_text(encoding="utf-8")
    assert "id: design" in text
    assert f"/{REFERENCES}/01-design.md" in text

    _run("advance", root, PATH, "--today", TODAY, "--owner", "fixture-author")
    _run("advance", root, PATH, "--today", TODAY)
    _run("advance", root, PATH, "--today", TODAY, "--resolved-in", "abc1234")
    text = page.read_text(encoding="utf-8")
    assert "phase: done" in text
    assert "work_status: resolved" in text
    assert "workflow" + "_status" not in text

    result = runner.invoke(app, ["lint", root, "--today", TODAY, "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert [finding for finding in report["findings"] if finding["severity"] == "error"] == []


def test_missing_canonical_artifact_is_a_warning_not_a_write_refusal(tmp_path: Path) -> None:
    root = str(tmp_path)
    _run("init", root, "--today", TODAY)
    _run(
        "file",
        root,
        "--type",
        "TechDebt",
        "--title",
        "Compose the CLI",
        "--description",
        "d",
        "--affects",
        "packages/work-tracker-okf",
        "--today",
        TODAY,
    )
    _run("advance", root, PATH, "--today", TODAY)
    _run("advance", root, PATH, "--today", TODAY, "--effort", "small")
    result = runner.invoke(app, ["lint", root, "--today", TODAY, "--json"])
    assert result.exit_code == 0
    codes = {finding["code"] for finding in json.loads(result.stdout)["findings"]}
    assert "structure.source-missing" in codes
