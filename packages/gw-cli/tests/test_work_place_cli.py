"""Placement CLI modes, effects, diagnostics, and refusal envelopes."""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from gw_cli import exit_codes
from gw_cli.cli import app
from gw_cli.work_cli import main as work_main
from gw_pipeline import place, transactions
from okf_io import parse
from typer.testing import CliRunner
from workflow_orca import _cli as orca_cli
from workflow_orca.port import OrcaCliPort

runner = CliRunner()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


@pytest.fixture(autouse=True)
def without_orca(monkeypatch: pytest.MonkeyPatch) -> None:
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **kw: None if name == "orca" else which(name, *a, **kw))


@pytest.fixture
def placed_item(workspace: Path, tmp_path: Path) -> tuple[str, Path]:
    repo = tmp_path / "code"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("pass\n", encoding="utf-8", newline="\n")
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.com"),
        ("add", "."),
        ("commit", "-q", "--allow-empty", "-m", "Initial"),
    ):
        _git(repo, *args)
    manifest = workspace / "workspace.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "repositories: {}", f"repositories:\n  code:\n    path: {repo.as_posix()}"
        ),
        encoding="utf-8",
        newline="\n",
    )
    filed = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Placed",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--affects",
            "src",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert filed.exit_code == 0, filed.output
    path = json.loads(filed.stdout)["path"]
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.com"),
        ("add", "."),
        ("commit", "-q", "-m", "Initial workspace"),
    ):
        _git(workspace, *args)
    return path, repo


def _args(workspace: Path, path: str | None = None, *extra: str) -> list[str]:
    return ["work", "place", *([] if path is None else [path]), "--workspace", str(workspace), *extra]


@pytest.mark.parametrize("explicit", [False, True])
def test_place_json_ensure(workspace: Path, placed_item: tuple[str, Path], explicit: bool) -> None:
    path, repo = placed_item
    result = runner.invoke(app, _args(workspace, path, "--json", *(["--ensure"] if explicit else [])))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["mode"] == "ensure" and payload["repo"] == {"name": "code", "path": str(repo)}
    assert payload["chain"][0]["action"] == "created"
    assert _git(Path(payload["placement"]["worktree"]), "branch", "--show-current") == payload["placement"]["branch"]


def test_place_refusal_exit_code(workspace: Path, placed_item: tuple[str, Path]) -> None:
    path, _repo = placed_item
    plan = runner.invoke(app, _args(workspace, path, "--dry-run", "--json"))
    assert plan.exit_code == 0, plan.output
    target = Path(json.loads(plan.stdout)["placement"]["worktree"])
    target.mkdir(parents=True)
    (target / "foreign.txt").write_text("keep me", encoding="utf-8", newline="")
    result = runner.invoke(app, _args(workspace, path, "--json"))
    assert result.exit_code == exit_codes.GENERIC
    error = json.loads(result.stdout)["error"]
    assert error["command"] == "work place" and error["reason"] == "refused"
    assert error["payload"]["refusal"]["kind"] == "foreign-checkout"
    assert "foreign-checkout" in result.stderr and (target / "foreign.txt").read_text(encoding="utf-8") == "keep me"


@pytest.mark.parametrize("modes", list(itertools.combinations(("ensure", "start", "remove", "prune"), 2)))
def test_place_modes_are_exclusive(workspace: Path, modes: tuple[str, str]) -> None:
    result = runner.invoke(app, _args(workspace, "work/feature-any", *(f"--{mode}" for mode in modes), "--json"))
    assert result.exit_code != 0
    error = json.loads(result.stdout)["error"]
    assert error["reason"] == "usage" and error["payload"] is None


@pytest.mark.parametrize("mode", [None, "ensure", "start", "remove", "prune"])
@pytest.mark.parametrize("path", [None, ""], ids=["omitted", "empty"])
def test_place_requires_path_unless_prune(workspace: Path, mode: str | None, path: str | None) -> None:
    result = runner.invoke(app, _args(workspace, path, *([] if mode is None else [f"--{mode}"]), "--json"))
    payload = json.loads(result.stdout)
    if mode == "prune":
        assert result.exit_code == 0, result.output
        assert payload["path"] is None and payload["mode"] == "prune"
        assert payload["pruned"] == {"removed_worktrees": [], "deleted_branches": [], "kept": []}
    else:
        assert result.exit_code != 0 and payload["error"]["reason"] == "usage"


