"""Read a directory of JSONSchema documents. The only filesystem access here.

**Nothing is auto-discovered.** `load_schemas(path)` reads the directory the
caller names and no other, following the precedent `tags/vocabulary.py` set.
`schema/` remains a documented convention that tools may default to —
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
from urllib.parse import unquote

from jsonschema.exceptions import SchemaError as JsonSchemaError
from jsonschema.validators import validator_for
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from okf_ext.schemas.model import (
    AboutMandate,
    Proposables,
    ProposableType,
    ProposalGuidance,
    ProposalPromotion,
    SchemaError,
    SchemaSet,
)

#: The conventional directory name. A default tools may offer, never one this
#: module reaches for.
DEFAULT_SCHEMA_DIRNAME = "schema"

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


def declared_directories(schema_set: SchemaSet) -> dict[str, str]:
    """`{type: directory}` for every type in *schema_set* declaring one.

    The inverse shape of a per-type lookup: one pass, so a caller building a
    rule over the whole set does not re-walk it per document. Lives here rather
    than in the capability that consumes it because `x-okf-directory` is a
    schema annotation, and this is the module that already knows what a schema
    document looks like.

    A type declaring no annotation, a blank one, or a non-string one is omitted
    rather than reported. Nothing about a missing annotation is an error: what
    a caller does with the absence is the caller's rule.
    """
    found: dict[str, str] = {}
    for type_name, schema in schema_set.schemas.items():
        directory = schema.get("x-okf-directory")
        if isinstance(directory, str) and directory.strip():
            found[type_name] = directory
    return found


def declared_members(schema_set: SchemaSet) -> dict[str, tuple[str, ...]]:
    """`{type: (property, ...)}` for every type declaring an `x-okf-member` property.

    `"x-okf-member": true` on a property says its string value must name a
    member of the bundle. It is the vocabulary half of
    `schemas.unresolved-member`: the rule ships the mechanism, the schema's
    owner declares which fields it applies to -- the same split
    `x-okf-directory` makes for placement.

    Only **top-level** `properties` are scanned, and only a literal JSON `true`
    counts. Nested properties, `$ref`-reached properties and array items are
    not scanned: nothing needs them, and this boundary is stated so it is not
    a surprise. Properties are sorted by name; a type declaring none is
    omitted. As with `x-okf-directory`, an odd or missing annotation is
    ignored rather than reported.
    """
    found: dict[str, tuple[str, ...]] = {}
    for type_name, schema in schema_set.schemas.items():
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            continue
        fields = tuple(
            sorted(
                name
                for name, sub_schema in properties.items()
                if isinstance(sub_schema, Mapping) and sub_schema.get("x-okf-member") is True
            )
        )
        if fields:
            found[type_name] = fields
    return found


def _resolve_ref(
    schema_set: SchemaSet, ref: str, document: Mapping[str, Any]
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """`(target, its document)` for a `$ref`, or `None` when it dangles.

    The file part is looked up by filename, as `build_registry` keys it; an
    empty file part stays in *document*. The fragment is a JSON pointer.
    """
    file_part, _, fragment = ref.partition("#")
    target_doc = schema_set.documents.get(file_part) if file_part else document
    if target_doc is None:
        return None
    target: object = target_doc
    pointer = unquote(fragment)
    if pointer and not pointer.startswith("/"):
        return None
    for token in pointer.split("/")[1:]:
        part = token.replace("~1", "/").replace("~0", "~")
        if isinstance(target, Mapping):
            target = target.get(part)
        elif isinstance(target, list):
            if (
                not part.isascii()
                or not part.isdecimal()
                or (len(part) > 1 and part.startswith("0"))
                or len(part) > len(str(len(target)))
            ):
                return None
            index = int(part)
            if index >= len(target):
                return None
            target = target[index]
        else:
            return None
    return (target, target_doc) if isinstance(target, Mapping) else None


def declares_property(schema_set: SchemaSet, type_name: str, name: str) -> bool:
    """Whether *type_name*'s schema declares property *name*.

    Follows `$ref` (file part by filename, then the `#/…` fragment), `allOf`,
    `anyOf` and `oneOf`. Only `properties` keys at each reached schema count:
    nested object properties are someone else's. An unknown type, a dangling
    `$ref` and a cycle all answer no rather than raising.
    """
    root = schema_set.schemas.get(type_name)
    if root is None:
        return False
    seen: set[int] = set()

    def walk(schema: Mapping[str, Any], document: Mapping[str, Any]) -> bool:
        if id(schema) in seen:
            return False
        seen.add(id(schema))
        properties = schema.get("properties")
        if isinstance(properties, Mapping) and name in properties:
            return True
        ref = schema.get("$ref")
        if isinstance(ref, str):
            resolved = _resolve_ref(schema_set, ref, document)
            if resolved is not None and walk(*resolved):
                return True
        for key in ("allOf", "anyOf", "oneOf"):
            branches = schema.get(key)
            if isinstance(branches, list) and any(
                walk(branch, document) for branch in branches if isinstance(branch, Mapping)
            ):
                return True
        return False

    return walk(root, root)


def declared_about(schema_set: SchemaSet) -> dict[str, AboutMandate]:
    """`{type: AboutMandate}` for every type carrying an `x-okf-about` object.

    `"x-okf-about": {}` mandates `about:`; `{"entries": "<key>"}` also names
    the entry list a live page must fill. The annotation sits at the top level
    of the schema, like `x-okf-directory`.

    As with the other two annotations, an odd one is ignored rather than
    reported: a non-object annotation, or an `entries` that is not a non-blank
    string naming one of the type's own **top-level** `properties`, omits the
    type. An unusable declaration declares nothing.
    """
    found: dict[str, AboutMandate] = {}
    for type_name, schema in schema_set.schemas.items():
        annotation = schema.get("x-okf-about")
        if not isinstance(annotation, Mapping):
            continue
        entries = annotation.get("entries")
        if entries is None:
            found[type_name] = AboutMandate()
            continue
        properties = schema.get("properties")
        if isinstance(entries, str) and entries.strip() and isinstance(properties, Mapping) and entries in properties:
            found[type_name] = AboutMandate(entries=entries)
    return found


#: Keys a promoted page gets without the type's promotion block: `type`,
#: `title` and `description` come off the proposal, and the rest are what
#: `okf_ext.proposals` writes itself (`OWNED_PROVENANCE_KEYS`). Restated, not
#: imported -- this capability may not import `proposals` (the `independence`
#: contract) -- and `test_schemas_proposables.py` pins the two together.
PROMOTION_SUPPLIED: frozenset[str] = frozenset({"type", "title", "description", "generated", "sources", "verified"})

_GUIDANCE_KEYS = frozenset({"summary", "question", "signals", "anti_signals", "title_pattern"})
_PROMOTION_KEYS = frozenset({"dated", "frontmatter"})


def _strings(value: object) -> tuple[str, ...] | None:
    """*value* as a tuple of stripped non-blank strings, or None if it is not one."""
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        return None
    return tuple(item.strip() for item in value)


def _guidance(value: object) -> ProposalGuidance | str:
    """The parsed guidance, or the `invalid-guidance: …` reason it is unusable."""
    if not isinstance(value, Mapping):
        return "invalid-guidance: x-okf-proposal-guidance must be an object"
    unknown = sorted(str(key) for key in value if key not in _GUIDANCE_KEYS)
    if unknown:
        return f"invalid-guidance: unknown key(s) {', '.join(unknown)}"
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return "invalid-guidance: `summary` must be a non-blank string"
    question = value.get("question")
    if not isinstance(question, str) or not question.strip():
        return "invalid-guidance: `question` must be a non-blank string"
    signals = _strings(value.get("signals", []))
    if signals is None:
        return "invalid-guidance: `signals` must be a list of non-blank strings"
    anti_signals = _strings(value.get("anti_signals", []))
    if anti_signals is None:
        return "invalid-guidance: `anti_signals` must be a list of non-blank strings"
    title_pattern = value.get("title_pattern")
    if "title_pattern" in value and (not isinstance(title_pattern, str) or not title_pattern.strip()):
        return "invalid-guidance: `title_pattern` must be a non-blank string"
    return ProposalGuidance(
        summary=summary.strip(),
        question=question.strip(),
        signals=signals,
        anti_signals=anti_signals,
        title_pattern=None if title_pattern is None else title_pattern.strip(),
    )


def _promotion(value: object) -> ProposalPromotion | str:
    """The parsed promotion, or the `invalid-promotion: …` reason it is unusable."""
    if not isinstance(value, Mapping):
        return "invalid-promotion: x-okf-proposal-promotion must be an object"
    unknown = sorted(str(key) for key in value if key not in _PROMOTION_KEYS)
    if unknown:
        return f"invalid-promotion: unknown key(s) {', '.join(unknown)}"
    dated = value.get("dated", False)
    if not isinstance(dated, bool):
        return "invalid-promotion: `dated` must be a boolean"
    frontmatter = value.get("frontmatter", {})
    if not isinstance(frontmatter, Mapping) or not all(
        isinstance(key, str) and key.strip() and isinstance(item, str) for key, item in frontmatter.items()
    ):
        return "invalid-promotion: `frontmatter` must map non-blank keys to strings"
    collisions = sorted(key for key in frontmatter if key in PROMOTION_SUPPLIED)
    if collisions:
        return f"invalid-promotion: `frontmatter` may not set {', '.join(collisions)}; promotion supplies them"
    return ProposalPromotion(dated=dated, frontmatter=MappingProxyType(dict(frontmatter)))


def _effective_required(schema_set: SchemaSet, schema: Mapping[str, Any]) -> frozenset[str] | None:
    """Every `required` name reached through `$ref` and `allOf`, or None when a `$ref` dangles.

    `anyOf`/`oneOf` branches are not walked: a field one branch requires is not
    required of every page, so a promotion need not supply it.
    """
    seen: set[int] = set()
    found: set[str] = set()

    def walk(node: Mapping[str, Any], document: Mapping[str, Any]) -> bool:
        if id(node) in seen:
            return True
        seen.add(id(node))
        required = node.get("required")
        if isinstance(required, list):
            found.update(item for item in required if isinstance(item, str))
        ref = node.get("$ref")
        if isinstance(ref, str):
            resolved = _resolve_ref(schema_set, ref, document)
            if resolved is None or not walk(*resolved):
                return False
        branches = node.get("allOf")
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, Mapping) and not walk(branch, document):
                    return False
        return True

    return frozenset(found) if walk(schema, schema) else None


def declared_proposables(schema_set: SchemaSet) -> Proposables:
    """The proposal pool *schema_set* declares through `x-okf-accept-proposals`.

    Absent is no answer at all; `false` locks the type; `true` admits it when
    its `x-okf-directory`, `x-okf-proposal-guidance` and optional
    `x-okf-proposal-promotion` hold up, and its effective `required` set minus
    `PROMOTION_SUPPLIED` is covered by the promotion's `frontmatter`.

    **Nothing raises.** A misconfigured flagged type is excluded with a reason
    -- `invalid-flag`, `no-directory`, `missing-guidance`,
    `invalid-guidance: …`, `invalid-promotion: …`, `unresolved-base`,
    `uncovered-required: …` -- and every other type is unaffected, the posture
    of `declared_directories`. Underscore-prefixed files are `$ref` targets,
    not types, and are never considered.
    """
    types: list[ProposableType] = []
    locked: list[str] = []
    refused: list[tuple[str, str]] = []
    for type_name, schema in schema_set.schemas.items():
        if "x-okf-accept-proposals" not in schema:
            continue
        flag = schema["x-okf-accept-proposals"]
        if flag is False:
            locked.append(type_name)
            continue
        if flag is not True:
            refused.append((type_name, "invalid-flag"))
            continue
        directory = schema.get("x-okf-directory")
        if not isinstance(directory, str) or not directory.strip():
            refused.append((type_name, "no-directory"))
            continue
        if "x-okf-proposal-guidance" not in schema:
            refused.append((type_name, "missing-guidance"))
            continue
        guidance = _guidance(schema["x-okf-proposal-guidance"])
        if isinstance(guidance, str):
            refused.append((type_name, guidance))
            continue
        promotion: ProposalPromotion | None = None
        if "x-okf-proposal-promotion" in schema:
            parsed = _promotion(schema["x-okf-proposal-promotion"])
            if isinstance(parsed, str):
                refused.append((type_name, parsed))
                continue
            promotion = parsed
        required = _effective_required(schema_set, schema)
        if required is None:
            refused.append((type_name, "unresolved-base"))
            continue
        supplied = set(PROMOTION_SUPPLIED) | (set(promotion.frontmatter) if promotion is not None else set())
        uncovered = sorted(required - supplied)
        if uncovered:
            refused.append((type_name, f"uncovered-required: {', '.join(uncovered)}"))
            continue
        types.append(ProposableType(name=type_name, directory=directory, guidance=guidance, promotion=promotion))
    return Proposables(types=tuple(types), locked=tuple(locked), refused=tuple(refused))
