import importlib.resources

from okf_ext.schemas import load_schemas

_EXPECTED_DIRECTORY = {
    "Package": "packages/",
    "App": "apps/",
    "Dependency": "dependencies/",
    "TestSuite": "test-suites/",
    "Repository": "repositories/",
    "AgentPlugin": "agent-plugins/",
    "File": "files/",
}

#: `Dependency` is the one type whose `required` reaches past the universal
#: three: `ecosystem` is generator-owned and non-optional in
#: `DependencyDescription`, so requiring it costs generated pages nothing and
#: catches the hand-authored ones (design spec §3.2).
_EXPECTED_REQUIRED = {
    "Dependency": ["type", "title", "resource", "ecosystem"],
}
_UNIVERSAL_REQUIRED = ["type", "title", "resource"]


def test_seed_schemas_load_and_declare_directories() -> None:
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "schema"
    schema_set = load_schemas(str(assets))
    assert set(schema_set.types) == set(_EXPECTED_DIRECTORY)
    for type_name, directory in _EXPECTED_DIRECTORY.items():
        schema = schema_set.schemas[type_name]
        assert schema["x-okf-directory"] == directory
        assert schema["required"] == _EXPECTED_REQUIRED.get(type_name, _UNIVERSAL_REQUIRED)
        assert schema["properties"]["type"] == {"const": type_name}
