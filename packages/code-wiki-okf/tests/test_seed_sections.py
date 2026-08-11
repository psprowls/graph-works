import importlib.resources
from datetime import date
from pathlib import Path

from code_wiki_okf.init import install_bundle
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import load_sections, section_rule
from okf_io import load_bundle, validate

_TODAY = date(2026, 1, 1)

_EXPECTED_OWNED = {
    "Package": ("language", "version", "depends_on", "test_suites", "entry_points"),
    "App": ("language", "version", "depends_on", "test_suites", "entry_points"),
    "Dependency": ("ecosystem", "used_by", "versions_in_use"),
    "TestSuite": ("tested_packages", "suite_kind", "file_count"),
    "Repository": ("package_count",),
    "AgentPlugin": ("ecosystem", "version"),
    "File": ("language", "package", "role_flags"),
}

#: Each type's one required *prose* section -- the human's, and the first one
#: declared. Every generated section is required too (see
#: `test_every_generated_section_is_required`), so "the required one" is no
#: longer a single section per type.
_EXPECTED_REQUIRED_PROSE_HEADING = {
    "Package": "Purpose",
    "App": "Purpose",
    "Dependency": "Why we depend on this",
    "TestSuite": "Purpose",
    "Repository": "Overview",
    "AgentPlugin": "Purpose",
    "File": "Notes",
}

#: Design spec §3.1: the count is thirteen, across five declarations.
#: `Repository.yaml` and `Dependency.yaml` declare none -- matching
#: `render_repository`/`render_dependency`, which pass no `sections`.
_EXPECTED_GENERATED = {
    "Package": ("Files",),
    "App": ("Files",),
    "TestSuite": ("Files",),
    "AgentPlugin": ("Commands", "Agents", "Skills", "Scripts", "Hooks", "MCP servers"),
    "File": ("Symbols", "Imports", "Exports", "Imported By"),
    "Repository": (),
    "Dependency": (),
}


def _seed_sections():
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_sections"
    return load_sections(str(assets))


def test_seed_sections_load_and_declare_ownership() -> None:
    section_set = _seed_sections()
    assert set(section_set.type_names) == set(_EXPECTED_OWNED)
    for type_name, owned in _EXPECTED_OWNED.items():
        declaration = section_set.types[type_name]
        assert declaration.frontmatter.owned == owned
        assert declaration.frontmatter.provenance == ("generated", "last_updated_commit", "tokens")
        prose_required = [s for s in declaration.sections if s.required and s.ownership == "prose"]
        assert len(prose_required) == 1
        assert prose_required[0].heading == _EXPECTED_REQUIRED_PROSE_HEADING[type_name]


def test_seed_sections_generated_sections_carry_a_not_yet_generated_placeholder() -> None:
    for declaration in _seed_sections().types.values():
        for section in declaration.sections:
            if section.ownership == "generated":
                assert "not yet generated" in section.placeholder


def test_every_generated_section_is_required() -> None:
    """Design spec §3.1. A generated section's heading IS the anchor the
    splicer writes into: lose it and `regenerate_body` reports
    `Skipped(reason="section-missing")` into a sync run's output and nowhere
    else, silently dropping the section's content on every later run.
    `required: true` is what makes `sections.missing` say so at validate time
    -- flag-only, because a renamed deterministic heading cannot be safely
    auto-healed.
    """
    section_set = _seed_sections()
    total = 0
    for type_name, headings in _EXPECTED_GENERATED.items():
        declaration = section_set.types[type_name]
        generated = [s for s in declaration.sections if s.ownership == "generated"]
        assert tuple(s.heading for s in generated) == headings
        assert all(s.required for s in generated), type_name
        # Deliberately NOT `seeded_is_complete`: a page that was created but
        # never regenerated genuinely does carry an unfilled generated
        # section, and every renderer fills the ones it declares (worst case
        # `_(none)_`), so a synced page never reports one.
        assert not any(s.seeded_is_complete for s in generated), type_name
        total += len(generated)
    assert total == 13


def _bundle_with(tmp_path: Path, relative: str, text: str) -> Path:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return root


def _sections_report(root: Path):
    return validate(load_bundle(root), today=_TODAY, extra_rules=[section_rule(_seed_sections())])


_PACKAGE_HEAD = '---\ntype: Package\ntitle: "widgets"\nresource: "pkg:x/y/widgets"\n---\n\n'
_PACKAGE_PROSE = "## Purpose\n\nReal purpose text, filled in properly for this test.\n\n"


def test_a_renamed_generated_heading_now_reports_sections_missing(tmp_path: Path) -> None:
    root = _bundle_with(
        tmp_path,
        "packages/widgets.md",
        _PACKAGE_HEAD + _PACKAGE_PROSE + "## File map\n\n- [src/a.py](/repositories/acme/src/a.py.md)\n",
    )
    findings = _sections_report(root).by_code("sections.missing")
    assert [f.path for f in findings] == ["packages/widgets.md"]
    assert "Files" in findings[0].message


def test_a_filled_generated_section_reports_no_unfilled(tmp_path: Path) -> None:
    """The property `seeded_is_complete` would have bought and did not need:
    `_(none)_` is what `_files_section` writes for an entity with no files,
    and it is not the placeholder, so a synced page is clean."""
    root = _bundle_with(tmp_path, "packages/widgets.md", _PACKAGE_HEAD + _PACKAGE_PROSE + "## Files\n\n_(none)_\n")
    report = _sections_report(root)
    assert not report.by_code("sections.missing")
    assert not report.by_code("sections.unfilled")


def test_a_dependency_without_ecosystem_reports_schemas_invalid(tmp_path: Path) -> None:
    """Design spec §3.2: closed by the declaration rather than a rule, and
    `schemas.invalid` is the code for "frontmatter does not satisfy the schema
    for its type"."""
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_schema"
    root = _bundle_with(
        tmp_path,
        "dependencies/ruamel.yaml.md",
        '---\ntype: Dependency\ntitle: "ruamel.yaml"\nresource: "dep:pypi/ruamel.yaml"\n---\n\n'
        "## Why we depend on this\n\nRound-trippable YAML.\n",
    )
    report = validate(load_bundle(root), today=_TODAY, extra_rules=[schema_rule(load_schemas(str(assets)))])
    findings = report.by_code("schemas.invalid")
    assert [f.path for f in findings] == ["dependencies/ruamel.yaml.md"]
    assert "ecosystem" in findings[0].message
