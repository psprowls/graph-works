"""`run_reconcile_context` and its parts.

The sibling scan is pure — constructed `WorkItem`s, no filesystem, no git — so
it is tested here directly rather than through the composed command.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.work import reconcile
from graph_works_core.workspace.layout import WorkspaceLayout
from ruamel.yaml import YAML
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import WorkItem


def _item(
    slug,
    *,
    type="Feature",
    status="open",
    workflow_status="open",
    resolved_in=None,
    affects=(),
    depends_on=(),
    parent=None,
):
    """A `WorkItem` with only the fields the sibling scan reads set.

    `status` is OKF's document-status axis (`draft`/`stable`/`deprecated`)
    and `workflow_status` is the work-lifecycle axis the sibling scan
    actually reads (`TERMINAL_STATUSES`) -- kept as separate parameters so a
    test cannot accidentally flip the wrong one and still pass.
    """
    return WorkItem(
        slug=slug,
        path=f"work/{slug}.md",
        archived=False,
        type=type,
        title=slug,
        description="d",
        status=status,
        workflow_status=workflow_status,
        phase="design",
        effort="medium",
        blast_radius=None,
        target=None,
        opened="2026-08-01",
        updated="2026-08-01",
        affects=tuple(affects),
        parent=parent,
        depends_on=tuple(DependencyEdge(slug=dep_slug) for dep_slug in depends_on),
        dependency_issues=(),
        children=(),
        owner=None,
        resolved_in=resolved_in,
        worktree=None,
        branch=None,
        superseded_by=None,
        tags=(),
        sources=(),
        has_spec_doc=False,
        has_plan_doc=False,
    )


def _landed(slug, **kwargs):
    return _item(slug, workflow_status="resolved", resolved_in=f"{slug}-sha", **kwargs)


def test_the_declared_arm_selects_a_landed_dependency():
    subject = _item("b", depends_on=("a",), parent="e")
    items = [_item("e", type="Epic"), _landed("a", parent="e"), subject]
    assert [s.slug for s in reconcile._landed_siblings(items, subject, "e")] == ["a"]


def test_the_overlap_arm_selects_an_undeclared_sibling_sharing_a_path():
    subject = _item("b", affects=("packages/x",), parent="e")
    items = [_item("e", type="Epic"), _landed("a", affects=("packages/x",), parent="e"), subject]
    assert [s.slug for s in reconcile._landed_siblings(items, subject, "e")] == ["a"]


def test_a_sibling_matching_both_arms_appears_once():
    subject = _item("b", affects=("packages/x",), depends_on=("a",), parent="e")
    items = [_item("e", type="Epic"), _landed("a", affects=("packages/x",), parent="e"), subject]
    selected = reconcile._landed_siblings(items, subject, "e")
    assert [s.slug for s in selected] == ["a"]


def test_a_terminal_sibling_without_resolved_in_is_not_landed():
    other = _item("a", workflow_status="resolved", resolved_in=None, affects=("packages/x",), parent="e")
    subject = _item("b", affects=("packages/x",), depends_on=("a",), parent="e")
    items = [_item("e", type="Epic"), other, subject]
    assert reconcile._landed_siblings(items, subject, "e") == ()


def test_a_wontfix_sibling_changed_no_code_and_is_excluded():
    other = _item("a", workflow_status="wontfix", resolved_in=None, affects=("packages/x",), parent="e")
    subject = _item("b", affects=("packages/x",), parent="e")
    items = [_item("e", type="Epic"), other, subject]
    assert reconcile._landed_siblings(items, subject, "e") == ()


def test_an_overlapping_item_under_a_different_epic_is_excluded():
    subject = _item("b", affects=("packages/x",), parent="e")
    items = [
        _item("e", type="Epic"),
        _item("f", type="Epic"),
        _landed("a", affects=("packages/x",), parent="f"),
        subject,
    ]
    assert reconcile._landed_siblings(items, subject, "e") == ()


def test_a_declared_dependency_in_another_epic_still_counts():
    # The declared arm is the coupling the author wrote down; it is not
    # epic-scoped, which is what makes it different from the overlap arm.
    subject = _item("b", depends_on=("a",), parent="e")
    items = [
        _item("e", type="Epic"),
        _item("f", type="Epic"),
        _landed("a", parent="f"),
        subject,
    ]
    assert [s.slug for s in reconcile._landed_siblings(items, subject, "e")] == ["a"]


def test_an_item_is_never_its_own_sibling():
    subject = _landed("b", affects=("packages/x",), depends_on=("b",), parent="e")
    items = [_item("e", type="Epic"), subject]
    assert reconcile._landed_siblings(items, subject, "e") == ()


def test_a_selected_sibling_carries_its_ref_and_paths():
    subject = _item("b", affects=("packages/x",), parent="e")
    items = [_item("e", type="Epic"), _landed("a", affects=("packages/x", "packages/y"), parent="e"), subject]
    (sibling,) = reconcile._landed_siblings(items, subject, "e")
    assert sibling == reconcile.LandedSibling(slug="a", resolved_in="a-sha", affects=("packages/x", "packages/y"))


def test_selection_order_follows_the_items_sequence_not_declaration_order():
    # `depends_on` names "y" before "x", but selection order must follow the
    # order the two siblings appear in `items`, not the order they are
    # declared in -- pinning `_landed_siblings`'s "order follows *items*"
    # docstring claim.
    subject = _item("b", depends_on=("y", "x"), parent="e")
    items = [
        _item("e", type="Epic"),
        _landed("x", parent="e"),
        _landed("y", parent="e"),
        subject,
    ]
    assert [s.slug for s in reconcile._landed_siblings(items, subject, "e")] == ["x", "y"]


def test_no_epic_means_no_overlap_arm():
    subject = _item("b", affects=("packages/x",))
    items = [_landed("a", affects=("packages/x",)), subject]
    assert reconcile._landed_siblings(items, subject, None) == ()


def test_the_result_types_are_frozen():
    import dataclasses

    for cls in (
        reconcile.ReconcileContext,
        reconcile.LandedSibling,
        reconcile.CommitRef,
        reconcile.CitedDecision,
    ):
        assert dataclasses.fields(cls)  # a dataclass, not a plain class
        assert cls.__dataclass_params__.frozen is True


# --- run_reconcile_context: the composed command ----------------------------

TODAY = date(2026, 8, 17)

_ITEM = """---
type: {type}
title: {slug}
description: d
status: {status}
workflow_status: open
phase: {phase}
effort: medium
opened: 2026-08-01
updated: 2026-08-01
affects:
{affects}{extra}---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _code_repo(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "seed.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "seed.txt")
    _git(root, "commit", "-m", "seed")
    return root


def _workspace(tmp_path, code=None):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    if code:
        yaml = YAML()
        yaml.preserve_quotes = True
        with layout.manifest_path.open(encoding="utf-8") as handle:
            data = yaml.load(handle)
        data["repositories"] = {"code": {"path": str(code)}}
        with layout.manifest_path.open("w", encoding="utf-8") as handle:
            yaml.dump(data, handle)
    return layout


def _write_item(layout, slug, *, type="Feature", status="stable", phase="design", affects=("packages/a",), extra=""):
    rendered_affects = "".join(f"- {path}\n" for path in affects)
    (layout.bundle_dir / "work" / f"{slug}.md").write_text(
        _ITEM.format(type=type, slug=slug, status=status, phase=phase, affects=rendered_affects, extra=extra),
        encoding="utf-8",
    )


def _write_spec(layout, slug, text):
    path = layout.bundle_dir / "work" / slug / "references" / "01-design-spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_ledger(layout, epic_slug, text):
    path = layout.bundle_dir / "work" / epic_slug / "references" / "00-decisions.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_an_unknown_slug_is_a_caller_error(tmp_path):
    layout = _workspace(tmp_path)
    with pytest.raises(ValueError, match="no-such-slug"):
        reconcile.run_reconcile_context(layout, "no-such-slug")


def test_a_workspace_with_no_declared_repo_still_produces_a_usable_result(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    context = reconcile.run_reconcile_context(layout, "a")
    assert context.slug == "a"
    assert context.spec_anchor_commit is None
    assert context.anchor_source == "none"
    assert context.commit_range is None
    assert context.diff_command is None
    assert any("repo" in warning for warning in context.warnings)


def test_a_missing_spec_warns_and_reconciles_against_nothing(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    context = reconcile.run_reconcile_context(layout, "a")
    assert context.cited_decisions == ()
    assert any("design spec" in warning for warning in context.warnings)


def test_an_item_with_no_epic_ancestor_warns_and_has_no_ledger_evidence(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    context = reconcile.run_reconcile_context(layout, "a")
    assert context.epic_slug == ""
    assert context.has_open_decision is False
    assert any("epic" in warning for warning in context.warnings)


def test_the_spec_git_history_is_the_first_anchor_arm(tmp_path):
    code = _code_repo(tmp_path)
    # A workspace whose bundle_dir IS the code repo: the conventional spec
    # path then lands inside *code* and is genuinely tracked there, which is
    # what it takes to reach the tracked-spec arm rather than merely
    # tolerating a fallback.
    layout = WorkspaceLayout(
        root=code,
        config_dir=code / "_config",
        cache_dir=code / "_cache",
        bundle_dir=code,
        worktrees_dir=code / "worktrees",
    )
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    _write_item(layout, "a")
    _write_spec(layout, "a", "# spec\n")
    _git(code, "add", "work")
    _git(code, "commit", "-m", "add spec")
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=code, capture_output=True, text=True, check=True
    ).stdout.strip()

    context = reconcile.run_reconcile_context(layout, "a", repo=code)
    assert context.anchor_source == "spec-git-history"
    assert context.spec_anchor_commit == expected


def test_the_last_reconciled_heading_is_the_second_anchor_arm(tmp_path):
    code = _code_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=code, capture_output=True, text=True, check=True
    ).stdout.strip()
    layout = _workspace(tmp_path, code=code)
    _write_item(layout, "a")
    _write_spec(layout, "a", f"# spec\n\n## Reconciled 2026-08-12 ({head}..{head})\n")
    context = reconcile.run_reconcile_context(layout, "a")
    assert context.anchor_source == "last-reconciled-heading"
    assert context.spec_anchor_commit == head
    assert context.commit_range == f"{head}..HEAD"


def test_the_baseline_commit_is_the_last_anchor_arm(tmp_path):
    code = _code_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=code, capture_output=True, text=True, check=True
    ).stdout.strip()
    layout = _workspace(tmp_path, code=code)
    _write_item(layout, "a")
    _write_spec(layout, "a", f"# spec\n\n**Baseline commit:** `{head}`\n")
    context = reconcile.run_reconcile_context(layout, "a")
    assert context.anchor_source == "baseline-commit"
    assert context.spec_anchor_commit == head


def test_an_unverifiable_fallback_sha_degrades_rather_than_producing_a_bogus_range(tmp_path):
    code = _code_repo(tmp_path)
    layout = _workspace(tmp_path, code=code)
    _write_item(layout, "a")
    _write_spec(layout, "a", "# spec\n\n**Baseline commit:** `0123456789abcdef0123456789abcdef01234567`\n")
    context = reconcile.run_reconcile_context(layout, "a")
    assert context.anchor_source == "none"
    assert context.commit_range is None
    assert any("anchor" in warning for warning in context.warnings)


def test_the_spec_resolves_from_the_sources_entry_when_present(tmp_path):
    layout = _workspace(tmp_path)
    adopted = layout.bundle_dir / "work" / "a" / "references" / "99-adopted-spec.md"
    adopted.parent.mkdir(parents=True, exist_ok=True)
    adopted.write_text("# adopted\n", encoding="utf-8")
    _write_item(
        layout,
        "a",
        # An earlier, non-matching source precedes the design-spec one, so
        # `_spec_ref`'s scan must actually walk past it rather than only ever
        # seeing a single-element list.
        extra=(
            "sources:\n"
            "- id: plan\n"
            "  resource: /work/a/references/02-plan-plan.md\n"
            "- id: design-spec\n"
            "  resource: /work/a/references/99-adopted-spec.md\n"
        ),
    )
    context = reconcile.run_reconcile_context(layout, "a")
    assert context.spec_path.endswith("99-adopted-spec.md")


def test_the_spec_falls_back_to_the_conventional_path(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    _write_spec(layout, "a", "# spec\n")
    context = reconcile.run_reconcile_context(layout, "a")
    assert context.spec_path.endswith("work/a/references/01-design-spec.md")


def test_an_archived_item_reads_its_spec_from_the_archive_lane(tmp_path):
    layout = _workspace(tmp_path)
    archive = layout.bundle_dir / "work" / "_archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "a.md").write_text(
        _ITEM.format(type="Feature", slug="a", status="resolved", phase="done", affects="- packages/a\n", extra=""),
        encoding="utf-8",
    )
    spec = archive / "a" / "references" / "01-design-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("# archived spec\n", encoding="utf-8")
    context = reconcile.run_reconcile_context(layout, "a")
    assert context.spec_path == str(spec)


def test_cited_decisions_classify_missing_and_superseded(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "e", type="Epic")
    _write_item(layout, "a", extra="parent: e\n")
    _write_spec(layout, "a", "# spec\n\nfollows D-001 and D-002 and D-099\n")
    _write_ledger(
        layout,
        "e",
        "## D-001 — answered one\nstatus: answered\n\n## D-002 — retired two\nstatus: superseded\n",
    )
    context = reconcile.run_reconcile_context(layout, "a")
    assert [(c.id, c.status) for c in context.cited_decisions] == [
        ("D-001", "answered"),
        ("D-002", "superseded"),
        ("D-099", "missing"),
    ]
    assert [c.id for c in context.contradictions] == ["D-002"]
    assert set(context.contradictions) <= set(context.cited_decisions)


def test_an_open_decision_naming_this_slug_is_reported(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "e", type="Epic")
    _write_item(layout, "a", extra="parent: e\n")
    _write_ledger(layout, "e", "## D-001 — still open\nstatus: open\naffects: [a]\n")
    assert reconcile.run_reconcile_context(layout, "a").has_open_decision is True


def test_an_open_decision_naming_only_a_sibling_is_not_this_slugs(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "e", type="Epic")
    _write_item(layout, "a", extra="parent: e\n")
    _write_item(layout, "b", extra="parent: e\n")
    _write_ledger(layout, "e", "## D-001 — still open\nstatus: open\naffects: [b]\n")
    assert reconcile.run_reconcile_context(layout, "a").has_open_decision is False


def test_the_ledger_is_read_once_per_call(tmp_path, monkeypatch):
    layout = _workspace(tmp_path)
    _write_item(layout, "e", type="Epic")
    _write_item(layout, "a", extra="parent: e\n")
    _write_spec(layout, "a", "# spec\n\ncites D-001\n")
    _write_ledger(layout, "e", "## D-001 — one\nstatus: open\naffects: [a]\n")

    calls = []
    original = reconcile._decisions.load

    def counting(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(reconcile._decisions, "load", counting)
    reconcile.run_reconcile_context(layout, "a")
    assert len(calls) == 1


def test_code_drift_scopes_the_diff_command_to_the_touched_paths(tmp_path):
    code = _code_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=code, capture_output=True, text=True, check=True
    ).stdout.strip()
    (code / "packages").mkdir()
    (code / "packages" / "a").write_text("x\n", encoding="utf-8")
    _git(code, "add", "packages/a")
    _git(code, "commit", "-m", "touch a")
    layout = _workspace(tmp_path, code=code)
    _write_item(layout, "a", affects=("packages/a",))
    _write_spec(layout, "a", f"# spec\n\n**Baseline commit:** `{head}`\n")
    context = reconcile.run_reconcile_context(layout, "a")
    assert context.touched_paths == ("packages/a",)
    assert context.diff_command == f"git diff {head}..HEAD -- packages/a"
    assert [c.subject for c in context.commits_since] == ["touch a"]


def test_the_repo_override_wins_over_the_declared_repository(tmp_path):
    code = _code_repo(tmp_path)
    layout = _workspace(tmp_path)  # declares nothing
    _write_item(layout, "a")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=code, capture_output=True, text=True, check=True
    ).stdout.strip()
    _write_spec(layout, "a", f"# spec\n\n**Baseline commit:** `{head}`\n")
    context = reconcile.run_reconcile_context(layout, "a", repo=code)
    assert context.anchor_source == "baseline-commit"


def test_a_full_run_changes_nothing_on_disk(tmp_path):
    code = _code_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=code, capture_output=True, text=True, check=True
    ).stdout.strip()
    layout = _workspace(tmp_path, code=code)
    _write_item(layout, "e", type="Epic")
    _write_item(layout, "a", extra="parent: e\n")
    _write_spec(layout, "a", f"# spec\n\ncites D-001\n\n**Baseline commit:** `{head}`\n")
    _write_ledger(layout, "e", "## D-001 — one\nstatus: open\naffects: [a]\n")

    def snapshot():
        return {path: path.read_bytes() for path in sorted(Path(layout.root).rglob("*")) if path.is_file()}

    before = snapshot()
    reconcile.run_reconcile_context(layout, "a")
    assert snapshot() == before
