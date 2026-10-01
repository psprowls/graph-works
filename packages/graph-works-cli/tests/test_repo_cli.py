from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from code_graph_io import open_reader
from graph_works_cli.cli import app
from graph_works_core.graph.commands import graph_target
from graph_works_core.workspace.discovery import resolve
from typer.testing import CliRunner

runner = CliRunner()
GIT = shutil.which("git") or "git"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@e",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [GIT, *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@e")
    git(root, "config", "user.name", "t")
    assert runner.invoke(app, ["bootstrap", "--topic", "Repos", "--workspace", str(root)]).exit_code == 0
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def url(tmp_path: Path) -> str:
    bare, work = tmp_path / "up.git", tmp_path / "up"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    git(bare, "config", "uploadpack.allowFilter", "true")
    git(tmp_path, "init", "-q", "-b", "main", str(work))
    (work / "a.py").write_text("a = 1\n", encoding="utf-8", newline="")
    git(work, "add", "a.py")
    git(work, "commit", "-q", "-m", "c1")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "HEAD:main")
    return bare.as_uri()


def _json(args: list[str]) -> tuple[int, dict[str, object]]:
    result = runner.invoke(app, args)
    return result.exit_code, json.loads(result.stdout)


def test_add_restore_advance_round_trip_in_json(workspace: Path, url: str) -> None:
    code, added = _json(["repo", "add", url, "--name", "demo", "--json", "--workspace", str(workspace)])
    assert code == 0 and added["ok"] is True and added["name"] == "demo"
    shutil.rmtree(workspace / "okf" / "repositories" / "demo" / "references" / "git", ignore_errors=True)
    code, restored = _json(["repo", "restore", "--json", "--workspace", str(workspace)])
    assert code == 0 and restored["outcomes"] == [
        {"name": "demo", "outcome": "cloned", "commit": added["commit"], "refusal": None, "checkout": None}
    ]
    code, advanced = _json(["repo", "advance", "demo", "--json", "--workspace", str(workspace)])
    assert code == 0 and advanced["outcome"] == "up-to-date"


def test_add_dry_run_in_human_form(workspace: Path, url: str) -> None:
    result = runner.invoke(app, ["repo", "add", url, "--name", "demo", "--dry-run", "--workspace", str(workspace)])
    assert result.exit_code == 0
    assert "would add demo" in result.stdout
    assert not (workspace / "okf" / "repositories" / "demo.md").exists()


def test_a_refused_add_emits_the_envelope_and_exits_nonzero(workspace: Path, url: str) -> None:
    result = runner.invoke(app, ["repo", "add", url, "--name", "Bad Name", "--json", "--workspace", str(workspace)])
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert (error["command"], error["reason"]) == ("repo add", "refused")
    assert error["payload"]["refusal"]["code"] == "invalid-name"


def test_restore_with_a_refused_repository_prints_outcomes_and_exits_nonzero(workspace: Path) -> None:
    code, restored = _json(["repo", "restore", "nope", "--json", "--workspace", str(workspace)])
    assert code == 1
    assert restored["outcomes"][0]["refusal"]["code"] == "not-found"  # type: ignore[index]


def test_advance_refusal_envelope(workspace: Path) -> None:
    code, payload = _json(["repo", "advance", "missing", "--json", "--workspace", str(workspace)])
    assert code == 1
    assert payload["error"]["command"] == "repo advance"
    assert payload["error"]["payload"]["refusal"]["code"] == "not-found"


def test_human_restore_empty_and_refused(workspace: Path) -> None:
    result = runner.invoke(app, ["repo", "restore", "--workspace", str(workspace)])
    assert result.exit_code == 0
    assert "no repositories to restore" in result.stdout
    result = runner.invoke(app, ["repo", "restore", "missing", "--workspace", str(workspace)])
    assert result.exit_code == 1
    assert "missing: refused (not-found)" in result.stdout


def test_human_restore_dry_run_and_present(workspace: Path, url: str) -> None:
    assert runner.invoke(app, ["repo", "add", url, "--name", "demo", "--workspace", str(workspace)]).exit_code == 0
    result = runner.invoke(app, ["repo", "restore", "demo", "--dry-run", "--workspace", str(workspace)])
    assert result.exit_code == 0
    assert "demo: would be present" in result.stdout
    result = runner.invoke(app, ["repo", "advance", "demo", "--workspace", str(workspace)])
    assert result.exit_code == 0
    assert "demo: up to date" in result.stdout


