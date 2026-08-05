from __future__ import annotations

from datetime import date

import pytest
from okf_ext.schemas import CODES, TOPIC, load_schemas, schema_rule
from okf_io import Finding, load_bundle, validate

TODAY = date(2026, 8, 4)

BASE = """
$defs:
  actor:
    type: object
    required: [name]
    properties:
      name: {type: string}
    additionalProperties: false
"""

METRIC = """
type: object
required: [type, title, owner]
properties:
  type: {const: Metric}
  title: {type: string}
  description: {type: string}
  owner: {$ref: "_base.schema.yaml#/$defs/actor"}
  tags:
    type: array
    items: {type: string}
"""

VALID = "---\ntype: Metric\ntitle: Revenue\nowner:\n  name: finance\n---\n\n# Revenue\n"


def build(tmp_path, **concepts):
    """A bundle plus a schema set beside it, never inside it."""
    schema_dir = tmp_path / "_schema"
    schema_dir.mkdir()
    (schema_dir / "_base.schema.yaml").write_text(BASE, encoding="utf-8")
    (schema_dir / "Metric.schema.yaml").write_text(METRIC, encoding="utf-8")
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    for name, text in concepts.items():
        (bundle_dir / f"{name}.md").write_text(text, encoding="utf-8")
    return load_bundle(bundle_dir), load_schemas(schema_dir)


def run(tmp_path, *, severity="warn", strict=False, **concepts):
    bundle, schema_set = build(tmp_path, **concepts)
    report = validate(bundle, today=TODAY, extra_rules=[schema_rule(schema_set, severity=severity)], strict=strict)
    return [f for f in report.findings if f.code.startswith(f"{TOPIC}.")]


# --- The four per-concept branches ------------------------------------------


def test_a_conforming_concept_yields_nothing(tmp_path):
    assert run(tmp_path, revenue=VALID) == []


def test_a_parse_error_is_skipped_not_re_reported(tmp_path):
    """okf-io already emitted `frontmatter.unparseable`; saying so twice in two
    vocabularies helps nobody."""
    assert run(tmp_path, broken="---\ntype: [unclosed\n---\n\n# B\n") == []


@pytest.mark.parametrize(
    "text",
    [
        "---\ntitle: No type\n---\n\n# T\n",
        "---\ntype: '   '\ntitle: Blank\n---\n\n# T\n",
    ],
)
def test_a_missing_or_blank_type_is_skipped(tmp_path, text):
    assert run(tmp_path, untyped=text) == []


def test_an_unknown_type_yields_one_no_schema_finding(tmp_path):
    found = run(tmp_path, term="---\ntype: Glossary\ntitle: Churn\n---\n\n# Churn\n")
    assert len(found) == 1
    assert found[0].code == "schemas.no-schema-for-type"
    assert "Glossary" in found[0].message
    assert found[0].path == "term.md"
    assert found[0].line == 2  # the `type:` key


# --- One finding per error, anchored --------------------------------------


def test_one_finding_per_jsonschema_error(tmp_path):
    text = "---\ntype: Metric\ntitle: Orders\nowner:\n  name: 42\ntags: [ops, 7]\n---\n\n# Orders\n"
    found = run(tmp_path, orders=text)
    assert {f.code for f in found} == {"schemas.invalid"}
    assert len(found) == 2


def test_a_nested_key_anchors_to_its_own_line(tmp_path):
    text = "---\ntype: Metric\ntitle: Orders\nowner:\n  name: 42\n---\n\n# Orders\n"
    found = run(tmp_path, orders=text)
    assert len(found) == 1
    assert found[0].line == 5
    assert "at `owner.name`" in found[0].message


def test_a_sequence_item_anchors_to_its_own_line(tmp_path):
    text = "---\ntype: Metric\ntitle: T\nowner:\n  name: f\ntags:\n  - ok\n  - 7\n---\n\n# T\n"
    found = run(tmp_path, t=text)
    assert len(found) == 1
    assert found[0].line == 8
    assert "at `tags.1`" in found[0].message


def test_a_root_error_carries_no_line_and_no_at_clause(tmp_path):
    """A `required` error's `absolute_path` is empty. A wrong line is worse than
    no line, and an invented key name is worse than none."""
    found = run(tmp_path, orphan="---\ntype: Metric\ntitle: Orphan\n---\n\n# Orphan\n")
    assert len(found) == 1
    assert found[0].line is None
    assert " at `" not in found[0].message
    assert found[0].message.endswith("'owner' is a required property.")


