"""Work lint selects each item's effective repository, including linked vaults."""

from __future__ import annotations

from datetime import date

import pytest
from code_wiki_okf.config import Config, StateGateConfig
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from graph_works_core.workspace.discovery import resolve
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.lint_repos import lint_repositories
from graph_works_core.workspace.provenance import probe_git
from graph_works_core.workspace.repos import resolve_repos
from okf_io import load_bundle
from work_tracker_okf.indexes import plan_indexes
from work_tracker_okf.items import IGNORE, load_items

TODAY = date(2026, 8, 17)
_FEATURE = """---
type: Feature
title: {slug}
description: d
status: stable
work_status: open
phase: execute
effort: medium
opened: 2026-08-01
updated: 2026-08-01
affects:
- packages/a
---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""
_EPIC = _FEATURE.replace("type: Feature", "type: Epic")


def _config(layout):
    return Config(
        graph_dir=layout.cache_dir / "graph",
        declarations_dir=layout.config_dir,
        repos=(),
        state_gate=StateGateConfig(enabled=False, branches=("main",)),
    )


def _write(layout, slug, template):
    page = layout.bundle_dir / "work" / f"{slug}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(template.format(slug=slug), encoding="utf-8", newline="\n")
    if "type: Feature" in template:
        ledger = page.with_suffix("") / "references/00-decisions.md"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("# Decisions\n", encoding="utf-8", newline="\n")
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    for plan in plan_indexes(bundle.root, load_items(bundle)):
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        plan.path.write_text(plan.after, encoding="utf-8", newline="\n")


def _git(cwd, *args):
    out = probe_git(cwd, *args)
    assert out.returncode == 0, out.stderr or out.cause


def _primary_with_linked(tmp_path):
    primary_root = tmp_path / "primary"
    primary_root.mkdir()
    _git(primary_root, "init", "-q", "-b", "main")
    primary = apply_init(plan_init(primary_root, today=TODAY, topic="Work")).layout
    primary.manifest_path.write_text(
        "version: 1\nworkflow:\n  dispatch_rules: dispatch.yaml\n"
        "repositories:\n  gw:\n    path: okf/repositories/gw/references/git\n"
        "    checkout: .gw/worktrees/gw/main\n",
        encoding="utf-8",
        newline="\n",
    )
    (primary_root / ".gitignore").write_text(
        "/.gw/worktrees/\n/okf/repositories/gw/references/git/\n", encoding="utf-8", newline="\n"
    )
    (primary.config_dir / ".gitignore").write_text("/worktrees/\n", encoding="utf-8", newline="\n")
    checkout = primary_root / ".gw/worktrees/gw/main"
    (checkout / "packages/a").mkdir(parents=True)
    _git(primary_root, "add", "-A")
    _git(primary_root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    linked_root = tmp_path / "linked"
    _git(primary_root, "worktree", "add", "-q", "-b", "epic/x", str(linked_root))
    linked = resolve(workspace=linked_root, environ={})
    assert (linked.config_dir / "schema/Feature.schema.json").is_file()
    assert (linked.config_dir / "sections").is_dir()
    return primary, linked, checkout


def _two(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    (one / "packages/a").mkdir(parents=True)
    (two / "packages/b").mkdir(parents=True)
    layout = apply_init(plan_init(tmp_path / "ws", today=TODAY, topic="Work")).layout
    layout.manifest_path.write_text(
        "version: 1\nworkflow:\n  dispatch_rules: dispatch.yaml\n"
        f"repositories:\n  one:\n    path: {one}\n  two:\n    path: {two}\n",
        encoding="utf-8",
        newline="\n",
    )
    return layout


def _repo(template, name):
    return template.replace("status: stable\n", f"status: stable\nrepo: {name}\n")


def _lint(layout, **kwargs):
    return work.run_lint(layout, _config(layout), repositories=lint_repositories(layout), today=TODAY, **kwargs)


def test_linked_workspace_lint_matches_primary(tmp_path):
    primary, linked, _checkout = _primary_with_linked(tmp_path)
    for layout in (primary, linked):
        _write(layout, "feature-a", _repo(_FEATURE, "gw"))
        assert _lint(layout).by_code("targets.affects-missing") == ()


def test_legacy_resolve_repos_reproduces_the_bug(tmp_path):
    _primary, linked, _checkout = _primary_with_linked(tmp_path)
    _write(linked, "feature-a", _repo(_FEATURE, "gw"))
    report = work.run_lint(linked, _config(linked), repo_roots=resolve_repos(linked), today=TODAY)
    assert [f.path for f in report.by_code("targets.affects-missing")] == ["work/feature-a.md"]


def test_nonexistent_target_still_errors_in_linked_workspace(tmp_path):
    _primary, linked, _checkout = _primary_with_linked(tmp_path)
    _write(linked, "feature-a", _repo(_FEATURE.replace("- packages/a", "- packages/nope"), "gw"))
    assert [f.path for f in _lint(linked).by_code("targets.affects-missing")] == ["work/feature-a.md"]


def test_item_repo_is_not_satisfied_by_a_sibling_repository(tmp_path):
    layout = _two(tmp_path)
    _write(layout, "feature-a", _repo(_FEATURE, "two"))
    assert [f.path for f in _lint(layout).by_code("targets.affects-missing")] == ["work/feature-a.md"]


@pytest.mark.parametrize("target, missing", [("a", True), ("b", False)])
def test_child_inherits_ancestor_repo_for_affects(tmp_path, target, missing):
    layout = _two(tmp_path)
    _write(layout, "epic-a", _repo(_EPIC.replace("packages/a", "packages/b"), "two"))
    child = "epic-a/children/feature-a"
    _write(layout, child, _FEATURE.replace("packages/a", f"packages/{target}"))
    for path in (None, f"work/{child}"):
        findings = _lint(layout, path=path).by_code("targets.affects-missing")
        assert [f.path for f in findings] == ([f"work/{child}.md"] if missing else [])


def test_untagged_item_uses_the_union(tmp_path):
    layout = _two(tmp_path)
    _write(layout, "feature-a", _FEATURE.replace("- packages/a", "- packages/a\n- packages/b"))
    for path in (None, "work/feature-a"):
        assert _lint(layout, path=path).by_code("targets.affects-missing") == ()


def test_scoped_parent_includes_child_choosing_another_repo(tmp_path):
    layout = _two(tmp_path)
    _write(layout, "epic-a", _repo(_EPIC, "one"))
    _write(layout, "epic-a/children/feature-a", _repo(_FEATURE, "two"))
    _write(layout, "feature-other", _repo(_FEATURE.replace("packages/a", "packages/nope"), "one"))
    assert [f.path for f in _lint(layout, path="work/epic-a").by_code("targets.affects-missing")] == [
        "work/epic-a/children/feature-a.md"
    ]


def test_scoped_lint_does_not_resolve_unrelated_undeclared_repo(tmp_path):
    layout = _two(tmp_path)
    _write(layout, "feature-a", _repo(_FEATURE, "one"))
    _write(layout, "feature-other", _repo(_FEATURE, "ghost"))
    assert _lint(layout, path="work/feature-a").by_code("targets.affects-missing") == ()


def test_undeclared_repo_refuses(tmp_path):
    layout = _two(tmp_path)
    _write(layout, "feature-a", _repo(_FEATURE, "ghost"))
    with pytest.raises(WorkspaceError, match="ghost"):
        _lint(layout)


def test_archived_item_with_undeclared_repo_does_not_refuse(tmp_path):
    layout = _two(tmp_path)
    _write(layout, "_archive/feature-old", _repo(_FEATURE, "ghost"))
    assert _lint(layout).by_code("targets.affects-missing") == ()


def test_plan_action_vault_path_still_resolves(tmp_path):
    layout = _two(tmp_path)
    _write(
        layout,
        "feature-a",
        _repo(_FEATURE, "one") + "| Execute implementation plan: work/feature-a/references/02-plan.md | done | why |\n",
    )
    (layout.bundle_dir / "work/feature-a/references/02-plan.md").write_text("# Plan\n", encoding="utf-8", newline="\n")
    assert _lint(layout).by_code("plan.action-target-missing") == ()


@pytest.mark.parametrize("repo, missing", [("one", False), ("two", True)])
def test_plan_action_uses_the_items_repository(tmp_path, repo, missing):
    layout = _two(tmp_path)
    _write(layout, "feature-a", _repo(_FEATURE, repo) + "| Edit packages/a/code.py | done | why |\n")
    (tmp_path / "one/packages/a/code.py").write_text("", encoding="utf-8", newline="\n")
    assert [f.path for f in _lint(layout).by_code("plan.action-target-missing")] == (
        ["work/feature-a.md"] if missing else []
    )


def test_no_non_target_finding_is_duplicated(tmp_path):
    layout = _two(tmp_path)
    _write(layout, "feature-a", _repo(_FEATURE.replace("effort: medium", "effort: invalid"), "one"))
    findings = [f for f in _lint(layout).findings if f.code.startswith("schemas.") and f.path == "work/feature-a.md"]
    assert len(findings) == 1


def test_strict_still_promotes(tmp_path):
    layout = _two(tmp_path)
    _write(layout, "epic-a", _repo(_EPIC, "one"))
    assert _lint(layout).ok
    assert not _lint(layout, strict=True).ok


@pytest.mark.parametrize("argument", ["repo_root", "repo_roots"])
def test_repositories_and_repo_roots_are_exclusive(tmp_path, argument):
    layout = _two(tmp_path)
    value = tmp_path if argument == "repo_root" else (tmp_path,)
    with pytest.raises(ValueError, match="pass repositories="):
        _lint(layout, **{argument: value})


def test_no_repositories_skips_affects_but_checks_plan_vault_paths(tmp_path):
    layout = apply_init(plan_init(tmp_path / "ws", today=TODAY, topic="Work")).layout
    _write(layout, "feature-a", _FEATURE + "| Edit /work/missing.md | done | why |\n")
    report = _lint(layout)
    assert report.by_code("targets.affects-missing") == ()
    assert [f.path for f in report.by_code("plan.action-target-missing")] == ["work/feature-a.md"]
