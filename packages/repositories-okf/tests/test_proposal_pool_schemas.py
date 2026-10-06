"""The repositories types are registry-owned, so they are locked (D-009); the base carries no flag."""

import importlib.resources

from okf_ext.schemas import declared_proposables, load_schemas


def test_every_repositories_type_is_locked_and_the_base_is_unflagged() -> None:
    schema_set = load_schemas(str(importlib.resources.files("repositories_okf") / "assets" / "schema"))
    found = declared_proposables(schema_set)
    assert found.types == () and found.refused == ()
    assert found.locked == ("ManagedRepository", "ReferenceRepository", "RepositoryChangelog", "RepositorySnapshot")
    assert "x-okf-accept-proposals" not in schema_set.documents["_base-repository.schema.json"]
