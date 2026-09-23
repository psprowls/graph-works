"""Characterization of the 2026-09-17 silent double advance.

Pins today's behaviour of `gw work advance` so the fix has a committed reproduction to
flip. See the spike's findings at
`work/epic-auto-drive-reliability/children/spike-reproduce-silent-double-advance`
(`references/01-design.md`), and the Bug it re-scopes,
`work/epic-auto-drive-reliability/children/bug-gw-work-advance-can-silently-advance`.

Assertions marked `DEFECT` record the wrong outcome on purpose. Fix 3
(routing requires effort before writing, so the planner refuses `effort-required`
before any write) instead turns the rollback test's first call into that refusal,
which flips its `DEFECT`-marked message assertions there. Fix 1 (a from-phase
precondition, opt-in via `--from`/an expected-phase argument) flips nothing in this
file, since neither test ever passes it; that fix needs its own retry-with-expected-
phase test. Update those assertions *with* the fix, never before it. The rollback
test's byte-for-byte equality is not a defect under any of the three: it pins the
atomicity the Bug's original premise asked for, which already holds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli.cli import app
from okf_io import load
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    return root


def _file_epic(workspace: Path) -> str:
    # `--affects` matters: without it the design->plan postcondition also fails on
    # `affects`, and every `--effort` retry keeps failing, so no double advance occurs.
    result = runner.invoke(
        app,
        [
            "work", "file", "--title", "Big Thing", "--kind", "Epic", "--summary", "d",
            "--affects", "packages/x", "--workspace", str(workspace), "--json",
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    return str(json.loads(result.stdout)["path"])


def _advance(workspace: Path, path: str, *extra: str) -> tuple[int, str, str]:
    result = runner.invoke(app, ["work", "advance", path, "--no-infer-worktree", *extra, "--workspace", str(workspace)])
    return result.exit_code, result.stdout, result.stderr


def _page(workspace: Path, path: str) -> Path:
    return workspace / "okf" / f"{path}.md"


def _epic_at_design(workspace: Path) -> str:
    path = _file_epic(workspace)
    code, _, stderr = _advance(workspace, path)  # the dispatch transition: None -> design
    assert code == 0, stderr
    assert load(_page(workspace, path)).fm_data()["phase"] == "design"
    artifact = workspace / "okf" / path / "references" / "01-design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Design\n", encoding="utf-8", newline="")
    return path


def test_a_failed_design_to_plan_advance_rolls_back_byte_for_byte(workspace: Path) -> None:
    """The byte-equality below is the atomicity pin (H1 refuted): the failure writes
    nothing, and that already holds regardless of which fix the Bug chooses."""
    path = _epic_at_design(workspace)
    before = _page(workspace, path).read_bytes()

    code, _, stderr = _advance(workspace, path)

    assert code != 0
    # DEFECT (fix 3): postcondition failure instead of an effort-required refusal
    assert "'effort' is a required property" in stderr
    assert "'affects' is a required property" not in stderr
    # DEFECT (fix 3): postcondition failure instead of an effort-required refusal
    assert "apply was incomplete" in stderr
    assert _page(workspace, path).read_bytes() == before


def test_a_retried_effort_advance_refuses_without_a_plan(workspace: Path) -> None:
    """A repeated completion cannot walk past plan without its artifact."""
    path = _epic_at_design(workspace)
    assert _advance(workspace, path)[0] != 0  # the effort-less first attempt

    code, _, stderr = _advance(workspace, path, "--effort", "large")
    assert code == 0, stderr
    fm = load(_page(workspace, path)).fm_data()
    assert (fm["phase"], fm["effort"], fm["status"]) == ("plan", "large", "stable")
    before = _page(workspace, path).read_bytes()

    # The retry: byte-for-byte the same command line.
    code, _, stderr = _advance(workspace, path, "--effort", "large")
    fm = load(_page(workspace, path)).fm_data()
    plan_sources = [s for s in fm.get("sources", []) if s["id"] == "plan"]

    assert code != 0
    assert "artifact-missing" in stderr and "02-plan.md" in stderr
    assert (fm["phase"], fm["work_status"]) == ("plan", "open")
    assert plan_sources == []
    assert _page(workspace, path).read_bytes() == before


def test_guarded_retry_refuses_even_with_a_real_plan(workspace: Path) -> None:
    path = _epic_at_design(workspace)
    refs = workspace / "okf" / path / "references"
    refs.mkdir(parents=True, exist_ok=True)
    for filename in ("01-design.md", "02-plan.md"):
        (refs / filename).write_text("# Produced artifact\n", encoding="utf-8", newline="")
    args = ("--from", "design", "--effort", "large")
    code, _, stderr = _advance(workspace, path, *args)
    assert code == 0, stderr
    before = _page(workspace, path).read_bytes()
    for extra in ((), ("--dry-run",), ("--json",)):
        code, stdout, stderr = _advance(workspace, path, *args, *extra)
        assert code != 0
        if extra == ("--json",):
            payload = json.loads(stdout)["error"]["payload"]
            assert payload["refusal"]["reason"] == "phase-mismatch"
        else:
            assert "phase-mismatch" in stderr
        assert _page(workspace, path).read_bytes() == before


@pytest.mark.parametrize("value", ["bogus", "None", ""])
def test_from_rejects_invalid_cli_choices(workspace: Path, value: str) -> None:
    path = _epic_at_design(workspace)
    code, _, _ = _advance(workspace, path, "--from", value)
    assert code == 2


def test_from_none_matches_absent_phase_and_omission_is_unchecked(workspace: Path) -> None:
    path = _file_epic(workspace)
    code, _, stderr = _advance(workspace, path, "--from", "none")
    assert code == 0, stderr
    artifact = workspace / "okf" / path / "references" / "01-design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Design\n", encoding="utf-8", newline="")
    code, _, stderr = _advance(workspace, path, "--effort", "large")
    assert code == 0, stderr
