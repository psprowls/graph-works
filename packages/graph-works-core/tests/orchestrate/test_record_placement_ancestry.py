"""`run_record_placement` keeps a recorded baseline across a pair change only on proved ancestry."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from graph_works_core.orchestrate.placement import run_record_placement
from graph_works_core.work import commands as work
from graph_works_core.workspace.provenance import GIT_ENV
from okf_io import load
from test_orchestrate_shell import TODAY, _initialized_workspace, _write

PATH = "work/feature-p"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _repo(where: Path) -> tuple[Path, str]:
    where.mkdir(parents=True)
    _git(where, "init", "-q", "-b", "main")
    (where / "packages/a").mkdir(parents=True)
    (where / "packages/a/x.py").write_text("one\n", encoding="utf-8", newline="\n")
    _git(where, "add", ".")
    _git(where, "commit", "-qm", "first")
    return where, _git(where, "rev-parse", "HEAD")


def _island(repo: Path, where: Path) -> None:
    """A linked worktree on an orphan branch: history unrelated to everything else."""
    _git(repo, "worktree", "add", "-q", "--orphan", "-b", "island", str(where))
    _git(where, "commit", "-q", "--allow-empty", "-m", "unrelated")


def _setup(tmp_path: Path, *, repos: tuple[str, ...] = ("code",)):
    layout = _initialized_workspace(tmp_path)
    made = {name: _repo(tmp_path / name) for name in repos}
    kept = re.sub(r"(?m)^repositories:.*\n(?:[ \t]+.*\n)*", "", layout.manifest_path.read_text(encoding="utf-8"))
    declared = "".join(f"  {n}:\n    path: {json.dumps(str(r))}\n" for n, (r, _) in made.items())
    layout.manifest_path.write_text(kept + "repositories:\n" + declared, encoding="utf-8", newline="\n")
    return layout, made


def _item(layout, extra: str) -> None:
    _write(layout, PATH, phase="execute", work_status="in-progress", extra=extra + "repo: code\n")
    assert work.run_regen_indexes(layout, dry_run=False).application.ok


def _place(layout, worktree: Path | str, branch: str, **kw):
    return run_record_placement(
        layout, PATH, root=PATH, phase="execute", worktree=str(worktree), branch=branch, today=TODAY, **kw
    )


def _fm(layout) -> dict:
    return load(layout.bundle_dir / f"{PATH}.md").fm_data()


def test_a_first_pair_keeps_a_proved_ancestor(tmp_path: Path) -> None:
    layout, made = _setup(tmp_path)
    repo, c1 = made["code"]
    _git(repo, "worktree", "add", "-q", "-b", "feature/p", str(tmp_path / "wt"), c1)
    _item(layout, f"start_sha: {c1}\n")
    record = _place(layout, tmp_path / "wt", "feature/p", dry_run=False)
    assert record.plan.refusal is None and record.written and _fm(layout)["start_sha"] == c1


def test_a_changed_pair_keeps_an_ancestor_and_drops_an_unrelated_destination(tmp_path: Path) -> None:
    layout, made = _setup(tmp_path)
    repo, c1 = made["code"]
    _git(repo, "worktree", "add", "-q", "-b", "feature/new", str(tmp_path / "new"), c1)
    _git(tmp_path / "new", "commit", "-q", "--allow-empty", "-m", "ahead")
    _island(repo, tmp_path / "island")
    _item(layout, f"worktree: {(tmp_path / 'old').as_posix()}\nbranch: old\nstart_sha: {c1}\n")
    assert _place(layout, tmp_path / "new", "feature/new").plan.start_after == c1
    dropped = _place(layout, tmp_path / "island", "island", dry_run=False)
    assert dropped.plan.refusal is None and "start_sha" not in _fm(layout)


def test_require_start_sha_sees_the_dropped_baseline(tmp_path: Path) -> None:
    layout, made = _setup(tmp_path)
    repo, c1 = made["code"]
    _island(repo, tmp_path / "island")
    _item(layout, f"worktree: {(tmp_path / 'old').as_posix()}\nbranch: old\nstart_sha: {c1}\n")
    assert _place(layout, tmp_path / "island", "island", require_start_sha=True).plan.refusal == "baseline-missing"


@pytest.mark.parametrize("case", ["missing-destination", "other-repository", "missing-commit", "broken-git"])
def test_unreadable_evidence_refuses_without_writing(tmp_path: Path, case: str) -> None:
    layout, made = _setup(tmp_path, repos=("code", "other"))
    repo, c1 = made["code"]
    other, _ = made["other"]
    _git(repo, "worktree", "add", "-q", "-b", "feature/new", str(tmp_path / "new"), c1)
    recorded = "f" * 40 if case == "missing-commit" else c1
    _item(layout, f"worktree: {(tmp_path / 'old').as_posix()}\nbranch: old\nstart_sha: {recorded}\n")
    destination = {"missing-destination": tmp_path / "gone", "other-repository": other}.get(case, tmp_path / "new")
    environ = {**os.environ, GIT_ENV: str(tmp_path / "no-git")} if case == "broken-git" else None
    before = (layout.bundle_dir / f"{PATH}.md").read_bytes()
    record = _place(layout, destination, "feature/new", dry_run=False, environ=environ)
    assert record.plan.refusal == "git-unavailable" and not record.written
    assert (layout.bundle_dir / f"{PATH}.md").read_bytes() == before


def test_no_proof_needed_means_no_git(tmp_path: Path) -> None:
    layout, made = _setup(tmp_path)
    _repo_path, c1 = made["code"]
    broken = {**os.environ, GIT_ENV: str(tmp_path / "no-git")}
    _item(layout, f"worktree: {(tmp_path / 'wt').as_posix()}\nbranch: b\nstart_sha: {c1}\n")
    assert _place(layout, tmp_path / "wt", "b", environ=broken).plan.refusal is None
    _write(layout, PATH, phase="execute", work_status="in-progress", extra="repo: code\n")
    assert _place(layout, tmp_path / "elsewhere", "c", environ=broken).plan.refusal is None


def test_a_symlinked_destination_still_proves_ancestry(tmp_path: Path) -> None:
    layout, made = _setup(tmp_path)
    repo, c1 = made["code"]
    _git(repo, "worktree", "add", "-q", "-b", "feature/new", str(tmp_path / "new"), c1)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(tmp_path / "new", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this host")
    _item(layout, f"worktree: {(tmp_path / 'old').as_posix()}\nbranch: old\nstart_sha: {c1}\n")
    assert _place(layout, alias, "feature/new").plan.start_after == c1


def test_a_foreign_stamp_proves_against_its_own_repository_and_baseline(tmp_path: Path) -> None:
    layout, made = _setup(tmp_path, repos=("code", "ui"))
    _code, c1 = made["code"]
    ui, u1 = made["ui"]
    _git(ui, "worktree", "add", "-q", "-b", "feature/u2", str(tmp_path / "u2"), u1)
    stamps = f"repo_stamps:\n  ui:\n    worktree: {(tmp_path / 'u1').as_posix()}\n    branch: u1\n    start_sha: {u1}\n"
    _item(layout, f"start_sha: {c1}\n{stamps}")
    record = _place(layout, tmp_path / "u2", "feature/u2", repo="ui", dry_run=False)
    assert record.plan.refusal is None and record.written
    data = _fm(layout)
    assert data["repo_stamps"]["ui"]["start_sha"] == u1 and data["start_sha"] == c1


def test_a_workspace_stamp_proves_against_the_workspace_repository(tmp_path: Path) -> None:
    layout, _made = _setup(tmp_path)
    _git(layout.root, "init", "-q", "-b", "main")
    _git(layout.root, "add", ".")
    _git(layout.root, "commit", "-qm", "workspace")
    w1 = _git(layout.root, "rev-parse", "HEAD")
    _git(layout.root, "worktree", "add", "-q", "-b", "ws/p", str(tmp_path / "ws-p"), w1)
    stamps = (
        f"repo_stamps:\n  _workspace:\n    worktree: {(tmp_path / 'ws-old').as_posix()}\n"
        f"    branch: ws/old\n    start_sha: {w1}\n"
    )
    _item(layout, stamps)
    record = _place(layout, tmp_path / "ws-p", "ws/p", repo="_workspace")
    assert record.plan.refusal is None and record.plan.start_after == w1


def test_the_decision_rereads_the_baseline_it_proves(tmp_path: Path) -> None:
    layout, made = _setup(tmp_path)
    repo, c1 = made["code"]
    _git(repo, "worktree", "add", "-q", "-b", "feature/new", str(tmp_path / "new"), c1)
    _island(repo, tmp_path / "island")
    island = _git(tmp_path / "island", "rev-parse", "HEAD")
    _item(layout, f"worktree: {(tmp_path / 'old').as_posix()}\nbranch: old\nstart_sha: {c1}\n")
    assert _place(layout, tmp_path / "new", "feature/new").plan.start_after == c1
    _item(layout, f"worktree: {(tmp_path / 'old').as_posix()}\nbranch: old\nstart_sha: {island}\n")
    live = _place(layout, tmp_path / "new", "feature/new", dry_run=False)
    assert live.plan.start_after is None and "start_sha" not in _fm(layout)
