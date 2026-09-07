from __future__ import annotations

from datetime import date
from pathlib import Path

from helpers import BUNDLES, write_tree
from okf_io import bundle
from okf_io.validate import validate

TODAY = date(2026, 8, 3)


def report_for(tmp_path: Path, files: dict[str, str]):
    return validate(bundle.load(write_tree(tmp_path, files)), today=TODAY)


def concept(frontmatter: str, body: str = "# Definition\n") -> str:
    return f"---\ntype: Metric\ntitle: T\ndescription: D\n{frontmatter}---\n\n{body}"


def test_an_entry_with_no_resource_is_an_error(tmp_path):
    fm = "sources:\n  - id: a\n  - id: b\n    resource: /p.md\n"
    report = report_for(tmp_path, {"a.md": concept(fm, "Text. [^a] [^b]\n")})
    findings = report.by_code("provenance.source-resource-missing")
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "sources[0]" in findings[0].message


def test_usage_count_needs_a_window(tmp_path):
    fm = "sources:\n  - id: a\n    resource: /p.md\n    usage_count: 5000\n"
    report = report_for(tmp_path, {"a.md": concept(fm, "Text. [^a]\n")})
    assert report.by_code("provenance.usage-count-unframed")[0].severity == "warn"


def test_either_window_frames_it(tmp_path):
    sibling = (
        "usage_window: { from: 2026-06-01, to: 2026-06-30 }\n"
        "sources:\n  - id: a\n    resource: /p.md\n    usage_count: 5000\n"
    )
    entry = (
        "sources:\n  - id: a\n    resource: /p.md\n    usage_count: 5000\n"
        "    usage_window: { from: 2026-06-01, to: 2026-06-30 }\n"
    )
    for fm in (sibling, entry):
        report = report_for(tmp_path, {"a.md": concept(fm, "Text. [^a]\n")})
        assert report.by_code("provenance.usage-count-unframed") == ()


def test_the_join_fails_in_both_directions(tmp_path):
    fm = "sources:\n  - id: lonely\n    resource: /p.md\n"
    report = report_for(tmp_path, {"a.md": concept(fm, "Text. [^ghost]\n")})
    unjoined = report.by_code("provenance.footnote-unjoined")
    uncited = report.by_code("provenance.source-uncited")
    assert "ghost" in unjoined[0].message
    assert "lonely" in uncited[0].message
    assert {unjoined[0].severity, uncited[0].severity} == {"warn"}


def test_a_definition_only_footnote_counts_as_a_citation(tmp_path):
    """Regression: acme_retail/metrics/gross-margin.md defines `[^revenue-policy]`
    without referencing it while legitimately carrying that source."""
    fm = "sources:\n  - id: policy\n    resource: /p.md\n"
    body = "# Definition\n\nText.\n\n[^policy]: Revenue Recognition Policy\n"
    report = report_for(tmp_path, {"a.md": concept(fm, body)})
    assert report.by_code("provenance.source-uncited") == ()
    assert report.by_code("provenance.footnote-unjoined") == ()


def test_the_vendored_regression_is_silent():
    graph = validate(bundle.load(BUNDLES / "acme_retail"), today=TODAY)
    uncited = graph.by_code("provenance.source-uncited")
    assert not [f for f in uncited if f.path == "metrics/gross-margin.md"]


def test_fallback_sources_carry_no_id_so_the_join_stays_quiet(tmp_path):
    body = "# Definition\n\nText.\n\n# Citations\n\n- [Policy](/p.md)\n"
    report = report_for(tmp_path, {"a.md": concept("", body), "p.md": concept("")})
    assert report.by_code("provenance.source-uncited") == ()
    assert report.by_code("provenance.source-resource-missing") == ()
