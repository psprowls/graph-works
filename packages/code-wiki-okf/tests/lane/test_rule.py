from __future__ import annotations

import importlib.resources
import json
from datetime import date
from pathlib import Path

from code_wiki_okf.init import install_bundle
from code_wiki_okf.lane.rule import CODES, TOPIC, lane_rule
from okf_ext.schemas import SchemaSet, load_schemas
from okf_io import Report, Severity, load_bundle, validate

_TODAY = date(2026, 1, 1)
_RESOURCE = "pkg:acme/repo-a/widgets"


def _seed_schemas() -> SchemaSet:
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_schema"
    return load_schemas(str(assets))


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    return root


def _write_concept(root: Path, relative: str, *, type_: str, resource: str | None = _RESOURCE) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"type: {type_}", f'title: "{relative}"']
    if resource is not None:
        lines.append(f'resource: "{resource}"')
    body = "---\n" + "\n".join(lines) + "\n---\n\n## Purpose\n\nA test page.\n"
    path.write_text(body, encoding="utf-8")


def _report(root: Path, *, schema_set: SchemaSet | None = None, severity: Severity = "error") -> Report:
    schemas = _seed_schemas() if schema_set is None else schema_set
    return validate(load_bundle(root), today=_TODAY, extra_rules=[lane_rule(schemas, severity=severity)])


def test_topic_is_lane_and_every_code_carries_the_prefix() -> None:
    assert TOPIC == "lane"
    assert CODES == ("lane.directory-mismatch", "lane.duplicate-resource")
    assert all(code.startswith(f"{TOPIC}.") for code in CODES)
    # The module name IS the code prefix -- okf-io's own catalog invariant,
    # asserted mechanically rather than by eye.
    assert lane_rule.__module__.split(".")[-2] == TOPIC


def test_a_seeded_bundle_reports_nothing(tmp_path: Path) -> None:
    report = _report(_bundle(tmp_path))
    assert not report.by_code("lane.directory-mismatch")
    assert not report.by_code("lane.duplicate-resource")


def test_a_package_page_outside_its_lane_is_an_error(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _write_concept(root, "dependencies/widgets.md", type_="Package")
    findings = _report(root).by_code("lane.directory-mismatch")
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].path == "dependencies/widgets.md"
    assert "packages/" in findings[0].message


