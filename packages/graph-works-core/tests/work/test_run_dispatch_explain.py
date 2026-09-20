"""`run_dispatch_explain`: the fold `gw next` performs, shown rule by rule."""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_core.work import commands as work
from graph_works_core.workspace.dispatch import packaged_rule, rule_matches
from graph_works_core.workspace.errors import WorkspaceError
from test_run_next import CHILD, EPIC, _layout, _spec, _write


def _dispatch(layout, *, shared: str, local: str = "") -> None:
    (layout.root / "dispatch.yaml").write_text(f"version: 1\npipeline:\n  rules:\n{shared}", encoding="utf-8")
    if local:
        (layout.root / "dispatch.local.yaml").write_text(f"version: 1\npipeline:\n  rules:\n{local}", encoding="utf-8")


def test_a_plan_stage_item_explains_the_same_resolution_next_computes(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    path = "work/feature-a"
    _write(layout, path, phase="plan")
    _spec(layout, path)
    _dispatch(
        layout,
        shared="  - match: {stage: design}\n    model: haiku\n",
        local="  - name: planning\n    match: {stage: plan}\n    model: opus\n",
    )

    explained = work.run_dispatch_explain(layout, path)
    nexted = work.run_next(layout, path, dry_run=True)

    assert explained.resolution == nexted.dispatch_resolution
    assert explained.attributes is not None and explained.attributes["variant"] == "single"
    assert explained.packaged_rule == packaged_rule("single")
    assert [matched for _rule, matched in explained.rules] == [False, True]
    assert all(matched == rule_matches(rule.match, explained.attributes) for rule, matched in explained.rules)
    assert explained.resolution is not None
    assert explained.resolution.provenance["model"].rule.name == "planning"
    assert explained.resolution.provenance["skill"].rule.source == "packaged"


def test_an_agent_change_reset_is_explained(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    path = "work/feature-a"
    _write(layout, path, phase="plan")
    _dispatch(
        layout,
        shared="  - match: {}\n    model: opus\n    reasoning_effort: high\n",
        local="  - match: {variant: single}\n    agent: codex\n",
    )

    explained = work.run_dispatch_explain(layout, path)

    assert explained.resolution == work.run_next(layout, path, dry_run=True).dispatch_resolution
    assert explained.resolution is not None
    assert explained.resolution.provenance["model"].reason == "agent-change"


def test_a_blocked_epic_has_no_dispatch_and_nexts_blockers(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD, phase="plan")
    _dispatch(layout, shared="  - match: {}\n    model: opus\n")

    explained = work.run_dispatch_explain(layout, EPIC)
    nexted = work.run_next(layout, EPIC, dry_run=True)

    assert explained.attributes is None
    assert explained.packaged_rule is None
    assert explained.resolution is None
    assert [matched for _rule, matched in explained.rules] == [False]
    assert explained.next_result.route.blockers == nexted.route.blockers
    assert explained.next_result.route.blockers


def test_a_profile_refusal_is_a_preflight_not_a_failure(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    path = "work/feature-a"
    _write(layout, path, phase="plan")
    _dispatch(layout, shared="  - match: {}\n    reasoning_effort: high\n")

    explained = work.run_dispatch_explain(layout, path)

    assert explained.attributes is None and explained.resolution is None
    assert explained.next_result.dispatch_preflight == work.run_next(layout, path, dry_run=True).dispatch_preflight
    assert explained.next_result.dispatch_preflight is not None


def test_unknown_path_is_a_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown work item"):
        work.run_dispatch_explain(_layout(tmp_path), "work/nope")


def test_a_malformed_dispatch_file_fails_the_read(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-a", phase="plan")
    _dispatch(layout, shared="  - match: {}\n    model: opus\n", local="  - match: {}\n    colour: red\n")

    with pytest.raises(WorkspaceError, match=r"dispatch\.local\.yaml: rule 0"):
        work.run_dispatch_explain(layout, "work/feature-a")


def test_explain_writes_nothing_when_a_design_source_needs_repair(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    path = "work/feature-a"
    _write(layout, path, phase="design")
    _spec(layout, path)
    page = layout.bundle_dir / f"{path}.md"
    before = page.read_bytes()

    work.run_dispatch_explain(layout, path)

    assert page.read_bytes() == before
