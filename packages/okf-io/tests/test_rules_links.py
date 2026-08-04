from __future__ import annotations

from datetime import date
from pathlib import Path

from okf_io import bundle
from okf_io.validate import validate

TODAY = date(2026, 8, 3)
CONCEPT = "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n"


def report_for(tmp_path: Path, files: dict[str, str], **kwargs):
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return validate(bundle.load(tmp_path), today=TODAY, **kwargs)


def test_a_missing_target_warns(tmp_path):
    report = report_for(tmp_path, {"a.md": CONCEPT + "[x](./nope.md)\n"})
    finding = report.by_code("links.broken")[0]
    assert finding.severity == "warn"
    assert "./nope.md" in finding.message
    assert finding.message.startswith("Link")
    assert finding.path == "a.md"


def test_a_missing_image_warns_and_says_so(tmp_path):
    report = report_for(tmp_path, {"a.md": CONCEPT + "![d](/assets/none.png)\n"})
    assert report.by_code("links.broken")[0].message.startswith("Image")


def test_broken_links_never_make_a_bundle_fail(tmp_path):
    """ADR-0004: a broken link may be knowledge not yet written."""
    report = report_for(tmp_path, {"a.md": CONCEPT + "[x](./nope.md)\n"})
    assert report.ok is True


def test_strict_is_how_a_ci_job_is_harsher_than_the_spec(tmp_path):
    report = report_for(tmp_path, {"a.md": CONCEPT + "[x](./nope.md)\n"}, strict=True)
    assert report.ok is False
    assert report.by_code("links.broken")[0].severity == "error"


def test_the_repair_hint_suggests_the_absolute_form(tmp_path):
    report = report_for(
        tmp_path,
        {"deep/a.md": CONCEPT + "[p](policies/p.md)\n", "policies/p.md": CONCEPT + "# P\n"},
    )
    assert "`/policies/p.md`" in report.by_code("links.broken")[0].message


def test_no_hint_when_the_root_form_would_not_resolve_either(tmp_path):
    report = report_for(tmp_path, {"a.md": CONCEPT + "[x](./nope.md)\n"})
    assert "did you mean" not in report.by_code("links.broken")[0].message


def test_resolving_external_and_fenced_links_are_silent(tmp_path):
    body = "[ok](/b.md) [ext](https://example.com)\n\n```\n[f](/nope.md)\n```\n"
    report = report_for(tmp_path, {"a.md": CONCEPT + body, "b.md": CONCEPT + "# B\n"})
    assert report.by_code("links.broken") == ()


def test_the_line_points_at_the_source_line(tmp_path):
    report = report_for(tmp_path, {"a.md": CONCEPT + "# D\n\nText.\n\n[x](./nope.md)\n"})
    line = report.by_code("links.broken")[0].line
    assert line is not None
    raw = (tmp_path / "a.md").read_text(encoding="utf-8").splitlines()
    assert "[x](./nope.md)" in raw[line - 1]
