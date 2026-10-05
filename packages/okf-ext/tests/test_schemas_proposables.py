"""`declared_proposables`: the proposal pool read off a schema set."""

from __future__ import annotations

import json
from datetime import date

import pytest
from ext_helpers import write
from okf_ext.schemas import (
    Proposables,
    ProposableType,
    ProposalGuidance,
    ProposalPromotion,
    declared_proposables,
    load_schemas,
)
from okf_ext.schemas import loader as schemas_loader

GUIDANCE = {"summary": "the reader wants to understand why.", "question": "Does the reader want to understand why?"}
BASE = {"type": "object", "required": ["type", "title", "description"]}


def _set(tmp_path, **documents):
    """A schema directory of `<Name>.schema.json` files; `_x` names are `$ref` targets."""
    root = tmp_path / "schema"
    root.mkdir(exist_ok=True)
    for name, body in documents.items():
        write(root / f"{name}.schema.json", json.dumps(body))
    return load_schemas(root)


def _flagged(**extra):
    return {
        "$ref": "_base.schema.json",
        "properties": {"type": {"const": "Widget"}},
        "x-okf-directory": "widgets/",
        "x-okf-accept-proposals": True,
        "x-okf-proposal-guidance": GUIDANCE,
        **extra,
    }


def test_a_flagged_type_joins_the_pool_with_its_guidance(tmp_path):
    found = declared_proposables(_set(tmp_path, _base=BASE, Widget=_flagged()))
    assert found == Proposables(
        types=(
            ProposableType(
                name="Widget",
                directory="widgets/",
                guidance=ProposalGuidance(summary=GUIDANCE["summary"], question=GUIDANCE["question"]),
                promotion=None,
            ),
        ),
        locked=(),
        refused=(),
    )


def test_optional_guidance_fields_are_carried_stripped(tmp_path):
    guidance = {**GUIDANCE, "signals": [" a "], "anti_signals": ["b"], "title_pattern": " T "}
    (entry,) = declared_proposables(
        _set(tmp_path, _base=BASE, Widget=_flagged(**{"x-okf-proposal-guidance": guidance}))
    ).types
    assert entry.guidance.signals == ("a",)
    assert entry.guidance.anti_signals == ("b",)
    assert entry.guidance.title_pattern == "T"


def test_an_unflagged_type_is_silently_absent(tmp_path):
    body = {k: v for k, v in _flagged().items() if k != "x-okf-accept-proposals"}
    assert declared_proposables(_set(tmp_path, _base=BASE, Widget=body)) == Proposables((), (), ())


def test_false_locks_the_type_whatever_else_it_carries(tmp_path):
    body = _flagged(**{"x-okf-accept-proposals": False, "x-okf-proposal-guidance": "garbage"})
    assert declared_proposables(_set(tmp_path, _base=BASE, Widget=body)) == Proposables((), ("Widget",), ())


@pytest.mark.parametrize("flag", ["yes", 1, 0, None, []])
def test_a_non_boolean_flag_refuses_the_type(tmp_path, flag):
    found = declared_proposables(_set(tmp_path, _base=BASE, Widget=_flagged(**{"x-okf-accept-proposals": flag})))
    assert found.refused == (("Widget", "invalid-flag"),)
    assert found.types == ()


def test_a_flagged_type_with_no_directory_is_refused(tmp_path):
    body = {k: v for k, v in _flagged().items() if k != "x-okf-directory"}
    assert declared_proposables(_set(tmp_path, _base=BASE, Widget=body)).refused == (("Widget", "no-directory"),)


def test_a_flagged_type_with_no_guidance_is_refused(tmp_path):
    body = {k: v for k, v in _flagged().items() if k != "x-okf-proposal-guidance"}
    assert declared_proposables(_set(tmp_path, _base=BASE, Widget=body)).refused == (("Widget", "missing-guidance"),)


@pytest.mark.parametrize(
    ("guidance", "fragment"),
    [
        ("text", "must be an object"),
        ({"question": "q?"}, "`summary`"),
        ({"summary": "  ", "question": "q?"}, "`summary`"),
        ({"summary": "s", "question": ""}, "`question`"),
        ({**GUIDANCE, "signals": "one"}, "`signals`"),
        ({**GUIDANCE, "anti_signals": ["ok", ""]}, "`anti_signals`"),
        ({**GUIDANCE, "title_pattern": 3}, "`title_pattern`"),
        ({**GUIDANCE, "title_pattern": None}, "`title_pattern`"),
        ({**GUIDANCE, "signal": ["typo"]}, "unknown key(s) signal"),
    ],
)
def test_malformed_guidance_refuses_the_type_with_its_reason(tmp_path, guidance, fragment):
    found = declared_proposables(_set(tmp_path, _base=BASE, Widget=_flagged(**{"x-okf-proposal-guidance": guidance})))
    ((name, reason),) = found.refused
    assert name == "Widget"
    assert reason.startswith("invalid-guidance: ")
    assert fragment in reason


