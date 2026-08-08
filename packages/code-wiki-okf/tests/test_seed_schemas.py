import importlib.resources

from okf_ext.schemas import load_schemas

_EXPECTED_DIRECTORY = {
    "Package": "packages/",
    "App": "apps/",
    "Dependency": "dependencies/",
    "TestSuite": "test-suites/",
    "Repository": "repositories/",
    "AgentPlugin": "agent-plugins/",
    "File": "repositories/",
}


def test_seed_schemas_load_and_declare_directories() -> None:
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_schema"
    schema_set = load_schemas(str(assets))
    assert set(schema_set.types) == set(_EXPECTED_DIRECTORY)
    for type_name, directory in _EXPECTED_DIRECTORY.items():
        schema = schema_set.schemas[type_name]
        assert schema["x-okf-directory"] == directory
        assert schema["required"] == ["type", "title", "resource"]
        assert schema["properties"]["type"] == {"const": type_name}
