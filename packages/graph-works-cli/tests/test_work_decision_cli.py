"""`gw work decision` — the epic-owned ledger at the CLI boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.work_cli import decision as decision_module
from typer.testing import CliRunner

runner = CliRunner()

EPIC = "2026-08-01-epic-e"
CHILD = "2026-08-02-feature-a"

ITEM = """---
type: {type}
title: {slug}
description: d
status: stable
workflow_status: open
phase: design
effort: medium
opened: 2026-08-01
updated: 2026-08-01
affects:
- packages/a
{extra}---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    layout = decision_module.resolve_workspace(str(root))
    work_dir = layout.bundle_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / f"{EPIC}.md").write_text(ITEM.format(type="Epic", slug=EPIC, extra=""), encoding="utf-8")
    (work_dir / f"{CHILD}.md").write_text(
        ITEM.format(type="Feature", slug=CHILD, extra=f"parent: {EPIC}\n"), encoding="utf-8"
    )
    return root


def test_add_appends_to_the_owning_epics_ledger(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "add",
            EPIC,
            "--question",
            "Which store?",
            "--status",
            "assumed",
            "--answer",
            "sqlite",
            "--if-wrong",
            "re-plan storage",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["epic_slug"] == EPIC
    assert payload["entry"]["status"] == "assumed"
    assert payload["written"] is True


def test_a_child_slug_resolves_to_its_epic_and_reports_the_redirect(workspace: Path) -> None:
    result = runner.invoke(
        app,
        ["work", "decision", "add", CHILD, "--question", "q?", "--workspace", str(workspace), "--json"],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["epic_slug"] == EPIC
    assert payload["resolved_from"] == CHILD


def test_list_counts_the_whole_ledger_not_the_filtered_slice(workspace: Path) -> None:
    runner.invoke(
        app, ["work", "decision", "add", EPIC, "--question", "a?", "--status", "open", "--workspace", str(workspace)]
    )
    runner.invoke(
        app,
        [
            "work",
            "decision",
            "add",
            EPIC,
            "--question",
            "b?",
            "--status",
            "assumed",
            "--answer",
            "guess",
            "--if-wrong",
            "re-plan",
            "--workspace",
            str(workspace),
        ],
    )
    result = runner.invoke(
        app, ["work", "decision", "list", EPIC, "--status", "open", "--workspace", str(workspace), "--json"]
    )
    payload = json.loads(result.stdout)
    assert [entry["question"] for entry in payload["entries"]] == ["a?"]
    assert payload["counts"] == {
        "answered": 0,
        "assumed": 1,
        "open": 1,
        "superseded": 0,
        "invalid": 0,
        "total": 2,
    }


def test_answer_flips_an_open_decision(workspace: Path) -> None:
    added = runner.invoke(
        app, ["work", "decision", "add", EPIC, "--question", "q?", "--workspace", str(workspace), "--json"]
    )
    decision_id = json.loads(added.stdout)["entry"]["id"]
    result = runner.invoke(
        app,
        ["work", "decision", "answer", EPIC, decision_id, "--answer", "yes", "--workspace", str(workspace), "--json"],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert json.loads(result.stdout)["entry"]["status"] == "answered"


def test_supersede_retires_the_old_id_and_records_the_replacement(workspace: Path) -> None:
    added = runner.invoke(
        app,
        [
            "work",
            "decision",
            "add",
            EPIC,
            "--question",
            "q?",
            "--status",
            "answered",
            "--answer",
            "a",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    decision_id = json.loads(added.stdout)["entry"]["id"]
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "supersede",
            EPIC,
            decision_id,
            "--question",
            "q2?",
            "--answer",
            "b",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["superseded"] == decision_id
    assert payload["entry"]["question"] == "q2?"


def test_overturn_files_the_follow_up_alongside_the_replacement(workspace: Path) -> None:
    added = runner.invoke(
        app,
        [
            "work",
            "decision",
            "add",
            EPIC,
            "--question",
            "q?",
            "--status",
            "answered",
            "--answer",
            "a",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    decision_id = json.loads(added.stdout)["entry"]["id"]
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "overturn",
            EPIC,
            decision_id,
            "--answer",
            "the reversal",
            "--follow-up-title",
            "Undo the landed shape",
            "--follow-up-kind",
            "TechDebt",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["superseded"] == decision_id
    assert payload["follow_up"]["slug"].endswith("-tech-debt-undo-the-landed-shape")


def test_a_slug_with_no_epic_ancestor_exits_ambiguous(workspace: Path) -> None:
    layout = decision_module.resolve_workspace(str(workspace))
    orphan = "2026-08-03-feature-orphan"
    (layout.bundle_dir / "work" / f"{orphan}.md").write_text(
        ITEM.format(type="Feature", slug=orphan, extra=""), encoding="utf-8"
    )
    result = runner.invoke(app, ["work", "decision", "list", orphan, "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.AMBIGUOUS


def _write_orphan(workspace: Path) -> str:
    layout = decision_module.resolve_workspace(str(workspace))
    orphan = "2026-08-03-feature-orphan"
    (layout.bundle_dir / "work" / f"{orphan}.md").write_text(
        ITEM.format(type="Feature", slug=orphan, extra=""), encoding="utf-8"
    )
    return orphan


def test_config_error_on_overturn_maps_to_schema_mismatch(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path, **_kwargs: object) -> object:
        raise decision_module.ConfigError("bad declarations")

    monkeypatch.setattr(decision_module, "load_config", boom)
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "overturn",
            EPIC,
            "D-001",
            "--answer",
            "a",
            "--follow-up-title",
            "t",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "bad declarations" in result.stderr


def test_config_os_error_on_overturn_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(decision_module, "load_config", boom)
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "overturn",
            EPIC,
            "D-001",
            "--answer",
            "a",
            "--follow-up-title",
            "t",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_add_with_answered_status_and_no_answer_is_refused(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "add",
            EPIC,
            "--question",
            "q?",
            "--status",
            "answered",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "refused" in result.stderr
    assert "ledger:" in result.stdout


def test_add_unknown_epic_ancestor_exits_ambiguous(workspace: Path) -> None:
    orphan = _write_orphan(workspace)
    result = runner.invoke(app, ["work", "decision", "add", orphan, "--question", "q?", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.AMBIGUOUS


def test_add_os_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(decision_module.work, "run_decision_add", boom)
    result = runner.invoke(app, ["work", "decision", "add", EPIC, "--question", "q?", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_list_os_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(decision_module.work, "run_decision_list", boom)
    result = runner.invoke(app, ["work", "decision", "list", EPIC, "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_list_human_output_renders_entries_and_counts(workspace: Path) -> None:
    runner.invoke(
        app, ["work", "decision", "add", EPIC, "--question", "q?", "--status", "open", "--workspace", str(workspace)]
    )
    result = runner.invoke(app, ["work", "decision", "list", EPIC, "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert "ledger:" in result.stdout
    assert "q?" in result.stdout
    assert "counts:" in result.stdout


def test_answer_unknown_epic_ancestor_exits_ambiguous(workspace: Path) -> None:
    orphan = _write_orphan(workspace)
    result = runner.invoke(
        app, ["work", "decision", "answer", orphan, "D-001", "--answer", "a", "--workspace", str(workspace)]
    )
    assert result.exit_code == exit_codes.AMBIGUOUS


def test_answer_os_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(decision_module.work, "run_decision_answer", boom)
    result = runner.invoke(
        app, ["work", "decision", "answer", EPIC, "D-001", "--answer", "a", "--workspace", str(workspace)]
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_supersede_unknown_epic_ancestor_exits_ambiguous(workspace: Path) -> None:
    orphan = _write_orphan(workspace)
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "supersede",
            orphan,
            "D-001",
            "--question",
            "q2?",
            "--answer",
            "a",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.AMBIGUOUS


def test_supersede_os_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(decision_module.work, "run_decision_supersede", boom)
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "supersede",
            EPIC,
            "D-001",
            "--question",
            "q2?",
            "--answer",
            "a",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_overturn_unknown_decision_id_is_refused_and_renders_human_output(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "overturn",
            EPIC,
            "D-999",
            "--answer",
            "the reversal",
            "--follow-up-title",
            "Undo it",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "refused (decision-refused)" in result.stderr
    assert "ledger:" in result.stdout


def test_overturn_unknown_epic_ancestor_exits_ambiguous(workspace: Path) -> None:
    orphan = _write_orphan(workspace)
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "overturn",
            orphan,
            "D-001",
            "--answer",
            "a",
            "--follow-up-title",
            "t",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.AMBIGUOUS


def test_overturn_apply_error_reports_the_split(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise decision_module.work.OverturnApplyError("filing blew up", decision_module.work.OverturnApplication())

    monkeypatch.setattr(decision_module.work, "run_decision_overturn", boom)
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "overturn",
            EPIC,
            "D-001",
            "--answer",
            "a",
            "--follow-up-title",
            "t",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "disagree; reconcile by hand" in result.stderr


def test_overturn_workspace_error_maps_to_schema_mismatch(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise decision_module.WorkspaceError("bad config")

    monkeypatch.setattr(decision_module.work, "run_decision_overturn", boom)
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "overturn",
            EPIC,
            "D-001",
            "--answer",
            "a",
            "--follow-up-title",
            "t",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH


def test_overturn_os_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(decision_module.work, "run_decision_overturn", boom)
    result = runner.invoke(
        app,
        [
            "work",
            "decision",
            "overturn",
            EPIC,
            "D-001",
            "--answer",
            "a",
            "--follow-up-title",
            "t",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr
