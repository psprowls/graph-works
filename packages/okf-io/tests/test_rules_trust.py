from __future__ import annotations

from datetime import date
from pathlib import Path

from okf_io import bundle
from okf_io.validate import validate

TODAY = date(2026, 8, 3)


def report_for(tmp_path: Path, frontmatter: str, body: str = "# Definition\n"):
    target = tmp_path / "a.md"
    target.write_text(
        f"---\ntype: Metric\ntitle: T\ndescription: D\n{frontmatter}---\n\n{body}",
        encoding="utf-8",
    )
    return validate(bundle.load(tmp_path), today=TODAY)


def test_generated_without_by_is_an_error(tmp_path):
    report = report_for(tmp_path, "generated: { at: 2026-06-30T14:00:00Z }\n")
    assert report.by_code("trust.generated-by-missing")[0].severity == "error"


def test_a_v01_timestamp_is_not_a_trust_error(tmp_path):
    """The v0.1 read fallback synthesises `generated` with no `by`. Reading the
    view here would make every unmigrated v0.1 concept fail conformance."""
    report = report_for(tmp_path, "timestamp: 2024-05-01T00:00:00Z\n")
    assert report.by_code("trust.generated-by-missing") == ()
    assert report.by_code("legacy.timestamp") != ()


def test_unparseable_timestamps_name_their_field(tmp_path):
    frontmatter = "generated: { by: human:a@b, at: yesterday }\nverified:\n  - { by: human:a@b, at: soon }\n"
    report = report_for(tmp_path, frontmatter)
    messages = [f.message for f in report.by_code("trust.timestamp-not-iso")]
    assert any("generated.at" in m for m in messages)
    assert any("verified[0].at" in m for m in messages)


def test_an_incomplete_verified_entry_names_what_is_missing(tmp_path):
    report = report_for(tmp_path, "verified:\n  - { by: human:a@b }\n")
    finding = report.by_code("trust.verified-entry-incomplete")[0]
    assert finding.severity == "warn"
    assert "`at`" in finding.message


def test_the_actor_convention_covers_all_three_fields(tmp_path):
    frontmatter = (
        "generated: { by: team:data, at: 2026-06-30T14:00:00Z }\n"
        "verified:\n  - { by: squad:x, at: 2026-07-01T09:00:00Z }\n"
        "sources:\n  - id: s\n    resource: /p.md\n    author: dept:finance\n"
    )
    report = report_for(tmp_path, frontmatter, "Text. [^s]\n")
    messages = [f.message for f in report.by_code("trust.actor-convention")]
    assert len(messages) == 3
    assert any("generated.by" in m for m in messages)
    assert any("verified[0].by" in m for m in messages)
    assert any("sources[0].author" in m for m in messages)


def test_the_three_spec_forms_are_silent(tmp_path):
    frontmatter = (
        "generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-30T14:00:00Z }\n"
        "verified:\n"
        "  - { by: human:jsmith@acme, at: 2026-07-01T09:00:00Z }\n"
        "  - { by: process:finance-nightly, at: 2026-07-02T09:00:00Z }\n"
    )
    report = report_for(tmp_path, frontmatter)
    assert not [f for f in report.findings if f.code.startswith("trust.")]


def test_acme_retail_bundle_trust_convention_regression():
    """Pins the current trust.actor-convention findings in the vendored acme_retail bundle."""
    from helpers import BUNDLES

    acme = bundle.load(BUNDLES / "acme_retail")
    report = validate(acme, today=TODAY)

    findings = report.by_code("trust.actor-convention")
    assert len(findings) == 2

    # Find the findings by path
    revenue_ytd = [f for f in findings if f.path == "computations/revenue-ytd.md"]
    orders = [f for f in findings if f.path == "tables/orders.md"]

    assert len(revenue_ytd) == 1
    assert len(orders) == 1

    # Check the revenue-ytd finding
    assert revenue_ytd[0].severity == "warn"
    assert "sources[1].author" in revenue_ytd[0].message
    assert "team:data-platform" in revenue_ytd[0].message

    # Check the orders finding
    assert orders[0].severity == "warn"
    assert "sources[0].author" in orders[0].message
    assert "team:data-platform" in orders[0].message

    # Ensure report.ok stays True for warnings
    assert report.ok
