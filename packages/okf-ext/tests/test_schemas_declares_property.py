"""`declares_property`: does a type's schema, through `$ref` and the combinators, declare a property?"""

from __future__ import annotations

import json
from pathlib import Path

from okf_ext.schemas import declares_property, load_schemas


def _write(root: Path, name: str, schema: dict[str, object]) -> None:
    (root / name).write_text(json.dumps(schema), encoding="utf-8", newline="")


def test_declares_property_follows_ref_into_the_base(tmp_path: Path) -> None:
    _write(tmp_path, "_base.schema.json", {"properties": {"updated": {"type": "string"}}})
    _write(
        tmp_path,
        "Explanation.schema.json",
        {"$ref": "_base.schema.json", "properties": {"type": {"const": "Explanation"}}},
    )
    _write(tmp_path, "Package.schema.json", {"properties": {"type": {"const": "Package"}}})
    schemas = load_schemas(tmp_path)
    assert declares_property(schemas, "Explanation", "updated") is True
    assert declares_property(schemas, "Explanation", "type") is True
    assert declares_property(schemas, "Package", "updated") is False
    assert declares_property(schemas, "Missing", "updated") is False


def test_declares_property_follows_fragments_and_combinators(tmp_path: Path) -> None:
    _write(tmp_path, "_base.schema.json", {"$defs": {"dated": {"properties": {"updated": {}}}}})
    _write(
        tmp_path,
        "Note.schema.json",
        {"allOf": [{"properties": {"type": {}}}, {"$ref": "_base.schema.json#/$defs/dated"}]},
    )
    _write(
        tmp_path,
        "Local.schema.json",
        {"$defs": {"extra": {"properties": {"owner": {}}}}, "anyOf": [{"$ref": "#/$defs/extra"}]},
    )
    _write(tmp_path, "Choice.schema.json", {"oneOf": [{"properties": {"a": {}}}, {"properties": {"b": {}}}]})
    schemas = load_schemas(tmp_path)
    assert declares_property(schemas, "Note", "updated") is True
    assert declares_property(schemas, "Local", "owner") is True
    assert declares_property(schemas, "Choice", "b") is True
    assert declares_property(schemas, "Choice", "c") is False


def test_declares_property_tolerates_dangling_and_cyclic_refs(tmp_path: Path) -> None:
    _write(tmp_path, "Gone.schema.json", {"$ref": "_missing.schema.json", "properties": {"type": {}}})
    _write(tmp_path, "Loop.schema.json", {"$ref": "#", "allOf": [{"$ref": "#/$defs/nope"}]})
    schemas = load_schemas(tmp_path)
    assert declares_property(schemas, "Gone", "updated") is False
    assert declares_property(schemas, "Loop", "updated") is False
