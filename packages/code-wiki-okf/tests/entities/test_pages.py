from pathlib import Path

import pytest
from code_wiki_okf.entities.pages import (
    default_concept_id,
    new_page_text,
    slug,
)
from code_wiki_okf.placement import PlacementError
from okf_ext.schemas import load_schemas
from okf_ext.shape import load_sections
from okf_io import parse

_ASSETS = Path(__file__).parents[2] / "src" / "code_wiki_okf" / "assets"


def test_slug_replaces_slash() -> None:
    assert slug("@babel/core") == "@babel__core"
    assert slug("okf-io") == "okf-io"


def test_default_concept_id_delegates_placement_to_the_resource_policy() -> None:
    assert (
        default_concept_id(type_name="Package", resource="pkg:acme/repo-a/widgets")
        == "code-graph/repo-a/entities/packages/widgets"
    )
    assert (
        default_concept_id(type_name="Dependency", resource="dependency:acme/repo-a/npm/@babel/core")
        == "code-graph/repo-a/entities/dependencies/npm/@babel__core"
    )


def test_default_concept_id_refuses_an_unsafe_resource_identity() -> None:
    resource = "pkg:acme/repo-a/trailing."
    with pytest.raises(PlacementError, match=resource):
        default_concept_id(type_name="Package", resource=resource)


def test_new_page_text_parses_clean_and_carries_every_declared_section() -> None:
    schema_set = load_schemas(_ASSETS / "schema")
    section_set = load_sections(_ASSETS / "sections")
    text = new_page_text(
        schema_set=schema_set,
        section_set=section_set,
        type_name="Package",
        title="okf-io",
        resource="pkg:acme/agent-workspace/okf-io",
    )
    document = parse(text)
    assert document.parse_error is None
    assert document.fm.type == "Package"
    assert document.fm.title == "okf-io"
    assert document.fm.resource == "pkg:acme/agent-workspace/okf-io"
    for heading in ("Purpose", "Public API", "Files"):
        assert f"## {heading}" in document.body
