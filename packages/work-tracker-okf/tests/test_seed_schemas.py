import importlib.resources
from datetime import date
from pathlib import Path

import pytest
from okf_ext.schemas import load_schemas, schema_rule
from okf_io import load_bundle
from okf_io import validate as okf_validate

_TYPES = ("Bug", "Epic", "Feature", "Spike", "TechDebt", "TestGap")


def _schema_set():
    assets = importlib.resources.files("work_tracker_okf") / "assets" / "schema"
    return load_schemas(str(assets))


def test_seed_schemas_load_as_exactly_the_six_types() -> None:
    schema_set = _schema_set()
    assert tuple(sorted(schema_set.schemas)) == _TYPES


def test_the_base_is_a_ref_target_not_a_type() -> None:
    """`_`-prefixed means "in `documents` so a `$ref` reaches it, out of
    `schemas` so no concept can match `type: _base`"."""
    schema_set = _schema_set()
    assert "_base" not in schema_set.schemas
    assert "_base.schema.json" in schema_set.documents


def test_each_wrapper_pins_the_const_to_its_own_stem() -> None:
    schema_set = _schema_set()
    for type_name in _TYPES:
        assert schema_set.schemas[type_name]["properties"]["type"] == {"const": type_name}
        assert schema_set.schemas[type_name]["$ref"] == "_base.schema.json"


def test_every_type_declares_the_work_directory() -> None:
    """Written on each wrapper, not only on the base: a `$ref` does not
    surface the base's keywords through `SchemaSet.schemas[type]`, and child 2
    reads this the way the entity lane does."""
    schema_set = _schema_set()
    for type_name in _TYPES:
        assert schema_set.schemas[type_name]["x-okf-directory"] == "work/"


def _findings(tmp_path: Path, frontmatter: str) -> list[str]:
    root = tmp_path / "bundle"
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    (root / "work" / "item.md").write_text(f"---\n{frontmatter}---\n\n## Plan\n", encoding="utf-8")
    bundle = load_bundle(root)
    report = okf_validate(bundle, today=date(2026, 2, 3), extra_rules=[schema_rule(_schema_set())])
    return [f.message for f in report.by_code("schemas.invalid")]


_COMPLETE = (
    "type: Feature\n"
    "title: A feature\n"
    "description: A feature.\n"
    "workflow_status: open\n"
    "opened: '2026-01-05'\n"
    "updated: '2026-02-02'\n"
)


def test_a_non_draft_item_must_carry_effort_and_affects(tmp_path: Path) -> None:
    """W-D: the strict branch is the default, and an absent `status` lands in
    it -- which is what the `required: [status]` inside the `if` buys."""
    messages = _findings(tmp_path, _COMPLETE)
    assert any("'effort' is a required property" in m for m in messages)
    assert any("'affects' is a required property" in m for m in messages)


def test_a_draft_item_is_exempt_from_effort_and_affects(tmp_path: Path) -> None:
    assert _findings(tmp_path, _COMPLETE + "status: draft\n") == []


def test_base_schema_accepts_structured_edges_and_filing_metadata(tmp_path: Path) -> None:
    messages = _findings(
        tmp_path,
        _COMPLETE
        + "status: draft\nblast_radius: package\ntarget: 2026-Q4\n"
        + "depends_on:\n  - slug: a\n    blocks: plan\n    needs: design\n",
    )
    assert messages == []


@pytest.mark.parametrize("target", ["Q4-2026", "2026-Q5", "2026-13", "2026"])
def test_base_schema_rejects_bad_targets(tmp_path: Path, target: str) -> None:
    assert _findings(tmp_path, _COMPLETE + f"status: draft\ntarget: {target}\n")


def test_the_base_enums_reach_the_wrapper_through_the_ref(tmp_path: Path) -> None:
    messages = _findings(tmp_path, _COMPLETE.replace("workflow_status: open", "workflow_status: nope"))
    assert any("'nope' is not one of" in m for m in messages)


def test_a_mistyped_source_id_is_refused_and_a_suffixed_one_is_not(tmp_path: Path) -> None:
    good = _COMPLETE + (
        "status: draft\n"
        "sources:\n"
        "  - id: transcript-plan-subagent-1\n"
        "    resource: /work/item/references/03-plan-transcript.txt\n"
    )
    assert _findings(tmp_path, good) == []

    bad = good.replace("transcript-plan-subagent-1", "design_spec")
    assert any("does not match" in m for m in _findings(tmp_path, bad))
