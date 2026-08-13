"""The taxonomy is complete, derived, and safe to embed."""

import dataclasses
from pathlib import Path

from doc_wiki_okf.diataxis.rubric import RUBRIC, TYPE_NAMES, TypeRubric, brief
from doc_wiki_okf.resources import SEED_RELATIVE_PATHS

#: Declared by this package but deliberately outside `RUBRIC` (S-K). A
#: `TypeRubric` carries `question`, `signals`, `anti_signals` and
#: `title_pattern` — aids for a judgment call about author intent. There is no
#: judgment to make about whether ingested material is a Source; it is one by
#: construction, and filling those four fields would mean inventing content for
#: a decision nobody makes.
NON_RUBRIC_TYPES = frozenset({"Source"})


def test_the_four_names_in_diataxis_order() -> None:
    assert TYPE_NAMES == ("Tutorial", "HowTo", "Reference", "Explanation")


def test_type_names_is_derived_from_the_rubric() -> None:
    assert tuple(entry.type_name for entry in RUBRIC) == TYPE_NAMES


def test_the_value_type_is_frozen_and_slotted() -> None:
    assert dataclasses.fields(TypeRubric)
    params = TypeRubric.__dataclass_params__
    assert params.frozen is True
    assert TypeRubric.__slots__


def test_every_entry_carries_a_question_signals_and_a_title_pattern() -> None:
    for entry in RUBRIC:
        assert entry.question.strip()
        assert entry.title_pattern.strip()
        assert len(entry.signals) >= 3
        assert len(entry.anti_signals) >= 2


def test_brief_names_every_type_as_a_heading() -> None:
    text = brief()
    for name in TYPE_NAMES:
        assert f"## {name}" in text


def test_brief_carries_every_signal_verbatim() -> None:
    text = brief()
    for entry in RUBRIC:
        for signal in (*entry.signals, *entry.anti_signals):
            assert signal in text


def test_brief_seeds_no_render_finding() -> None:
    """It gets embedded in C2's ingest brief; a `<slot>` there becomes a
    `render.angle-bracket` in whatever page quotes it."""
    text = brief()
    assert "<" not in text
    assert "[[" not in text


def test_every_rubric_type_has_both_declaration_files() -> None:
    """Spec §7.7: adding a fifth type without its two files fails here rather
    than at a consumer."""
    for name in TYPE_NAMES:
        assert f"_schema/{name}.schema.json" in SEED_RELATIVE_PATHS
        assert f"_sections/{name}.yaml" in SEED_RELATIVE_PATHS


def test_no_declaration_names_a_type_the_rubric_does_not() -> None:
    """The other direction: a schema shipped for a type nothing can classify,
    minus the types declared outside the rubric on purpose."""
    declared = {
        path.removeprefix("_schema/").removesuffix(".schema.json")
        for path in SEED_RELATIVE_PATHS
        if path.startswith("_schema/") and not path.startswith("_schema/_")
    }
    assert declared - NON_RUBRIC_TYPES == set(TYPE_NAMES)
    assert declared & NON_RUBRIC_TYPES == NON_RUBRIC_TYPES


def test_a_non_rubric_type_is_still_refused_by_classify_and_retype(tmp_path: Path) -> None:
    """Both gate on `TYPE_NAMES` before consulting the schema set, so declaring
    `Source` does not make it a Diátaxis type anyone can classify a page into."""
    import importlib.resources

    from diataxis_helpers import build_bundle, schema_set
    from doc_wiki_okf.diataxis.classify import Unclassified, classify
    from doc_wiki_okf.diataxis.retype import plan_retype
    from okf_ext.schemas import load_schemas

    schemas = load_schemas(str(importlib.resources.files("doc_wiki_okf") / "assets" / "_schema"))
    outcome = classify(schemas, type_name="Source", title="A source", rationale="because", decided_by="agent:test")
    assert isinstance(outcome, Unclassified)
    assert outcome.reason == "unknown-type"

    root: Path = tmp_path / "bundle"
    bundle = build_bundle(
        root,
        {"explanations/test": "---\ntype: Explanation\ntitle: Test\ndescription: A test.\n---\n\n## Context\n"},
    )
    plan = plan_retype(bundle, schema_set(), "explanations/test", "Source")
    assert plan.ok is False
    assert [refusal.kind for refusal in plan.refusals] == ["unknown-type"]
