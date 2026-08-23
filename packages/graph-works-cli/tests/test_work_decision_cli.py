"""Path-keyed decision commands and all-or-nothing JSON behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    epic_run = runner.invoke(
        app, ["work", "file", "--title", "Epic", "--kind", "Epic", "--summary", "d", "--workspace", str(root), "--json"]
    )
    epic = str(json.loads(epic_run.stdout)["path"])
    child_run = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Child",
            "--kind",
            "Bug",
            "--summary",
            "d",
            "--parent-path",
            epic,
            "--workspace",
            str(root),
            "--json",
        ],
    )
    child = str(json.loads(child_run.stdout)["path"])
    return root, epic, child


def add(root: Path, path: str, question: str = "q?") -> dict[str, object]:
    result = runner.invoke(
        app, ["work", "decision", "add", path, "--question", question, "--workspace", str(root), "--json"]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_add_uses_an_explicit_owner_and_requested_path(workspace: tuple[Path, str, str]) -> None:
    root, epic, child = workspace
    payload = add(root, child)
    assert payload["owner_path"] == epic
    assert payload["requested_path"] == child
    assert payload["applied"] is True and payload["rolled_back"] is False
    assert "epic_slug" not in payload and "resolved_from" not in payload


def test_list_counts_the_complete_ledger(workspace: tuple[Path, str, str]) -> None:
    root, epic, _child = workspace
    add(root, epic, "a?")
    result = runner.invoke(
        app, ["work", "decision", "list", epic, "--status", "open", "--workspace", str(root), "--json"]
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert [entry["question"] for entry in payload["entries"]] == ["a?"]
    assert payload["counts"]["total"] == 1


def test_answer_and_supersede_are_path_keyed(workspace: tuple[Path, str, str]) -> None:
    root, epic, _child = workspace
    decision_id = str(add(root, epic)["entry"]["id"])  # type: ignore[index]
    answered = runner.invoke(
        app, ["work", "decision", "answer", epic, decision_id, "--answer", "yes", "--workspace", str(root), "--json"]
    )
    assert json.loads(answered.stdout)["entry"]["status"] == "answered"
    superseded = runner.invoke(
        app,
        [
            "work",
            "decision",
            "supersede",
            epic,
            decision_id,
            "--question",
            "q2?",
            "--answer",
            "no",
            "--workspace",
            str(root),
            "--json",
        ],
    )
    assert json.loads(superseded.stdout)["superseded"] == decision_id


def test_refused_decision_emits_diagnostics_only(workspace: tuple[Path, str, str]) -> None:
    root, epic, _child = workspace
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "add",
            epic,
            "--question",
            "q?",
            "--status",
            "answered",
            "--workspace",
            str(root),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == "" and "refused" in result.stderr


def test_overturn_files_a_path_native_follow_up(workspace: tuple[Path, str, str]) -> None:
    root, epic, _child = workspace
    original = runner.invoke(
        app,
        [
            "work",
            "decision",
            "add",
            epic,
            "--question",
            "q?",
            "--status",
            "answered",
            "--answer",
            "a",
            "--workspace",
            str(root),
            "--json",
        ],
    )
    decision_id = json.loads(original.stdout)["entry"]["id"]
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "overturn",
            epic,
            decision_id,
            "--answer",
            "reverse",
            "--follow-up-title",
            "Undo shape",
            "--workspace",
            str(root),
            "--json",
        ],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["follow_up"]["path"] == "work/tech-debt-undo-shape"
    assert payload["follow_up_filed"] is True


def test_unknown_path_is_ambiguous_and_has_no_stdout(workspace: tuple[Path, str, str]) -> None:
    root, _epic, _child = workspace
    result = runner.invoke(
        app, ["work", "decision", "list", "work/feature-missing", "--workspace", str(root), "--json"]
    )
    assert result.exit_code == exit_codes.AMBIGUOUS and result.stdout == ""
