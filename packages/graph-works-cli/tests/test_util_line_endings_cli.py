"""`gw util line-endings [--fix] [--json]` -- CRLF reaccumulation detector/repairer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    return root


def _bundle_dir(workspace: Path) -> Path:
    return workspace / "okf"


def test_a_clean_bundle_reports_nothing_and_exits_zero(initialized_workspace: Path) -> None:
    result = runner.invoke(app, ["util", "line-endings", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.SUCCESS, result.output


def test_a_crlf_member_is_listed_with_its_count_and_exits_nonzero(initialized_workspace: Path) -> None:
    target = _bundle_dir(initialized_workspace) / "index.md"
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))

    result = runner.invoke(app, ["util", "line-endings", "--workspace", str(initialized_workspace)])

    assert result.exit_code != exit_codes.SUCCESS
    assert "index.md" in result.stdout


def test_json_output_carries_every_finding(initialized_workspace: Path) -> None:
    target = _bundle_dir(initialized_workspace) / "index.md"
    original = target.read_bytes().replace(b"\n", b"\r\n")
    target.write_bytes(original)

    result = runner.invoke(app, ["util", "line-endings", "--workspace", str(initialized_workspace), "--json"])

    payload = json.loads(result.stdout)
    assert payload["fixed"] is False
    assert {entry["member"] for entry in payload["findings"]} == {"index.md"}
    assert payload["findings"][0]["crlf_count"] == original.count(b"\r\n")


def test_fix_rewrites_the_member_to_lf_and_leaves_it_clean_on_rerun(initialized_workspace: Path) -> None:
    target = _bundle_dir(initialized_workspace) / "index.md"
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))

    fixed_run = runner.invoke(app, ["util", "line-endings", "--fix", "--workspace", str(initialized_workspace)])
    assert fixed_run.exit_code == exit_codes.SUCCESS, fixed_run.output
    assert b"\r\n" not in target.read_bytes()

    clean_run = runner.invoke(app, ["util", "line-endings", "--workspace", str(initialized_workspace)])
    assert clean_run.exit_code == exit_codes.SUCCESS, clean_run.output


def test_line_endings_appears_in_util_help() -> None:
    result = runner.invoke(app, ["util", "--help"])

    assert result.exit_code == 0
    assert "line-endings" in result.stdout


def test_line_endings_appears_in_describe_surface() -> None:
    result = runner.invoke(app, ["util", "describe-surface", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "util line-endings" in json.dumps(payload)
