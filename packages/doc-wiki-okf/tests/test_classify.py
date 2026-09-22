"""Every refusal reason is reachable, and a good decision derives placement."""

import dataclasses
import importlib.resources

import pytest
from doc_wiki_okf.diataxis.classify import Classification, Unclassified, classify
from doc_wiki_okf.diataxis.rubric import TYPE_NAMES
from okf_ext.schemas import load_schemas

_GOOD = {
    "type_name": "Reference",
    "title": "CLI flags",
    "rationale": "It is a lookup table of flags, not a walkthrough.",
    "decided_by": "agent:ingestor",
}


@pytest.fixture
def schema_set():
    assets = importlib.resources.files("doc_wiki_okf") / "assets" / "schema"
    return load_schemas(str(assets))


def test_a_good_decision_becomes_a_classification(schema_set) -> None:
    result = classify(schema_set, **_GOOD)
    assert isinstance(result, Classification)
    assert result.type_name == "Reference"
    assert result.concept_id == "docs/reference/cli-flags"
    assert result.title == "CLI flags"
    assert result.rationale.startswith("It is a lookup table")
    assert result.decided_by == "agent:ingestor"


def test_both_value_types_are_frozen_and_slotted() -> None:
    for cls in (Classification, Unclassified):
        assert dataclasses.fields(cls)
        assert cls.__dataclass_params__.frozen is True
        assert cls.__slots__


@pytest.mark.parametrize("declined", ["", "   ", "\t\n"])
def test_a_blank_type_is_undecided_not_unknown(schema_set, declined) -> None:
    """Spec §4.3: `undecided` is a first-class outcome."""
    result = classify(schema_set, **{**_GOOD, "type_name": declined})
    assert isinstance(result, Unclassified)
    assert result.reason == "undecided"


def test_an_unrecognised_type_is_unknown_type(schema_set) -> None:
    result = classify(schema_set, **{**_GOOD, "type_name": "Concept"})
    assert isinstance(result, Unclassified)
    assert result.reason == "unknown-type"
    assert "Concept" in result.detail


def test_a_rubric_type_with_no_installed_schema_is_undeclared_type(tmp_path) -> None:
    """The declarations were never installed. A partial `schema/` is the case."""
    partial = tmp_path / "schema"
    partial.mkdir()
    (partial / "Tutorial.schema.json").write_text(
        '{"$schema": "https://json-schema.org/draft/2020-12/schema",'
        ' "properties": {"type": {"const": "Tutorial"}},'
        ' "x-okf-directory": "docs/tutorials/"}',
        encoding="utf-8",
    )
    result = classify(load_schemas(partial), **_GOOD)
    assert isinstance(result, Unclassified)
    assert result.reason == "undeclared-type"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_title_is_no_title(schema_set, blank) -> None:
    result = classify(schema_set, **{**_GOOD, "title": blank})
    assert isinstance(result, Unclassified)
    assert result.reason == "no-title"


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_a_blank_rationale_is_a_refusal_not_a_warning(schema_set, blank) -> None:
    """Spec §4.3: a blank value folding silently is `effective_kind()`'s bug."""
    result = classify(schema_set, **{**_GOOD, "rationale": blank})
    assert isinstance(result, Unclassified)
    assert result.reason == "no-rationale"


def test_the_reason_vocabulary_is_exactly_five(schema_set) -> None:
    seen = {
        classify(schema_set, **{**_GOOD, key: value}).reason  # type: ignore[union-attr]
        for key, value in (
            ("type_name", ""),
            ("type_name", "Concept"),
            ("title", ""),
            ("rationale", ""),
        )
    }
    assert seen == {"undecided", "unknown-type", "no-title", "no-rationale"}


def test_nothing_raises_for_any_input(schema_set) -> None:
    """The content-path posture: a bad value is returned, never thrown."""
    for kwargs in (
        {**_GOOD, "type_name": "Tutorial!!"},
        {**_GOOD, "title": "  "},
        {**_GOOD, "rationale": ""},
        {**_GOOD, "decided_by": ""},
    ):
        assert classify(schema_set, **kwargs) is not None


def test_a_lane_type_outside_the_rubric_is_accepted_when_the_caller_allows_it(schema_set) -> None:
    """The ADR lane files a type of its own (`Adr`); the rubric's four stay the default."""
    args = dict(type_name="Adr", title="Adopt JWTs", rationale="A decision.", decided_by="agent:x")

    refused = classify(schema_set, **args)
    assert isinstance(refused, Unclassified) and refused.reason == "unknown-type"

    accepted = classify(schema_set, allowed_types=(*TYPE_NAMES, "Adr"), **args)
    assert not isinstance(accepted, Unclassified)
    assert accepted.type_name == "Adr" and accepted.concept_id.startswith("adrs/")