def test_a_package_page_in_its_lane_reports_nothing(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _write_concept(root, "packages/widgets.md", type_="Package")
    assert not _report(root).by_code("lane.directory-mismatch")


def test_a_repository_page_below_depth_one_is_an_error(tmp_path: Path) -> None:
    """`Repository.schema.json` and `File.schema.json` declare the same
    `x-okf-directory`, so a plain prefix check passes this page. The depth
    rule is what catches it -- the discipline `entities/delete.py`'s
    `exact_depth=` already carries."""
    root = _bundle(tmp_path)
    _write_concept(root, "repositories/acme/nested.md", type_="Repository", resource="repo:acme")
    findings = _report(root).by_code("lane.directory-mismatch")
    assert len(findings) == 1
    assert findings[0].path == "repositories/acme/nested.md"


def test_a_repository_page_at_depth_one_reports_nothing(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _write_concept(root, "repositories/acme.md", type_="Repository", resource="repo:acme")
    assert not _report(root).by_code("lane.directory-mismatch")


def test_a_file_page_at_depth_one_is_an_error(tmp_path: Path) -> None:
    """The mirror lane's other half of the same shared prefix."""
    root = _bundle(tmp_path)
    _write_concept(root, "repositories/loose.md", type_="File", resource="file:acme/loose.py")
    findings = _report(root).by_code("lane.directory-mismatch")
    assert len(findings) == 1
    assert findings[0].path == "repositories/loose.md"


def test_a_file_page_below_depth_one_reports_nothing(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _write_concept(root, "repositories/acme/src/app.py.md", type_="File", resource="file:acme/src/app.py")
    assert not _report(root).by_code("lane.directory-mismatch")


def test_two_pages_claiming_one_resource_report_the_loser(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _write_concept(root, "packages/alpha.md", type_="Package", resource="pkg:x/y/dup")
    _write_concept(root, "packages/beta.md", type_="Package", resource="pkg:x/y/dup")
    findings = _report(root).by_code("lane.duplicate-resource")
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].path == "packages/beta.md"  # `alpha` sorts first, so find-by-resource keeps it
    assert "packages/alpha.md" in findings[0].message


def test_three_pages_claiming_one_resource_report_both_losers(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    for name in ("alpha", "beta", "gamma"):
        _write_concept(root, f"packages/{name}.md", type_="Package", resource="pkg:x/y/dup")
    findings = _report(root).by_code("lane.duplicate-resource")
    assert [f.path for f in findings] == ["packages/beta.md", "packages/gamma.md"]


def test_a_page_with_no_resource_is_not_a_duplicate(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _write_concept(root, "packages/alpha.md", type_="Package", resource=None)
    _write_concept(root, "packages/beta.md", type_="Package", resource=None)
    assert not _report(root).by_code("lane.duplicate-resource")


def test_a_type_with_no_schema_is_left_to_the_schema_catalog(tmp_path: Path) -> None:
    """`schemas.no-schema-for-type` already reports this (§2.5); a second
    code for the same fact is the duplication this port exists to remove."""
    root = _bundle(tmp_path)
    _write_concept(root, "packages/note.md", type_="Concept", resource="concept:note")
    assert not _report(root).by_code("lane.directory-mismatch")


def test_a_blank_type_is_skipped(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    (root / "packages").mkdir()
    (root / "packages" / "blank.md").write_text(
        '---\ntype: "   "\ntitle: "blank"\nresource: "pkg:x/y/blank"\n---\n\n## Purpose\n\nText.\n',
        encoding="utf-8",
    )
    assert not _report(root).by_code("lane.directory-mismatch")


def test_an_unparseable_page_is_skipped(tmp_path: Path) -> None:
    """okf-io's never-raise content model: a malformed concept still yields a
    `Document`, carrying its failure in `parse_error`. Re-reporting it here
    would duplicate `frontmatter.*`."""
    root = _bundle(tmp_path)
    (root / "dependencies").mkdir()
    (root / "dependencies" / "broken.md").write_text(
        "---\ntype: Package\n  bad: [unclosed\n---\n\n## Purpose\n\nText.\n", encoding="utf-8"
    )
    bundle = load_bundle(root)
    # Assert the fixture really is unparseable, so a future okf-io that
    # accepts this YAML makes the test fail loudly instead of quietly
    # testing nothing -- the page is in the wrong lane on purpose.
    assert bundle.concepts["dependencies/broken"].parse_error is not None
    report = validate(bundle, today=_TODAY, extra_rules=[lane_rule(_seed_schemas())])
    assert not report.by_code("lane.directory-mismatch")


def _schema_dir(tmp_path: Path, directory: object) -> SchemaSet:
    """A one-type `_schema/` whose `x-okf-directory` the caller chooses."""
    schema_dir = tmp_path / "declarations" / "_schema"
    schema_dir.mkdir(parents=True)
    document: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["type", "title", "resource"],
        "properties": {"type": {"const": "Package"}},
    }
    if directory is not None:
        document["x-okf-directory"] = directory
    (schema_dir / "Package.schema.json").write_text(json.dumps(document), encoding="utf-8")
    return load_schemas(schema_dir)


def test_a_schema_declaring_no_directory_is_skipped(tmp_path: Path) -> None:
    """`x-okf-directory` is an extension annotation, not a JSONSchema
    requirement -- a hand-authored `_schema/` entry without it declares no
    lane, and a rule must never raise for content."""
    root = _bundle(tmp_path)
    _write_concept(root, "dependencies/widgets.md", type_="Package")
    assert not _report(root, schema_set=_schema_dir(tmp_path, None)).by_code("lane.directory-mismatch")


def test_a_schema_declaring_a_blank_directory_is_skipped(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _write_concept(root, "dependencies/widgets.md", type_="Package")
    assert not _report(root, schema_set=_schema_dir(tmp_path, "   ")).by_code("lane.directory-mismatch")


def test_severity_is_overridable(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _write_concept(root, "dependencies/widgets.md", type_="Package")
    report = _report(root, severity="warn")
    assert report.by_code("lane.directory-mismatch")[0].severity == "warn"


def test_every_declared_code_fires_in_one_walk(tmp_path: Path) -> None:
    """What a nonconformant-vault golden buys elsewhere: one built bundle, an
    asserted code set, and no golden to regenerate."""
    root = _bundle(tmp_path)
    _write_concept(root, "dependencies/widgets.md", type_="Package", resource="pkg:x/y/dup")
    _write_concept(root, "packages/widgets.md", type_="Package", resource="pkg:x/y/dup")
    report = _report(root)
    fired = {finding.code for finding in report.findings if finding.code.startswith(f"{TOPIC}.")}
    assert fired == set(CODES)
