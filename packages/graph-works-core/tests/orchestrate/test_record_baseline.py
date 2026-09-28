"""`gw work record-baseline`: the attended execute stage's starting commit."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from graph_works_core.orchestrate.placement import run_record_baseline
from test_orchestrate_shell import TODAY, _code_repo, _initialized_workspace, _ready

PATH = "work/feature-a"


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _setup(tmp_path: Path):
    layout = _initialized_workspace(tmp_path)
    repo, fork = _code_repo(tmp_path / "c")
    kept = re.sub(r"(?m)^repositories:.*\n(?:[ \t]+.*\n)*", "", layout.manifest_path.read_text(encoding="utf-8"))
    layout.manifest_path.write_text(
        kept + f"repositories:\n  code:\n    path: {json.dumps(str(repo))}\n", encoding="utf-8", newline="\n"
    )
    return layout, repo, fork


def test_an_attended_execute_records_head_once(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    _ready(layout, PATH)
    first = run_record_baseline(layout, PATH, cwd=repo, today=TODAY, dry_run=False)
    assert first.plan.refusal is None and first.written and first.plan.after == _head(repo)
    (repo / "packages/a/x.py").write_text("three\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "work"], cwd=repo, check=True, capture_output=True)
    again = run_record_baseline(layout, PATH, cwd=repo, today=TODAY, dry_run=False)
    assert again.plan.refusal is None and not again.plan.changed and again.plan.after == first.plan.after


def test_a_cwd_outside_the_items_repository_refuses(tmp_path: Path) -> None:
    layout, _repo, _fork = _setup(tmp_path)
    other, _ = _code_repo(tmp_path / "other")
    _ready(layout, PATH)
    result = run_record_baseline(layout, PATH, cwd=other, today=TODAY, dry_run=False)
    assert result.plan.refusal == "outside-repository" and not result.written


def test_a_diverged_head_refuses_baseline_conflict(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    _ready(layout, PATH, extra=f"start_sha: {_head(repo)}\n")
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    result = run_record_baseline(layout, PATH, cwd=repo, today=TODAY, dry_run=False)
    assert result.plan.refusal == "baseline-conflict"


def test_a_non_execute_item_refuses(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    _ready(layout, PATH, phase="plan")
    assert run_record_baseline(layout, PATH, cwd=repo, today=TODAY, dry_run=False).plan.refusal == "not-execute"
