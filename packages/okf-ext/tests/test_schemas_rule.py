from __future__ import annotations

from datetime import date

import pytest
from ext_helpers import write
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
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    write(schema_dir / "_base.schema.yaml", BASE)
    write(schema_dir / "Metric.schema.yaml", METRIC)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    for name, text in concepts.items():
        write(bundle_dir / f"{name}.md", text)
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
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    write(schema_dir / "metric.schema.yaml", "type: object\nproperties:\n  updated: {type: string}\n")
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    write(bundle_dir / "d.md", "---\ntype: metric\ntitle: T\nupdated: 2026-08-04\n---\n\n# T\n")
    schema_set = load_schemas(schema_dir)
    report = validate(load_bundle(bundle_dir), today=TODAY, extra_rules=[schema_rule(schema_set)])
    assert [f for f in report.findings if f.code.startswith("schemas.")] == []


def test_additional_properties_false_sees_unknown_keys(tmp_path):
    """`fm_data()` is a plain projection of `fm_raw`, which holds every key the
    document carries. The typed `Frontmatter` view is not involved."""
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    write(
        schema_dir / "metric.schema.yaml",
        "type: object\nproperties:\n  type: {type: string}\nadditionalProperties: false\n",
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    write(bundle_dir / "d.md", "---\ntype: metric\nhouse_key: yes\n---\n\n# T\n")
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


# --- x-okf-member: schemas.unresolved-member --------------------------------

CITED = """
type: object
required: [type, title]
properties:
  type: {const: Cited}
  title: {type: string}
  source_path: {type: string, minLength: 1, x-okf-member: true}
  origin: {type: string}
"""


def cited(source_path: str) -> str:
    return f"---\ntype: Cited\ntitle: C\nsource_path: {source_path}\n---\n\n# C\n"


def run_cited(tmp_path, files, *, severity="warn", ignore=(), scope=None):
    """A `Cited` schema beside a bundle of *files* (bundle-relative path -> text)."""
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    write(schema_dir / "Cited.schema.yaml", CITED)
    bundle_dir = tmp_path / "bundle"
    for relative, text in files.items():
        target = bundle_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        write(target, text)
    report = validate(
        load_bundle(bundle_dir, ignore=ignore),
        today=TODAY,
        extra_rules=[schema_rule(load_schemas(schema_dir), severity=severity)],
        scope=scope,
    )
    return [f for f in report.findings if f.code == "schemas.unresolved-member"]


def test_a_dangling_member_path_is_reported_with_path_line_severity_and_spec(tmp_path):
    found = run_cited(tmp_path, {"page.md": cited("refs/gone.md")})
    assert len(found) == 1
    finding = found[0]
    assert finding.path == "page.md"
    assert finding.line == 4
    assert finding.severity == "warn"
    assert finding.spec == "Cited.schema.yaml"
    assert finding.message == (
        "`source_path` `refs/gone.md` names no member of the bundle (declared `x-okf-member` in `Cited.schema.yaml`)."
    )


@pytest.mark.parametrize(
    "files",
    [
        {"page.md": cited("other.md"), "other.md": "---\ntitle: O\n---\n\n# O\n"},  # a concept
        {"page.md": cited("refs/data.csv"), "refs/data.csv": "a,b\n"},  # an asset
        {"page.md": cited("/refs/data.csv"), "refs/data.csv": "a,b\n"},  # root-absolute spelling
    ],
)
def test_a_resolving_member_path_is_quiet(tmp_path, files):
    assert run_cited(tmp_path, files) == []


def test_an_ignored_member_still_resolves(tmp_path):
    """`has_member` counts ignored members: a wiki page pointing into `work/`,
    which the wiki lane ignores, has a working `source_path`."""
    files = {"page.md": cited("work/item/references/01-design.md"), "work/item/references/01-design.md": "# D\n"}
    assert run_cited(tmp_path, files, ignore=("work/*",)) == []


@pytest.mark.parametrize(
    "value",
    [
        "/Users/pat/okf/sources/references/x.md",  # absolute filesystem path
        "https://example.com/x.md",  # a URL
        "../outside.md",  # never resolved
        "//double.md",  # only one leading slash is stripped
    ],
)
def test_shapes_that_are_never_members_are_reported(tmp_path, value):
    files = {"page.md": cited(value), "outside.md": "# O\n", "double.md": "# D\n", "x.md": "# X\n"}
    assert len(run_cited(tmp_path, files)) == 1


@pytest.mark.parametrize("line", ["source_path: 7\n", "source_path: ['a.md']\n", ""])
def test_a_non_string_or_absent_value_is_left_to_schemas_invalid(tmp_path, line):
    text = f"---\ntype: Cited\ntitle: C\n{line}---\n\n# C\n"
    assert run_cited(tmp_path, {"page.md": text}) == []


@pytest.mark.parametrize(
    "line",
    ["source_path: ''\n", "source_path: ' '\n", "source_path: /\n", "source_path: '/ '\n"],
)
def test_a_blank_string_is_still_an_unresolved_member(tmp_path, line):
    text = f"---\ntype: Cited\ntitle: C\n{line}---\n\n# C\n"
    assert len(run_cited(tmp_path, {"page.md": text})) == 1


def test_an_unannotated_property_is_never_checked(tmp_path):
    text = "---\ntype: Cited\ntitle: C\norigin: nowhere/at/all.md\n---\n\n# C\n"
    assert run_cited(tmp_path, {"page.md": text}) == []


def test_scope_narrows_the_member_check(tmp_path):
    files = {"a.md": cited("gone.md"), "b.md": cited("gone.md")}
    found = run_cited(tmp_path, files, scope=frozenset({"b.md"}))
    assert [f.path for f in found] == ["b.md"]


def test_the_severity_knob_applies_to_the_member_check(tmp_path):
    found = run_cited(tmp_path, {"page.md": cited("gone.md")}, severity="error")
    assert [f.severity for f in found] == ["error"]


def test_an_undeclared_type_is_never_member_checked(tmp_path):
    """No schema means `no-schema-for-type`, and nothing else."""
    text = "---\ntype: Unknown\ntitle: U\nsource_path: gone.md\n---\n\n# U\n"
    assert run_cited(tmp_path, {"page.md": text}) == []


# --- The topic contract -----------------------------------------------------


def test_every_code_starts_with_the_topic_prefix():
    assert CODES
    assert all(code.startswith(f"{TOPIC}.") for code in CODES)


def test_every_emitted_code_is_a_member_of_codes(tmp_path):
    bundle, schema_set = build(
        tmp_path,
        orphan="---\ntype: Metric\ntitle: O\n---\n\n# O\n",
        term="---\ntype: Glossary\ntitle: C\n---\n\n# C\n",
        page=cited("gone.md"),
    )
    write(schema_set.root / "Cited.schema.yaml", CITED)
    report = validate(bundle, today=TODAY, extra_rules=[schema_rule(load_schemas(schema_set.root))])
    emitted = {f.code for f in report.findings if f.code.startswith(f"{TOPIC}.")}
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
