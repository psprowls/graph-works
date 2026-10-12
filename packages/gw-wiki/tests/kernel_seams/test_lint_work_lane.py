"""The lint aggregator. The mechanical half is a fixture workspace with one
known violation per topic, asserted as a hand-written code set — okf-io's
`test_catalog.py` idiom, where the hand-written set is what keeps a
regenerated golden honest."""

from __future__ import annotations

from datetime import date

import pytest
from code_wiki_okf.config import load_config
from gw_kernel import apply_init, plan_init
from gw_kernel.workspace.discovery import resolve
from gw_kernel.workspace.lint_repos import lint_repositories
from gw_kernel.workspace.provenance import probe_git
from gw_kernel.workspace.repos import resolve_repos
from gw_wiki.lint_drift.lint import LintReport, run_mechanical
from gw_work import commands as work

TODAY = date(2026, 8, 13)


def _codes(report: LintReport) -> set[str]:
    return {finding.code for lane in report.mechanical for finding in lane.report.findings}


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


def _layout_config(layout):
    return load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )


def _repo_item(layout, name="gw", target="packages/a"):
    page = layout.bundle_dir / "work/feature-a.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\ntitle: A\ndescription: d\nstatus: stable\nwork_status: open\n"
        f"repo: {name}\nphase: execute\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
        f"affects: [{target}]\n---\n",
        encoding="utf-8",
        newline="\n",
    )


@pytest.mark.parametrize("target, missing", [("packages/a", False), ("packages/nope", True)])
def test_run_mechanical_work_lane_uses_effective_roots(tmp_path, target, missing):
    primary, linked, _checkout = _primary_with_linked(tmp_path)
    lane_findings = []
    for layout in (primary, linked):
        _repo_item(layout, target=target)
        config = _layout_config(layout)
        repositories = lint_repositories(layout)
        report = run_mechanical(layout, config, today=TODAY, repositories=repositories)
        work_lane = next(lane.report for lane in report.mechanical if lane.name == "work")
        findings = work_lane.by_code("targets.affects-missing")
        assert [f.path for f in findings] == (["work/feature-a.md"] if missing else [])
        standalone = work.run_lint(layout, config, today=TODAY, repositories=repositories)
        assert findings == standalone.by_code("targets.affects-missing")
        lane_findings.append(work_lane.findings)
    assert lane_findings[0] == lane_findings[1]
    legacy = run_mechanical(linked, _layout_config(linked), today=TODAY, repo_roots=resolve_repos(linked))
    assert "targets.affects-missing" in _codes(legacy)
