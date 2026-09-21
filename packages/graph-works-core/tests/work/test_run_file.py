"""`run_file`: one graph-aware page/index/log filing composition."""

from __future__ import annotations

import json
from datetime import date

from code_wiki_okf.config import Config, StateGateConfig
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from work_tracker_okf.dependencies import DependencyEdge

TODAY = date(2026, 8, 17)
EPIC = "work/epic-parent"
SIBLING = f"{EPIC}/children/feature-sibling"


def _workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout


def _config(layout):
    return Config(
        graph_dir=layout.cache_dir / "graph",
        declarations_dir=layout.config_dir,
        repos=(),
        state_gate=StateGateConfig(enabled=False, branches=("main",)),
    )


def seeded_workspace(tmp_path):
    layout = _workspace(tmp_path)
    work_dir = layout.bundle_dir / "work"
    work_dir.mkdir(exist_ok=True)
    (layout.bundle_dir / f"{EPIC}.md").write_text(
        "---\ntype: Epic\ntitle: Epic\ndescription: d\nstatus: draft\n"
        "work_status: open\nopened: 2026-08-01\nupdated: 2026-08-01\n---\n",
        encoding="utf-8",
    )
    sibling_page = layout.bundle_dir / f"{SIBLING}.md"
    sibling_page.parent.mkdir(parents=True, exist_ok=True)
    sibling_page.write_text(
        "---\ntype: Feature\ntitle: Sibling\ndescription: d\nstatus: draft\n"
        "work_status: open\nopened: 2026-08-02\nupdated: 2026-08-02\n---\n",
        encoding="utf-8",
    )
    return layout, _config(layout)


def test_run_file_accepts_typed_edges_and_all_metadata(tmp_path) -> None:
    layout, config = seeded_workspace(tmp_path)
    result = work.run_file(
        layout,
        config,
        type="Feature",
        title="Compatibility child",
        description="d",
        on=TODAY,
        effort="medium",
        blast_radius="package",
        owner="pat",
        parent_path=EPIC,
        depends_on=(DependencyEdge(SIBLING, blocks="plan", needs="design"),),
        affects=("packages/graph-works-core",),
        tags=("compat",),
    )
    assert result.plan.filing.frontmatter["owner"] == "pat"
    assert result.application is None


def test_run_file_accepts_release_only_metadata_for_a_release(tmp_path) -> None:
    layout, config = seeded_workspace(tmp_path)
    result = work.run_file(
        layout,
        config,
        type="Release",
        title="Q4 release",
        description="d",
        on=TODAY,
        version="1.2.0",
        target_date=date(2026, 12, 1),
    )
    assert result.plan.filing.frontmatter["version"] == "1.2.0"
    assert result.plan.filing.frontmatter["target_date"] == date(2026, 12, 1)
    assert result.application is None


def test_a_dry_run_writes_nothing(tmp_path):
    layout = _workspace(tmp_path)
    outcome = work.run_file(
        layout,
        _config(layout),
        type="Feature",
        title="A new feature",
        description="d",
        on=TODAY,
    )
    assert outcome.application is None
    assert outcome.plan.filing.refusal is None
    assert not outcome.plan.filing.target.exists()


def test_run_file_apply_matches_its_plan(tmp_path) -> None:
    layout = _workspace(tmp_path)
    config = _config(layout)
    dry = work.run_file(layout, config, type="Feature", title="Child", description="d", on=TODAY)
    real = work.run_file(layout, config, type="Feature", title="Child", description="d", on=TODAY, dry_run=False)
    assert real.plan == dry.plan
    assert real.application is not None and real.application.ok
    assert real.plan.filing.target.is_file()


