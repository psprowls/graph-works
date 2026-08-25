"""Canonical reparenting through the durable core executor."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

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
        f'version: 1\nrepositories:\n  "code":\n    path: {json.dumps(str(code))}\n',
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
