"""The child 1 / child 2 names tech-debt-core-consumers-derive-stages consumes."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import get_args

from work_tracker_okf import pipeline
from work_tracker_okf.compose import advance_and_stamp
from work_tracker_okf.workflow import Blocker, BlockerKind, RouteResult, route_attributes


def test_stage_table_accessors_exist() -> None:
    assert pipeline.phase_order()[-1] == pipeline.DONE == "done"
    assert pipeline.read_only_phases() == frozenset({"design", "plan"})
    assert pipeline.results_phases() == frozenset({"execute", "finish"})
    assert pipeline.code_phases() == frozenset({"execute", "finish"})
    assert pipeline.dispatch_phases() == frozenset({"design", "plan", "execute", "finish"})
    assert tuple(row.stage for row in pipeline.STAGE_TABLE) == ("design", "plan", "execute", "finish")


def test_artifacts_are_a_stage_keyed_mapping_of_artifact_specs() -> None:
    artifacts = pipeline.PACKAGED_DEFINITION.artifacts
    assert isinstance(artifacts, Mapping)
    assert list(artifacts) == ["design", "plan", "execute"]
    design = artifacts["design"]
    assert (design.stage, design.file, design.source, design.required) == ("design", "01-design.md", "design", True)
    assert artifacts["execute"].required is False


def test_path_resolution_shapes() -> None:
    attrs = pipeline.path_attributes(
        type_="Feature", effort="medium", blast_radius=None, has_spec=False, has_plan=False, spec_stale=False
    )
    resolution = pipeline.resolve_path(pipeline.PACKAGED_DEFINITION, attrs)
    agreed = resolution.agreed
    assert agreed is not None
    assert agreed.stages == ("design", "plan", "execute", "finish")
    assert (agreed.rule.source, agreed.rule.index, agreed.rule.name) == ("packaged", 0, "default")
    assert pipeline.entry_stage(pipeline.PACKAGED_DEFINITION, attrs) == "design"


def test_blockers_are_typed() -> None:
    assert {"invalid", "done", "terminal", "hold", "dependencies", "effort-required"} <= set(get_args(BlockerKind))
    assert [f for f in Blocker.__dataclass_fields__] == ["kind", "message"]
    assert "path_candidates" in RouteResult.__dataclass_fields__
    assert callable(route_attributes)


def test_compose_and_core_take_a_definition() -> None:
    from graph_works_core.work import commands
    from graph_works_core.workspace.dispatch_config import DispatchConfig

    assert "definition" in inspect.signature(advance_and_stamp).parameters
    assert "definition" in inspect.signature(commands._plan_route).parameters
    assert isinstance(inspect.getattr_static(DispatchConfig, "definition"), property)
    assert callable(commands._load_config) and callable(commands._definition_of)
