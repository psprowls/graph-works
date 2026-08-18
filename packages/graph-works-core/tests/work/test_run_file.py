"""`run_file`: a direct pass-through to `compose.file_and_reconcile`."""

from __future__ import annotations

from datetime import date

from code_wiki_okf.config import Config, StateGateConfig
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work

TODAY = date(2026, 8, 17)


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
    assert outcome.path is None
    assert outcome.changed is False
    assert outcome.plan.refusal is None


def test_a_real_run_writes_the_page_reconciles_the_index_and_logs_it(tmp_path):
    layout = _workspace(tmp_path)
    outcome = work.run_file(
        layout,
        _config(layout),
        type="Feature",
        title="A new feature",
        description="d",
        on=TODAY,
        dry_run=False,
    )
    assert outcome.path is not None
    assert outcome.path.is_file()
    assert outcome.logged is not None
    assert any(update.changed for update in outcome.indexes)


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
