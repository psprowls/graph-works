from itertools import product

from _legacy_variants import LEGACY_PIPELINE, legacy_variant
from graph_works_core.workspace.dispatch import (
    dispatch_attributes,
    packaged_rules_matching,
    parse_rules,
    resolve_dispatch,
)
from work_tracker_okf.pipeline import ATTRIBUTES
from work_tracker_okf.vocabulary import EFFORTS, PHASES, TYPES
from work_tracker_okf.workflow import RouteState, route


def test_the_ten_packaged_rules_resolve_like_the_legacy_variants() -> None:
    compared = 0
    for type_, effort, spec, plan, stale, phase in product(
        sorted(TYPES), [None, *sorted(EFFORTS)], (False, True), (False, True), (False, True), [None, *sorted(PHASES)]
    ):
        status = "in-progress" if phase in {"execute", "finish"} else "open"
        state = RouteState(
            type=type_,
            work_status=status,
            phase=phase,
            effort=effort,
            has_spec_doc=spec,
            has_plan_doc=plan,
            stale_spec=("work/sibling",) if stale else (),
            has_branch=True,
        )
        result = route(state)
        if result.dispatch is None:
            continue
        attributes = dispatch_attributes(state, result.dispatch)
        if result.dispatch.stage == "finish":
            assert packaged_rules_matching(attributes)[-1].fields["prompt_tail"] is None
            rules = parse_rules(
                [{"match": {"stage": "finish"}, "prompt_tail": "parity relay tail"}],
                source="parity",
                attributes=frozenset(ATTRIBUTES),
            )
        else:
            rules = ()
        profile = resolve_dispatch(attributes, rules=rules).profile
        skill, mode, tail = LEGACY_PIPELINE[legacy_variant(state, result.dispatch.stage)]
        assert (profile.skill, profile.mode, profile.prompt_tail) == (
            skill,
            mode,
            "parity relay tail" if result.dispatch.stage == "finish" else tail,
        ), state
        assert profile.agent == "claude" and profile.model is None and profile.reasoning_effort is None
        compared += 1
    assert compared > 500
