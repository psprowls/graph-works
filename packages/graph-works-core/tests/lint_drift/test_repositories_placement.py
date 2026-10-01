"""The wiki lane places the repositories lane's two types directly under `repositories/`."""

from __future__ import annotations

from datetime import date

import pytest
from code_wiki_okf.config import load_config
from graph_works_core import apply_init, plan_init
from graph_works_core.lint_drift.lint import run_mechanical

TODAY = date(2026, 9, 29)

_PAGE = (
    "---\ntype: ReferenceRepository\ntitle: X\ndescription: X upstream.\n"
    "url: https://example.com/x.git\n---\n\n## Summary\n\nx\n"
)


@pytest.fixture
def initialized(tmp_path):
    return apply_init(plan_init(tmp_path / "works", today=TODAY))


def _write(workspace, relative, text=_PAGE):
    path = workspace.layout.bundle_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _lint(workspace):
    layout = workspace.layout
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    report = run_mechanical(layout, config, today=TODAY, repo_root=layout.repo_root)
    return [f for lane in report.mechanical for f in lane.report.findings]


def test_a_lane_page_directly_under_the_lane_is_clean(initialized):
    _write(initialized, "repositories/x.md")
    found = [f for f in _lint(initialized) if f.path == "repositories/x.md" and f.code.startswith("placement.")]
    assert not found


def test_a_nested_lane_page_is_a_placement_error(initialized):
    _write(initialized, "repositories/x/y.md")
    findings = [f for f in _lint(initialized) if f.path == "repositories/x/y.md"]
    assert any(f.code == "placement.directory-mismatch" and f.severity == "error" for f in findings)


def test_a_lane_page_outside_the_lane_is_a_placement_error(initialized):
    _write(initialized, "concepts/x.md")
    assert any(f.code == "placement.directory-mismatch" and f.path == "concepts/x.md" for f in _lint(initialized))


def test_a_lane_page_missing_its_summary_is_a_section_finding(initialized):
    _write(initialized, "repositories/x.md", _PAGE.replace("## Summary\n\nx\n", "## Other\n\nx\n"))
    assert any(f.path == "repositories/x.md" and f.code.startswith("sections.") for f in _lint(initialized))


def test_a_lane_page_missing_url_is_a_schema_finding(initialized):
    _write(initialized, "repositories/x.md", _PAGE.replace("url: https://example.com/x.git\n", ""))
    assert any(f.path == "repositories/x.md" and f.code == "schemas.invalid" for f in _lint(initialized))
