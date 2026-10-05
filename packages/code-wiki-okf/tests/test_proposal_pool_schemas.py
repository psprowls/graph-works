"""Every code-wiki type is generated, so every one is locked out of the proposal pool."""

import importlib.resources

from okf_ext.schemas import declared_proposables, load_schemas


def test_every_code_wiki_type_is_locked() -> None:
    schema_set = load_schemas(str(importlib.resources.files("code_wiki_okf") / "assets" / "schema"))
    found = declared_proposables(schema_set)
    assert found.types == () and found.refused == ()
    assert found.locked == schema_set.types
