"""Characterization of the 2026-09-17 silent double advance.

Pins today's behaviour of `gw work advance` so the fix has a committed reproduction to
flip. See the spike's findings at
`work/epic-auto-drive-reliability/children/spike-reproduce-silent-double-advance`
(`references/01-design.md`), and the Bug it re-scopes,
`work/epic-auto-drive-reliability/children/bug-gw-work-advance-can-silently-advance`.

Assertions marked `DEFECT` record the wrong outcome on purpose. Whichever fix the Bug
chooses (a from-phase precondition, refusing a dangling plan stamp, or routing that
requires effort before writing), it changes at least one of them. Update those
assertions *with* the fix, never before it. The rollback test is not a defect: it pins
the atomicity the Bug's original premise asked for, which already holds.
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
    return path


def test_a_failed_design_to_plan_advance_rolls_back_byte_for_byte(workspace: Path) -> None:
    """H1 (partial apply) refuted: the failure writes nothing."""
    path = _epic_at_design(workspace)
    before = _page(workspace, path).read_bytes()

    code, _, stderr = _advance(workspace, path)

    assert code != 0
    assert "'effort' is a required property" in stderr
    assert "'affects' is a required property" not in stderr
    assert "apply was incomplete" in stderr
    assert _page(workspace, path).read_bytes() == before


def test_a_retried_effort_advance_moves_a_second_phase_onto_a_missing_plan(workspace: Path) -> None:
    """H2 reproduced: two identical `--effort large` calls walk design -> plan -> execute."""
    path = _epic_at_design(workspace)
    assert _advance(workspace, path)[0] != 0  # the effort-less first attempt

    code, _, stderr = _advance(workspace, path, "--effort", "large")
    assert code == 0, stderr
    fm = load(_page(workspace, path)).fm_data()
    assert (fm["phase"], fm["effort"], fm["status"]) == ("plan", "large", "stable")

    # The retry: byte-for-byte the same command line.
    code, _, stderr = _advance(workspace, path, "--effort", "large")
    fm = load(_page(workspace, path)).fm_data()
    plan_sources = [s for s in fm.get("sources", []) if s["id"] == "plan"]

    assert code == 0, stderr  # DEFECT: a retry is not idempotent and is not refused
    assert (fm["phase"], fm["work_status"]) == ("execute", "accepted")  # DEFECT: plan stage skipped
    assert [s["resource"] for s in plan_sources] == [f"/{path}/references/02-plan.md"]
    assert not (workspace / "okf" / path / "references" / "02-plan.md").exists()  # DEFECT: dangling
    assert "02-plan.md" not in stderr  # DEFECT: the dangling stamp is not even warned about
