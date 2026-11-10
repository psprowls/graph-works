import importlib.resources

import pytest
from okf_ext.schemas import load_schemas
from okf_ext.schemas.rule import frontmatter_errors

_EXPECTED_DIRECTORY = {
    "Package": "packages/",
    "App": "apps/",
    "Dependency": "dependencies/",
    "TestSuite": "test-suites/",
    "Repository": "code-graph/",
    "AgentPlugin": "agent-plugins/",
    "File": "file-system/",
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


def test_dependency_schema_comment_documents_the_nested_directory() -> None:
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "schema"
    schema = load_schemas(str(assets)).schemas["Dependency"]
    comment = schema["$comment"]
    assert "code-graph/<repo>/entities/" in comment
    assert "<ecosystem>/" in comment


def test_dependency_schema_comment_does_not_change_validation() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "schema"
    schema = dict(load_schemas(str(assets)).schemas["Dependency"])
    bare = {key: value for key, value in schema.items() if key != "$comment"}
    page = {"type": "Dependency", "title": "pytest", "resource": "dependency:o/r/pypi/pytest", "ecosystem": "pypi"}
    bad = {"type": "Dependency", "title": "pytest"}
    for candidate in (page, bad):
        with_comment = jsonschema.Draft202012Validator(schema).is_valid(candidate)
        without = jsonschema.Draft202012Validator(bare).is_valid(candidate)
        assert with_comment == without


_APP_BASE = {"type": "App", "title": "cli-app", "resource": "app:x/y/cli-app"}


def _app_errors(**extra: object) -> tuple[str, ...] | None:
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "schema"
    return frontmatter_errors(load_schemas(str(assets)), "App", {**_APP_BASE, **extra})


def test_app_schema_admits_app_kind_and_app_signals() -> None:
    assert not _app_errors(app_kind="cli", app_signals=["has-shebang"])
    assert not _app_errors()


def test_app_schema_rejects_wrongly_typed_app_kind_and_app_signals() -> None:
    assert _app_errors(app_kind=3)
    assert _app_errors(app_kind="")
    assert _app_errors(app_signals="has-shebang")
