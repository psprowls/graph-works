"""Four arms in precedence order, each degrading into the next.

The merge-base arm exists because arms 2-4 cannot fire in this workspace's
split topology: the spec has no git history in the code repo at all, and a
freshly brainstormed spec carries neither a `## Reconciled` heading nor a
`**Baseline commit:**` line.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from graph_works_core.workspace import anchor
from okf_io import Source
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import SPEC_SOURCE_ID


def _item(path: str, **overrides: object) -> WorkItem:
    base: dict[str, object] = {
        "path": path,
        "page_path": f"{path}.md",
        "basename": path.rsplit("/", 1)[-1],
        "archived": False,
        "type": "Feature",
        "title": path,
        "description": "d",
        "status": "stable",
        "work_status": "open",
        "phase": "execute",
        "effort": "medium",
        "blast_radius": None,
        "target": None,
        "opened": "2026-08-01",
        "updated": "2026-08-01",
        "affects": (),
        "parent_path": None,
        "ancestor_paths": (),
        "active_child_paths": (),
        "archived_child_paths": (),
        "dependency_edges": (),
        "dependency_issues": (),
        "owner": None,
        "resolved_in": None,
        "worktree": None,
        "branch": None,
        "superseded_by": None,
        "tags": (),
        "sources": (),
        "has_design_artifact": True,
        "has_plan_artifact": False,
        "version": None,
        "target_date": None,
        "released_at": None,
    }
    base.update(overrides)
    return WorkItem(**base)  # type: ignore[arg-type]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _sha(cwd: Path, ref: str = "HEAD") -> str:
    return subprocess.run(["git", "rev-parse", ref], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "code"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-m", "first")
    return root


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """The split topology: a spec directory that is NOT the code repo."""
    root = tmp_path / "vault"
    root.mkdir()
    return root


def test_arm_one_uses_the_specs_own_git_history(repo: Path) -> None:
    spec = repo / "spec.md"
    spec.write_text("# spec\n", encoding="utf-8")
    _git(repo, "add", "spec.md")
    _git(repo, "commit", "-m", "add spec")
    assert anchor.resolve_anchor(repo, spec, "# spec\n") == (_sha(repo), "spec-git-history")


def test_arm_two_reads_the_last_reconciled_heading(repo: Path, vault: Path) -> None:
    head = _sha(repo)
    text = f"## Reconciled 2026-08-12 (0000000..{head})\n"
    assert anchor.resolve_anchor(repo, vault / "spec.md", text) == (head, "last-reconciled-heading")


def test_arm_three_reads_the_baseline_commit_line(repo: Path, vault: Path) -> None:
    head = _sha(repo)
    text = f"**Baseline commit:** `{head}`\n"
    assert anchor.resolve_anchor(repo, vault / "spec.md", text) == (head, "baseline-commit")


def test_an_unreachable_stamped_sha_degrades_to_no_anchor(repo: Path, vault: Path) -> None:
    text = "**Baseline commit:** `deadbeefdeadbeefdeadbeefdeadbeefdeadbeef`\n"
    assert anchor.resolve_anchor(repo, vault / "spec.md", text) == (None, "none")


def test_no_repo_has_no_anchor(vault: Path) -> None:
    assert anchor.resolve_anchor(None, vault / "spec.md", "") == (None, "none")


def test_the_merge_base_arm_wins_and_needs_nothing_stamped(repo: Path, vault: Path) -> None:
    fork = _sha(repo)
    _git(repo, "checkout", "-b", "feature/x")
    (repo / "b.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "second")
    # The spec is in the vault, has no heading and no baseline line: arms 2-4
    # all answer None. This is the shape a fresh design spec actually has.
    assert anchor.phase_start_sha(repo, vault / "spec.md", "") == fork


def test_on_the_base_branch_the_merge_base_is_head(repo: Path, vault: Path) -> None:
    # Main-mode degrades honestly to an empty range rather than a wrong one.
    assert anchor.phase_start_sha(repo, vault / "spec.md", "") == _sha(repo)


def test_phase_start_sha_falls_through_to_a_spec_arm(repo: Path, vault: Path, monkeypatch) -> None:
    monkeypatch.setattr(anchor.provenance, "merge_base", lambda *args: None)
    head = _sha(repo)
    text = f"**Baseline commit:** `{head}`\n"
    assert anchor.phase_start_sha(repo, vault / "spec.md", text) == head


def test_phase_start_sha_with_nothing_to_go_on_is_none(repo: Path, vault: Path, monkeypatch) -> None:
    monkeypatch.setattr(anchor.provenance, "merge_base", lambda *args: None)
    assert anchor.phase_start_sha(repo, vault / "spec.md", "") is None


def test_spec_ref_prefers_a_sources_hit() -> None:
    item = _item(
        "work/widget",
        sources=(Source(id=SPEC_SOURCE_ID, resource="/work/widget/references/adopted-spec.md"),),
    )
    assert anchor.spec_ref(item) == "work/widget/references/adopted-spec.md"


def test_spec_ref_falls_back_to_the_conventional_artifact_path() -> None:
    item = _item("work/widget")
    assert anchor.spec_ref(item) == "work/widget/references/01-design.md"
