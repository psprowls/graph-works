"""pipeline.path and pipeline.artifacts load, layer and validate (design §3.3)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Literal

import pytest
from graph_works_core.workspace.dispatch_config import (
    apply_dispatch_write,
    load_dispatch_config,
    plan_dispatch_write,
)
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for
from work_tracker_okf.pipeline import PACKAGED_DEFINITION, ArtifactSpec

PACKAGED_NAMES = [rule.name for rule in PACKAGED_DEFINITION.path_rules]


def _workspace(tmp_path: Path, shared: str = "{}\n", local: str | None = None):
    (tmp_path / "workspace.yaml").write_text(
        "version: 1\nworkflow:\n  dispatch_rules: dispatch.yaml\n", encoding="utf-8", newline=""
    )
    (tmp_path / "dispatch.yaml").write_text(shared, encoding="utf-8", newline="")
    if local is not None:
        (tmp_path / "dispatch.local.yaml").write_text(local, encoding="utf-8", newline="")
    return layout_for(tmp_path)


def _refusal(tmp_path: Path, *, shared: str = "{}\n", local: str | None = None) -> str:
    with pytest.raises(WorkspaceError) as excinfo:
        load_dispatch_config(_workspace(tmp_path, shared, local))
    return str(excinfo.value)


FEATURE_SKIPS_PLAN = (
    "pipeline:\n  path:\n    - name: features-skip-plan\n      match: {type: Feature}\n"
    "      stages: [design, execute, finish]\n"
)


def test_absent_blocks_mean_packaged_path_rules(tmp_path: Path) -> None:
    config = load_dispatch_config(_workspace(tmp_path))
    assert [layered.rule.name for layered in config.path_rules] == PACKAGED_NAMES
    assert {layered.origin.source for layered in config.path_rules} == {"packaged"}
    assert [layered.origin.index for layered in config.path_rules] == list(range(len(PACKAGED_NAMES)))


def test_path_composes_packaged_then_shared_then_local(tmp_path: Path) -> None:
    local = "pipeline:\n  path:\n    - match: {type: Feature, effort: small}\n      stages: [execute, finish]\n"
    config = load_dispatch_config(_workspace(tmp_path, FEATURE_SKIPS_PLAN, local))
    tail = config.path_rules[len(PACKAGED_NAMES) :]
    assert [(layered.origin.source, layered.origin.index, layered.origin.name) for layered in tail] == [
        (str(tmp_path / "dispatch.yaml"), 0, "features-skip-plan"),
        (str(tmp_path / "dispatch.local.yaml"), 0, None),
    ]
    assert tuple(tail[0].rule.stages) == ("design", "execute", "finish")


@pytest.mark.parametrize("empty", ["pipeline:\n  path: []\n", "pipeline: {}\n", "{}\n"])
def test_empty_or_absent_local_path_adds_nothing(tmp_path: Path, empty: str) -> None:
    config = load_dispatch_config(_workspace(tmp_path, local=empty))
    assert len(config.path_rules) == len(PACKAGED_NAMES)


@pytest.mark.parametrize(
    ("text", "needles"),
    [
        ("pipeline:\n  path: null\n", ["pipeline.path must be a list"]),
        ("pipeline:\n  path: {a: 1}\n", ["pipeline.path must be a list"]),
        ("pipeline:\n  path:\n    - match: {stage: design}\n      stages: [finish]\n", ["stage"]),
        ("pipeline:\n  path:\n    - match: {}\n      stages: []\n", ["stages"]),
        ("pipeline:\n  path:\n    - match: {}\n      stages: [execute, design, finish]\n", ["stages"]),
        ("pipeline:\n  path:\n    - match: {}\n      stages: [design, plan]\n", ["stages"]),
        ("pipeline:\n  path:\n    - match: {}\n      stages: [design, review, finish]\n", ["stages"]),
        ("pipeline:\n  path:\n    - match: {}\n      stages: [finish]\n      skill: x\n", ["skill"]),
        ("pipeline:\n  path:\n    - stages: [finish]\n", ["match"]),
        ("pipeline:\n  path:\n    - match: {variant: planned}\n      stages: [finish]\n", ["variant"]),
    ],
)
@pytest.mark.parametrize("layer", ["shared", "local"])
def test_path_field_refusals_name_the_file(tmp_path: Path, layer: str, text: str, needles: list[str]) -> None:
    message = _refusal(tmp_path, shared=text if layer == "shared" else "{}\n", local=text if layer == "local" else None)
    path = tmp_path / ("dispatch.yaml" if layer == "shared" else "dispatch.local.yaml")
    assert str(path) in message
    for needle in needles:
        assert needle in message


def test_declared_attributes_bound_workspace_path_matches(tmp_path: Path) -> None:
    shared = (
        "pipeline:\n  attributes: [stage, type]\n  path:\n    - name: by-effort\n"
        "      match: {effort: small}\n      stages: [execute, finish]\n"
    )
    message = _refusal(tmp_path, shared=shared)
    assert f"{tmp_path / 'dispatch.yaml'}: path rule 0 (by-effort): match.effort" in message
    assert "not declared" in message


def test_declared_attributes_do_not_bound_packaged_path_rules(tmp_path: Path) -> None:
    config = load_dispatch_config(_workspace(tmp_path, "pipeline:\n  attributes: [stage, type]\n"))
    assert [layered.rule.name for layered in config.path_rules] == PACKAGED_NAMES


def test_absent_artifacts_are_the_packaged_three(tmp_path: Path) -> None:
    config = load_dispatch_config(_workspace(tmp_path))
    assert list(config.artifacts) == ["design", "plan", "execute"]
    with pytest.raises(TypeError):
        config.artifacts["design"] = config.artifacts["plan"]
    with pytest.raises(FrozenInstanceError):
        config.artifacts["design"].file = "mutated.md"
    assert {stage: (a.file, a.source, a.required, a.origin) for stage, a in config.artifacts.items()} == {
        "design": ("01-design.md", "design", True, "packaged"),
        "plan": ("02-plan.md", "plan", True, "packaged"),
        "execute": ("03-execute-coverage.md", "execute-coverage", False, "packaged"),
    }


def test_local_entry_replaces_shared_entry_for_its_stage_and_keeps_packaged_source(tmp_path: Path) -> None:
    shared = "pipeline:\n  artifacts:\n    design: {file: spec.md, required: false}\n    plan: {file: plan.md}\n"
    local = "pipeline:\n  artifacts:\n    design: {file: local-spec.md}\n"
    config = load_dispatch_config(_workspace(tmp_path, shared, local))
    design, plan = config.artifacts["design"], config.artifacts["plan"]
    assert (design.file, design.source, design.required, design.origin) == (
        "local-spec.md",
        "design",
        True,
        str(tmp_path / "dispatch.local.yaml"),
    )
    assert (plan.file, plan.source, plan.origin) == ("plan.md", "plan", str(tmp_path / "dispatch.yaml"))


def test_execute_override_without_required_becomes_gating(tmp_path: Path) -> None:
    config = load_dispatch_config(_workspace(tmp_path, "pipeline:\n  artifacts:\n    execute: {file: coverage.md}\n"))
    assert config.artifacts["execute"].required is True
    assert config.artifacts["execute"].source == "execute-coverage"


@pytest.mark.parametrize("empty", ["pipeline:\n  artifacts: {}\n", "pipeline: {}\n"])
def test_empty_artifacts_layer_adds_nothing(tmp_path: Path, empty: str) -> None:
    config = load_dispatch_config(_workspace(tmp_path, empty))
    assert {a.origin for a in config.artifacts.values()} == {"packaged"}


@pytest.mark.parametrize(
    ("block", "needles"),
    [
        ("  artifacts: null\n", ["pipeline.artifacts must be a mapping"]),
        ("  artifacts: [design]\n", ["pipeline.artifacts must be a mapping"]),
        ("  artifacts:\n    finish: {file: f.md}\n", ["unknown stage 'finish'", "design", "plan", "execute"]),
        ("  artifacts:\n    Design: {file: f.md}\n", ["unknown stage 'Design'"]),
        ("  artifacts:\n    1: {file: f.md}\n", ["unknown stage 1"]),
        ("  artifacts:\n    design: f.md\n", ["artifacts.design: must be a mapping"]),
        ("  artifacts:\n    design: {required: true}\n", ["artifacts.design.file: required"]),
        ("  artifacts:\n    design: {file: f.md, title: x}\n", ["artifacts.design: unknown keys ['title']"]),
        (
            "  artifacts:\n    design: {file: f.md, source: spec}\n",
            ["artifacts.design.source", "gw:ingest", "'design'"],
        ),
        ("  artifacts:\n    design: {file: notes/f.md}\n", ["artifacts.design.file", "bare *.md basename"]),
        ('  artifacts:\n    design: {file: "bad\\0name.md"}\n', ["artifacts.design.file"]),
        ("  artifacts:\n    design: {file: 'a\\\\b.md'}\n", ["artifacts.design.file"]),
        ("  artifacts:\n    design: {file: .hidden.md}\n", ["artifacts.design.file"]),
        ("  artifacts:\n    design: {file: design.txt}\n", ["artifacts.design.file"]),
        ("  artifacts:\n    design: {file: 7}\n", ["artifacts.design.file"]),
        ("  artifacts:\n    design: {file: f.md, required: 'yes'}\n", ["artifacts.design.required", "boolean"]),
    ],
)
def test_artifact_field_refusals_name_file_stage_and_field(tmp_path: Path, block: str, needles: list[str]) -> None:
    message = _refusal(tmp_path, local="pipeline:\n" + block)
    assert str(tmp_path / "dispatch.local.yaml") in message
    for needle in needles:
        assert needle in message


def test_duplicate_file_across_stages_is_refused(tmp_path: Path) -> None:
    local = "pipeline:\n  artifacts:\n    design: {file: same.md}\n    plan: {file: same.md}\n"
    message = _refusal(tmp_path, local=local)
    assert "artifacts.plan.file: 'same.md' is already artifacts.design.file" in message


def test_file_equal_to_another_managed_artifact_is_refused(tmp_path: Path) -> None:
    message = _refusal(tmp_path, shared="pipeline:\n  artifacts:\n    plan: {file: 00-decisions.md}\n")
    assert (
        f"{tmp_path / 'dispatch.yaml'}: artifacts.plan.file: '00-decisions.md' is another managed artifact" in message
    )


def test_restating_a_stage_own_managed_filename_is_allowed(tmp_path: Path) -> None:
    config = load_dispatch_config(_workspace(tmp_path, "pipeline:\n  artifacts:\n    design: {file: 01-design.md}\n"))
    assert config.artifacts["design"].origin == str(tmp_path / "dispatch.yaml")


def test_definition_is_built_from_the_resolved_rules_and_artifacts(tmp_path: Path) -> None:
    local = "pipeline:\n  artifacts:\n    plan: {file: plan.md, required: false}\n"
    config = load_dispatch_config(_workspace(tmp_path, FEATURE_SKIPS_PLAN, local))
    definition = config.definition
    assert definition.stage_table == PACKAGED_DEFINITION.stage_table
    assert [rule.name for rule in definition.path_rules] == [*PACKAGED_NAMES, "features-skip-plan"]
    assert dict(definition.artifacts) == {
        "design": ArtifactSpec("design", "01-design.md", "design", required=True),
        "plan": ArtifactSpec("plan", "plan.md", "plan", required=False),
        "execute": ArtifactSpec("execute", "03-execute-coverage.md", "execute-coverage", required=False),
    }


def test_default_config_definition_equals_packaged(tmp_path: Path) -> None:
    config = load_dispatch_config(_workspace(tmp_path))
    definition = config.definition
    assert definition == PACKAGED_DEFINITION
    assert config.definition is not definition


@pytest.mark.parametrize(
    "document",
    [
        {"pipeline": {"path": [{"match": {"stage": "plan"}, "stages": ["finish"]}]}},
        {"pipeline": {"artifacts": {"design": {"file": "x.md", "source": "spec"}}}},
        {"pipeline": {"artifacts": {"design": {"file": "02-plan.md"}}}},
        {"pipeline": {"artifacts": {"design": {"file": "bad\x00name.md"}}}},
    ],
)
@pytest.mark.parametrize("layer", ["shared", "local"])
def test_plan_dispatch_write_refuses_malformed_blocks_and_writes_nothing(
    tmp_path: Path, layer: Literal["shared", "local"], document: dict[str, object]
) -> None:
    layout = _workspace(tmp_path)
    shared = tmp_path / "dispatch.yaml"
    before = shared.read_bytes()
    with pytest.raises(WorkspaceError):
        plan_dispatch_write(layout, layer=layer, document=document)
    assert shared.read_bytes() == before
    assert not (tmp_path / "dispatch.local.yaml").exists()


@pytest.mark.parametrize("layer", ["shared", "local"])
def test_a_valid_path_block_round_trips_through_apply(tmp_path: Path, layer: Literal["shared", "local"]) -> None:
    layout = _workspace(tmp_path)
    document = {"pipeline": {"path": [{"match": {"type": "Feature"}, "stages": ["design", "execute", "finish"]}]}}
    apply_dispatch_write(plan_dispatch_write(layout, layer=layer, document=document))
    config = load_dispatch_config(layout)
    path = tmp_path / ("dispatch.yaml" if layer == "shared" else "dispatch.local.yaml")
    assert config.path_rules[-1].origin.source == str(path)
    assert dict(config.path_rules[-1].rule.match) == {"type": "Feature"}
    assert config.path_rules[-1].rule.stages == ("design", "execute", "finish")