def test_place_uses_null_notifier_without_orca(workspace: Path, placed_item: tuple[str, Path]) -> None:
    path, _repo = placed_item
    result = runner.invoke(app, _args(workspace, path, "--json"))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["chain"][0]["orca"] == "orca: not configured"


def test_place_dry_run_json(workspace: Path, placed_item: tuple[str, Path]) -> None:
    path, repo = placed_item
    before = _git(workspace, "rev-parse", "HEAD")
    branches = _git(repo, "branch", "--list")
    result = runner.invoke(app, _args(workspace, path, "--dry-run", "--json", "--repo-name", "code"))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True and payload["chain"][0]["action"] == "created"
    assert not Path(payload["placement"]["worktree"]).exists()
    assert _git(repo, "branch", "--list") == branches and _git(workspace, "rev-parse", "HEAD") == before


def test_place_start_and_remove_modes(workspace: Path, placed_item: tuple[str, Path]) -> None:
    path, repo = placed_item
    page = workspace / "okf" / f"{path}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("work_status: open", "work_status: in-progress\nphase: execute"),
        encoding="utf-8",
        newline="",
    )
    started = runner.invoke(app, _args(workspace, path, "--start", "--json"))
    assert started.exit_code == 0, started.output
    payload = json.loads(started.stdout)
    assert payload["mode"] == "start" and payload["start_sha"]["after"] == _git(repo, "rev-parse", "HEAD")
    removed = runner.invoke(app, _args(workspace, path, "--remove", "--json"))
    assert removed.exit_code != 0
    refusal = json.loads(removed.stdout)["error"]["payload"]
    assert refusal["mode"] == "remove" and refusal["refusal"]["kind"] == "not-finished"


@pytest.mark.parametrize("failure", ["preimage-race", "rollback-only"])
@pytest.mark.parametrize("json_output", [False, True])
def test_place_start_incomplete_apply_is_an_error(workspace, placed_item, monkeypatch, failure, json_output):
    path, _repo = placed_item
    page = workspace / "okf" / f"{path}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("work_status: open", "work_status: in-progress\nphase: execute"),
        encoding="utf-8",
        newline="",
    )
    real = place.apply_mutation

    def failed(layout, plan, **kwargs):
        if failure == "preimage-race":
            page.write_bytes(page.read_bytes() + b"\nConcurrent edit\n")
        application = real(layout, plan, **kwargs)
        if failure == "rollback-only":
            assert application.rolled_back
            application = replace(application, failures=())
        return application

    if failure == "rollback-only":
        monkeypatch.setattr(transactions, "_validate_postconditions", lambda *a, **kw: ("injected validation failure",))
    monkeypatch.setattr(place, "apply_mutation", failed)
    result = runner.invoke(app, _args(workspace, path, "--start", *(["--json"] if json_output else [])))

    assert result.exit_code == exit_codes.GENERIC
    assert "apply was incomplete" in result.stderr
    assert parse(page.read_text(encoding="utf-8")).fm_data().get("start_sha") is None
    if json_output:
        error = json.loads(result.stdout)["error"]
        assert error["reason"] == "incomplete-apply" and error["command"] == "work place"
        assert error["payload"]["start_sha"] == {"before": None, "after": None}
        assert error["payload"]["refusal"] is None
        assert bool(error["payload"]["warnings"]) is (failure == "preimage-race")
    else:
        assert result.stdout == ""


