"""The four schemas load, ref, and annotate. Mirrors work-tracker-okf's own."""

import importlib.resources
from datetime import date
from pathlib import Path

from okf_ext.schemas import declared_members, load_schemas, schema_rule
from okf_io import load_bundle
from okf_io import validate as okf_validate

#: The four the rubric classifies. `Source` is declared but is not one of them.
_DIATAXIS_TYPES = ("Explanation", "HowTo", "Reference", "Tutorial")

#: Every type this package ships a schema for, sorted.
_ALL_TYPES = ("Explanation", "HowTo", "Reference", "Source", "Tutorial")

_LANES = {
    "Tutorial": "tutorials/",
    "HowTo": "how-tos/",
    "Reference": "references/",
    "Explanation": "explanations/",
    "Source": "sources/",
}


def _schema_set():
    assets = importlib.resources.files("doc_wiki_okf") / "assets" / "schema"
    return load_schemas(str(assets))


def test_seed_schemas_load_as_exactly_the_five_types() -> None:
    assert tuple(sorted(_schema_set().schemas)) == _ALL_TYPES


def test_the_base_is_a_ref_target_not_a_type() -> None:
    schema_set = _schema_set()
    assert "_base-diataxis" not in schema_set.schemas
    assert "_base-diataxis.schema.json" in schema_set.documents


def test_each_diataxis_wrapper_pins_the_const_to_its_own_stem() -> None:
    schema_set = _schema_set()
    for type_name in _DIATAXIS_TYPES:
        assert schema_set.schemas[type_name]["properties"]["type"] == {"const": type_name}
        assert schema_set.schemas[type_name]["$ref"] == "_base-diataxis.schema.json"


def test_each_type_declares_its_own_lane() -> None:
    """Spec §2.1: the four lanes differ, so the annotation is per type."""
    schema_set = _schema_set()
    for type_name, lane in _LANES.items():
        assert schema_set.schemas[type_name]["x-okf-directory"] == lane


def test_the_base_declares_no_directory() -> None:
    """A base-level annotation would be wrong for three of four types."""
    assert "x-okf-directory" not in _schema_set().documents["_base-diataxis.schema.json"]


def _findings(
    tmp_path: Path, frontmatter: str, code: str = "schemas.invalid", lane: str = "tutorials", heading: str = "Steps"
) -> list[str]:
    root = tmp_path / "bundle"
    (root / lane).mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    (root / lane / "page.md").write_text(f"---\n{frontmatter}---\n\n## {heading}\n", encoding="utf-8")
    report = okf_validate(
        load_bundle(root),
        today=date(2026, 8, 12),
        extra_rules=[schema_rule(_schema_set())],
    )
    return [finding.message for finding in report.by_code(code)]


_COMPLETE = "type: Tutorial\ntitle: A tutorial\ndescription: A tutorial.\n"


def test_the_base_required_keys_reach_the_wrapper_through_the_ref(tmp_path: Path) -> None:
    messages = _findings(tmp_path, "type: Tutorial\ntitle: A tutorial\n")
    assert any("'description' is a required property" in message for message in messages)


def test_the_three_added_keys_are_all_optional(tmp_path: Path) -> None:
    """Spec §2.3: no page that exists today can fail on one."""
    assert _findings(tmp_path, _COMPLETE) == []


def test_the_added_keys_are_typed_when_present(tmp_path: Path) -> None:
    messages = _findings(tmp_path, _COMPLETE + "prerequisites: not-a-list\n")
    assert any("is not of type 'array'" in message for message in messages)


def test_a_mismatched_const_is_a_finding(tmp_path: Path) -> None:
    messages = _findings(
        tmp_path, _COMPLETE.replace("type: Tutorial", "type: Tutorail"), code="schemas.no-schema-for-type"
    )
    assert len(messages) == 1
    assert "No schema for type `Tutorail`" in messages[0]


def test_source_is_standalone_and_pins_its_own_const() -> None:
    """S-B: `Source` is not a Diátaxis type, and inheriting the base's `status`
    enum and property set would assert a kinship that does not exist."""
    schema = _schema_set().schemas["Source"]
    assert "$ref" not in schema
    assert schema["properties"]["type"] == {"const": "Source"}
    assert schema["type"] == "object"


