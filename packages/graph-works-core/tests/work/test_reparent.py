"""Canonical reparenting through the durable core executor."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from _transaction_helpers import _init_git, assert_workspace_commit
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work

TODAY = date(2026, 8, 23)


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Paths")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write(layout, path: str, *, type: str = "Feature", work_status: str = "open") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\nphase: execute\neffort: medium\nopened: 2026-08-01\n"
        "updated: 2026-08-01\naffects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )


def test_reparent_dry_run_uses_canonical_path_mapping(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/epic-a", type="Epic")
    _write(layout, "work/feature-b")
    result = work.run_reparent(layout, "work/feature-b", "work/epic-a")
    assert result.application is None
    assert result.plan.path_mapping == {"work/feature-b": "work/epic-a/children/feature-b"}
    assert (layout.bundle_dir / "work/feature-b.md").is_file()


def test_reparent_live_run_is_journaled_and_reloads_at_final_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/epic-a", type="Epic")
    _write(layout, "work/feature-b")
    result = work.run_reparent(layout, "work/feature-b", "work/epic-a", dry_run=False)
    assert result.application is not None and result.application.ok
    assert result.application.journal.is_file()
    assert (layout.bundle_dir / "work/epic-a/children/feature-b.md").is_file()
    assert not (layout.bundle_dir / "work/feature-b.md").exists()


def test_release_adoption_is_singular_and_source_first(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/release-v1", type="Release")
    _write(layout, "work/epic-a", type="Epic")
    result = work.run_release_adoption(layout, "work/epic-a", "work/release-v1")
    assert result.plan.path_mapping == {"work/epic-a": "work/release-v1/children/epic-a"}


def test_reparent_commits_moved_work_item(tmp_path: Path) -> None:
    layout = apply_init(plan_init(tmp_path / "ws", today=TODAY, topic="Paths")).layout
    _write(layout, "work/epic-a", type="Epic")
    _write(layout, "work/feature-b")
    _init_git(layout.root)
    result = work.run_reparent(layout, "work/feature-b", "work/epic-a", dry_run=False)
    assert result.application is not None and result.application.ok
    assert_workspace_commit(layout.root, "workspace: reparent feature-b under epic-a")


def test_release_adoption_commits_moved_work_item(tmp_path: Path) -> None:
    layout = apply_init(plan_init(tmp_path / "ws", today=TODAY, topic="Paths")).layout
    _write(layout, "work/release-v1", type="Release")
    _write(layout, "work/epic-a", type="Epic")
    _init_git(layout.root)
    result = work.run_release_adoption(layout, "work/epic-a", "work/release-v1", dry_run=False)
    assert result.application is not None and result.application.ok
    assert_workspace_commit(layout.root, "workspace: adopt children into release-v1")


def test_refused_path_mutation_never_invokes_executor(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-a")
    result = work.run_reparent(layout, "work/feature-a", "work/missing", dry_run=False)
    assert not result.plan.ok
    assert result.application is None


def _split_layout(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / ".git").mkdir(parents=True)
    code = tmp_path / "code"
    (code / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(vault / ".works", today=TODAY, topic="Split")).layout
    layout.manifest_path.write_text(
        "version: 1\nworkflow: {dispatch_rules: dispatch.yaml}\nrepositories:\n"
        f'  "code":\n    path: {json.dumps(str(code))}\n',
        encoding="utf-8",
    )
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def test_split_topology_reparent_validates_against_the_declared_code_repo(tmp_path: Path) -> None:
    layout = _split_layout(tmp_path)
    _write(layout, "work/epic-a", type="Epic")
    _write(layout, "work/feature-b")
    result = work.run_reparent(layout, "work/feature-b", "work/epic-a", dry_run=False)
    assert result.application is not None
    assert result.application.ok, result.application.failures


def test_split_topology_adoption_validates_against_the_declared_code_repo(tmp_path: Path) -> None:
    layout = _split_layout(tmp_path)
    _write(layout, "work/release-v1", type="Release")
    _write(layout, "work/epic-a", type="Epic")
    result = work.run_release_adoption(layout, "work/epic-a", "work/release-v1", dry_run=False)
    assert result.application is not None
    assert result.application.ok, result.application.failures


def test_adoption_collects_release_references_but_excludes_foreign_references(tmp_path: Path) -> None:
    from _transaction_helpers import _git

    layout = apply_init(plan_init(tmp_path / "ws", today=TODAY, topic="Paths")).layout
    for path, kind in (
        ("work/release-v1", "Release"),
        ("work/epic-a", "Epic"),
        ("work/release-v1/children/epic-existing", "Epic"),
        ("work/epic-foreign", "Epic"),
    ):
        _write(layout, path, type=kind)
    _init_git(layout.root)
    owned = "okf/work/release-v1/references/guidance-plan.md"
    foreign = [
        "okf/work/release-v1/children/epic-existing/references/pending.md",
        "okf/work/epic-foreign/references/pending.md",
    ]
    for member in [owned, *foreign]:
        target = layout.root / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("pending\n", encoding="utf-8", newline="\n")
    _git(layout.root, "add", "--", foreign[1])
    result = work.run_release_adoption(layout, "work/epic-a", "work/release-v1", dry_run=False)
    assert result.application is not None and result.application.ok
    assert result.application.commit.status == "committed"
    changed = set(_git(layout.root, "show", "--name-only", "--format=", "HEAD").splitlines())
    assert owned in changed
    assert "okf/work/release-v1/children/epic-a.md" in changed
    assert not changed.intersection(foreign)
    assert _git(layout.root, "diff", "--cached", "--name-only").strip() == foreign[1]
    assert (layout.root / foreign[0]).read_text(encoding="utf-8") == "pending\n"
    assert _git(layout.root, "status", "--porcelain", "--", owned) == ""
