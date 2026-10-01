from __future__ import annotations

import os
import shutil
from pathlib import Path

from adopt_fixture import adoptable
from gitrepo import git
from graph_works_core.lint_drift.lint import run_mechanical
from graph_works_core.repositories.adopt import run_repo_adopt
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.repos import declared_repositories, resolve_repos
from okf_io import load
from repositories_okf.pin import read_pin
from workspace_fixture import NOW


def _link_is_relative(worktree: Path) -> bool:
    link = (worktree / ".git").read_text(encoding="utf-8").removeprefix("gitdir: ").strip()
    return not Path(link).is_absolute()


def test_adopt_moves_detaches_links_and_records_in_one_commit(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    layout = fixture.layout
    commits_before = int(git(layout.root, "rev-list", "--count", "HEAD"))
    result = run_repo_adopt(layout, "demo", now=NOW)
    assert result.ok, result.refusal
    assert result.commit_outcome is not None and result.commit_outcome.status == "committed"

    clone = layout.bundle_dir / "repositories/demo/references/git"
    checkout = layout.worktrees_dir / "demo" / "main"
    assert not fixture.source.exists()
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert git(clone, "rev-parse", "HEAD") == fixture.head
    assert git(checkout, "symbolic-ref", "--short", "HEAD") == "main"
    for worktree in (*fixture.linked, checkout):
        assert (
            Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
            == (clone / ".git").resolve()
        )
        assert _link_is_relative(worktree)
    assert git(fixture.linked[0], "symbolic-ref", "--short", "HEAD") == "feature-a"

    page = load(layout.bundle_dir / "repositories/demo.md")
    assert page.fm_data()["type"] == "ManagedRepository"
    assert page.fm_data()["url"] == fixture.upstream.url
    assert page.fm_data()["track"] == "main"
    pin = read_pin(page)
    assert pin is not None and pin.commit == fixture.head and pin.previous is None
    assert pin.generation is not None and pin.generation.scan_config_hash

    manifest = layout.manifest_path.read_bytes().decode("utf-8")
    checkout_rel = Path(os.path.relpath(checkout, layout.root)).as_posix()
    assert manifest == fixture.manifest_before.replace(
        "    path: ../demo  # sibling clone\n",
        f"    path: okf/repositories/demo/references/git  # sibling clone\n    checkout: {checkout_rel}\n",
    )
    assert declared_repositories(layout)["demo"] == checkout.resolve()

    assert int(git(layout.root, "rev-list", "--count", "HEAD")) == commits_before + 1
    assert git(layout.root, "log", "-1", "--format=%s") == "workspace: adopt managed repository demo"
    committed = git(layout.root, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert "workspace.yaml" in committed
    assert not [path for path in committed if "references/git" in path]
    assert git(layout.root, "status", "--porcelain") == ""
    assert "repo-adopt" in (layout.bundle_dir / "log.md").read_text(encoding="utf-8")


def test_the_lane_lint_is_silent_after_adopt(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    assert run_repo_adopt(fixture.layout, "demo", now=NOW).ok
    report = run_mechanical(
        fixture.layout,
        load_workspace_config(fixture.layout),
        today=NOW.date(),
        repo_roots=resolve_repos(fixture.layout),
    )
    findings = [finding for lane in report.mechanical for finding in lane.report.findings]
    codes = sorted({finding.code for finding in findings if finding.code.startswith("repository.")})
    errors = [f for f in findings if f.severity == "error" and str(f.path or "").startswith("repositories/")]
    assert codes == []
    assert errors == []


def test_bootstrap_case_reuses_an_existing_track_worktree(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path, linked=1)
    checkout = fixture.layout.worktrees_dir / "demo" / "main"
    checkout.parent.mkdir(parents=True, exist_ok=True)
    git(fixture.source, "switch", "-q", "--detach")
    git(fixture.source, "worktree", "add", "-q", str(checkout), "main")
    result = run_repo_adopt(fixture.layout, "demo", track="main", now=NOW)
    assert result.ok, result.refusal
    assert result.checkout_created is False
    assert git(checkout, "symbolic-ref", "--short", "HEAD") == "main"
    assert _link_is_relative(checkout)


def test_a_prunable_worktree_is_skipped_and_reported(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    shutil.rmtree(fixture.linked[1])
    result = run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert result.ok, result.refusal
    assert any("prunable" in warning and str(fixture.linked[1]) in warning for warning in result.warnings)
    assert _link_is_relative(fixture.linked[0])
