"""The dispatch table: packaged default, workspace override, total over Variant."""

from __future__ import annotations

import typing

import pytest
from graph_works_core.workspace import pipeline
from subagents_io.dispatch import DISPATCH_MODES
from work_tracker_okf.workflow import Variant


def test_the_packaged_table_is_total_over_variant():
    # This is what makes 3.2's "an override cannot leave a hole" true rather
    # than hoped: a variant added in work-tracker-okf fails here until mapped.
    assert set(pipeline.PACKAGED_PIPELINE) == set(typing.get_args(Variant))


def test_every_packaged_mode_is_a_dispatch_mode():
    assert {entry.mode for entry in pipeline.PACKAGED_PIPELINE.values()} <= DISPATCH_MODES


def test_every_packaged_skill_is_plugin_qualified():
    # The name shape is load-bearing: the workflow skill invokes `action.skill`
    # verbatim, so a bare name here resolves to whichever plugin happens to
    # claim it. This is the regression guard for a ninth variant, or for a row
    # edited back to a bare name. Enforcement at dispatch time is a later
    # child's seam; this is the value-side half of the same property.
    for variant, entry in pipeline.PACKAGED_PIPELINE.items():
        plugin, sep, skill = entry.skill.partition(":")
        assert sep == ":", f"{variant}: packaged skill {entry.skill!r} is not <plugin>:<skill>"
        assert plugin, f"{variant}: packaged skill {entry.skill!r} has an empty plugin"
        assert skill, f"{variant}: packaged skill {entry.skill!r} has an empty skill"
        assert ":" not in skill, f"{variant}: packaged skill {entry.skill!r} is multiply qualified"


def test_packaged_only_resolution_needs_no_layout():
    table = pipeline.PACKAGED_PIPELINE
    assert table["exploration"] == pipeline.PipelineEntry(
        skill="superpowers:brainstorming", mode="attend", prompt_tail=pipeline.ATTEND_TAIL
    )
    assert table["branch"] == pipeline.PipelineEntry(
        skill="superpowers:finishing-a-development-branch", mode="relay", prompt_tail=None
    )


def test_the_epic_design_entry_is_attend_with_the_neutral_tail():
    """An epic design is a human-in-the-room stage, same as `exploration`. The
    skill name is asserted by suffix so this test survives child 5's
    fully-qualified rename without becoming a second place to edit."""
    entry = pipeline.PACKAGED_PIPELINE["epic-design"]
    assert entry.skill.endswith("epic-design")
    assert entry.mode == "attend"
    assert entry.prompt_tail == pipeline.ATTEND_TAIL


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


@pytest.mark.parametrize("name", ["brainstorming", "graph-works:brainstorming", "a:b"])
def test_a_well_formed_skill_name_is_accepted(name):
    # Bare names stay valid (D-002): user-level and repo-local skills carry no
    # plugin prefix, so requiring qualification would make them unroutable.
    assert pipeline.is_valid_skill_name(name)


@pytest.mark.parametrize("name", ["", "   ", "a:", ":b", "a:b:c", "a: ", " :b"])
def test_a_malformed_skill_name_is_rejected(name):
    # A colon signals qualification *intent*, so a malformed qualification is
    # still a refusable shape even though a bare name is not.
    assert not pipeline.is_valid_skill_name(name)


def test_check_skill_name_refuses_a_non_string(tmp_path):
    from graph_works_core.workspace.errors import WorkspaceError

    with pytest.raises(WorkspaceError, match="expects a skill name"):
        pipeline.check_skill_name(3, key="workflow.pipeline.single.skill", source=tmp_path / "workspace.yaml")


def test_every_packaged_skill_name_is_well_formed():
    # `PACKAGED_PIPELINE` is a module constant, deliberately *not* checked at
    # runtime -- a bad packaged value should fail the suite, not every command
    # for every user. Same posture as the totality test above.
    assert all(pipeline.is_valid_skill_name(entry.skill) for entry in pipeline.PACKAGED_PIPELINE.values())


def test_both_execute_variants_carry_the_coverage_obligation():
    table = pipeline.PACKAGED_PIPELINE
    assert table["planned"] == pipeline.PipelineEntry(
        skill="superpowers:subagent-driven-development", mode="autonomous", prompt_tail=pipeline.EXECUTE_TAIL
    )
    assert table["unplanned"] == pipeline.PipelineEntry(
        skill="superpowers:test-driven-development", mode="autonomous", prompt_tail=pipeline.EXECUTE_TAIL
    )


def test_the_execute_tail_names_the_artifact_and_its_placeholders():
    # The tail is substituted by `_prompt` with `str.replace` over a fixed
    # placeholder set; a tail naming a placeholder outside that set would ship
    # a literal brace to a worker.
    assert "03-execute-coverage.md" in pipeline.EXECUTE_TAIL
    assert "{workspace}" in pipeline.EXECUTE_TAIL
    assert "{path}" in pipeline.EXECUTE_TAIL
    assert "## Acceptance" in pipeline.EXECUTE_TAIL
