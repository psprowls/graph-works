import importlib.resources

from okf_ext.sections import load_sections

_EXPECTED_OWNED = {
    "Package": ("language", "version", "depends_on", "test_suites", "entry_points"),
    "App": ("language", "version", "depends_on", "test_suites", "entry_points"),
    "Dependency": ("ecosystem", "used_by", "versions_in_use"),
    "TestSuite": ("tested_packages", "suite_kind", "file_count"),
    "Repository": ("package_count",),
    "AgentPlugin": ("ecosystem", "version"),
    "File": ("language", "package", "role_flags"),
}

_EXPECTED_FIRST_REQUIRED_HEADING = {
    "Package": "Purpose",
    "App": "Purpose",
    "Dependency": "Why we depend on this",
    "TestSuite": "Purpose",
    "Repository": "Overview",
    "AgentPlugin": "Purpose",
    "File": "Notes",
}


def test_seed_sections_load_and_declare_ownership() -> None:
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_sections"
    section_set = load_sections(str(assets))
    assert set(section_set.type_names) == set(_EXPECTED_OWNED)
    for type_name, owned in _EXPECTED_OWNED.items():
        declaration = section_set.types[type_name]
        assert declaration.frontmatter.owned == owned
        assert declaration.frontmatter.provenance == ("generated", "last_updated_commit", "tokens")
        required = [s for s in declaration.sections if s.required]
        assert len(required) == 1
        assert required[0].heading == _EXPECTED_FIRST_REQUIRED_HEADING[type_name]


def test_seed_sections_generated_sections_carry_a_not_yet_generated_placeholder() -> None:
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_sections"
    section_set = load_sections(str(assets))
    for declaration in section_set.types.values():
        for section in declaration.sections:
            if section.ownership == "generated":
                assert "not yet generated" in section.placeholder