def test_restore_global_refusal_envelope(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from graph_works_cli.repo_cli import main
    from graph_works_core.repositories.commands import RepoRefusal, RepoRestoreResult

    monkeypatch.setattr(
        main,
        "run_repo_restore",
        lambda *args, **kwargs: RepoRestoreResult((), False, RepoRefusal("git-unavailable", "unavailable")),
    )
    code, payload = _json(["repo", "restore", "--json", "--workspace", str(workspace)])
    assert code == 1
    assert payload["error"]["command"] == "repo restore"


def test_advance_human_range_details() -> None:
    from graph_works_cli.repo_cli.rendering import advance_text
    from graph_works_core.repositories.commands import RepoAdvanceResult
    from repositories_okf.flagging import FlaggedLink, FlaggedPage
    from repositories_okf.git import FileChange, RangeFacts

    old, new = "a" * 40, "b" * 40
    facts = RangeFacts(old, new, old, True, 2, (FileChange("M", "a.py"),), (), ())
    page = FlaggedPage("concepts/a.md", "A", (FlaggedLink("a.py", "a.py", "M", None),))
    result = RepoAdvanceResult(
        "demo",
        "advanced",
        old,
        new,
        None,
        facts,
        (page,),
        ("repositories/demo.md", "repositories/demo/snapshots/new.md", "log.md"),
        ("proposals/a.md",),
        ("skip",),
        True,
        warnings=("warning",),
    )
    text = advance_text(result)
    assert "would advance demo: aaaaaaa..bbbbbbb, 2 commits, 1 files, rewritten upstream" in text
    assert "concepts/a.md" in text and "proposal: proposals/a.md" in text
    assert "skipped: skip" in text and "! warning" in text
    assert all(f"  {path}" in text for path in result.paths)
    assert "advanced demo" in advance_text(
        RepoAdvanceResult("demo", "advanced", old, new, None, None, (), (), (), (), False)
    )


def test_add_human_warnings() -> None:
    from graph_works_cli.repo_cli.rendering import add_text
    from graph_works_core.repositories.commands import RepoAddResult

    result = RepoAddResult(
        "demo",
        "https://e/demo.git",
        "main",
        "main",
        "a" * 40,
        "v1",
        ("repositories/demo.md",),
        False,
        warnings=("warning",),
    )
    text = add_text(result)
    assert "added demo" in text and "repositories/demo.md" in text and "! warning" in text


def test_managed_human_output() -> None:
    from graph_works_cli.repo_cli.rendering import add_text, advance_text, restore_text
    from graph_works_core.repositories.commands import (
        RepoAddResult,
        RepoAdvanceResult,
        RepoRestoreResult,
        RestoreOutcome,
    )

    added = RepoAddResult(
        "demo",
        "https://e/demo.git",
        "main",
        None,
        "a" * 40,
        None,
        (),
        False,
        managed=True,
        checkout=".gw/worktrees/demo/main",
    )
    assert "(managed; checkout .gw/worktrees/demo/main)" in add_text(added)
    restored = RepoRestoreResult((RestoreOutcome("demo", "present", "a" * 40, checkout="checkout-present"),), False)
    assert restore_text(restored) == "demo: present, checkout-present"
    advanced = RepoAdvanceResult(
        "demo",
        "advanced",
        "a" * 40,
        "b" * 40,
        None,
        None,
        (),
        ("code-graph/demo.md",),
        (),
        (),
        False,
        managed=True,
        commits=1,
    )
    assert advance_text(advanced) == "advanced demo: aaaaaaa..bbbbbbb, 1 commit(s), rescanned\n  code-graph/demo.md"
    preview = RepoAdvanceResult(
        "demo",
        "advanced",
        "a" * 40,
        "b" * 40,
        None,
        None,
        (),
        ("repositories/demo.md",),
        (),
        (),
        True,
        managed=True,
        commits=1,
    )
    assert (
        advance_text(preview)
        == "would advance demo: aaaaaaa..bbbbbbb, 1 commit(s), scan planned\n  repositories/demo.md"
    )


@pytest.mark.parametrize(
    ("args", "phrases"),
    [
        (["repo", "--help"], ("managed working checkouts",)),
        (["repo", "add", "--help"], ("managed checkout", "create no clone or checkout")),
        (["repo", "restore", "--help"], ("managed working checkouts", "checkout outcomes")),
        (["repo", "advance", "--help"], ("pages for a reference", "managed repository")),
    ],
)
def test_repo_help_describes_both_roles(args: list[str], phrases: tuple[str, ...]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert all(phrase in result.stdout for phrase in phrases)


def test_managed_add_then_advance_regenerates_the_code_graph(workspace: Path, url: str) -> None:
    code, added = _json(["repo", "add", url, "--name", "demo", "--managed", "--json", "--workspace", str(workspace)])
    assert code == 0 and added["managed"] is True and added["checkout"] == ".gw/worktrees/demo/main"
    checkout = workspace / ".gw" / "worktrees" / "demo" / "main"
    (checkout / "b.py").write_text("b = 1\n", encoding="utf-8", newline="")
    git(checkout, "add", "b.py")
    git(checkout, "commit", "-q", "-m", "feature merged")
    code, advanced = _json(["repo", "advance", "demo", "--json", "--workspace", str(workspace)])
    assert code == 0, advanced
    assert (advanced["outcome"], advanced["managed"], advanced["commits"]) == ("advanced", True, 1)
    graph_page = workspace / "okf" / "code-graph" / "demo.md"
    assert graph_page.is_file()
    assert "Lane page: [demo](/repositories/demo.md)" in graph_page.read_text(encoding="utf-8")
    reader = open_reader(graph_dir=graph_target(resolve(workspace=workspace)).graph_dir)
    try:
        assert "file:local/demo/b.py" in reader.file_uris()
    finally:
        reader.close()
    pin_page = (workspace / "okf" / "repositories" / "demo.md").read_text(encoding="utf-8")
    assert f"commit: {git(checkout, 'rev-parse', 'HEAD')}" in pin_page
    assert "generation:" in pin_page
    assert any(path.startswith("code-graph/demo") for path in advanced["paths"])  # type: ignore[union-attr]
    assert git(workspace, "status", "--porcelain") == ""
    code, again = _json(["repo", "advance", "demo", "--json", "--workspace", str(workspace)])
    assert code == 0 and again["outcome"] == "up-to-date"


def test_managed_restore_reports_the_checkout(workspace: Path, url: str) -> None:
    assert _json(["repo", "add", url, "--name", "demo", "--managed", "--json", "--workspace", str(workspace)])[0] == 0
    code, restored = _json(["repo", "restore", "--json", "--workspace", str(workspace)])
    assert code == 0 and restored["outcomes"][0]["checkout"] == "checkout-present"  # type: ignore[index]


def test_managed_add_uses_explicit_checkout(workspace: Path, url: str) -> None:
    checkout = workspace / "working" / "demo"
    code, added = _json(
        [
            "repo",
            "add",
            url,
            "--name",
            "demo",
            "--managed",
            "--checkout",
            str(checkout),
            "--json",
            "--workspace",
            str(workspace),
        ]
    )
    assert code == 0 and added["checkout"] == "working/demo"
    assert git(checkout, "branch", "--show-current") == "main"


@pytest.mark.parametrize("args", [["--managed", "--ref", "main"], ["--checkout", "elsewhere"]])
def test_managed_flag_misuse_is_a_usage_error(workspace: Path, url: str, args: list[str]) -> None:
    result = runner.invoke(app, ["repo", "add", url, "--name", "demo", *args, "--workspace", str(workspace)])
    assert result.exit_code == 2
    assert not (workspace / "okf" / "repositories" / "demo.md").exists()


def _adoptable_workspace(workspace: Path, url: str) -> tuple[Path, Path]:
    source = workspace.parent / "demo"
    git(workspace.parent, "clone", "-q", url, str(source))
    manifest = workspace / "workspace.yaml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace('repositories:\n  "ws":\n    path: "."\n', "")
    manifest.write_text(text + "repositories:\n  demo:\n    path: ../demo\n", encoding="utf-8", newline="")
    git(workspace, "add", "workspace.yaml")
    git(workspace, "commit", "-qm", "declare demo")
    return workspace, source


def test_repo_adopt_dry_run_json(workspace: Path, url: str) -> None:
    root, source = _adoptable_workspace(workspace, url)
    result = runner.invoke(app, ["repo", "adopt", "demo", "--dry-run", "--json", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] and payload["dry_run"] and payload["track"] == "main"
    assert payload["clone"] == "okf/repositories/demo/references/git"
    assert source.is_dir()
    assert git(source, "branch", "--show-current") == "main"
    assert not (root / "okf/repositories/demo/references/git").exists()
    assert git(root, "status", "--porcelain") == ""


def test_repo_adopt_acts_and_renders_text(workspace: Path, url: str) -> None:
    root, source = _adoptable_workspace(workspace, url)
    result = runner.invoke(app, ["repo", "adopt", "demo", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    assert "adopted demo" in result.output and "okf/repositories/demo/references/git" in result.output
    assert not source.exists()
    assert git(root / ".gw/worktrees/demo/main", "branch", "--show-current") == "main"
    assert git(root, "status", "--porcelain") == ""


def test_repo_adopt_refusal_exits_nonzero_with_code(workspace: Path, url: str) -> None:
    root, source = _adoptable_workspace(workspace, url)
    result = runner.invoke(app, ["repo", "adopt", "nope", "--json", "--workspace", str(root)])
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert (error["command"], error["reason"]) == ("repo adopt", "refused")
    assert error["payload"]["refusal"]["code"] == "unknown-repository"
    assert source.is_dir()


def test_repo_adopt_human_preview_and_warnings() -> None:
    from graph_works_cli.repo_cli.rendering import adopt_text
    from graph_works_core.repositories.adopt import RepoAdoptResult

    result = RepoAdoptResult(
        "demo", "../demo", "clone", "checkout", "main", "a" * 40, False, ("legacy",), (), True, warnings=("attention",)
    )
    text = adopt_text(result)
    assert "would adopt demo: ../demo -> clone @ aaaaaaa" in text
    assert "checkout: checkout on main (reused)" in text
    assert "repaired worktrees: 1" in text and "warning: attention" in text


def test_repo_adopt_passes_explicit_track(workspace: Path, url: str) -> None:
    root, source = _adoptable_workspace(workspace, url)
    git(source, "checkout", "-qb", "feature")
    result = runner.invoke(
        app, ["repo", "adopt", "demo", "--track", "feature", "--dry-run", "--json", "--workspace", str(root)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["track"] == "feature"
    assert payload["checkout"] == ".gw/worktrees/demo/feature"
    assert git(source, "branch", "--show-current") == "feature"
