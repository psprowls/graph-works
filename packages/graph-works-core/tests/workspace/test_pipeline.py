"""The dispatch table: packaged default, workspace override, total over Variant."""

from __future__ import annotations

import typing

import pytest
from config_io import InvalidValueError
from graph_works_core.workspace import manifest, pipeline
from graph_works_core.workspace.layout import layout_for
from subagents_io.dispatch import DISPATCH_MODES
from work_tracker_okf.workflow import Variant


def _workspace(tmp_path, manifest_text="version: 1\n"):
    (tmp_path / "workspace.yaml").write_text(manifest_text, encoding="utf-8")
    return layout_for(tmp_path)


def test_the_packaged_table_is_total_over_variant():
    # This is what makes 3.2's "an override cannot leave a hole" true rather
    # than hoped: a variant added in work-tracker-okf fails here until mapped.
    assert set(pipeline.PACKAGED_PIPELINE) == set(typing.get_args(Variant))


def test_every_packaged_mode_is_a_dispatch_mode():
    assert {entry.mode for entry in pipeline.PACKAGED_PIPELINE.values()} <= DISPATCH_MODES


def test_packaged_only_resolution_needs_no_layout():
    table = pipeline.pipeline_table()
    assert table["exploration"] == pipeline.PipelineEntry(
        skill="brainstorming", mode="attend", prompt_tail=pipeline.ATTEND_TAIL
    )
    assert table["branch"] == pipeline.PipelineEntry(
        skill="finishing-a-development-branch", mode="relay", prompt_tail=None
    )


def test_one_override_replaces_one_field_and_nothing_else(tmp_path):
    layout = _workspace(
        tmp_path,
        'version: 1\nworkflow:\n  pipeline:\n    branch:\n      prompt_tail: "merge target is {merge_target}"\n',
    )
    table = pipeline.pipeline_table(layout=layout)
    assert table["branch"] == pipeline.PipelineEntry(
        skill="finishing-a-development-branch",
        mode="relay",
        prompt_tail="merge target is {merge_target}",
    )
    assert table["single"] == pipeline.PACKAGED_PIPELINE["single"]


def test_an_override_cannot_introduce_a_variant(tmp_path):
    layout = _workspace(tmp_path, "version: 1\nworkflow:\n  pipeline:\n    invented:\n      skill: nope\n")
    assert set(pipeline.pipeline_table(layout=layout)) == set(typing.get_args(Variant))


def test_an_explicit_null_does_not_shadow_the_packaged_value(tmp_path):
    layout = _workspace(tmp_path, "version: 1\nworkflow:\n  pipeline:\n    single:\n      skill: null\n")
    assert pipeline.pipeline_table(layout=layout)["single"].skill == "writing-plans"


def test_a_bad_mode_is_refused_at_set_time(tmp_path):
    path = tmp_path / "workspace.yaml"
    path.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(InvalidValueError):
        manifest.set_value(path, "workflow.pipeline.branch.mode", "nope")


def test_entry_for_reads_one_variant(tmp_path):
    assert pipeline.entry_for("diagnosis").skill == "systematic-debugging"


def test_a_hand_edited_bad_mode_is_refused_at_read_time(tmp_path):
    # The set-time refusal above only fires through `gw config set`. A
    # hand-edited manifest reaches `PipelineEntry.mode` raw and surfaces as
    # `UnsupportedMode` from the backend, far from the file that caused it.
    from graph_works_core.workspace.errors import WorkspaceError

    layout = _workspace(tmp_path, "version: 1\nworkflow:\n  pipeline:\n    exploration:\n      mode: yolo\n")
    with pytest.raises(WorkspaceError) as excinfo:
        pipeline.pipeline_table(layout=layout)
    assert "workflow.pipeline.exploration.mode" in str(excinfo.value)
    assert str(layout.manifest_path) in str(excinfo.value)


