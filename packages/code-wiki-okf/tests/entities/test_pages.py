from pathlib import Path

from code_wiki_okf.entities.pages import (
    default_concept_id,
    directory_for,
    new_page_text,
    slug,
)
from okf_ext.schemas import load_schemas
from okf_ext.shape import load_sections
from okf_io import parse

_ASSETS = Path(__file__).parents[2] / "src" / "code_wiki_okf" / "assets"


def test_slug_replaces_slash() -> None:
    assert slug("@babel/core") == "@babel__core"
    assert slug("okf-io") == "okf-io"


def test_directory_for_reads_x_okf_directory_from_schema() -> None:
    schema_set = load_schemas(_ASSETS / "schema")
    assert directory_for(schema_set, "Dependency") == "dependencies/"
    assert directory_for(schema_set, "Repository") == "repositories/"
    assert directory_for(schema_set, "Package") == "packages/"


def test_default_concept_id_combines_directory_and_slug() -> None:
    schema_set = load_schemas(_ASSETS / "schema")
    assert default_concept_id(schema_set, type_name="Package", name="okf-io") == "packages/okf-io"
    assert default_concept_id(schema_set, type_name="Dependency", name="@babel/core") == "dependencies/@babel__core"


def test_default_concept_id_nests_under_repository_when_repo_name_given() -> None:
    schema_set = load_schemas(_ASSETS / "schema")
    assert (
        default_concept_id(schema_set, type_name="Package", name="widgets", repo_name="repo-a")
        == "repositories/repo-a/packages/widgets"
    )
    assert (
        default_concept_id(schema_set, type_name="App", name="cli-app", repo_name="repo-a")
        == "repositories/repo-a/apps/cli-app"
    )


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
