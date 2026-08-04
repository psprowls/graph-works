from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from helpers import BUNDLES
from okf_io import bundle
from okf_io.validate import validate

TODAY = date(2026, 8, 3)


def report_for(tmp_path: Path, files: dict[str, str]):
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return validate(bundle.load(tmp_path), today=TODAY)


def test_a_non_root_index_may_not_carry_frontmatter(tmp_path):
    files = {"index.md": "# Subdirectories\n", "m/index.md": "---\ntype: Index\n---\n\n# M\n"}
    report = report_for(tmp_path, files)
    finding = report.by_code("reserved.index-frontmatter")[0]
    assert finding.severity == "error"
    assert finding.path == "m/index.md"


def test_a_root_index_may_carry_only_okf_version(tmp_path):
    report = report_for(tmp_path, {"index.md": '---\nokf_version: "0.2"\n---\n\n# Sub\n'})
    assert not [f for f in report.findings if f.code.startswith("reserved.")]


def test_extra_keys_in_a_root_index_are_an_error(tmp_path):
    text = '---\nokf_version: "0.2"\ntitle: Bundle\ntype: Index\n---\n\n# Sub\n'
    report = report_for(tmp_path, {"index.md": text})
    finding = report.by_code("reserved.index-extra-keys")[0]
    assert finding.severity == "error"
    assert "title" in finding.message and "type" in finding.message


@pytest.mark.parametrize("value", ['"0.9"', "0.9", '"1.0"'])
def test_an_unrecognised_version_warns(tmp_path, value):
    report = report_for(tmp_path, {"index.md": f"---\nokf_version: {value}\n---\n\n# Sub\n"})
    assert report.by_code("reserved.okf-version-unknown")[0].severity == "warn"


@pytest.mark.parametrize("value", ['"0.1"', '"0.2"', "0.2"])
def test_a_recognised_version_is_silent(tmp_path, value):
    report = report_for(tmp_path, {"index.md": f"---\nokf_version: {value}\n---\n\n# Sub\n"})
    assert report.by_code("reserved.okf-version-unknown") == ()


def test_log_date_headings_must_be_iso(tmp_path):
    body = "# History\n\n## 2026-07-01\n\n- Verified.\n\n## Q3 2026\n\n- Something.\n"
    report = report_for(tmp_path, {"log.md": body})
    findings = report.by_code("reserved.log-heading-not-date")
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "Q3 2026" in findings[0].message
    raw = (tmp_path / "log.md").read_text(encoding="utf-8").splitlines()
    assert findings[0].line is not None
    assert raw[findings[0].line - 1].startswith("## Q3")


def test_only_level_two_headings_are_dates(tmp_path):
    body = "# Directory Update Log\n\n## 2026-07-01\n\n### Notes\n\n- Something.\n"
    assert report_for(tmp_path, {"log.md": body}).by_code("reserved.log-heading-not-date") == ()


@pytest.mark.parametrize("heading", ["20260701", "2026-W27-3"])
def test_iso_8601_variants_are_rejected(tmp_path, heading):
    """§9 requires literal YYYY-MM-DD, not other ISO 8601 formats."""
    body = f"# History\n\n## {heading}\n\n- Not ISO 8601 strict.\n"
    findings = report_for(tmp_path, {"log.md": body}).by_code("reserved.log-heading-not-date")
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert heading in findings[0].message


def test_a_date_shaped_but_impossible_heading_is_still_wrong(tmp_path):
    body = "# History\n\n## 2026-13-45\n\n- Something.\n"
    assert report_for(tmp_path, {"log.md": body}).by_code("reserved.log-heading-not-date") != ()


def test_log_frontmatter_is_deliberately_not_flagged(tmp_path):
    """§8 restricts frontmatter in `index.md`; §9 says nothing of the sort."""
    body = "---\ntype: Log\ntitle: History\n---\n\n# History\n\n## 2026-07-01\n\n- Done.\n"
    report = report_for(tmp_path, {"log.md": body})
    assert not [f for f in report.findings if f.code.startswith("reserved.")]


@pytest.mark.parametrize("name", ["acme_retail", "ga4"])
def test_the_vendored_bundles_are_clean(name):
    report = validate(bundle.load(BUNDLES / name), today=TODAY)
    assert not [f for f in report.findings if f.code.startswith("reserved.")]