def test_place_start_continues_after_default_orca_transport_timeout(workspace, placed_item, monkeypatch):
    path, repo = placed_item
    page = workspace / "okf" / f"{path}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("work_status: open", "work_status: in-progress\nphase: execute"),
        encoding="utf-8",
        newline="",
    )
    which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name, *a, **kw: "/tools/orca" if name == "orca" else which(name, *a, **kw)
    )
    real_run = subprocess.run
    timeouts = []

    def stalled(argv, **kwargs):
        if argv[0] != "orca":
            return real_run(argv, **kwargs)
        timeouts.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(orca_cli.subprocess, "run", stalled)
    result = runner.invoke(app, _args(workspace, path, "--start", "--json"))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["chain"][0]["action"] == "created"
    assert payload["chain"][0]["orca"].startswith("orca: unavailable (")
    assert timeouts and all(timeout == 5 for timeout in timeouts)
    head = _git(repo, "rev-parse", "HEAD")
    assert payload["start_sha"]["after"] == head
    assert parse(page.read_text(encoding="utf-8")).fm_data()["start_sha"] == head
    assert Path(payload["placement"]["worktree"]).exists()


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize(
    ("exception", "reason", "code"),
    [
        (work_main.WorkspaceError("bad workspace"), "workspace", exit_codes.SCHEMA_MISMATCH),
        (ValueError("unresolved repository"), "unresolved", exit_codes.AMBIGUOUS),
        (OSError("disk failure"), "io", exit_codes.GENERIC),
    ],
)
def test_place_maps_core_exceptions(workspace, monkeypatch, json_output, exception, reason, code):
    def fail(*_args, **_kwargs):
        raise exception

    monkeypatch.setattr(work_main, "run_place", fail, raising=False)
    result = runner.invoke(app, _args(workspace, "work/feature-any", *(["--json"] if json_output else [])))
    assert result.exit_code == code and str(exception) in result.stderr
    if json_output:
        error = json.loads(result.stdout)["error"]
        assert error["reason"] == reason and error["command"] == "work place" and error["payload"] is None
    else:
        assert result.stdout == ""


def test_place_human_chain_and_commit(workspace: Path, placed_item: tuple[str, Path]) -> None:
    path, _repo = placed_item
    # Exercise a real workspace commit in addition to the chain rendering.
    page = workspace / "okf" / f"{path}.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nEdited\n", encoding="utf-8", newline="")
    result = runner.invoke(app, _args(workspace, path))
    assert result.exit_code == 0, result.output
    assert f"{path}: created " in result.stdout and "; orca: not configured" in result.stdout
    assert "[ok] committed " in result.stdout and "workspace: place feature-placed" in result.stdout
    again = runner.invoke(app, _args(workspace, path))
    assert again.exit_code == 0, again.output
    assert f"{path}: verified " in again.stdout and ", refresh none" in again.stdout


def test_place_human_prune_lists(workspace: Path, placed_item: tuple[str, Path]) -> None:
    path, _repo = placed_item
    ensured = runner.invoke(app, _args(workspace, path, "--json"))
    assert ensured.exit_code == 0, ensured.output
    result = runner.invoke(app, _args(workspace, path, "--prune"))
    assert result.exit_code == 0, result.output
    assert "removed worktrees:" in result.stdout and "deleted branches:" in result.stdout
    assert "kept:" in result.stdout and path in result.stdout and "open" in result.stdout


@pytest.mark.parametrize("json_output", [False, True])
def test_place_warns_for_workspace_only_item(workspace: Path, json_output: bool) -> None:
    filed = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Notes",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--affects",
            "gw:workspace",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert filed.exit_code == 0, filed.output
    path = json.loads(filed.stdout)["path"]
    result = runner.invoke(app, _args(workspace, path, *(["--json"] if json_output else [])))
    assert result.exit_code == 0, result.output
    assert "[warn] no-repo:" in result.stderr
    if json_output:
        assert json.loads(result.stdout)["warnings"][0].startswith("no-repo:")


def test_place_notifies_orca_when_available(workspace: Path, placed_item: tuple[str, Path], monkeypatch) -> None:
    path, repo = placed_item
    which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name, *a, **kw: "/tools/orca" if name == "orca" else which(name, *a, **kw)
    )
    # Keep the real notifier; replace only the external Orca port operations.
    monkeypatch.setattr(OrcaCliPort, "repo_show", lambda self, selector: None)
    monkeypatch.setattr(
        OrcaCliPort,
        "worktree_show",
        lambda self, selector: {
            "id": "wt-test",
            "repo_id": "repo-test",
            "path": selector.removeprefix("path:"),
            "branch": None,
            "display_name": "Code",
            "is_main": None,
            "parent_id": None,
            "comment": None,
        },
    )
    notifications = []
    monkeypatch.setattr(
        OrcaCliPort, "worktree_set", lambda self, selector, **kwargs: notifications.append((selector, kwargs))
    )
    result = runner.invoke(app, _args(workspace, path, "--json"))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["chain"][0]["orca"] == "orca: linked under Code"
    assert notifications == [
        (f"path:{payload['placement']['worktree']}", {"display_name": "feature-placed", "parent": f"path:{repo}"})
    ]
