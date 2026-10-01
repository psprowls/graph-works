"""The child-1 names this item consumes, pinned so a divergence fails here first."""

from __future__ import annotations

import dataclasses
import inspect

from graph_works_core.workspace import dispatch
from work_tracker_okf import pipeline
from work_tracker_okf.workflow import route


def test_attribute_vocabulary_is_child_ones_without_variant() -> None:
    assert set(pipeline.ATTRIBUTES) == {"stage", "type", "effort", "blast_radius", "has_spec", "has_plan", "spec_stale"}
    assert dict(dispatch._ATTRIBUTE_VALUES) == dict(pipeline.ATTRIBUTES)


def test_route_takes_a_keyword_definition_defaulting_to_packaged() -> None:
    parameter = inspect.signature(route).parameters["definition"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is pipeline.PACKAGED_DEFINITION


def test_definition_is_a_dataclass_with_path_rules_and_artifacts() -> None:
    fields = {field.name for field in dataclasses.fields(pipeline.PipelineDefinition)}
    assert {"stage_table", "path_rules", "artifacts"} <= fields
    assert pipeline.PACKAGED_DEFINITION.stage_table == pipeline.STAGE_TABLE


def test_packaged_artifacts_are_artifact_specs_in_stage_order() -> None:
    specs = tuple(pipeline.PACKAGED_DEFINITION.artifacts.values())
    assert all(isinstance(spec, pipeline.ArtifactSpec) for spec in specs)
    assert [(spec.stage, spec.file, spec.source, spec.required) for spec in specs] == [
        ("design", "01-design.md", "design", True),
        ("plan", "02-plan.md", "plan", True),
        ("execute", "03-execute-coverage.md", "execute-coverage", False),
    ]


def test_packaged_path_rules_are_the_four_named_rows() -> None:
    assert [rule.name for rule in pipeline.PACKAGED_DEFINITION.path_rules] == [
        "default",
        "small-bug-like-skips-plan",
        "testgap-enters-at-plan",
        "small-testgap-enters-at-execute",
    ]


def test_parse_path_rules_takes_plain_data_and_a_source() -> None:
    rules = pipeline.parse_path_rules(
        [{"name": "x", "match": {"type": "Feature"}, "stages": ["design", "execute", "finish"]}], source="s.yaml"
    )
    assert len(rules) == 1
    assert rules[0].name == "x"
    assert dict(rules[0].match) == {"type": "Feature"}
    assert tuple(rules[0].stages) == ("design", "execute", "finish")


def test_parse_path_rules_refuses_stage_in_match_with_its_error_type() -> None:
    try:
        pipeline.parse_path_rules([{"match": {"stage": "design"}, "stages": ["finish"]}], source="s.yaml")
    except pipeline.PipelineConfigError as exc:
        assert "s.yaml" in str(exc)
        assert isinstance(exc, ValueError)
    else:  # pragma: no cover - failure path
        raise AssertionError("stage in a path match must be refused")
