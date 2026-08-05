from __future__ import annotations

from datetime import date
from pathlib import Path

from okf_io import bundle
from okf_io.validate import validate

TODAY = date(2026, 8, 3)


def report_for(tmp_path: Path, files: dict[str, str]):
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return validate(bundle.load(tmp_path), today=TODAY)


def concept(frontmatter: str, body: str) -> str:
    return f"---\ntype: Metric\ntitle: T\ndescription: D\n{frontmatter}---\n\n{body}"


def test_a_v01_timestamp_is_a_migration_hint(tmp_path):
    report = report_for(tmp_path, {"a.md": concept("timestamp: 2024-05-01\n", "# D\n")})
    finding = report.by_code("legacy.timestamp")[0]
    assert finding.severity == "warn"
    assert "generated" in finding.message
    assert report.ok is True


def test_it_fires_even_alongside_generated(tmp_path):
    frontmatter = "timestamp: 2024-05-01\ngenerated: { by: human:a@b, at: 2026-06-30T14:00:00Z }\n"
    assert report_for(tmp_path, {"a.md": concept(frontmatter, "# D\n")}).by_code("legacy.timestamp") != ()


def test_a_body_citations_list_is_a_migration_hint(tmp_path):
    body = "# Definition\n\nText.\n\n# Citations\n\n- [Policy](/p.md)\n"
    files = {"a.md": concept("", body), "p.md": concept("", "# P\n")}
    report = report_for(tmp_path, files)
    assert report.by_code("legacy.body-citations")[0].severity == "warn"
    assert report.ok is True


def test_real_sources_beside_leftover_prose_fire_nothing(tmp_path):
    """The fallback did not fire, so there is nothing to migrate."""
    frontmatter = "sources:\n  - id: real\n    resource: /p.md\n"
    body = "# Definition\n\nText. [^real]\n\n# Citations\n\n- [Old](/p.md)\n"
    files = {"a.md": concept(frontmatter, body), "p.md": concept("", "# P\n")}
    report = report_for(tmp_path, files)
    assert report.by_code("legacy.body-citations") == ()
    assert report.by_code("legacy.timestamp") == ()


def test_a_clean_v02_concept_fires_neither(tmp_path):
    report = report_for(tmp_path, {"a.md": concept("status: stable\n", "# D\n")})
    assert not [f for f in report.findings if f.code.startswith("legacy.")]


def test_null_timestamp_is_not_a_hint(tmp_path):
    """A null timestamp carries no data to migrate."""
    report = report_for(tmp_path, {"a.md": concept("timestamp:\n", "# D\n")})
    assert report.by_code("legacy.timestamp") == ()
    assert report.ok is True


def test_empty_string_timestamp_is_not_a_hint(tmp_path):
    """An empty string timestamp carries no data to migrate."""
    report = report_for(tmp_path, {"a.md": concept('timestamp: ""\n', "# D\n")})
    assert report.by_code("legacy.timestamp") == ()
    assert report.ok is True


def test_whitespace_only_timestamp_is_not_a_hint(tmp_path):
    """A whitespace-only timestamp carries no data to migrate."""
    report = report_for(tmp_path, {"a.md": concept('timestamp: "   "\n', "# D\n")})
    assert report.by_code("legacy.timestamp") == ()
    assert report.ok is True


def test_date_timestamp_is_a_hint(tmp_path):
    """A YAML date timestamp should fire the migration hint."""
    report = report_for(tmp_path, {"a.md": concept("timestamp: 2024-05-01\n", "# D\n")})
    finding = report.by_code("legacy.timestamp")[0]
    assert finding.severity == "warn"
    assert report.ok is True
