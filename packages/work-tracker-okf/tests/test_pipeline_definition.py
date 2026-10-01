from __future__ import annotations

import pytest
from work_tracker_okf.pipeline import (
    PACKAGED_DEFINITION,
    STAGE_TABLE,
    ArtifactSpec,
    PipelineConfigError,
    PipelineDefinition,
    entry_stage,
    parse_artifacts,
    parse_path_rules,
    path_attributes,
    resolve_path,
)
from work_tracker_okf.workflow import RouteState, route


def _attrs(type_: str, effort: str | None = None, blast_radius: str | None = None) -> dict:
    return dict(
        path_attributes(
            type_=type_, effort=effort, blast_radius=blast_radius, has_spec=False, has_plan=False, spec_stale=False
        )
    )


def _definition(raw: list[dict]) -> PipelineDefinition:
    return PipelineDefinition(STAGE_TABLE, parse_path_rules(raw, source="custom.yaml"), PACKAGED_DEFINITION.artifacts)


def test_packaged_rules_and_artifacts() -> None:
    assert [(r.name, r.stages) for r in PACKAGED_DEFINITION.path_rules] == [
        ("default", ("design", "plan", "execute", "finish")),
        ("small-bug-like-skips-plan", ("design", "execute", "finish")),
        ("testgap-enters-at-plan", ("plan", "execute", "finish")),
        ("small-testgap-enters-at-execute", ("execute", "finish")),
    ]
    assert dict(PACKAGED_DEFINITION.artifacts) == {
        "design": ArtifactSpec("design", "01-design.md", "design"),
        "plan": ArtifactSpec("plan", "02-plan.md", "plan"),
        "execute": ArtifactSpec("execute", "03-execute-coverage.md", "execute-coverage", required=False),
    }


@pytest.mark.parametrize(
    ("type_", "effort", "stages"),
    [
        ("Feature", None, ("design", "plan", "execute", "finish")),
        ("Epic", "small", ("design", "plan", "execute", "finish")),
        ("Bug", "small", ("design", "execute", "finish")),
        ("TechDebt", "xtra-small", ("design", "execute", "finish")),
        ("Bug", "large", ("design", "plan", "execute", "finish")),
        ("TestGap", "medium", ("plan", "execute", "finish")),
        ("TestGap", "small", ("execute", "finish")),
    ],
)
def test_packaged_resolution(type_, effort, stages) -> None:
    resolution = resolve_path(PACKAGED_DEFINITION, _attrs(type_, effort))
    assert resolution.agreed is not None and resolution.agreed.stages == stages


def test_unsized_bug_yields_two_candidate_paths() -> None:
    resolution = resolve_path(PACKAGED_DEFINITION, _attrs("Bug"))
    assert resolution.agreed is None
    assert resolution.unset == ("effort",)
    assert {c.stages for c in resolution.candidates} == {
        ("design", "plan", "execute", "finish"),
        ("design", "execute", "finish"),
    }
    assert [c.assignment["effort"] for c in resolution.candidates] == [
        "xtra-small",
        "small",
        "medium",
        "large",
        "xtra-large",
    ]


def test_rules_that_agree_across_effort_never_fork() -> None:
    resolution = resolve_path(PACKAGED_DEFINITION, _attrs("Feature"))
    assert resolution.agreed is not None
    assert {c.stages for c in resolution.candidates} == {("design", "plan", "execute", "finish")}


def test_entry_stage() -> None:
    assert entry_stage(PACKAGED_DEFINITION, _attrs("Feature")) == "design"
    assert entry_stage(PACKAGED_DEFINITION, _attrs("Bug")) == "design"
    assert entry_stage(PACKAGED_DEFINITION, _attrs("TestGap")) is None
    assert entry_stage(PACKAGED_DEFINITION, _attrs("TestGap", "small")) == "execute"
    assert entry_stage(PACKAGED_DEFINITION, _attrs("TestGap", "large")) == "plan"


def test_blast_radius_keyed_rule_forks_on_blast_radius() -> None:
    definition = _definition(
        [
            {"name": "default", "match": {}, "stages": ["design", "plan", "execute", "finish"]},
            {"name": "wide", "match": {"blast_radius": ["domain", "system"]}, "stages": ["plan", "execute", "finish"]},
        ]
    )
    resolution = resolve_path(definition, _attrs("Feature", "large"))
    assert resolution.unset == ("blast_radius",)
    assert resolution.agreed is None


def test_custom_path_is_honoured() -> None:
    definition = _definition(
        [{"name": "no-plan", "match": {"type": "Feature"}, "stages": ["design", "execute", "finish"]}]
    )
    resolution = resolve_path(definition, _attrs("Feature"))
    assert resolution.agreed is not None and resolution.agreed.stages == ("design", "execute", "finish")