def test_run_file_refuses_a_target_created_after_domain_planning(tmp_path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    config = _config(layout)
    original = work.plan_file_and_reconcile
    external = b"external owner\n"

    def inject_after_planning(*args, **kwargs):
        outcome = original(*args, **kwargs)
        outcome.plan.filing.target.parent.mkdir(parents=True, exist_ok=True)
        outcome.plan.filing.target.write_bytes(external)
        return outcome

    monkeypatch.setattr(work, "plan_file_and_reconcile", inject_after_planning)
    result = work.run_file(
        layout,
        config,
        type="Feature",
        title="Raced target",
        description="d",
        on=TODAY,
        dry_run=False,
    )
    assert result.application is not None and not result.application.ok
    assert result.plan.filing.target.read_bytes() == external


def test_run_file_refuses_a_log_changed_after_domain_planning(tmp_path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    config = _config(layout)
    original = work.plan_file_and_reconcile
    log_path = layout.bundle_dir / "log.md"
    external = log_path.read_bytes() + b"\nexternal log owner\n"

    def inject_after_planning(*args, **kwargs):
        outcome = original(*args, **kwargs)
        log_path.write_bytes(external)
        return outcome

    monkeypatch.setattr(work, "plan_file_and_reconcile", inject_after_planning)
    result = work.run_file(
        layout,
        config,
        type="Feature",
        title="Raced log",
        description="d",
        on=TODAY,
        dry_run=False,
    )
    assert result.application is not None and not result.application.ok
    assert log_path.read_bytes() == external
    assert not result.plan.filing.target.exists()


def _split_workspace(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".git").mkdir(parents=True)
    code = tmp_path / "code"
    (code / "packages/foo").mkdir(parents=True)
    layout = apply_init(plan_init(vault / ".works", today=TODAY, topic="Split")).layout
    layout.manifest_path.write_text(
        f'version: 1\nrepositories:\n  "code":\n    path: {json.dumps(str(code))}\n',
        encoding="utf-8",
    )
    return layout


def test_split_topology_files_against_the_declared_code_repo_not_the_vault(tmp_path) -> None:
    layout = _split_workspace(tmp_path)
    outcome = work.run_file(
        layout,
        _config(layout),
        type="Feature",
        title="Split-topology filing",
        description="d",
        on=TODAY,
        affects=("packages/foo",),
        dry_run=False,
    )
    assert outcome.plan.filing.refusal is None
    assert outcome.application is not None
    assert outcome.application.ok, outcome.application.failures


def _two_repo_workspace(tmp_path):
    """A split vault declaring two code repositories, each owning one path."""
    layout = _split_workspace(tmp_path)
    other = tmp_path / "other"
    (other / "apps/ui").mkdir(parents=True)
    code = tmp_path / "code"
    layout.manifest_path.write_text(
        "version: 1\nrepositories:\n"
        f'  "code":\n    path: {json.dumps(str(code))}\n'
        f'  "other":\n    path: {json.dumps(str(other))}\n',
        encoding="utf-8",
    )
    return layout


def test_two_declared_repos_file_against_every_declared_repo(tmp_path) -> None:
    layout = _two_repo_workspace(tmp_path)
    outcome = work.run_file(
        layout,
        _config(layout),
        type="Feature",
        title="Two-repo filing",
        description="d",
        on=TODAY,
        affects=("packages/foo", "apps/ui"),
        dry_run=False,
    )
    assert outcome.plan.filing.refusal is None
    assert outcome.application is not None
    assert outcome.application.ok, outcome.application.failures


def test_two_declared_repos_still_refuse_a_path_under_neither(tmp_path) -> None:
    layout = _two_repo_workspace(tmp_path)
    outcome = work.run_file(
        layout,
        _config(layout),
        type="Feature",
        title="Two-repo filing",
        description="d",
        on=TODAY,
        affects=("apps/gone",),
        dry_run=False,
    )
    assert outcome.application is not None
    assert not outcome.application.ok
    assert any("targets.affects-missing" in failure for failure in outcome.application.failures)


def test_the_vertical_is_not_hoisted_to_the_front_door():
    # Deliberate: `graph_works_core.work.commands.run_lint` would collide
    # with the already-hoisted `graph_works_core.run_lint` (from
    # `lint_drift.lint`) if this vertical were hoisted too. This vertical
    # stays reachable only at `graph_works_core.work.commands.*`.
    import graph_works_core

    assert not hasattr(graph_works_core, "run_file")
    assert not hasattr(graph_works_core, "run_status")
    assert not hasattr(graph_works_core, "StatusReport")
    assert graph_works_core.run_lint is not work.run_lint