def test_source_requires_the_four_keys() -> None:
    """S-J, minus `source_kind`: K-C drops it from `required` because nothing
    behavioural depends on the value, so forcing a classification on material
    that genuinely has no genre buys nothing."""
    assert sorted(_schema_set().schemas["Source"]["required"]) == [
        "description",
        "source_path",
        "title",
        "type",
    ]


def test_source_kind_is_a_closed_vocabulary_of_seven() -> None:
    """K-B: nine values cut to seven -- `example` and `note` dropped, `pr`
    widened to `code-review`. This is the one authored copy; a follow-up
    deletes the duplicated `SOURCE_TYPES` tuple and the duplicated prose copy
    in the ingestor prompt so that every consumer derives from this enum at
    run time."""
    enum = _schema_set().schemas["Source"]["properties"]["source_kind"]["enum"]
    assert enum == ["spec", "article", "ticket", "skill", "doc", "transcript", "code-review"]


def test_source_type_is_gone() -> None:
    """K-A: the old key collided with OKF's own `type: Source` three lines up."""
    assert "source_type" not in _schema_set().schemas["Source"]["properties"]


def test_source_admits_the_vault_keys_it_does_not_declare() -> None:
    """`additionalProperties: true` keeps the 18 `last_sync_commit` skill pages
    and the 8 `source_url` pages valid without declaring vault keys."""
    assert _schema_set().schemas["Source"]["additionalProperties"] is True


def test_source_path_is_the_one_declared_member_property() -> None:
    """C1: `source_path` must name a bundle member (the ingest contract's
    `sources/references/` copy). No other shipped property is annotated."""
    assert declared_members(_schema_set()) == {"Source": ("source_path",)}


_SOURCE = "type: Source\ntitle: S\ndescription: A source.\nsource_path: {path}\n"


def _source_findings(tmp_path: Path, source_path: str, *, with_target: bool) -> list[str]:
    root = tmp_path / "bundle"
    (root / "sources" / "references").mkdir(parents=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8", newline="")
    if with_target:
        (root / "sources" / "references" / "2026-08-x.md").write_text("# X\n", encoding="utf-8", newline="")
    (root / "sources" / "2026-08-x.md").write_text(
        f"---\n{_SOURCE.format(path=source_path)}---\n\n# S\n", encoding="utf-8", newline=""
    )
    report = okf_validate(load_bundle(root), today=date(2026, 8, 12), extra_rules=[schema_rule(_schema_set())])
    return [finding.path for finding in report.by_code("schemas.unresolved-member")]


def test_a_dangling_source_path_is_reported(tmp_path: Path) -> None:
    assert _source_findings(tmp_path, "sources/references/2026-08-x.md", with_target=False) == ["sources/2026-08-x.md"]


def test_a_copied_source_path_is_quiet(tmp_path: Path) -> None:
    assert _source_findings(tmp_path, "sources/references/2026-08-x.md", with_target=True) == []


_COMPLETE_SOURCE = (
    "type: Source\n"
    "title: A source\n"
    "description: What it is.\n"
    "source_kind: spec\n"
    "source_path: sources/references/2026-08-a-source.md\n"
)


def test_a_complete_source_page_produces_no_finding(tmp_path: Path) -> None:
    assert _findings(tmp_path, _COMPLETE_SOURCE, lane="sources", heading="TL;DR") == []


def test_a_source_page_missing_source_path_is_a_finding(tmp_path: Path) -> None:
    """S-J's cost, measured: exactly one of 237 live pages fails this way."""
    without = _COMPLETE_SOURCE.replace("source_path: sources/references/2026-08-a-source.md\n", "")
    messages = _findings(tmp_path, without, lane="sources", heading="TL;DR")
    assert any("'source_path' is a required property" in message for message in messages)


def test_an_unknown_source_kind_is_a_finding(tmp_path: Path) -> None:
    bad_source = _COMPLETE_SOURCE.replace("source_kind: spec", "source_kind: blog")
    messages = _findings(tmp_path, bad_source, lane="sources", heading="TL;DR")
    assert any("'blog' is not one of" in message for message in messages)


def test_an_absent_source_kind_is_no_finding(tmp_path: Path) -> None:
    """K-C: optional means an unclassified page is valid, not a warning."""
    without = _COMPLETE_SOURCE.replace("source_kind: spec\n", "")
    assert _findings(tmp_path, without, lane="sources", heading="TL;DR") == []