def test_no_matching_rule_yields_no_candidates() -> None:
    definition = _definition([{"name": "bugs", "match": {"type": "Bug"}, "stages": ["design", "execute", "finish"]}])
    assert resolve_path(definition, _attrs("Feature")).candidates == ()


def test_partial_custom_rule_does_not_select_an_unmatched_effort() -> None:
    definition = _definition([{"name": "small", "match": {"effort": "small"}, "stages": ["execute", "finish"]}])
    resolution = resolve_path(definition, _attrs("Feature"))
    assert not resolution.complete
    assert resolution.agreed is None
    assert entry_stage(definition, _attrs("Feature")) is None
    decision = route(RouteState(type="Feature", work_status="open"), definition=definition)
    assert decision.dispatch is None
    assert decision.blockers[0].kind == "path-invalid"


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("nope", "custom.yaml: pipeline.path must be a list"),
        ([{"match": {}, "stages": []}], "custom.yaml: path rule 0: stages: must be a non-empty list"),
        (
            [{"match": {}, "stages": ["plan", "design", "finish"]}],
            "path rule 0: stages: must follow design, plan, execute, finish",
        ),
        ([{"match": {}, "stages": ["design", "plan"]}], "path rule 0: stages: must end in finish"),
        ([{"match": {}, "stages": ["design", "review", "finish"]}], "path rule 0: stages: unknown stage 'review'"),
        (
            [{"match": {}, "stages": ["design", "design", "finish"]}],
            "path rule 0: stages: must follow design, plan, execute, finish",
        ),
        (
            [{"match": {"stage": "design"}, "stages": ["finish"]}],
            "path rule 0: match.stage: stage is an output of routing",
        ),
        (
            [{"match": {"variant": "branch"}, "stages": ["finish"]}],
            "path rule 0: match.variant: routing variants are retired; replace with branch -> {stage: finish}",
        ),
        ([{"match": {"colour": "red"}, "stages": ["finish"]}], "path rule 0: match.colour: unknown attribute"),
        ([{"match": {"type": "Story"}, "stages": ["finish"]}], "path rule 0: match.type: value must be one of"),
        ([{"match": {"has_spec": "yes"}, "stages": ["finish"]}], "path rule 0: match.has_spec: expects a boolean"),
        ([{"match": {"type": []}, "stages": ["finish"]}], "path rule 0: match.type: list must be non-empty"),
        (
            [{"match": {"type": None}, "stages": ["finish"]}],
            "path rule 0: match.type: null constraints are not allowed",
        ),
        ([{"stages": ["finish"]}], "path rule 0: match: required and must be a mapping"),
        ([{"match": {}, "stages": ["finish"], "skill": "x"}], "path rule 0: unknown keys: ['skill']"),
        ([{"name": " ", "match": {}, "stages": ["finish"]}], "path rule 0: name: must be a non-empty string"),
    ],
)
def test_path_rule_validation(raw, fragment) -> None:
    with pytest.raises(PipelineConfigError) as caught:
        parse_path_rules(raw, source="custom.yaml")
    assert fragment in str(caught.value)


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("nope", "custom.yaml: pipeline.artifacts must be a mapping"),
        ({"review": {"file": "x.md", "source": "x"}}, "artifacts.review: unknown stage"),
        ({"design": {"source": "design"}}, "artifacts.design: file: must be a non-empty file name"),
        ({"design": {"file": "a/b.md", "source": "design"}}, "artifacts.design: file: must be a non-empty file name"),
        (
            {"design": {"file": "01-design.md", "source": "Design"}},
            "artifacts.design: source: must be a kebab-case source id",
        ),
        (
            {"design": {"file": "01-design.md", "source": "design", "required": "yes"}},
            "artifacts.design: required: must be a boolean",
        ),
        (
            {"design": {"file": "01-design.md", "source": "design", "gate": True}},
            "artifacts.design: unknown keys: ['gate']",
        ),
    ],
)
def test_artifact_validation(raw, fragment) -> None:
    with pytest.raises(PipelineConfigError) as caught:
        parse_artifacts(raw, source="custom.yaml")
    assert fragment in str(caught.value)


def test_pipeline_config_error_is_a_value_error() -> None:
    assert issubclass(PipelineConfigError, ValueError)


@pytest.mark.parametrize("invalid", [{"match": {"stage": "design"}}, {"stages": []}, {"skill": "x"}])
def test_named_invalid_path_rule_identifies_index_and_name(invalid) -> None:
    rule = {"name": "features-skip-plan", "match": {}, "stages": ["finish"], **invalid}
    with pytest.raises(PipelineConfigError) as caught:
        parse_path_rules([{"match": {}, "stages": ["finish"]}, rule], source="dispatch.yaml")
    assert "dispatch.yaml: path rule 1 (features-skip-plan):" in str(caught.value)
