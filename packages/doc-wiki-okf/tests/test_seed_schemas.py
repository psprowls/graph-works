"""The four schemas load, ref, and annotate. Mirrors work-tracker-okf's own."""

import importlib.resources
from datetime import date
from pathlib import Path

from okf_ext.schemas import load_schemas, schema_rule
from okf_io import load_bundle
from okf_io import validate as okf_validate

_TYPES = ("Explanation", "HowTo", "Reference", "Tutorial")

_LANES = {
    "Tutorial": "tutorials/",
    "HowTo": "how-tos/",
    "Reference": "references/",
    "Explanation": "explanations/",
}


def _schema_set():
    assets = importlib.resources.files("doc_wiki_okf") / "assets" / "_schema"
    return load_schemas(str(assets))


def test_seed_schemas_load_as_exactly_the_four_types() -> None:
    assert tuple(sorted(_schema_set().schemas)) == _TYPES


def test_the_base_is_a_ref_target_not_a_type() -> None:
    schema_set = _schema_set()
    assert "_base-diataxis" not in schema_set.schemas
    assert "_base-diataxis.schema.json" in schema_set.documents


def test_each_wrapper_pins_the_const_to_its_own_stem() -> None:
    schema_set = _schema_set()
    for type_name in _TYPES:
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


def _findings(tmp_path: Path, frontmatter: str, code: str = "schemas.invalid") -> list[str]:
    root = tmp_path / "bundle"
    (root / "tutorials").mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    (root / "tutorials" / "page.md").write_text(f"---\n{frontmatter}---\n\n## Steps\n", encoding="utf-8")
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
