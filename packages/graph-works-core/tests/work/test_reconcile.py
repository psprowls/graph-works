"""Canonical reconcile-context evidence assembly."""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_core.work import reconcile
from graph_works_core.workspace.layout import layout_for

OWNER = "work/epic-a"
SUBJECT = f"{OWNER}/children/feature-b"
LANDED = f"{OWNER}/children/feature-a"


def _layout(tmp_path: Path):
    (tmp_path / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    layout = layout_for(tmp_path)
    (layout.bundle_dir / "work").mkdir(parents=True)
    return layout


def _write(
    layout,
    path: str,
    *,
    type: str,
    work_status: str = "open",
    resolved_in: str | None = None,
    depends_on: str | None = None,
) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    resolved = f"resolved_in: {resolved_in}\n" if resolved_in else ""
    dependency = f"depends_on:\n- path: {depends_on}\n  blocks: execute\n  needs: resolved\n" if depends_on else ""
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: {work_status}\n"
        f"phase: design\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n{resolved}{dependency}"
        "affects:\n- packages/a\n---\n\n## Summary\nd\n",
        encoding="utf-8",
    )


def _workspace(tmp_path: Path):
    layout = _layout(tmp_path)
    _write(layout, OWNER, type="Epic")
    _write(layout, LANDED, type="Feature", work_status="resolved", resolved_in="abc123")
    _write(layout, SUBJECT, type="Feature", depends_on=LANDED)
    spec = layout.bundle_dir / f"{SUBJECT}/references/01-design.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Design\n\nCites D-001.\n", encoding="utf-8")
    ledger = layout.bundle_dir / f"{SUBJECT}/references/00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decisions\n\n## D-001 — question\nstatus: superseded\naffects: [" + SUBJECT + "]\n",
        encoding="utf-8",
    )
    return layout


def test_reconcile_uses_canonical_design_artifact_and_full_sibling_paths(tmp_path: Path) -> None:
    context = reconcile.run_reconcile_context(_workspace(tmp_path), SUBJECT, repo=tmp_path)
    assert context.path == SUBJECT
    assert context.owner_path == SUBJECT
    assert context.spec_path.endswith(f"{SUBJECT}/references/01-design.md")
    assert [sibling.path for sibling in context.landed_siblings] == [LANDED]


def test_reconcile_reports_cited_contradiction_and_open_hold_by_path(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    context = reconcile.run_reconcile_context(layout, SUBJECT, repo=None)
    assert [decision.id for decision in context.contradictions] == ["D-001"]
    assert context.has_open_decision is False


def test_overlap_evidence_uses_shared_structural_parent_not_decision_owner(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    subject = layout.bundle_dir / f"{SUBJECT}.md"
    text = subject.read_text(encoding="utf-8")
    start = text.index("depends_on:\n")
    end = text.index("affects:\n", start)
    subject.write_text(text[:start] + text[end:], encoding="utf-8")
    context = reconcile.run_reconcile_context(layout, SUBJECT, repo=None)
    assert [sibling.path for sibling in context.landed_siblings] == [LANDED]


def test_overlapping_item_under_a_different_structural_parent_is_excluded(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    other_owner = "work/epic-b"
    other = f"{other_owner}/children/feature-a"
    _write(layout, other_owner, type="Epic")
    _write(layout, other, type="Feature", work_status="resolved", resolved_in="def456")
    context = reconcile.run_reconcile_context(layout, SUBJECT, repo=None)
    assert other not in {sibling.path for sibling in context.landed_siblings}


def test_declared_dependency_under_another_parent_still_counts(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    other_owner = "work/epic-b"
    other = f"{other_owner}/children/feature-a"
    _write(layout, OWNER, type="Epic")
    _write(layout, other_owner, type="Epic")
    _write(layout, other, type="Feature", work_status="resolved", resolved_in="def456")
    _write(layout, SUBJECT, type="Feature", depends_on=other)
    context = reconcile.run_reconcile_context(layout, SUBJECT, repo=None)
    assert [sibling.path for sibling in context.landed_siblings] == [other]


def test_missing_design_artifact_warns_without_writing(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    spec = layout.bundle_dir / f"{SUBJECT}/references/01-design.md"
    spec.unlink()
    before = {
        path.relative_to(layout.bundle_dir): path.read_bytes()
        for path in layout.bundle_dir.rglob("*")
        if path.is_file()
    }
    context = reconcile.run_reconcile_context(layout, SUBJECT, repo=None)
    after = {
        path.relative_to(layout.bundle_dir): path.read_bytes()
        for path in layout.bundle_dir.rglob("*")
        if path.is_file()
    }
    assert any("no design spec" in warning for warning in context.warnings)
    assert before == after


def test_unknown_canonical_path_is_a_caller_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="work/missing"):
        reconcile.run_reconcile_context(_workspace(tmp_path), "work/missing")


def _declare_two(layout, tmp_path: Path) -> tuple[Path, Path]:
    code, ui = tmp_path / "code", tmp_path / "ui"
    code.mkdir()
    ui.mkdir()
    layout.manifest_path.write_text(
        f'version: 1\nrepositories:\n  code:\n    path: "{code.as_posix()}"\n  ui:\n    path: "{ui.as_posix()}"\n',
        encoding="utf-8",
    )
    return code.resolve(), ui.resolve()


def _tag(layout, path: str, repo: str) -> None:
    page = layout.bundle_dir / f"{path}.md"
    text = page.read_text(encoding="utf-8").replace("status: stable\n", f"status: stable\nrepo: {repo}\n")
    page.write_text(text, encoding="utf-8")


def test_reconcile_resolves_the_item_s_inherited_repo(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    _code, ui = _declare_two(layout, tmp_path)
    _tag(layout, OWNER, "ui")
    seen: list[Path | None] = []
    real = reconcile.resolve_anchor

    def capture(repo, *args, **kwargs):
        seen.append(repo)
        return real(repo, *args, **kwargs)

    monkeypatch.setattr(reconcile, "resolve_anchor", capture)
    context = reconcile.run_reconcile_context(layout, SUBJECT)
    assert seen == [ui]
    assert "no repo resolved; code drift unavailable" not in context.warnings


def test_reconcile_refuses_an_untagged_item_among_several_repos(tmp_path: Path) -> None:
    from graph_works_core.workspace.errors import WorkspaceError

    layout = _workspace(tmp_path)
    _declare_two(layout, tmp_path)
    with pytest.raises(WorkspaceError, match=SUBJECT):
        reconcile.run_reconcile_context(layout, SUBJECT)
