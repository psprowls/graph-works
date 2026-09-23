"""A workspace declaring two code repositories, at the CLI boundary.

`resolve_repo` refuses when several repositories are declared and no name
selects one. The verbs here used to reach it with no name and no way to pass
one, so a second `repositories:` entry broke all of them. Each test pins the
decided multi-repo behaviour for one verb: validate against every declared
repo (work/archive/wiki lint), or expose `--repo-name` and keep strict
resolution (wiki drift, orchestrate, record-placement).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from code_graph_io.testing import raw_conn
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import drift as drift_module
from graph_works_core.lint_drift.propagate_drift import DriftBrief
from okf_io import load
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def two_repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    """`(workspace, code, ui)`: a bootstrapped vault declaring two repositories.

    `packages/core` exists only under *code*, `apps/ui` only under *ui* -- so a
    check against either one alone fails for the other's path.
    """
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    code, ui = tmp_path / "code", tmp_path / "ui"
    (code / "packages/core").mkdir(parents=True)
    (ui / "apps/ui").mkdir(parents=True)
    manifest = root / "workspace.yaml"
    declared = (
        f'repositories:\n  "code":\n    path: {json.dumps(str(code))}\n  "ui":\n    path: {json.dumps(str(ui))}\n'
    )
    text = manifest.read_text(encoding="utf-8")
    assert "repositories: {}\n" in text
    manifest.write_text(text.replace("repositories: {}\n", declared), encoding="utf-8")
    return root, code, ui


def _file(root: Path, title: str, *affects: str) -> str:
    args = ["work", "file", "--title", title, "--kind", "Feature", "--summary", "d", "--workspace", str(root), "--json"]
    for path in affects:
        args.extend(("--affects", path))
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return str(json.loads(result.stdout)["path"])


def test_work_file_validates_affects_against_every_declared_repo(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, _ui = two_repos
    path = _file(root, "Spans both", "packages/core", "apps/ui")
    assert (root / "okf" / f"{path}.md").is_file()


def test_work_file_still_refuses_a_path_under_no_declared_repo(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, _ui = two_repos
    args = ["work", "file", "--title", "Nowhere", "--kind", "Feature", "--summary", "d", "--affects", "apps/gone"]
    result = runner.invoke(app, [*args, "--workspace", str(root), "--json"])
    assert result.exit_code != 0
    assert "targets.affects-missing" in result.output


def test_work_lint_checks_affects_against_every_declared_repo(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, _ui = two_repos
    _file(root, "Spans both", "packages/core", "apps/ui")
    result = runner.invoke(app, ["work", "lint", "--workspace", str(root), "--json"])
    assert result.exit_code == 0, result.output
    assert "targets.affects-missing" not in result.stdout


def test_work_regen_index_applies_in_a_two_repo_workspace(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, _ui = two_repos
    _file(root, "Indexed", "apps/ui")
    result = runner.invoke(app, ["work", "regen-index", "--workspace", str(root), "--json"])
    assert result.exit_code == 0, result.output


def test_work_archive_applies_in_a_two_repo_workspace(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, _ui = two_repos
    path = _file(root, "Done already", "apps/ui")
    page = root / "okf" / f"{path}.md"
    document = load(page)
    document.set("work_status", "wontfix")
    document.save()
    result = runner.invoke(app, ["work", "archive", "--workspace", str(root), "--json"])
    assert result.exit_code == 0, result.output
    assert not page.exists()


def test_wiki_lint_checks_the_work_lane_against_every_declared_repo(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, _ui = two_repos
    _file(root, "Spans both", "packages/core", "apps/ui")
    result = runner.invoke(app, ["wiki", "lint", "--json", "--workspace", str(root)])
    payload = json.loads(result.stdout)
    findings = [finding for lane in payload["mechanical"] for finding in lane["findings"]]
    assert not any(finding["code"] == "targets.affects-missing" for finding in findings), findings
    assert result.exit_code == 0, result.output


@pytest.fixture
def two_repo_graph(two_repos: tuple[Path, Path, Path]) -> tuple[Path, Path, Path]:
    """`two_repos` plus the empty `code.db` `gw wiki drift` opens before anything else."""
    root = two_repos[0]
    graph_dir = root / ".gw" / "cache"
    graph_dir.mkdir(parents=True, exist_ok=True)
    raw_conn(graph_dir / "code.db", create=True).close()
    return two_repos


def test_wiki_drift_requires_repo_name_when_several_are_declared(two_repo_graph: tuple[Path, Path, Path]) -> None:
    root = two_repo_graph[0]
    result = runner.invoke(app, ["wiki", "drift", "--backend", "claude_code", "--workspace", str(root)])
    assert result.exit_code != 0
    assert "--repo-name" in result.stderr
    assert "'code'" in result.stderr and "'ui'" in result.stderr


def test_wiki_drift_repo_name_selects_the_declared_repo(
    monkeypatch: pytest.MonkeyPatch, two_repo_graph: tuple[Path, Path, Path]
) -> None:
    root, _code, ui = two_repo_graph
    seen: list[Path | None] = []

    def fake_plan_drift_brief(layout: object, config: object, reader: object, *, repo_root: Path | None) -> DriftBrief:
        seen.append(repo_root)
        return DriftBrief(targets=())

    monkeypatch.setattr(drift_module, "plan_drift_brief", fake_plan_drift_brief)
    result = runner.invoke(
        app, ["wiki", "drift", "--backend", "claude_code", "--repo-name", "ui", "--workspace", str(root)]
    )
    assert result.exit_code == 0, result.output
    assert seen == [ui.resolve()]


def test_wiki_drift_refuses_a_repo_name_nothing_declares(two_repo_graph: tuple[Path, Path, Path]) -> None:
    root = two_repo_graph[0]
    result = runner.invoke(
        app, ["wiki", "drift", "--backend", "claude_code", "--repo-name", "nope", "--workspace", str(root)]
    )
    assert result.exit_code != 0
    assert "nope" in result.stderr


def test_work_orchestrate_refuses_without_repo_name_when_several_are_declared(
    two_repos: tuple[Path, Path, Path],
) -> None:
    root, _code, _ui = two_repos
    path = _file(root, "Orchestrated", "apps/ui")
    result = runner.invoke(app, ["work", "orchestrate", path, "--workspace", str(root), "--json"])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "repo_name" in result.output


def test_work_orchestrate_repo_name_selects_the_declared_repo(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, ui = two_repos
    path = _file(root, "Orchestrated", "apps/ui")
    result = runner.invoke(app, ["work", "orchestrate", path, "--repo-name", "ui", "--workspace", str(root), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["repo"] == {"name": "ui", "path": str(ui.resolve()), "source": "flag"}


def test_work_orchestrate_plans_a_tagged_item_without_repo_name(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, ui = two_repos
    args = ["work", "file", "--title", "Tagged", "--kind", "Feature", "--summary", "d", "--affects", "apps/ui"]
    filed = runner.invoke(app, [*args, "--repo", "ui", "--workspace", str(root), "--json"])
    path = json.loads(filed.stdout)["path"]
    result = runner.invoke(app, ["work", "orchestrate", path, "--workspace", str(root), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["repo"] == {"name": "ui", "path": str(ui.resolve()), "source": "frontmatter"}


def _placement_args(root: Path, path: str, phase: str, worktree: str) -> list[str]:
    return [
        "work",
        "record-placement",
        path,
        "--root",
        path,
        "--phase",
        phase,
        "--worktree",
        worktree,
        "--branch",
        "feature/placed",
        "--workspace",
        str(root),
        "--json",
    ]


def test_work_record_placement_repo_name_selects_the_declared_repo(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, _ui = two_repos
    path = _file(root, "Placed", "apps/ui")
    document = load(root / "okf" / f"{path}.md")
    document.set("phase", "design")
    document.save()
    phase = "design"
    worktree = str(Path(Path.cwd().anchor, "wt", "placed"))

    refused = runner.invoke(app, _placement_args(root, path, phase, worktree))
    assert refused.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "repo_name" in refused.output

    result = runner.invoke(app, [*_placement_args(root, path, phase, worktree), "--repo-name", "ui"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["written"] is True


def test_work_advance_no_longer_refuses_in_a_two_repo_workspace(two_repos: tuple[Path, Path, Path]) -> None:
    """The test process's cwd is in neither declared repo: inference is
    skipped with a note, and the advance still applies."""
    root, _code, _ui = two_repos
    path = _file(root, "Advanced", "apps/ui")
    result = runner.invoke(app, ["work", "advance", path, "--workspace", str(root), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["refusal"] is None
    assert "2 repositories declared" in payload["repo_note"]


def test_work_file_repo_writes_the_repo_field(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, _ui = two_repos
    args = ["work", "file", "--title", "Tagged", "--kind", "Feature", "--summary", "d", "--affects", "apps/ui"]
    result = runner.invoke(app, [*args, "--repo", "ui", "--workspace", str(root), "--json"])
    assert result.exit_code == 0, result.output
    path = json.loads(result.stdout)["path"]
    assert load(root / "okf" / f"{path}.md").fm_data()["repo"] == "ui"


def test_work_record_placement_repo_writes_repo_stamps(two_repos: tuple[Path, Path, Path]) -> None:
    root, _code, _ui = two_repos
    args = ["work", "file", "--title", "Placed", "--kind", "Feature", "--summary", "d", "--affects", "apps/ui"]
    path = json.loads(runner.invoke(app, [*args, "--repo", "code", "--workspace", str(root), "--json"]).stdout)["path"]
    document = load(root / "okf" / f"{path}.md")
    document.set("phase", "design")
    document.save()
    worktree = str(Path(Path.cwd().anchor, "wt", "placed"))

    result = runner.invoke(app, [*_placement_args(root, path, "design", worktree), "--repo", "ui"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["written"] is True and payload["repo"] == "ui"
    assert load(root / "okf" / f"{path}.md").fm_data()["repo_stamps"] == {
        "ui": {"worktree": worktree, "branch": "feature/placed"}
    }