def test_a_non_string_skill_is_refused(tmp_path):
    # `mypy --strict` cannot see this: the override dict is `Any`, so an int
    # lands in `PipelineEntry.skill` typed `str` with no complaint.
    from graph_works_core.workspace.errors import WorkspaceError

    layout = _workspace(tmp_path, "version: 1\nworkflow:\n  pipeline:\n    single:\n      skill: 3\n")
    with pytest.raises(WorkspaceError, match="expects a string"):
        pipeline.pipeline_table(layout=layout)


def test_a_non_string_prompt_tail_is_refused(tmp_path):
    # Sharper than `skill`: `_prompt` calls `tail.replace(...)`, so an int here
    # is an AttributeError mid-plan rather than a wrong-but-running dispatch.
    from graph_works_core.workspace.errors import WorkspaceError

    layout = _workspace(tmp_path, "version: 1\nworkflow:\n  pipeline:\n    branch:\n      prompt_tail: 3\n")
    with pytest.raises(WorkspaceError, match="expects a string"):
        pipeline.pipeline_table(layout=layout)


def test_a_valid_override_still_layers_after_the_gate(tmp_path):
    layout = _workspace(tmp_path, "version: 1\nworkflow:\n  pipeline:\n    single:\n      mode: attend\n")
    table = pipeline.pipeline_table(layout=layout)
    assert table["single"] == pipeline.PipelineEntry(skill="writing-plans", mode="attend", prompt_tail=None)


def test_the_relay_seed_carries_both_halves_of_the_relay_contract():
    # `finishing-relay` triggers on the literal prefix and reads the merge
    # target out of the same line. A tail carrying one without the other arms
    # half the seam, so both are asserted here rather than in prose.
    assert pipeline.RELAY_TAIL_SEED.startswith("Auto-drive context:")
    assert "{merge_target}" in pipeline.RELAY_TAIL_SEED


def test_the_relay_seed_names_no_vendor():
    # Core is the thing writing this string; a workspace replaces it with its
    # own vendor wording through `gw config set`.
    lowered = pipeline.RELAY_TAIL_SEED.lower()
    assert "orca" not in lowered
    assert "graph-wiki" not in lowered


def test_the_packaged_branch_entry_stays_untailed():
    # The seed is a value the *workspace* owns. Shipping it in the packaged
    # table would make the `relay-untailed` blocker unreachable.
    assert pipeline.PACKAGED_PIPELINE["branch"].prompt_tail is None


def test_both_execute_variants_carry_the_coverage_obligation():
    table = pipeline.pipeline_table()
    assert table["planned"] == pipeline.PipelineEntry(
        skill="subagent-driven-development", mode="autonomous", prompt_tail=pipeline.EXECUTE_TAIL
    )
    assert table["unplanned"] == pipeline.PipelineEntry(
        skill="test-driven-development", mode="autonomous", prompt_tail=pipeline.EXECUTE_TAIL
    )


def test_the_execute_tail_names_the_artifact_and_its_placeholders():
    # The tail is substituted by `_prompt` with `str.replace` over a fixed
    # placeholder set; a tail naming a placeholder outside that set would ship
    # a literal brace to a worker.
    assert "03-execute-coverage.md" in pipeline.EXECUTE_TAIL
    assert "{workspace}" in pipeline.EXECUTE_TAIL
    assert "{path}" in pipeline.EXECUTE_TAIL
    assert "## Acceptance" in pipeline.EXECUTE_TAIL


def test_a_workspace_may_still_override_the_execute_tail(tmp_path):
    layout = _workspace(
        tmp_path,
        'version: 1\nworkflow:\n  pipeline:\n    unplanned:\n      prompt_tail: "ours"\n',
    )
    table = pipeline.pipeline_table(layout=layout)
    assert table["unplanned"].prompt_tail == "ours"
    assert table["planned"].prompt_tail == pipeline.EXECUTE_TAIL
