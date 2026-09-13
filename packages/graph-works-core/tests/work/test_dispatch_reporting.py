"""The two dispatch consumers resolve the same leaf snapshot."""

from pathlib import Path

import pytest
from graph_works_core.orchestrate.commands import run_orchestrate
from graph_works_core.work.commands import run_next
from test_run_next import _layout, _spec, _write


@pytest.mark.parametrize(
    ("kind", "phase", "spec", "variant"),
    [
        ("Feature", "design", False, "exploration"),
        ("Bug", "design", False, "diagnosis"),
        ("Feature", "design", True, "reconcile"),
        ("Epic", "design", False, "epic-design"),
        ("Epic", "plan", False, "decompose"),
        ("Feature", "plan", False, "single"),
        ("Feature", "execute", False, "unplanned"),
        ("Feature", "execute", True, "planned"),
        ("Feature", "finish", False, "branch"),
    ],
)
def test_next_and_orchestrate_share_resolution(tmp_path: Path, kind, phase, spec, variant):
    layout = _layout(tmp_path)
    path = "work/feature-a"
    _write(layout, path, type=kind, phase=phase)
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(
        page.read_text().replace(
            "effort: medium", "effort: medium\nblast_radius: package\nworktree: /wt/a\nbranch: feature/a"
        ),
        encoding="utf-8",
    )
    if spec:
        _spec(layout, path)
        if phase == "execute":
            from okf_io import load

            document = load(page)
            document.set("sources", [{"id": "plan", "resource": f"/{path}/references/02-plan.md"}])
            page.write_text(document.serialize(), encoding="utf-8")
            (layout.bundle_dir / f"{path}/references/02-plan.md").write_text("# Plan\n", encoding="utf-8")
    shared = layout.root / "dispatch.yaml"
    shared.write_text(
        "version: 1\npipeline:\n  attributes: [stage, variant, type, effort, blast_radius, has_spec, has_plan]\n"
        "  rules:\n  - match: {}\n    model: inherited\n    reasoning_effort: high\n"
        '  - match: {variant: branch}\n    prompt_tail: "merge {merge_target}"\n',
        encoding="utf-8",
    )
    (layout.root / "dispatch.local.yaml").write_text(
        "version: 1\npipeline:\n  rules:\n  - name: local-agent\n    match: {blast_radius: package}\n"
        "    agent: codex\n",
        encoding="utf-8",
    )
    next_result = run_next(layout, path, dry_run=False)
    from unittest.mock import patch

    with patch("graph_works_core.orchestrate.commands._stat_worktrees", return_value={"/wt/a": True}):
        planned_result = run_orchestrate(layout, path)
    planned = planned_result.dispatches[0]
    assert next_result.route.dispatch.variant == variant
    resolution = next_result.dispatch_resolution
    assert planned.agent == resolution.profile.agent == "codex"
    assert planned.model == resolution.profile.model is None
    assert planned.reasoning_effort == resolution.profile.reasoning_effort is None
    assert planned.skill == resolution.profile.skill
    assert planned_result.plan.dispatch_resolutions[planned.key] == resolution
    assert resolution.provenance["model"].reason == "agent-change"


def test_terminal_has_no_profile_even_without_dispatch_config(tmp_path):
    layout = _layout(tmp_path)
    _write(layout, "work/feature-a", phase="done")
    (layout.root / "dispatch.yaml").unlink()
    result = run_next(layout, "work/feature-a")
    assert result.dispatch_resolution is None
    assert result.dispatch_preflight is None


def test_unregistered_canonical_design_agrees_without_mutation(tmp_path):
    layout = _layout(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    _spec(layout, path)
    before = (layout.bundle_dir / f"{path}.md").read_bytes()
    next_result = run_next(layout, path)
    result = run_orchestrate(layout, path)
    assert next_result.dispatch_resolution.profile.skill == result.dispatches[0].skill == "gw:reconciling-spec"
    assert next_result.application.normalized == ()
    assert (layout.bundle_dir / f"{path}.md").read_bytes() == before


def test_failed_normalization_resolves_persisted_route(tmp_path, monkeypatch):
    from graph_works_core.work import commands as work

    layout = _layout(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    _spec(layout, path)
    monkeypatch.setattr(work, "_apply_normalizations", lambda *args, **kwargs: (work.NextApplication(), ("failed",)))
    result = run_next(layout, path, dry_run=False)
    assert result.route.dispatch.variant == "exploration"
    assert result.dispatch_resolution.profile.skill == "superpowers:brainstorming"


def test_descended_next_resolves_leaf_attributes_and_keeps_transitions(tmp_path):
    from okf_io import load
    from work_tracker_okf.workflow import route

    layout = _layout(tmp_path)
    parent = "work/epic-a"
    child = f"{parent}/children/feature-a"
    _write(layout, parent, type="Epic", phase="execute")
    _write(layout, child)
    page = layout.bundle_dir / f"{parent}.md"
    document = load(page)
    document.set("worktree", str(tmp_path))
    document.set("branch", "epic/a")
    page.write_text(document.serialize(), encoding="utf-8")
    (layout.root / "dispatch.local.yaml").write_text(
        "pipeline:\n  rules:\n  - match: {type: Feature}\n    agent: codex\n", encoding="utf-8"
    )
    next_result = run_next(layout, parent, descend=True)
    plan_result = run_orchestrate(layout, parent)
    assert plan_result.dispatches, plan_result.blocked
    planned = plan_result.dispatches[0]
    assert next_result.selected_path == planned.slug == child
    assert next_result.descent.path == (parent, child)
    assert next_result.dispatch_resolution.profile.agent == planned.agent == "codex"
    assert next_result.route == route(next_result.state)
    assert next_result.route.on_complete.phase == "plan"
    assert next_result.route.blockers == ()


def test_satisfied_parent_gate_has_transition_but_no_dispatch_profile(tmp_path):
    from okf_io import load

    layout = _layout(tmp_path)
    parent = "work/epic-a"
    child = f"{parent}/children/feature-a"
    _write(layout, parent, type="Epic", phase="execute")
    _write(layout, child, phase="done")
    page = layout.bundle_dir / f"{child}.md"
    document = load(page)
    document.set("work_status", "resolved")
    page.write_text(document.serialize(), encoding="utf-8")
    result = run_next(layout, parent)
    planned = run_orchestrate(layout, parent)
    assert result.dispatch_resolution is None
    assert result.dispatch_preflight is None
    assert result.route.on_complete.phase == "finish"
    assert planned.dispatches == ()
    assert planned.dispatch_resolutions == {}
    assert planned.advances[0].path == parent
