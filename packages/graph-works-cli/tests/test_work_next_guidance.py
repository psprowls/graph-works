"""`gw work next --file`: auto / path / skip, text summary, exit code unaffected."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import Result
from graph_works_cli.cli import app
from graph_works_core.guidance import assembly
from typer.testing import CliRunner

runner = CliRunner()
ITEM = "work/feature-g"


def _seed(workspace: Path, *, ledger: bool = True) -> Path:
    bundle = workspace / "okf"
    page = bundle / f"{ITEM}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\ntitle: G\ndescription: d\nstatus: stable\nwork_status: open\nphase: design\n"
        "effort: medium\nopened: 2026-09-01\nupdated: 2026-09-01\naffects:\n- packages/a\n---\n\n## Plan\n",
        encoding="utf-8",
        newline="\n",
    )
    if ledger:
        path = bundle / ITEM / "references" / "00-decisions.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Decisions\n\n## D-001 — Which?\nstatus: answered\n\n**Answer:** This.\n", encoding="utf-8", newline="\n"
        )
    return bundle / ITEM / "references" / "guidance-design.md"


def _run(workspace: Path, *extra: str) -> Result:
    return runner.invoke(app, ["work", "next", ITEM, "--workspace", str(workspace), *extra])


def test_default_auto_writes_and_reports_the_file(workspace: Path) -> None:
    target = _seed(workspace)
    result = _run(workspace, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["guidance_file"] == str(target) and target.exists()
    assert [e["id"] for e in payload["guidance"]] == ["D-001"]
    assert payload["guidance"][0]["kind"] == "ledger"


def test_empty_file_skips_the_write(workspace: Path) -> None:
    target = _seed(workspace)
    payload = json.loads(_run(workspace, "--json", "--file", "").stdout)
    assert payload["guidance_file"] is None and len(payload["guidance"]) == 1
    assert not target.exists()


def test_explicit_path(workspace: Path, tmp_path: Path) -> None:
    _seed(workspace)
    out = tmp_path / "g.md"
    payload = json.loads(_run(workspace, "--json", "--file", str(out)).stdout)
    assert payload["guidance_file"] == str(out) and out.exists()


def test_a_relative_path_is_reported_absolute(workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(workspace)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    payload = json.loads(_run(workspace, "--json", "--file", "out/g.md").stdout)
    expected = (cwd / "out" / "g.md").resolve()
    assert payload["guidance_file"] == str(expected) and expected.exists()


def test_text_mode_prints_one_summary_line(workspace: Path) -> None:
    target = _seed(workspace)
    result = _run(workspace)
    assert result.exit_code == 0
    assert "guidance: 1 entries, " in result.stdout and f"→ {target}" in result.stdout


def test_text_mode_none(workspace: Path) -> None:
    _seed(workspace, ledger=False)
    result = _run(workspace, "--file", "")
    assert "guidance: none" in result.stdout


def test_the_next_alias_takes_file_too(workspace: Path) -> None:
    _seed(workspace)
    result = runner.invoke(app, ["next", ITEM, "--workspace", str(workspace), "--json", "--file", ""])
    assert result.exit_code == 0 and json.loads(result.stdout)["guidance_file"] is None


def test_guidance_warnings_never_change_the_exit_code(workspace: Path) -> None:
    _seed(workspace)  # no code graph -> a closure warning
    result = _run(workspace, "--json")
    payload = json.loads(result.stdout)
    assert payload["guidance_warnings"] and result.exit_code == 0


def test_a_tokenizer_failure_never_changes_the_exit_code(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _seed(workspace)
    baseline = _run(workspace, "--json", "--file", "")
    assert baseline.exit_code == 0, baseline.output

    def no_tokenizer(text: str) -> int:
        raise OSError("ProxyError")

    monkeypatch.setattr(assembly, "count_tokens", no_tokenizer)
    result = _run(workspace, "--json")
    assert result.exit_code == baseline.exit_code, result.output
    payload = json.loads(result.stdout)
    assert payload["guidance"] == [] and payload["guidance_file"] is None
    assert payload["guidance_warnings"] == ["guidance unavailable: token counting failed: ProxyError"]
    assert payload["action"] == json.loads(baseline.stdout)["action"]
    assert not target.exists()
