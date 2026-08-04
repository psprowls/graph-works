from __future__ import annotations

from datetime import date
from pathlib import Path

from okf_io import bundle
from okf_io.validate import validate

TODAY = date(2026, 8, 3)


def codes(tmp_path: Path, files: dict[str, str], raw: dict[str, bytes] | None = None):
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for rel, payload in (raw or {}).items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    report = validate(bundle.load(tmp_path), today=TODAY)
    return report, {f.code for f in report.findings}


FULL = "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# Definition\n"


def test_no_block_at_all(tmp_path):
    report, found = codes(tmp_path, {"a.md": "# Definition\n\nNo frontmatter.\n"})
    assert "frontmatter.missing" in found
    assert report.by_code("frontmatter.missing")[0].severity == "error"
    assert "frontmatter.missing-type" not in found
    assert "frontmatter.title-recommended" not in found


def test_an_empty_block_is_a_block(tmp_path):
    """`---\\n---` has a block; it is missing a type, not missing a block."""
    _, found = codes(tmp_path, {"a.md": "---\n---\n\n# Definition\n"})
    assert "frontmatter.missing" not in found
    assert "frontmatter.missing-type" in found


def test_unparseable_carries_the_kind_and_the_line(tmp_path):
    report, found = codes(tmp_path, {"a.md": '---\ntype: "unterminated\n---\n\n# D\n'})
    assert "frontmatter.unparseable" in found
    finding = report.by_code("frontmatter.unparseable")[0]
    assert finding.severity == "error"
    assert "yaml" in finding.message
    assert finding.line is not None


def test_an_unterminated_block_is_unparseable_not_missing(tmp_path):
    _, found = codes(tmp_path, {"a.md": "---\ntype: Metric\n\n# D\n"})
    assert found & {"frontmatter.unparseable"}
    assert "frontmatter.missing" not in found


def test_a_member_nobody_can_decode(tmp_path):
    report, found = codes(
        tmp_path, {"good.md": FULL}, raw={"bad.md": b"---\ntype: Metric\n---\n\n\xff\xfe\n"}
    )
    assert "frontmatter.unreadable" in found
    finding = report.by_code("frontmatter.unreadable")[0]
    assert finding.severity == "error"
    assert finding.path == "bad.md"


def test_missing_type_covers_absent_empty_and_whitespace(tmp_path):
    for text in (
        "---\ntitle: T\n---\n\n# D\n",
        '---\ntype: ""\n---\n\n# D\n',
        '---\ntype: "  "\n---\n\n# D\n',
    ):
        _, found = codes(tmp_path, {"a.md": text})
        assert "frontmatter.missing-type" in found


def test_the_two_recommendations_fire_independently(tmp_path):
    _, found = codes(tmp_path, {"a.md": "---\ntype: Metric\ntitle: T\n---\n\n# D\n"})
    assert "frontmatter.description-recommended" in found
    assert "frontmatter.title-recommended" not in found


def test_a_complete_concept_produces_no_frontmatter_finding(tmp_path):
    _, found = codes(tmp_path, {"a.md": FULL})
    assert not {c for c in found if c.startswith("frontmatter.")}
