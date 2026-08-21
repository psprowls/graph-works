"""`gw bootstrap` — initialize a prospective workspace through the public CLI."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from graph_works_cli.cli import app
from graph_works_core import InitError
from typer.testing import CliRunner

runner = CliRunner()


@pytest.mark.parametrize(
    ("workspace", "pinned", "in_repo", "expected_relative"),
    [
        ("explicit", "pinned", True, "explicit"),
        ("", "pinned", True, "pinned"),
        ("", "", True, "repo/.works"),
        ("", "", False, "outside/.works"),
    ],
    ids=("explicit-over-environment", "environment-over-repository", "repository-default", "cwd-default"),
)
def test_bootstrap_resolves_the_prospective_root_before_a_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workspace: str,
    pinned: str,
    in_repo: bool,
    expected_relative: str,
) -> None:
    cwd = tmp_path / "repo" / "nested" if in_repo else tmp_path / "outside"
    cwd.mkdir(parents=True)
    if in_repo:
        (tmp_path / "repo" / ".git").mkdir()
    monkeypatch.chdir(cwd)
    if pinned:
        monkeypatch.setenv("GRAPH_WORKS_DIR", str(tmp_path / pinned))
    else:
        monkeypatch.delenv("GRAPH_WORKS_DIR", raising=False)

    args = ["bootstrap", "--topic", "Demo", "--json"]
    if workspace:
        args.extend(("--workspace", str(tmp_path / workspace)))
    result = runner.invoke(app, args)

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["workspace"] == str((tmp_path / expected_relative).resolve())
    assert Path(payload["workspace"]).joinpath("workspace.yaml").is_file()


def test_bootstrap_requires_topic() -> None:
    result = runner.invoke(app, ["bootstrap"])

    assert result.exit_code == 2
    assert "Missing option '--topic'" in result.stderr


def test_bootstrap_applies_by_default(tmp_path: Path) -> None:
    root = tmp_path / "works"

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])

    assert result.exit_code == 0
    assert "+ workspace.yaml" in result.stdout
    assert 'topic: "Demo"' in (root / "workspace.yaml").read_text(encoding="utf-8")


def test_bootstrap_json_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "works"

    first = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root), "--json"])
    second = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root), "--json"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert json.loads(first.stdout)["changed"] is True
    assert json.loads(second.stdout)["changed"] is False
    assert set(json.loads(second.stdout)) == {
        "ok",
        "changed",
        "workspace",
        "bundle_dir",
        "config_dir",
        "cache_dir",
        "created",
        "written",
    }


def test_bootstrap_preserves_an_authored_file(tmp_path: Path) -> None:
    root = tmp_path / "works"
    authored = root / "okf" / "index.md"
    authored.parent.mkdir(parents=True)
    authored.write_text("# My authored index\n", encoding="utf-8")

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])

    assert result.exit_code == 1
    assert authored.read_text(encoding="utf-8") == "# My authored index\n"


def test_bootstrap_refusal_does_not_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from graph_works_cli.wiki_cli import bootstrap as bootstrap_module

    root = tmp_path / "works"
    plan = SimpleNamespace(ok=False)
    calls: list[object] = []
    monkeypatch.setattr(bootstrap_module, "plan_init", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(bootstrap_module, "apply_init", calls.append)

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])

    assert result.exit_code == 1
    assert "workspace initialization plan was refused" in result.stderr
    assert calls == []


@pytest.mark.parametrize("removed_flag", ("--tool", "--force"))
def test_bootstrap_rejects_removed_flags(removed_flag: str) -> None:
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", removed_flag, "anything"])

    assert result.exit_code == 2
    assert f"No such option: {removed_flag}" in result.stderr


def test_bootstrap_reports_a_refused_plan_construction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A plan that cannot be built at all must name its reason instead of raising."""
    from graph_works_cli.wiki_cli import bootstrap as bootstrap_module

    def fail(*_args: object, **_kwargs: object) -> object:
        raise InitError("workspace path is a file")

    monkeypatch.setattr(bootstrap_module, "plan_init", fail)

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(tmp_path / "works")])

    assert result.exit_code == 1
    assert "Error: workspace path is a file" in result.stderr


def test_bootstrap_fails_when_initialization_is_incomplete(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A partially created workspace must not report success to the caller that will use it."""
    from graph_works_cli.wiki_cli import bootstrap as bootstrap_module

    monkeypatch.setattr(bootstrap_module, "plan_init", lambda *_args, **_kwargs: SimpleNamespace(ok=True))
    monkeypatch.setattr(
        bootstrap_module, "apply_init", lambda _plan: SimpleNamespace(ok=False, diff=lambda: "partial workspace")
    )

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(tmp_path / "works")])

    assert result.exit_code == 1
    assert result.stdout == "partial workspace\n"
    assert "workspace initialization was incomplete" in result.stderr


def test_repo_root_pins_a_repository_the_walk_up_cannot_find(tmp_path: Path) -> None:
    """The §1.2 regression: an out-of-repo workspace must still catalog its repo.

    `plan_init` accepts `repo_root=` precisely because a workspace outside the
    repo it catalogs is a walk-up `find_repo_root` cannot find. Dropping the
    parameter writes `repositories: {}`, and everything downstream scans nothing.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root = tmp_path / "outside" / "works"

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root), "--repo-root", str(repo)])

    assert result.exit_code == 0
    declared = (root / "_gw" / "_repositories.yaml").read_text(encoding="utf-8")
    assert "repositories: {}" not in declared
    assert "  repo:" in declared


def test_omitting_repo_root_keeps_the_walk_up(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root = repo / ".works"

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])

    assert result.exit_code == 0
    assert "  repo:" in (root / "_gw" / "_repositories.yaml").read_text(encoding="utf-8")


def test_dry_run_renders_the_plan_and_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "works"

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root), "--dry-run"])

    assert result.exit_code == 0
    assert "+ workspace.yaml" in result.stdout
    assert not root.exists()


def test_dry_run_json_keys_mirror_the_applied_payload(tmp_path: Path) -> None:
    root = tmp_path / "works"

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root), "--dry-run", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {"ok", "changed", "workspace", "bundle_dir", "config_dir", "cache_dir", "planned"}
    assert payload["changed"] is True
    assert "+ workspace.yaml" in payload["planned"]
    assert not root.exists()
