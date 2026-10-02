"""One repository's structural scan through plan/apply: no model, no other repository touched."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from _transaction_helpers import _git, _init_git
from code_wiki_okf.config import Config
from graph_works_core import apply_init, plan_init
from graph_works_core.scan import RepoScanRun, run_repo_scan
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.transactions import _HELD_BUNDLE_LOCKS
from ruamel.yaml import YAML
from scan_helpers import TODAY, make_repo

AT = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


def _repo(tmp_path: Path, name: str) -> Path:
    """A committed checkout whose directory, and so its graph identity, is *name*."""
    return make_repo(tmp_path / f"{name}-src").rename(tmp_path / name)


@pytest.fixture
def two_repo_workspace(tmp_path: Path) -> tuple[WorkspaceLayout, Config]:
    alpha, beta = _repo(tmp_path, "alpha"), _repo(tmp_path, "beta")
    layout = apply_init(plan_init(tmp_path / ".works", today=TODAY, topic="Scan")).layout
    yaml = YAML()
    yaml.preserve_quotes = True
    with layout.manifest_path.open(encoding="utf-8") as handle:
        data = yaml.load(handle)
    data["repositories"] = {"alpha": {"path": str(alpha)}, "beta": {"path": str(beta)}}
    data["state_gate"] = {"enabled": False}
    with layout.manifest_path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)
    return layout, load_workspace_config(layout)


def _bundle_snapshot(layout: WorkspaceLayout) -> dict[str, bytes]:
    root = layout.bundle_dir
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _git_init_workspace(layout: WorkspaceLayout) -> None:
    _init_git(layout.root)


def _git_status_porcelain(root: Path, path: str) -> str:
    return _git(root, "status", "--porcelain", "--untracked-files=all", "--", path)


def test_plan_writes_nothing_under_the_bundle(two_repo_workspace: tuple[WorkspaceLayout, Config]) -> None:
    layout, config = two_repo_workspace
    before = _bundle_snapshot(layout)
    run = run_repo_scan(layout, config, repo="alpha", at=AT, today=AT.date())
    assert run.refusal is None and run.applied is False
    assert run.structural.entities.created
    assert all(p.startswith("code-graph/alpha") for p in run.structural.entities.created)
    assert _bundle_snapshot(layout) == before


def test_apply_writes_only_the_scoped_repository_and_commits(
    two_repo_workspace: tuple[WorkspaceLayout, Config],
) -> None:
    layout, config = two_repo_workspace
    _git_init_workspace(layout)  # workspace root is its own git repo -> auto mode commits
    run_repo_scan(layout, config, repo="beta", at=AT, today=AT.date(), dry_run=False)
    beta_before = _tree(layout.bundle_dir / "code-graph" / "beta")
    run = run_repo_scan(layout, config, repo="alpha", at=AT, today=AT.date(), dry_run=False)
    assert run.applied and run.commit is not None and run.commit.status == "committed"
    assert run.commit.subject == "workspace: scan alpha"
    assert _tree(layout.bundle_dir / "code-graph" / "beta") == beta_before
    assert _git_status_porcelain(layout.root, "okf") == ""  # everything the scan wrote is committed


def test_rescan_with_nothing_changed_makes_no_commit(two_repo_workspace: tuple[WorkspaceLayout, Config]) -> None:
    layout, config = two_repo_workspace
    _git_init_workspace(layout)
    run_repo_scan(layout, config, repo="alpha", at=AT, today=AT.date(), dry_run=False)
    head = _git(layout.root, "rev-parse", "HEAD")
    again = run_repo_scan(layout, config, repo="alpha", at=AT, today=AT.date(), dry_run=False)
    assert again.applied and not again.structural.errors
    assert again.commit is None or again.commit.status == "skipped"
    assert _git(layout.root, "rev-parse", "HEAD") == head


def test_unknown_repository_is_refused_before_building(
    two_repo_workspace: tuple[WorkspaceLayout, Config], monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, config = two_repo_workspace
    from graph_works_core.graph import commands as graph

    def _no_build(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("an unknown repository must be refused before any build")

    monkeypatch.setattr(graph, "build", _no_build)
    run = run_repo_scan(layout, config, repo="nope", at=AT, today=AT.date())
    assert run.refusal == "unknown-repository"
    assert run.detail is not None and "nope" in run.detail


def test_failed_build_is_refused_with_its_error(
    two_repo_workspace: tuple[WorkspaceLayout, Config], monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, config = two_repo_workspace
    from graph_works_core.graph import commands as graph

    monkeypatch.setattr(graph, "build", lambda *_a, **_k: graph.GraphResult(1, "", "error: boom"))
    before = _bundle_snapshot(layout)
    seen: list[RepoScanRun] = []
    run = run_repo_scan(layout, config, repo="alpha", at=AT, today=AT.date(), dry_run=False, before_apply=seen.append)
    assert (run.refusal, run.detail, run.applied) == ("graph-build-failed", "error: boom", False)
    assert seen == [run]
    assert _bundle_snapshot(layout) == before


def test_before_apply_gets_the_plan_candidate(two_repo_workspace: tuple[WorkspaceLayout, Config]) -> None:
    layout, config = two_repo_workspace
    dry = run_repo_scan(layout, config, repo="alpha", at=AT, today=AT.date())
    before = _bundle_snapshot(layout)
    held = str(layout.bundle_dir.resolve())
    seen: list[RepoScanRun] = []
    observed: list[tuple[bool, bool]] = []

    def _check(candidate: RepoScanRun) -> None:
        seen.append(candidate)
        observed.append((held in _HELD_BUNDLE_LOCKS.get(), _bundle_snapshot(layout) == before))

    run_repo_scan(layout, config, repo="alpha", at=AT, today=AT.date(), dry_run=False, before_apply=_check)
    assert seen == [dry]
    assert observed == [(True, True)]  # under the bundle lock, before any bundle write


def test_before_apply_raising_writes_nothing(two_repo_workspace: tuple[WorkspaceLayout, Config]) -> None:
    layout, config = two_repo_workspace
    before = _bundle_snapshot(layout)

    def _abort(_candidate: RepoScanRun) -> None:
        raise RuntimeError("stale")

    with pytest.raises(RuntimeError, match="stale"):
        run_repo_scan(layout, config, repo="alpha", at=AT, today=AT.date(), dry_run=False, before_apply=_abort)
    assert _bundle_snapshot(layout) == before
