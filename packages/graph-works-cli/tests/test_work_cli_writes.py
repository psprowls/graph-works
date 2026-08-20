"""`gw work file` tests for writing work items, reconciling indexes, and logging arrivals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.work_cli import main as work_main
from graph_works_cli.workspace_resolution import resolve_workspace
from test_work_cli_reads import write_item
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    return root


def test_file_emits_the_slug_and_writes_the_page(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Shorten work item slugs",
            "--kind",
            "Feature",
            "--summary",
            "One line",
            "--affects",
            "packages/a, packages/b",
            "--slug-words",
            "shorten slugs",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert "slug" in payload
    assert payload["slug"].endswith("-feature-shorten-slugs")
    assert Path(payload["page_path"]).is_file()


def test_file_parses_dep_specs_into_typed_edges(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    captured: dict[str, object] = {}

    def spy(layout, config, **kwargs):
        captured.update(kwargs)
        # Return a mock outcome that indicates success without calling the real function
        outcome = MagicMock()
        outcome.plan.refusal = None
        outcome.plan.filing.slug = "2026-08-19-feature-t"
        outcome.plan.filing.target = workspace / "work" / "2026-08-19-feature-t.md"
        outcome.plan.filing.work_directory = workspace / "work"
        outcome.plan.filing.detail = ""
        outcome.plan.warnings = []
        outcome.application.indexes = []
        outcome.application.log = None
        return outcome

    monkeypatch.setattr(work_main.work, "run_file", spy)
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "s",
            "--depends-on",
            "a,b",
            "--dep",
            "slug=c,blocks=design,needs=plan",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert captured["depends_on"] == (
        work_main.work.DependencyEdge("a"),
        work_main.work.DependencyEdge("b"),
        work_main.work.DependencyEdge("c", "design", "plan"),
    )


@pytest.mark.parametrize(
    "spec",
    ["blocks=design", "slug=", "slug=a,nope=x", "slug=a,slug=b", "slug=a,blocks=nope", "slug=a,needs=nope", "a"],
)
def test_malformed_dep_specs_are_refused_before_any_write(workspace: Path, spec: str) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "s",
            "--dep",
            spec,
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert spec in result.stderr
    assert result.stdout == ""


def test_malformed_dep_spec_reports_correct_offender_when_multiple_slugs_overlap(
    workspace: Path,
) -> None:
    """Regression: when two --dep specs share a slug and the first is invalid,
    the error message should name the first (invalid) spec, not the second."""
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "s",
            "--dep",
            "slug=x,blocks=nope",
            "--dep",
            "slug=x,blocks=design",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    # The error should name the first spec (the invalid one), not the second
    assert "slug=x,blocks=nope" in result.stderr
    assert result.stdout == ""


def test_file_maps_a_workspace_error_to_schema_mismatch(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise work_main.WorkspaceError("bad config")

    monkeypatch.setattr(work_main.work, "run_file", boom)
    result = runner.invoke(
        app,
        ["work", "file", "--title", "T", "--kind", "Feature", "--summary", "s", "--workspace", str(workspace)],
    )
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH


def test_file_run_file_value_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise ValueError("bad filing")

    monkeypatch.setattr(work_main.work, "run_file", boom)
    result = runner.invoke(
        app,
        ["work", "file", "--title", "T", "--kind", "Feature", "--summary", "s", "--workspace", str(workspace)],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "bad filing" in result.stderr


def test_file_config_os_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(work_main, "load_config", boom)
    result = runner.invoke(
        app,
        ["work", "file", "--title", "T", "--kind", "Feature", "--summary", "s", "--workspace", str(workspace)],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_file_dep_spec_with_a_stray_empty_fragment_is_skipped(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def spy(layout, config, **kwargs):
        from unittest.mock import MagicMock

        captured.update(kwargs)
        outcome = MagicMock()
        outcome.plan.refusal = None
        outcome.plan.filing.slug = "2026-08-19-feature-t"
        outcome.plan.filing.target = workspace / "work" / "2026-08-19-feature-t.md"
        outcome.plan.filing.work_directory = workspace / "work"
        outcome.plan.filing.detail = ""
        outcome.plan.warnings = []
        outcome.application.indexes = []
        outcome.application.log = None
        return outcome

    monkeypatch.setattr(work_main.work, "run_file", spy)
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "s",
            "--dep",
            "slug=c,,blocks=design",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert captured["depends_on"] == (work_main.work.DependencyEdge("c", "design"),)


def test_file_is_refused_on_invalid_effort_and_writes_nothing(workspace: Path) -> None:
    layout = resolve_workspace(str(workspace))
    before = sorted(path.name for path in (layout.bundle_dir / "work").glob("*.md"))
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "s",
            "--effort",
            "not-a-real-effort",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "refused" in result.stderr
    assert sorted(path.name for path in (layout.bundle_dir / "work").glob("*.md")) == before


def test_file_refusal_still_reports_any_warnings_the_plan_carries(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused `FilingPlan` carries no warnings today (`_refused()` always sets
    `warnings=()`), but the CLI's own contract is: print whatever the plan
    carries before failing. Spy rather than rely on a domain combination core
    does not currently produce."""
    from unittest.mock import MagicMock

    def spy(layout, config, **kwargs):
        outcome = MagicMock()
        outcome.plan.refusal = "unknown-dependency"
        outcome.plan.filing.slug = "2026-08-19-feature-t"
        outcome.plan.filing.target = workspace / "work" / "2026-08-19-feature-t.md"
        outcome.plan.filing.work_directory = workspace / "work"
        outcome.plan.filing.detail = "unknown dependency 'no-such-slug'"
        outcome.plan.warnings = ["slug words: 6 words kept (recommended 1-4)"]
        outcome.application.indexes = []
        outcome.application.log = None
        return outcome

    monkeypatch.setattr(work_main.work, "run_file", spy)
    result = runner.invoke(
        app,
        ["work", "file", "--title", "T", "--kind", "Feature", "--summary", "s", "--workspace", str(workspace)],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert "words kept" in result.stderr
    assert "refused (unknown-dependency)" in result.stderr


def test_file_human_output_reports_warnings_indexes_and_log(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "s",
            "--slug-words",
            "one two three four five six",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert "words kept" in result.stderr
    assert "[ok] filed" in result.stdout
    assert "reconciled" in result.stdout
    assert "log.md:" in result.stdout


def test_file_dry_run_writes_nothing(workspace: Path) -> None:
    layout = resolve_workspace(str(workspace))
    before = sorted(path.name for path in (layout.bundle_dir / "work").glob("*.md"))
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "s",
            "--dry-run",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert sorted(path.name for path in (layout.bundle_dir / "work").glob("*.md")) == before


def test_advance_moves_the_phase_and_reports_the_contract_keys(workspace: Path) -> None:
    slug = "2026-08-01-feature-a"
    write_item(workspace, slug, phase="plan")
    result = runner.invoke(app, ["work", "advance", slug, "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["phase"] == "execute"
    assert payload["status"] == "accepted"
    assert payload["blockers"] == []
    assert "on_complete" in payload


def test_advance_dry_run_leaves_the_page_untouched(workspace: Path) -> None:
    slug = "2026-08-01-feature-a"
    page = write_item(workspace, slug, phase="plan")
    before = page.read_bytes()
    result = runner.invoke(app, ["work", "advance", slug, "--dry-run", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert page.read_bytes() == before


def test_advance_calls_the_provenance_guard(workspace: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(work_main, "warn_if_stale_routing", lambda: calls.append("guard"))
    write_item(workspace, "2026-08-01-feature-a", phase="plan")
    runner.invoke(app, ["work", "advance", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert calls == ["guard"]


def test_advance_refusal_exits_generic_and_writes_nothing(workspace: Path) -> None:
    slug = "2026-08-01-feature-a"
    page = write_item(workspace, slug, phase="done")
    before = page.read_bytes()
    result = runner.invoke(app, ["work", "advance", slug, "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert page.read_bytes() == before
    assert result.stdout == ""


def test_advance_forwards_the_worktree_branch_pair(workspace: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def spy(layout, slug, **kwargs):
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(work_main, "run_stage_advance", spy)
    write_item(workspace, "2026-08-01-feature-a", phase="plan")
    runner.invoke(
        app,
        [
            "work",
            "advance",
            "2026-08-01-feature-a",
            "--worktree",
            "/wt/a",
            "--branch",
            "feat/a",
            "--owner",
            "pat",
            "--effort",
            "medium",
            "--resolved-in",
            "abc1234",
            "--workspace",
            str(workspace),
        ],
    )
    assert captured["worktree"] == "/wt/a"
    assert captured["branch"] == "feat/a"
    assert captured["owner"] == "pat"
    assert captured["effort"] == "medium"
    assert captured["resolved_in"] == "abc1234"
    assert captured["dry_run"] is False


def test_advance_maps_a_workspace_error_to_schema_mismatch(workspace: Path, monkeypatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise work_main.WorkspaceError("bad config")

    monkeypatch.setattr(work_main, "run_stage_advance", boom)
    write_item(workspace, "2026-08-01-feature-a", phase="plan")
    result = runner.invoke(app, ["work", "advance", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH


def test_advance_run_stage_advance_value_error_exits_ambiguous(workspace: Path, monkeypatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise ValueError("bad slug")

    monkeypatch.setattr(work_main, "run_stage_advance", boom)
    write_item(workspace, "2026-08-01-feature-a", phase="plan")
    result = runner.invoke(app, ["work", "advance", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.AMBIGUOUS
    assert "bad slug" in result.stderr


def test_advance_run_stage_advance_os_error_is_reported(workspace: Path, monkeypatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(work_main, "run_stage_advance", boom)
    write_item(workspace, "2026-08-01-feature-a", phase="plan")
    result = runner.invoke(app, ["work", "advance", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_advance_json_mode_without_a_repo_note_reports_nothing_extra(workspace: Path, tmp_path: Path) -> None:
    slug = "2026-08-01-feature-a"
    write_item(workspace, slug, phase="plan")
    layout = resolve_workspace(str(workspace))
    repo_dir = tmp_path / "code-repo"
    repo_dir.mkdir()
    config_path = layout.bundle_dir / "_repositories.yaml"
    config_path.write_text(
        f"graph_dir: ../graphs/code\nrepositories:\n  main:\n    path: {repo_dir}\nignore: []\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["work", "advance", slug, "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert result.stderr == ""


def test_archive_sweep_with_nothing_eligible_succeeds(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", phase="plan")
    result = runner.invoke(app, ["work", "archive", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert "nothing to archive" in result.stdout


RESOLVED_ITEM = """---
type: Feature
title: {slug}
description: d
status: stable
workflow_status: resolved
phase: {phase}
effort: medium
opened: 2026-08-01
updated: 2026-08-01
affects:
- packages/a
---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""


def test_archive_human_output_reports_archived_pruned_indexes_and_log(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", phase="finish", template=RESOLVED_ITEM)
    layout = resolve_workspace(str(workspace))
    empty_working_dir = layout.bundle_dir / "work" / "2026-08-01-feature-a"
    empty_working_dir.mkdir(parents=True, exist_ok=True)
    result = runner.invoke(app, ["work", "archive", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert "archived 2026-08-01-feature-a" in result.stdout
    assert "pruned work/2026-08-01-feature-a" in result.stdout
    assert "reconciled" in result.stdout
    assert "log.md:" in result.stdout


def test_archive_json_output(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", phase="finish", template=RESOLVED_ITEM)
    result = runner.invoke(app, ["work", "archive", "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["archived"] == ["2026-08-01-feature-a"]


def test_archive_value_error_is_reported(workspace: Path, monkeypatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise ValueError("bad slug")

    monkeypatch.setattr(work_main, "run_archive", boom)
    result = runner.invoke(app, ["work", "archive", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "bad slug" in result.stderr


def test_archive_conflict_fails_and_applies_nothing(workspace: Path, monkeypatch) -> None:
    import dataclasses

    real_run_archive = work_main.run_archive

    def patched(*args: object, **kwargs: object) -> object:
        run = real_run_archive(*args, **kwargs)  # type: ignore[arg-type]
        return dataclasses.replace(run, conflict=("2026-08-01-feature-a",))

    monkeypatch.setattr(work_main, "run_archive", patched)
    write_item(workspace, "2026-08-01-feature-a", phase="finish", template=RESOLVED_ITEM)
    result = runner.invoke(app, ["work", "archive", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "cross-lane conflict" in result.stderr


def test_archive_named_non_terminal_item_exits_generic(workspace: Path) -> None:
    slug = "2026-08-01-feature-a"
    page = write_item(workspace, slug, phase="plan")
    result = runner.invoke(app, ["work", "archive", slug, "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert page.is_file()


def test_archive_dry_run_moves_nothing(workspace: Path) -> None:
    slug = "2026-08-01-feature-a"
    page = write_item(workspace, slug, phase="plan")
    result = runner.invoke(app, ["work", "archive", "--dry-run", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert page.is_file()


def test_archive_passes_sweep_mode_as_none(workspace: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def spy(layout, slugs=None, wiki_slugs=(), **kwargs):
        captured["slugs"] = slugs
        captured["wiki_slugs"] = wiki_slugs
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(work_main, "run_archive", spy)
    runner.invoke(app, ["work", "archive", "--workspace", str(workspace)])
    assert captured["slugs"] is None
    assert captured["wiki_slugs"] == ()
    assert captured["dry_run"] is False


def test_archive_dry_run_flag_plumbing(workspace: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def spy(layout, slugs=None, wiki_slugs=(), **kwargs):
        captured["slugs"] = slugs
        captured["wiki_slugs"] = wiki_slugs
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(work_main, "run_archive", spy)
    runner.invoke(app, ["work", "archive", "--dry-run", "--workspace", str(workspace)])
    assert captured["dry_run"] is True


def test_adopt_child_specs_unknown_epic_exits_ambiguous(workspace: Path) -> None:
    result = runner.invoke(app, ["work", "adopt-child-specs", "missing", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.AMBIGUOUS


def test_adopt_child_specs_os_error_is_reported(workspace: Path, monkeypatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(work_main.work, "run_adopt_child_specs", boom)
    epic_template = """---
type: Epic
title: {slug}
description: d
status: stable
workflow_status: open
phase: plan
opened: 2026-08-01
updated: 2026-08-01
---

## Summary
d

## Plan
"""
    write_item(workspace, "2026-08-01-epic-e", template=epic_template)
    result = runner.invoke(app, ["work", "adopt-child-specs", "2026-08-01-epic-e", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_adopt_child_specs_human_output_reports_orphaned_and_ambiguous(workspace: Path, monkeypatch) -> None:
    from unittest.mock import MagicMock

    def spy(layout, epic_slug, **kwargs):
        result = MagicMock()
        result.plan.refusal = "no-children-filed"
        move = MagicMock()
        move.child_slug = "2026-08-01-feature-a"
        move.draft = Path("work/2026-08-01-epic-e/references/child-specs/feature-a.md")
        move.source_ref.rel = "work/2026-08-01-feature-a/references/01-design-spec.md"
        result.plan.adopted = [move]
        result.plan.orphaned = [Path("work/2026-08-01-epic-e/references/child-specs/orphan.md")]
        result.plan.unseeded = ["2026-08-01-feature-b"]
        ambiguous_entry = MagicMock()
        ambiguous_entry.draft = Path("work/2026-08-01-epic-e/references/child-specs/dup.md")
        ambiguous_entry.candidate_slugs = ("2026-08-01-feature-c", "2026-08-01-feature-d")
        result.plan.ambiguous = [ambiguous_entry]
        result.plan.warnings = ["heads up"]
        result.application.moved = []
        result.application.registered = []
        result.application.warnings = []
        return result

    monkeypatch.setattr(work_main.work, "run_adopt_child_specs", spy)
    epic_template = """---
type: Epic
title: {slug}
description: d
status: stable
workflow_status: open
phase: plan
opened: 2026-08-01
updated: 2026-08-01
---

## Summary
d

## Plan
"""
    write_item(workspace, "2026-08-01-epic-e", template=epic_template)
    result = runner.invoke(app, ["work", "adopt-child-specs", "2026-08-01-epic-e", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "adopted 2026-08-01-feature-a" in result.stdout
    assert "unseeded: 2026-08-01-feature-b" in result.stdout
    assert "orphaned draft" in result.stderr
    assert "ambiguous" in result.stderr
    assert "heads up" in result.stderr
    assert "refused (no-children-filed)" in result.stderr


def test_adopt_child_specs_reports_every_result_bucket(workspace: Path) -> None:
    epic = "2026-08-01-epic-e"
    epic_template = """---
type: Epic
title: {slug}
description: d
status: stable
workflow_status: open
phase: plan
opened: 2026-08-01
updated: 2026-08-01
---

## Summary
d

## Plan
"""
    write_item(workspace, epic, template=epic_template)
    result = runner.invoke(app, ["work", "adopt-child-specs", epic, "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    for key in ("adopted", "unseeded_children", "orphaned_drafts", "ambiguous", "warnings"):
        assert key in payload


def test_adopt_child_specs_is_idempotent(workspace: Path) -> None:
    epic = "2026-08-01-epic-e"
    epic_template = """---
type: Epic
title: {slug}
description: d
status: stable
workflow_status: open
phase: plan
opened: 2026-08-01
updated: 2026-08-01
---

## Summary
d

## Plan
"""
    write_item(workspace, epic, template=epic_template)
    first = runner.invoke(app, ["work", "adopt-child-specs", epic, "--workspace", str(workspace), "--json"])
    second = runner.invoke(app, ["work", "adopt-child-specs", epic, "--workspace", str(workspace), "--json"])
    assert first.exit_code == second.exit_code == exit_codes.SUCCESS
    assert json.loads(second.stdout)["adopted"] == []


def test_adopt_child_specs_dry_run_moves_nothing(workspace: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def spy(layout, epic_slug, **kwargs):
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(work_main.work, "run_adopt_child_specs", spy)
    epic_template = """---
type: Epic
title: {slug}
description: d
status: stable
workflow_status: open
phase: plan
opened: 2026-08-01
updated: 2026-08-01
---

## Summary
d

## Plan
"""
    write_item(workspace, "2026-08-01-epic-e", template=epic_template)
    runner.invoke(app, ["work", "adopt-child-specs", "2026-08-01-epic-e", "--dry-run", "--workspace", str(workspace)])
    assert captured["dry_run"] is True
