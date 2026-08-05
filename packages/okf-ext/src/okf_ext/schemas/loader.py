"""Read a directory of JSONSchema documents. The only filesystem access here.

**Nothing is auto-discovered.** `load_schemas(path)` reads the directory the
caller names and no other, following the precedent `tags/vocabulary.py` set.
`_schema/` remains a documented convention that tools may default to —
`DEFAULT_SCHEMA_DIRNAME` and `okf_ext.schemas.DEFAULT_IGNORE` exist for callers
who want it — not magic this library performs.

Because the path is explicit, schemas need not live inside the bundle: one
shared schema set can validate many bundles.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema.exceptions import SchemaError as JsonSchemaError
from jsonschema.validators import validator_for
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from okf_ext.schemas.model import SchemaError, SchemaSet

#: The conventional directory name. A default tools may offer, never one this
#: module reaches for.
DEFAULT_SCHEMA_DIRNAME = "_schema"

#: Recognised filenames. YAML and JSON only
SCHEMA_SUFFIXES = (".schema.yaml", ".schema.yml", ".schema.json")

#: The draft assumed when a schema declares no `$schema`. `validator_for` picks
#: the validator per schema, so a file that *does* declare one still gets it.
_DEFAULT_SPECIFICATION = DRAFT202012


def _type_name(filename: str) -> str | None:
    """The type a filename claims, or `None` if it is not a schema file.

    A file named exactly `.schema.yaml` claims the empty type, which is no type
    at all, so it is not a schema file either.
    """
    for suffix in SCHEMA_SUFFIXES:
        if filename.endswith(suffix) and len(filename) > len(suffix):
            return filename[: -len(suffix)]
    return None


def _read(path: Path) -> Any:  # noqa: ANN401 -- arbitrary parsed schema document
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaError(f"{path.name}: not valid UTF-8 at byte offset {exc.start}: {exc.reason}") from exc
    if path.name.endswith(".schema.json"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"{path.name}: not valid JSON: {exc}") from exc
    try:
        return YAML(typ="safe").load(text)
    except YAMLError as exc:
        raise SchemaError(f"{path.name}: not valid YAML: {exc}") from exc


def build_registry(documents: Mapping[str, Mapping[str, Any]]) -> Registry[Any]:
    """A `referencing` registry keyed by **filename**.

    That key is what makes the documented `$ref: "_base.schema.yaml#/$defs/x"`
    form resolve, fragment and all. `okf_ext.schemas.rule` rebuilds the registry
    from `SchemaSet.documents` rather than receiving it, which is what keeps
    `SchemaSet` plain data.
    """
    return Registry().with_resources(
        (
            name,
            Resource.from_contents(dict(body), default_specification=_DEFAULT_SPECIFICATION),
        )
        for name, body in documents.items()
    )


def load_schemas(path: str | Path) -> SchemaSet:
    """Read every schema file directly under *path*.

    Accepts a `str` as well as a `Path`, coerced first -- the same forgiveness
    `load_vocabulary` extends. The directory is read non-recursively: a schema
    set is a directory of schemas, not a tree.

    Raises `SchemaError`, naming the file, for anything wrong with a schema's
    encoding, syntax, shape or validity, for two files claiming one type name,
    and for a directory holding no schema files at all -- overwhelmingly a wrong
    path, and raising keeps `schema_rule` free of an empty-set special case.
    Propagates `OSError` for a path that is not there, matching
    `load_vocabulary`: a missing directory is a different caller error and
    should not be dressed up as a format one.
    """
    root = Path(path)
    documents: dict[str, Mapping[str, Any]] = {}
    schemas: dict[str, Mapping[str, Any]] = {}
    sources: dict[str, str] = {}

    for entry in sorted(root.iterdir()):
        name = entry.name
        type_name = _type_name(name)
        if type_name is None or not entry.is_file():
            continue
        body = _read(entry)
        if not isinstance(body, Mapping):
            raise SchemaError(f"{name}: a schema must be a mapping, got {type(body).__name__}")
        try:
            validator_for(dict(body)).check_schema(dict(body))
        except JsonSchemaError as exc:
            raise SchemaError(f"{name}: not a valid JSONSchema: {exc.message}") from exc
        # A shallow copy, not `MappingProxyType(dict(body))`: the parser
        # (ruamel's safe loader or `json.loads`) already returns plain,
        # unproxied `dict`/`list`/scalar values all the way down, so this is
        # the layer at which `SchemaSet`'s mapping fields stop being frozen
        # and start being ordinary JSON-shaped data -- which is what lets a
        # whole schema document, nested keys included, survive `json.dumps`
        # with no encoder. Proxying here too would break exactly that.
        documents[name] = dict(body)
        # An underscore-prefixed file is a `$ref` target, not a type: it is in
        # `documents` so a `$ref` reaches it, and out of `schemas` so no concept
        # can match `type: _base`.
        if name.startswith("_"):
            continue
        if type_name in sources:
            raise SchemaError(f"{name}: type `{type_name}` is already claimed by `{sources[type_name]}`")
        schemas[type_name] = documents[name]
        sources[type_name] = name

    if not documents:
        raise SchemaError(f"{root}: no schema files found; expected one or more of {list(SCHEMA_SUFFIXES)}")

    return SchemaSet(
        schemas=MappingProxyType(dict(sorted(schemas.items()))),
        sources=MappingProxyType(dict(sorted(sources.items()))),
        documents=MappingProxyType(dict(sorted(documents.items()))),
        root=root,
    )
