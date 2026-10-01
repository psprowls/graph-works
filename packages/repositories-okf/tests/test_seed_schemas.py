from __future__ import annotations

import importlib.resources
from datetime import date
from pathlib import Path

import pytest
from okf_ext.schemas import SchemaSet, load_schemas, schema_rule
from okf_io import load_bundle
from okf_io import validate as okf_validate
from repositories_okf.lane import TYPES

_HEX = "0123456789abcdef0123456789abcdef01234567"


def _schema_set() -> SchemaSet:
    return load_schemas(str(importlib.resources.files("repositories_okf") / "assets" / "schema"))


def test_the_schemas_load_as_exactly_the_four_types() -> None:
    assert tuple(sorted(_schema_set().schemas)) == (
        "ManagedRepository",
        "ReferenceRepository",
        "RepositoryChangelog",
        "RepositorySnapshot",
    )


def test_the_base_is_a_ref_target_with_its_own_name() -> None:
    """`_base.schema.json` is work-tracker's; a second one would collide in a shared `schema/`."""
    schema_set = _schema_set()
    assert "_base-repository.schema.json" in schema_set.documents
    assert "_base.schema.json" not in schema_set.documents


def test_each_wrapper_pins_its_type_and_declares_the_lane_directory() -> None:
    schema_set = _schema_set()
    for type_name in TYPES:
        schema = schema_set.schemas[type_name]
        assert schema["$ref"] == "_base-repository.schema.json"
        assert schema["properties"]["type"] == {"const": type_name}
        assert schema["unevaluatedProperties"] is False
        assert schema["x-okf-directory"] == "repositories/"


def _messages(tmp_path: Path, frontmatter: str) -> list[str]:
    root = tmp_path / "bundle"
    (root / "repositories").mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8", newline="")
    page = root / "repositories" / "demo.md"
    page.write_text(f"---\n{frontmatter}---\n\n## Summary\n\nx\n", encoding="utf-8", newline="")
    report = okf_validate(load_bundle(root), today=date(2026, 9, 29), extra_rules=[schema_rule(_schema_set())])
    return [finding.message for finding in report.by_code("schemas.invalid")]


def _page(type_name: str = "ReferenceRepository", extra: str = "") -> str:
    return (
        f"type: {type_name}\n"
        "title: Demo\n"
        "description: The demo upstream.\n"
        "url: https://github.com/acme/demo.git\n"
        f"{extra}"
    )


_FULL_PIN = (
    "pin:\n"
    f"  commit: {_HEX}\n"
    "  fetched_at: '2026-09-29T12:00:00Z'\n"
    "  ref: v1.2.0\n"
    "  describe: v1.2.0-3-g0123456\n"
    f"  tree: {_HEX}\n"
    "  commit_date: '2026-09-28T08:00:00Z'\n"
    f"  previous: {_HEX}\n"
    "  generation:\n"
    "    gw_version: 0.6.5\n"
    "    scan_config_hash: abc123\n"
)


@pytest.mark.parametrize("type_name", ["ManagedRepository", "ReferenceRepository"])
def test_a_minimal_page_of_each_type_is_valid(tmp_path: Path, type_name: str) -> None:
    assert _messages(tmp_path, _page(type_name)) == []


def test_a_fully_populated_page_is_valid(tmp_path: Path) -> None:
    extra = (
        "track: main\n"
        "status: stable\n"
        "updated: '2026-09-29'\n"
        "tags: [upstream]\n"
        "sources:\n  - id: readme\n    resource: /repositories/demo.md\n"
        "generated:\n  by: gw\n  at: '2026-09-29T12:00:00Z'\n" + _FULL_PIN
    )
    assert _messages(tmp_path, _page(extra=extra)) == []


def test_a_missing_url_fails(tmp_path: Path) -> None:
    text = _page().replace("url: https://github.com/acme/demo.git\n", "")
    assert any("'url' is a required property" in m for m in _messages(tmp_path, text))


def test_a_missing_description_fails(tmp_path: Path) -> None:
    text = _page().replace("description: The demo upstream.\n", "")
    assert any("'description' is a required property" in m for m in _messages(tmp_path, text))


def test_a_stray_key_in_pin_fails(tmp_path: Path) -> None:
    extra = f"pin:\n  commit: {_HEX}\n  fetched_at: '2026-09-29T12:00:00Z'\n  branch: main\n"
    assert _messages(tmp_path, _page(extra=extra))


def test_a_non_hex_pin_commit_fails(tmp_path: Path) -> None:
    extra = "pin:\n  commit: not-a-sha\n  fetched_at: '2026-09-29T12:00:00Z'\n"
    assert _messages(tmp_path, _page(extra=extra))


def test_a_pin_without_fetched_at_fails(tmp_path: Path) -> None:
    extra = f"pin:\n  commit: {_HEX}\n"
    assert any("'fetched_at' is a required property" in m for m in _messages(tmp_path, _page(extra=extra)))


def test_an_unknown_top_level_key_fails(tmp_path: Path) -> None:
    assert _messages(tmp_path, _page(extra="branch: main\n"))


def test_a_stray_key_in_generation_fails(tmp_path: Path) -> None:
    extra = f"pin:\n  commit: {_HEX}\n  fetched_at: '2026-09-29T12:00:00Z'\n  generation:\n    extra: x\n"
    assert _messages(tmp_path, _page(extra=extra))


def test_an_empty_track_fails(tmp_path: Path) -> None:
    assert _messages(tmp_path, _page(extra="track: ''\n"))
