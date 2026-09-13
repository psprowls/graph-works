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
        "deleted",
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


def test_bootstrap_refuses_before_any_path_when_long_paths_are_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression this item was filed for: `gw bootstrap` must refuse
    with the existing long-path message, before creating anything, rather
    than raising an unhandled `FileNotFoundError` partway through."""
    from graph_works_core.workspace import anchors

    monkeypatch.setattr(anchors, "long_paths_enabled", lambda: False)
    root = tmp_path / "works"

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])

    assert result.exit_code == 1
    assert "long path support" in result.stderr
    assert "LongPathsEnabled" in result.stderr
    assert not root.exists()


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
    declared = (root / "workspace.yaml").read_text(encoding="utf-8")
    assert "repositories: {}" not in declared
    assert '  "repo":' in declared
    assert not (root / ".gw" / "_repositories.yaml").exists()


def test_omitting_repo_root_keeps_the_walk_up(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root = repo / ".works"

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])

    assert result.exit_code == 0
    assert '  "repo":' in (root / "workspace.yaml").read_text(encoding="utf-8")
    assert not (root / ".gw" / "_repositories.yaml").exists()


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


def test_bootstrap_previews_then_deletes_a_stale_bundle_context_pair(tmp_path: Path) -> None:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root), "--json"]).exit_code == 0
    stale_agents = root / "okf" / "AGENTS.md"
    stale_claude = root / "okf" / "CLAUDE.md"
    stale_agents.write_text("# stale\n", encoding="utf-8")
    stale_claude.write_text("@AGENTS.md\n", encoding="utf-8")

    preview = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root), "--dry-run", "--json"])
    assert preview.exit_code == 0
    assert "- okf/AGENTS.md" in json.loads(preview.stdout)["planned"]
    assert stale_agents.is_file()

    applied = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root), "--json"])
    assert applied.exit_code == 0
    payload = json.loads(applied.stdout)
    assert payload["changed"] is True
    assert payload["deleted"] == ["okf/AGENTS.md", "okf/CLAUDE.md"]
    assert "okf/AGENTS.md" not in payload["written"]
    assert not stale_agents.exists()
    assert not stale_claude.exists()


def test_bootstrap_text_output_shows_a_deletion(tmp_path: Path) -> None:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    (root / "okf" / "AGENTS.md").write_text("# stale\n", encoding="utf-8")

    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])

    assert result.exit_code == 0
    assert "- okf/AGENTS.md" in result.stdout.splitlines()


def test_bootstrap_seeds_dispatch_and_refuses_old_settings(tmp_path):
    root = tmp_path / "workspace"
    args = ["bootstrap", "--workspace", str(root), "--topic", "Demo"]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert (root / "dispatch.yaml").exists()
    assert not (root / "dispatch.local.yaml").exists()
    before = (root / "workspace.yaml").read_bytes()
    second = runner.invoke(app, args)
    assert second.exit_code == 0
    assert "+ " not in second.stdout
    assert (root / "workspace.yaml").read_bytes() == before
    (root / "workspace.local.yaml").write_text("workflow: {pipeline: {}}\n", encoding="utf-8")
    refused = runner.invoke(app, args)
    assert refused.exit_code != 0
    assert "retired key workflow.pipeline" in refused.output
    assert (root / "workspace.yaml").read_bytes() == before
