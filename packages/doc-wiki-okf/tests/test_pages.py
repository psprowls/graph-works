"""Placement comes off the schema; the new page reads back as what it claims."""

import importlib.resources

import pytest
from doc_wiki_okf.diataxis.classify import Classification
from doc_wiki_okf.diataxis.pages import default_concept_id, directory_for, new_page_text
from okf_ext.schemas import load_schemas
from okf_ext.sections import render_skeleton
from okf_ext.shape import load_sections
from okf_io import parse

_LANES = {
    "Tutorial": "tutorials/",
    "HowTo": "how-tos/",
    "Reference": "references/",
    "Explanation": "explanations/",
}


@pytest.fixture
def schema_set():
    return load_schemas(str(importlib.resources.files("doc_wiki_okf") / "assets" / "schema"))


@pytest.fixture
def section_set():
    return load_sections(str(importlib.resources.files("doc_wiki_okf") / "assets" / "sections"))


def test_directory_for_reads_each_declared_lane(schema_set) -> None:
    for type_name, lane in _LANES.items():
        assert directory_for(schema_set, type_name) == lane


def test_directory_for_raises_for_an_undeclared_type(schema_set) -> None:
    with pytest.raises(KeyError):
        directory_for(schema_set, "Concept")


def test_default_concept_id_slugs_the_title_and_carries_no_suffix(schema_set) -> None:
    got = default_concept_id(schema_set, type_name="Explanation", title="Why OKF Uses `type`")
    assert got == "explanations/why-okf-uses-type"
    assert not got.endswith(".md")


def test_default_concept_id_degrades_a_titleless_title(schema_set) -> None:
    """`reading.slugify` returns `untitled` for all-punctuation input."""
    assert default_concept_id(schema_set, type_name="Tutorial", title="!!!") == "tutorials/untitled"


def _classification(type_name: str, title: str) -> Classification:
    return Classification(
        type_name=type_name,
        concept_id=f"{_LANES[type_name]}slug",
        title=title,
        rationale="because",
        decided_by="agent:test",
    )


@pytest.mark.parametrize("type_name", list(_LANES))
def test_new_page_text_reads_back_as_what_it_claims(schema_set, section_set, type_name) -> None:
    text = new_page_text(
        schema_set=schema_set,
        section_set=section_set,
        classification=_classification(type_name, "A page"),
        description="What it is.",
    )
    document = parse(text)
    assert document.parse_error is None
    assert document.fm.type == type_name
    assert document.fm.title == "A page"
    assert document.fm.description == "What it is."
    assert document.body == render_skeleton(section_set.types[type_name])


def test_new_page_text_defaults_description_to_empty(schema_set, section_set) -> None:
    text = new_page_text(
        schema_set=schema_set,
        section_set=section_set,
        classification=_classification("Reference", "Flags"),
    )
    assert parse(text).fm.description == ""


def test_new_page_text_raises_for_a_type_no_schema_declares(section_set, tmp_path) -> None:
    partial = tmp_path / "schema"
    partial.mkdir()
    (partial / "Tutorial.schema.json").write_text(
        '{"$schema": "https://json-schema.org/draft/2020-12/schema",'
        ' "properties": {"type": {"const": "Tutorial"}},'
        ' "x-okf-directory": "tutorials/"}',
        encoding="utf-8",
    )
    with pytest.raises(KeyError):
        new_page_text(
            schema_set=load_schemas(partial),
            section_set=section_set,
            classification=_classification("Reference", "Flags"),
        )
