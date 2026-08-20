"""`run_file`: one graph-aware page/index/log filing composition."""

from __future__ import annotations

from datetime import date

from code_wiki_okf.config import Config, StateGateConfig
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from work_tracker_okf.dependencies import DependencyEdge

TODAY = date(2026, 8, 17)
EPIC = "2026-08-01-epic-parent"
SIBLING = "2026-08-02-feature-sibling"


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
    (work_dir / f"{EPIC}.md").write_text(
        "---\ntype: Epic\ntitle: Epic\ndescription: d\nstatus: draft\n"
        "workflow_status: open\nopened: 2026-08-01\nupdated: 2026-08-01\n---\n",
        encoding="utf-8",
    )
    (work_dir / f"{SIBLING}.md").write_text(
        "---\ntype: Feature\ntitle: Sibling\ndescription: d\nstatus: draft\n"
        f"workflow_status: open\nparent: {EPIC}\nopened: 2026-08-02\nupdated: 2026-08-02\n---\n",
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
        target="2026-Q4",
        owner="pat",
        parent=EPIC,
        depends_on=(DependencyEdge(SIBLING, blocks="plan", needs="design"),),
        affects=("packages/graph-works-core",),
        tags=("compat",),
    )
    assert result.plan.filing.frontmatter["owner"] == "pat"
    assert result.application.written is False


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
    assert outcome.application.written is False
    assert outcome.plan.filing.refusal is None
    assert not outcome.plan.filing.target.exists()


def test_run_file_apply_matches_its_plan(tmp_path) -> None:
    layout = _workspace(tmp_path)
    config = _config(layout)
    dry = work.run_file(layout, config, type="Feature", title="Child", description="d", on=TODAY)
    real = work.run_file(layout, config, type="Feature", title="Child", description="d", on=TODAY, dry_run=False)
    assert real.plan == dry.plan
    assert real.application.page == real.plan.filing.target
    assert real.application.written is True


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