def test_findings_cite_the_schema_file_that_says_so(tmp_path):
    found = run(tmp_path, orphan="---\ntype: Metric\ntitle: Orphan\n---\n\n# Orphan\n")
    assert {f.spec for f in found} == {"Metric.schema.yaml"}


def test_dates_reach_the_schema_as_iso_strings(tmp_path):
    """`fm_data(dates="iso")` renders `date` objects as strings. Validating
    `fm_raw` instead would hand jsonschema a `datetime.date`, where both
    `format: date` and `type: string` misfire."""
    schema_dir = tmp_path / "_schema"
    schema_dir.mkdir()
    (schema_dir / "metric.schema.yaml").write_text(
        "type: object\nproperties:\n  updated: {type: string}\n", encoding="utf-8"
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "d.md").write_text("---\ntype: metric\ntitle: T\nupdated: 2026-08-04\n---\n\n# T\n", encoding="utf-8")
    schema_set = load_schemas(schema_dir)
    report = validate(load_bundle(bundle_dir), today=TODAY, extra_rules=[schema_rule(schema_set)])
    assert [f for f in report.findings if f.code.startswith("schemas.")] == []


def test_additional_properties_false_sees_unknown_keys(tmp_path):
    """`fm_data()` is a plain projection of `fm_raw`, which holds every key the
    document carries. The typed `Frontmatter` view is not involved."""
    schema_dir = tmp_path / "_schema"
    schema_dir.mkdir()
    (schema_dir / "metric.schema.yaml").write_text(
        "type: object\nproperties:\n  type: {type: string}\nadditionalProperties: false\n",
        encoding="utf-8",
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "d.md").write_text("---\ntype: metric\nhouse_key: yes\n---\n\n# T\n", encoding="utf-8")
    report = validate(load_bundle(bundle_dir), today=TODAY, extra_rules=[schema_rule(load_schemas(schema_dir))])
    found = [f for f in report.findings if f.code == "schemas.invalid"]
    assert found and "house_key" in found[0].message


# --- Severity ---------------------------------------------------------------


def test_invalid_defaults_to_warn(tmp_path):
    found = run(tmp_path, orphan="---\ntype: Metric\ntitle: O\n---\n\n# O\n")
    assert {f.severity for f in found} == {"warn"}


def test_invalid_honours_the_severity_argument(tmp_path):
    found = run(tmp_path, severity="error", orphan="---\ntype: Metric\ntitle: O\n---\n\n# O\n")
    assert {f.severity for f in found} == {"error"}


def test_no_schema_for_type_stays_warn_under_severity_error(tmp_path):
    """A coverage gap in the schema set is not a violation by the document."""
    found = run(tmp_path, severity="error", term="---\ntype: Glossary\ntitle: C\n---\n\n# C\n")
    assert {f.severity for f in found} == {"warn"}


def test_the_default_never_changes_conformance(tmp_path):
    bundle, schema_set = build(tmp_path, orphan="---\ntype: Metric\ntitle: O\n---\n\n# O\n")
    without = validate(bundle, today=TODAY)
    with_rule = validate(bundle, today=TODAY, extra_rules=[schema_rule(schema_set)])
    assert with_rule.ok == without.ok is True


def test_strict_promotes_the_house_rule_with_no_second_namespace(tmp_path):
    found = run(tmp_path, strict=True, orphan="---\ntype: Metric\ntitle: O\n---\n\n# O\n")
    assert {f.severity for f in found} == {"error"}


# --- The topic contract -----------------------------------------------------


def test_every_code_starts_with_the_topic_prefix():
    assert CODES
    assert all(code.startswith(f"{TOPIC}.") for code in CODES)


def test_every_emitted_code_is_a_member_of_codes(tmp_path):
    emitted = {
        f.code
        for f in run(
            tmp_path,
            orphan="---\ntype: Metric\ntitle: O\n---\n\n# O\n",
            term="---\ntype: Glossary\ntitle: C\n---\n\n# C\n",
        )
    }
    assert emitted == set(CODES)


def test_findings_are_shape_identical_to_built_ins(tmp_path):
    """Extension point #1's actual contract, and the reason it was built before
    a consumer existed."""
    bundle, schema_set = build(tmp_path, orphan="---\ntype: Metric\ntitle: O\n---\n\n# O\n")
    report = validate(bundle, today=TODAY, extra_rules=[schema_rule(schema_set)])
    house = [f for f in report.findings if f.code.startswith("schemas.")]
    assert house
    assert all(isinstance(f, Finding) for f in house)
    assert all(f.path is not None and "." in f.code for f in house)
    assert report.findings == tuple(
        sorted(report.findings, key=lambda f: (f.path or "", f.line or 0, f.code, f.message))
    )
