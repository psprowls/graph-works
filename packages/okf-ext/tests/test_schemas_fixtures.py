from __future__ import annotations

from datetime import date

from ext_helpers import SCHEMA_DIR, SCHEMA_EXPECTED, SCHEMAD, schemad_bundle
from okf_ext.schemas import DEFAULT_IGNORE, load_schemas, schema_rule
from okf_io import load_bundle, validate

TODAY = date(2026, 8, 4)


def findings(*, severity="warn"):
    schema_set = load_schemas(SCHEMA_DIR)
    report = validate(
        schemad_bundle(ignore=DEFAULT_IGNORE),
        today=TODAY,
        extra_rules=[schema_rule(schema_set, severity=severity)],
    )
    return [f for f in report.findings if f.code.startswith("schemas.")]


def test_default_ignore_makes_the_schema_dir_not_a_concept():
    """Extension point #3: an ignored member is "not a concept", not "not
    there". Without `ignore=`, the schema files are ordinary assets."""
    ignored = schemad_bundle(ignore=DEFAULT_IGNORE)
    plain = schemad_bundle()
    names = {
        "schema/_base.schema.yaml",
        "schema/Metric.schema.yaml",
        "schema/Reference.schema.json",
    }
    assert names <= set(ignored.ignored)
    assert not (names & set(ignored.assets))
    assert names <= set(plain.assets)


def test_an_ignored_member_is_still_a_link_target():
    ignored = schemad_bundle(ignore=DEFAULT_IGNORE)
    assert ignored.has_member("schema/Metric.schema.yaml")


def test_the_walk_reports_exactly_the_expected_pairs():
    assert {(f.code, f.path) for f in findings()} == SCHEMA_EXPECTED


def test_the_schema_set_reads_the_fixture_directory():
    schema_set = load_schemas(SCHEMA_DIR)
    assert schema_set.types == ("Metric", "Reference")
    assert "_base.schema.yaml" in schema_set.documents


def test_orders_anchors_both_of_its_errors():
    found = sorted((f for f in findings() if f.path == "metrics/orders.md"), key=lambda f: f.line or 0)
    assert [f.line for f in found] == [6, 7]
    assert "at `owner.name`" in found[0].message
    assert "at `tags.1`" in found[1].message


def test_orphan_has_no_line():
    found = [f for f in findings() if f.path == "metrics/orphan.md"]
    assert len(found) == 1
    assert found[0].line is None


def test_conforming_concepts_and_the_broken_one_are_silent():
    reported = {f.path for f in findings()}
    for quiet in ("metrics/revenue.md", "refs/style-guide.md", "broken.md"):
        assert quiet not in reported


def test_severity_error_reaches_the_walk():
    by_code = {(f.code, f.severity) for f in findings(severity="error")}
    assert ("schemas.invalid", "error") in by_code
    assert ("schemas.no-schema-for-type", "warn") in by_code


def test_the_vendored_bundles_stay_clean():
    """`acme_retail` and `ga4` carry no `schema/`, so nothing here may change
    their zero-error result. Their own suite proves they are clean; this proves
    the schema rule adds nothing when pointed at them."""
    vendored = SCHEMAD.parents[3] / "okf-io" / "tests" / "fixtures" / "bundles"
    schema_set = load_schemas(SCHEMA_DIR)
    for name in ("acme_retail", "ga4"):
        report = validate(load_bundle(vendored / name), today=TODAY, extra_rules=[schema_rule(schema_set)])
        assert report.ok, f"{name} lost its zero-error result"
