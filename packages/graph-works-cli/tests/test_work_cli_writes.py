"""Path-native write commands and refusal stream contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.work_cli import main as work_main
from graph_works_cli.workspace_resolution import resolve_workspace
from okf_io import load
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    return root


def file_item(workspace: Path, title: str, *, kind: str = "Feature", parent: str | None = None) -> str:
    args = ["work", "file", "--title", title, "--kind", kind, "--summary", "d", "--workspace", str(workspace), "--json"]
    if parent:
        args.extend(("--parent-path", parent))
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return str(json.loads(result.stdout)["path"])


def test_file_writes_a_date_free_canonical_path_and_explicit_json(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Stable Name",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--name",
            "short name",
            "--version",
            "v2",
            "--target-date",
            "2026-09-01",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path"] == "work/feature-short-name"
    assert payload["applied"] is True and payload["rolled_back"] is False
    assert resolve_workspace(str(workspace)).bundle_dir.joinpath(f"{payload['path']}.md").is_file()
    assert "slug" not in result.stdout


def test_file_parses_complete_dependency_specs(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def spy(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        raise SystemExit(0)

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
            "d",
            "--dep",
            "path=work/feature-a,blocks=execute,needs=resolved",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == 0
    assert captured["depends_on"] == (work_main.work.DependencyEdge("work/feature-a", "execute", "resolved"),)


@pytest.mark.parametrize(
    "spec",
    [
        "path=work/feature-a",
        "blocks=execute,needs=resolved",
        "path=,blocks=execute,needs=resolved",
        "path=work/feature-a,blocks=execute,blocks=plan,needs=resolved",
        "path=work/feature-a,blocks=execute,needs=resolved,",
    ],
)
def test_incomplete_or_malformed_dep_is_refused_before_core(workspace: Path, spec: str) -> None:
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
            "d",
            "--dep",
            spec,
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == "" and spec in result.stderr


def test_file_refusal_emits_no_partial_json(workspace: Path) -> None:
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
            "d",
            "--effort",
            "huge",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == "" and "refused" in result.stderr


def test_file_json_keeps_warnings_on_stderr(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "one two three four five",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == 0 and json.loads(result.stdout)["warnings"]
    assert "words kept" in result.stderr


def test_advance_applies_by_default_and_accepts_released_at(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    result = runner.invoke(
        app, ["work", "advance", path, "--released-at", "2026-09-01", "--workspace", str(workspace), "--json"]
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path"] == path and payload["applied"] is True
    assert "work_status" in payload


def test_invalid_date_is_diagnostic_only(workspace: Path) -> None:
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
            "d",
            "--target-date",
            "not-a-date",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == 1 and result.stdout == ""
    assert "expected YYYY-MM-DD" in result.stderr


def test_reparent_live_apply_updates_lane_indexes(workspace: Path) -> None:
    parent = file_item(workspace, "Parent", kind="Epic")
    source = file_item(workspace, "Child")
    result = runner.invoke(
        app,
        ["work", "reparent", source, "--parent", parent, "--workspace", str(workspace), "--json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path_mapping"][source].startswith(f"{parent}/children/")
    assert payload["applied"] is True and payload["rolled_back"] is False
    layout = resolve_workspace(str(workspace))
    assert layout.bundle_dir.joinpath(f"{payload['path_mapping'][source]}.md").is_file()
    assert "work/index.md" in payload["indexes"]


def test_incomplete_path_apply_emits_no_partial_json(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(work_main.work, "run_reparent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        work_main.rendering,
        "path_mutation_payload",
        lambda _result: {
            "path_mapping": {"work/feature-a": "work/epic-e/children/feature-a"},
            "indexes": [],
            "warnings": [],
            "refusals": [],
            "applied": True,
            "rolled_back": True,
            "failures": ["stale snapshot"],
        },
    )
    result = runner.invoke(
        app,
        ["work", "reparent", "work/feature-a", "--parent", "work/epic-e", "--workspace", str(workspace), "--json"],
    )
    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == "" and "stale snapshot" in result.stderr


def test_adopt_live_apply_updates_lane_indexes(workspace: Path) -> None:
    release = file_item(workspace, "R1", kind="Release")
    source = file_item(workspace, "Epic", kind="Epic")
    result = runner.invoke(
        app,
        ["work", "adopt", source, "--release", release, "--workspace", str(workspace), "--json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path_mapping"][source].startswith(f"{release}/children/")
    assert payload["applied"] is True and payload["rolled_back"] is False
    layout = resolve_workspace(str(workspace))
    assert layout.bundle_dir.joinpath(f"{payload['path_mapping'][source]}.md").is_file()
    assert "work/index.md" in payload["indexes"]


def test_archive_live_apply_updates_lane_indexes(workspace: Path) -> None:
    path = file_item(workspace, "Done", kind="Bug")
    layout = resolve_workspace(str(workspace))
    page = layout.bundle_dir / f"{path}.md"
    document = load(page)
    document.set("work_status", "resolved")
    document.save()
    result = runner.invoke(app, ["work", "archive", path, "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path_mapping"][path] == "work/_archive/bug-done"
    assert payload["applied"] is True and payload["rolled_back"] is False
    assert layout.bundle_dir.joinpath("work/_archive/bug-done.md").is_file()
    assert "work/index.md" in payload["indexes"]


def test_archive_incomplete_apply_emits_no_partial_json(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = SimpleNamespace(
        plan=SimpleNamespace(move_plan=None),
        wiki_plan=SimpleNamespace(moves=SimpleNamespace(stranded=())),
        ok=False,
    )
    monkeypatch.setattr(work_main, "run_archive", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(
        work_main.rendering,
        "archive_payload",
        lambda *_args, **_kwargs: {
            "warnings": [],
            "path_mapping": {"work/bug-done": "work/_archive/bug-done"},
            "indexes": [],
            "logged": None,
            "conflict": [],
            "refusals": [],
            "applied": True,
            "rolled_back": False,
            "failures": ["stale lane index"],
        },
    )
    result = runner.invoke(app, ["work", "archive", "work/bug-done", "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "stale lane index" in result.stderr


def test_split_topology_files_and_lints_clean(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".git").mkdir(parents=True)
    root = vault / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    code = tmp_path / "code"
    (code / "packages/foo").mkdir(parents=True)
    layout = resolve_workspace(str(root))
    layout.manifest_path.write_text(
        f'version: 1\nrepositories:\n  "code":\n    path: {json.dumps(str(code))}\n',
        encoding="utf-8",
    )

    file_result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Split-topology filing",
            "--kind",
            "Feature",
            "--summary",
            "One line",
            "--affects",
            "packages/foo",
            "--workspace",
            str(root),
            "--json",
        ],
    )
    assert file_result.exit_code == 0, file_result.output

    lint_result = runner.invoke(app, ["work", "lint", "--workspace", str(root), "--json"])
    payload = json.loads(lint_result.stdout)
    assert payload["ok"] is True, payload["findings"]
    assert not any(finding["code"] == "targets.affects-missing" for finding in payload["findings"])