@pytest.mark.parametrize(
    ("promotion", "fragment"),
    [
        ([], "must be an object"),
        ({"dated": "yes"}, "`dated`"),
        ({"frontmatter": {"status": 1}}, "`frontmatter`"),
        ({"frontmatter": {" ": "x"}}, "`frontmatter`"),
        ({"frontmatter": {"sources": "x"}}, "may not set sources"),
        ({"frontmatter": {"title": "x", "generated": "y"}}, "may not set generated, title"),
        ({"date": True}, "unknown key(s) date"),
    ],
)
def test_malformed_promotion_refuses_the_type_with_its_reason(tmp_path, promotion, fragment):
    found = declared_proposables(_set(tmp_path, _base=BASE, Widget=_flagged(**{"x-okf-proposal-promotion": promotion})))
    ((name, reason),) = found.refused
    assert name == "Widget"
    assert reason.startswith("invalid-promotion: ")
    assert fragment in reason


def test_a_required_field_reached_through_the_ref_must_be_covered(tmp_path):
    base = {**BASE, "required": [*BASE["required"], "owner"]}
    found = declared_proposables(_set(tmp_path, _base=base, Widget=_flagged()))
    assert found.refused == (("Widget", "uncovered-required: owner"),)


def test_the_types_own_required_and_allof_branches_count_too(tmp_path):
    body = _flagged(required=["decision_date"], allOf=[{"required": ["status"]}])
    found = declared_proposables(_set(tmp_path, _base=BASE, Widget=body))
    assert found.refused == (("Widget", "uncovered-required: decision_date, status"),)


def test_promotion_frontmatter_covers_the_required_fields(tmp_path):
    promotion = {"dated": True, "frontmatter": {"decision_date": "{on}", "status": "stable"}}
    body = _flagged(required=["decision_date", "status"], **{"x-okf-proposal-promotion": promotion})
    (entry,) = declared_proposables(_set(tmp_path, _base=BASE, Widget=body)).types
    assert entry.promotion == ProposalPromotion(dated=True, frontmatter={"decision_date": "{on}", "status": "stable"})


def test_a_dangling_ref_is_an_unresolved_base(tmp_path):
    body = _flagged(**{"$ref": "_missing.schema.json"})
    assert declared_proposables(_set(tmp_path, _base=BASE, Widget=body)).refused == (("Widget", "unresolved-base"),)


def test_a_refused_type_leaves_its_neighbours_in_the_pool(tmp_path):
    other = {**_flagged(), "properties": {"type": {"const": "Gadget"}}, "x-okf-directory": "gadgets/"}
    found = declared_proposables(
        _set(tmp_path, _base=BASE, Widget=_flagged(**{"x-okf-accept-proposals": "yes"}), Gadget=other)
    )
    assert [entry.name for entry in found.types] == ["Gadget"]
    assert found.refused == (("Widget", "invalid-flag"),)


def test_frontmatter_on_expands_the_on_token_only():
    promotion = ProposalPromotion(frontmatter={"opened": "{on}", "note": "on {on} by {who}", "status": "stable"})
    assert promotion.frontmatter_on(date(2026, 10, 4)) == {
        "opened": "2026-10-04",
        "note": "on 2026-10-04 by {who}",
        "status": "stable",
    }


def test_the_promotion_exemptions_cover_every_key_the_proposals_capability_owns():
    """`schemas` may not import `proposals` (the independence contract), so the
    list is restated there; this pins the two together."""
    from okf_ext.proposals import OWNED_PROVENANCE_KEYS

    assert set(OWNED_PROVENANCE_KEYS) <= schemas_loader.PROMOTION_SUPPLIED
    assert {"type", "title", "description"} <= schemas_loader.PROMOTION_SUPPLIED


@pytest.mark.parametrize("pointer", ["/allOf/0", "/$defs/a~1b~0c", "/$defs/", "/$defs/a%20b"])
def test_covered_required_field_reached_through_json_pointer_joins_pool(tmp_path, pointer):
    target = {"required": ["owner"]}
    base = {"allOf": [target], "$defs": {"a/b~c": target, "": target, "a b": target}}
    body = _flagged(
        **{
            "$ref": f"_base.schema.json#{pointer}",
            "x-okf-proposal-promotion": {"frontmatter": {"owner": "team"}},
        }
    )
    found = declared_proposables(_set(tmp_path, _base=base, Widget=body))
    assert found.refused == ()
    assert [entry.name for entry in found.types] == ["Widget"]
    body.pop("x-okf-proposal-promotion")
    uncovered = declared_proposables(_set(tmp_path, _base=base, Widget=body))
    assert uncovered.refused == (("Widget", "uncovered-required: owner"),)


@pytest.mark.parametrize("index", ["01", "-1", "1", "-", "one"])
def test_invalid_array_pointer_is_refused_without_raising(tmp_path, index):
    base = {"allOf": [{"required": ["title"]}]}
    body = _flagged(**{"$ref": f"_base.schema.json#/allOf/{index}"})
    found = declared_proposables(_set(tmp_path, _base=base, Widget=body))
    assert found.refused == (("Widget", "unresolved-base"),)
