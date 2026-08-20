"""`run_lint`: `compose.rule_set` fed to `okf_io.validate` -- the same rule
set `lint_drift`'s work lane now uses too."""

from __future__ import annotations

from datetime import date

from code_wiki_okf.config import Config, StateGateConfig
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from okf_io import load

TODAY = date(2026, 8, 17)

_FEATURE = """---
type: Feature
title: {slug}
description: d
status: stable
workflow_status: open
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

# Phase `execute` with no children is `graph.epic-without-children` -- warn,
# not error, so it is what stays `ok` under the default and flips under
# `strict=True`.
_EPIC = """---
type: Epic
title: {slug}
description: d
status: stable
workflow_status: open
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


def _workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _config(layout):
    return Config(
        graph_dir=layout.cache_dir / "graph",
        declarations_dir=layout.config_dir,
        repos=(),
        state_gate=StateGateConfig(enabled=False, branches=("main",)),
    )


def _write(layout, slug, template):
    (layout.bundle_dir / "work" / f"{slug}.md").write_text(template.format(slug=slug), encoding="utf-8")


def test_a_conformant_bundle_reports_no_errors(tmp_path):
    layout = _workspace(tmp_path)
    _write(layout, "2026-08-01-feature-a", _FEATURE)
    report = work.run_lint(layout, _config(layout), today=TODAY)
    assert report.ok
    assert report.findings == ()


def test_a_bad_type_is_reported_as_a_schema_finding(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work" / "2026-08-17-bug-bad.md").write_text(
        "---\ntype: NotAType\ntitle: Bad\nstatus: accepted\n---\n\nBody.\n", encoding="utf-8"
    )
    report = work.run_lint(layout, _config(layout), today=TODAY)
    assert any(finding.code.startswith("schemas.") for finding in report.findings)
    assert not report.ok


def test_strict_promotes_a_warning_to_a_failure(tmp_path):
    layout = _workspace(tmp_path)
    # An epic at `phase: execute` with no children is `graph.epic-without-children`
    # -- warn by default, so this bundle is `ok` until `strict=True` promotes it.
    _write(layout, "2026-08-01-epic-x", _EPIC)

    lax = work.run_lint(layout, _config(layout), today=TODAY)
    assert lax.ok
    assert any(f.code == "graph.epic-without-children" and f.severity == "warn" for f in lax.findings)

    strict = work.run_lint(layout, _config(layout), today=TODAY, strict=True)
    assert not strict.ok
    assert any(f.code == "graph.epic-without-children" and f.severity == "error" for f in strict.findings)


def test_wiki_side_content_is_out_of_scope(tmp_path):
    # `layout.bundle_dir` is shared between the wiki and work lanes -- an
    # unscoped `load_bundle` would walk both, and `okf_io.validate`'s
    # built-in catalog would report on a page this function was never asked
    # about. A broken link and an unrecognized `type` in `concepts/` must
    # not fail a "does the work lane conform" check.
    layout = _workspace(tmp_path)
    _write(layout, "2026-08-01-feature-a", _FEATURE)
    concepts = layout.bundle_dir / "concepts"
    concepts.mkdir(exist_ok=True)
    (concepts / "broken.md").write_text(
        "---\ntype: NotAConceptKind\ntitle: Broken\n---\n\nSee [missing](/concepts/nope.md).\n",
        encoding="utf-8",
    )
    report = work.run_lint(layout, _config(layout), today=TODAY)
    assert report.ok
    assert report.findings == ()


def test_repo_root_none_skips_the_affects_check_but_a_real_root_enforces_it(tmp_path):
    layout = _workspace(tmp_path)
    # `affects: [packages/a]` names a path that exists under neither the repo
    # nor (trivially) nowhere -- `targets.affects-missing` only fires once a
    # repo root is given to check it against.
    _write(layout, "2026-08-01-feature-a", _FEATURE)
    repo = layout.repo_root

    without_root = work.run_lint(layout, _config(layout), today=TODAY, repo_root=None)
    assert not any(f.code == "targets.affects-missing" for f in without_root.findings)

    with_root = work.run_lint(layout, _config(layout), today=TODAY, repo_root=repo)
    assert any(f.code == "targets.affects-missing" for f in with_root.findings)
    assert not with_root.ok


def test_lint_reports_bad_edge_without_crashing(tmp_path) -> None:
    layout = _workspace(tmp_path)
    _write(layout, "2026-08-01-feature-a", _FEATURE)
    document = load(layout.bundle_dir / "work/2026-08-01-feature-a.md")
    document.set("depends_on", [{"slug": "missing", "blocks": "build"}])
    document.save()
    report = work.run_lint(layout, _config(layout), today=TODAY)
    assert any(finding.code == "graph.depends-on-invalid" for finding in report.findings)
