"""Read a directory of section declarations. The only filesystem access here.

**Nothing is auto-discovered.** `load_sections(path)` reads the directory the
caller names and no other, following `load_vocabulary` and `load_schemas`.
`sections/` remains a documented convention tools may default to --
`DEFAULT_SECTIONS_DIRNAME` and `DEFAULT_IGNORE`, defined below in this same
file, exist for callers who want it -- not magic this library performs.
Because the path is explicit, declarations need not live inside the bundle
they describe.

**Unknown keys are tolerated, unlike `load_vocabulary`.** That loader rejects
them because a typo in a hand-edited house-rule file is likelier than a
forward-compatible extension. Here the opposite is true and stated: the
shipped `generators` capability adds an ownership field to `SectionSpec`,
defined below in this same file, and the design spec calls that an
*additive* change -- which it is not if a declaration written today against
a newer field refuses to load. Type errors on keys this loader *does* know
are still refused.

**Refs resolve at load**, in a second pass over the whole directory, so a
`placeholder_ref` may name a fragment declared in any file -- not only in one
that sorts earlier.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from okf_ext.shape.model import (
    FrontmatterOwnership,
    Ownership,
    SectionError,
    SectionSet,
    SectionSpec,
    TypeSections,
)

#: The conventional directory name. A default tools may offer, never one this
#: module reaches for.
DEFAULT_SECTIONS_DIRNAME = "sections"

#: Recognised suffixes. The filename stem is the type name -- there is no
#: `type:` key, matching `schema/<type>.schema.yaml`.
SECTION_SUFFIXES = (".yaml", ".yml")

#: The three values `ownership` may take. A tuple rather than a set so the
#: refusal message can list them in a stable order.
_OWNERSHIP_VALUES: tuple[Ownership, ...] = ("prose", "generated", "template")

#: `sections/` is a **documented convention, not magic** -- nothing here
#: discovers it. A caller who keeps declarations inside the bundle they
#: describe splices this into `okf_io.load_bundle(root, ignore=...)`, where an
#: ignored member is "not a concept", not "not there". Two patterns because
#: the first is anchored at the start and so never matches a nested
#: `sections/`; `tags.DEFAULT_IGNORE` and `schemas.DEFAULT_IGNORE` each carry
#: two for the same reason.
DEFAULT_IGNORE = ("sections/*", "*/sections/*")

#: The deepest heading level a declaration may name. Markdown has six, and
#: `okf_ext.body.Section.level` is read straight off `token.tag[1:]`, so a
#: declaration outside this range could never match any heading.
_MAX_LEVEL = 6


def _type_name(filename: str) -> str | None:
    """The type a filename claims, or `None` if it is not a declaration file.

    A file named exactly `.yaml` claims the empty type, which is no type at
    all, so it is not a declaration file either.
    """
    for suffix in SECTION_SUFFIXES:
        if filename.endswith(suffix) and len(filename) > len(suffix):
            return filename[: -len(suffix)]
    return None


def _read(path: Path) -> Any:  # noqa: ANN401 -- arbitrary parsed YAML document
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SectionError(f"{path.name}: not valid UTF-8 at byte offset {exc.start}: {exc.reason}") from exc
    try:
        return YAML(typ="safe").load(text)
    except YAMLError as exc:
        raise SectionError(f"{path.name}: not valid YAML: {exc}") from exc


def _file_fragments(name: str, data: Mapping[str, Any]) -> dict[str, str]:
    raw = data.get("fragments", {})
    if not isinstance(raw, Mapping):
        raise SectionError(f"{name}: `fragments` must be a mapping, got {type(raw).__name__}")
    found: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise SectionError(f"{name}: fragment `{key}` must be a string, got {type(value).__name__}")
        found[str(key)] = value
    return found


def _key_list(where: str, raw: Any, field: str) -> tuple[str, ...]:  # noqa: ANN401 -- arbitrary parsed YAML
    """One `owned:` / `provenance:` list, refused for every shape §4.3 names.

    A `str` is rejected explicitly: it is a `Sequence` and would iterate as
    characters -- the trap `plan_merge` already guards against with a
    `TypeError`.
    """
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise SectionError(f"{where}: `{field}` must be a list, got {type(raw).__name__}")
    found: list[str] = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, str) or not entry.strip():
            raise SectionError(f"{where}: `{field}`[{position}] must be a non-empty string, got {entry!r}")
        name = entry.strip()
        if name in found:
            raise SectionError(f"{where}: `{field}` names `{name}` twice; a duplicate key is a declaration error")
        found.append(name)
    return tuple(found)


def _frontmatter(name: str, data: Mapping[str, Any]) -> FrontmatterOwnership:
    """The `frontmatter:` block, or the empty ownership when absent.

    Unknown keys inside it are tolerated, matching this loader's existing
    policy for the same reason: a declaration written against a newer field
    must not refuse to load in an older reader.
    """
    raw = data.get("frontmatter", {})
    if not isinstance(raw, Mapping):
        raise SectionError(f"{name}: `frontmatter` must be a mapping, got {type(raw).__name__}")
    owned = _key_list(name, raw.get("owned", []), "owned")
    provenance = _key_list(name, raw.get("provenance", []), "provenance")
    # Not a type error but a semantic one: the two classes prescribe
    # contradictory behaviour for the same key. Refusing at load is strictly
    # stronger than the disjointness unit test the upstream keeps.
    both = sorted(set(owned) & set(provenance))
    if both:
        raise SectionError(
            f"{name}: `{both[0]}` is in both `owned` and `provenance`; the two prescribe different behaviour"
        )
    return FrontmatterOwnership(owned=owned, provenance=provenance)


def _section_spec(where: str, entry: Mapping[str, Any], fragments: Mapping[str, str]) -> SectionSpec:
    raw_heading = entry.get("heading")
    if not isinstance(raw_heading, str) or not raw_heading.strip():
        raise SectionError(f"{where}: missing a non-empty `heading`")

    # `bool` is a subclass of `int`, so `level: true` would read as `1`
    # without the explicit exclusion -- the trap `load_vocabulary`'s `version`
    # check already documents.
    level = entry.get("level", 2)
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= _MAX_LEVEL:
        raise SectionError(f"{where}: `level` must be an integer 1-{_MAX_LEVEL}, got {level!r}")

    required = entry.get("required", False)
    if not isinstance(required, bool):
        raise SectionError(f"{where}: `required` must be true or false, got {required!r}")

    seeded = entry.get("seeded_is_complete", False)
    if not isinstance(seeded, bool):
        raise SectionError(f"{where}: `seeded_is_complete` must be true or false, got {seeded!r}")

    # `bool` is a subclass of `str`? No -- but `ownership: true` would read as
    # a truthy value under a bare falsy test, and `ownership: 2` under an `in`
    # test against the tuple would simply miss. Both are type errors and both
    # are named as such, so the message says what is wrong rather than only
    # that something is.
    raw_ownership = entry.get("ownership", "prose")
    if not isinstance(raw_ownership, str):
        raise SectionError(f"{where}: `ownership` must be a string, got {type(raw_ownership).__name__}")
    if raw_ownership not in _OWNERSHIP_VALUES:
        raise SectionError(f"{where}: `ownership` must be one of {list(_OWNERSHIP_VALUES)}, got {raw_ownership!r}")
    # No `cast` and no `# type: ignore` here: the `not in _OWNERSHIP_VALUES`
    # check above is a membership test against a `tuple[Ownership, ...]`, and
    # mypy narrows `raw_ownership` to `Ownership` from that alone. A `cast`
    # was tried first and mypy flagged it `redundant-cast` -- so re-adding one
    # is not a fix, it is rediscovering this comment the hard way.
    ownership: Ownership = raw_ownership

    literal = entry.get("placeholder")
    ref = entry.get("placeholder_ref")
    if literal is not None and ref is not None:
        raise SectionError(f"{where}: `placeholder` and `placeholder_ref` are mutually exclusive")
    if literal is not None:
        if not isinstance(literal, str):
            raise SectionError(f"{where}: `placeholder` must be a string, got {type(literal).__name__}")
        placeholder = literal
    elif ref is not None:
        if not isinstance(ref, str) or ref not in fragments:
            raise SectionError(f"{where}: `placeholder_ref` `{ref}` names no fragment in this directory")
        placeholder = fragments[ref]
    else:
        placeholder = ""

    if ownership == "template":
        if not placeholder.strip():
            # It would silently blank the section on every run, and "always
            # empty" is not a thing anyone declares this way.
            raise SectionError(f"{where}: `ownership: template` needs a non-empty `placeholder`")
        # A template section's content *is* its placeholder, by construction.
        # Setting this at load beats leaving an author to remember it: the
        # failure is silent in the direction that matters, and a permanent
        # `sections.unfilled` trains a reader to ignore the code.
        seeded = True

    return SectionSpec(
        heading=raw_heading.strip(),
        level=level,
        required=required,
        seeded_is_complete=seeded,
        placeholder=placeholder,
        ownership=ownership,
    )


def _type_sections(name: str, data: Mapping[str, Any], fragments: Mapping[str, str]) -> TypeSections:
    additional = data.get("additional_sections", True)
    if not isinstance(additional, bool):
        raise SectionError(f"{name}: `additional_sections` must be true or false, got {additional!r}")

    raw = data.get("sections", [])
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise SectionError(f"{name}: `sections` must be a list, got {type(raw).__name__}")

    specs: list[SectionSpec] = []
    seen: dict[str, str] = {}
    for position, entry in enumerate(raw):
        where = f"{name}: sections[{position}]"
        if not isinstance(entry, Mapping):
            raise SectionError(f"{where}: must be a mapping, got {type(entry).__name__}")
        spec = _section_spec(where, entry, fragments)
        # The rule matches casefolded and stripped, so two headings that fold
        # together are one section as far as every consumer is concerned --
        # and the second would be permanently unreachable.
        key = spec.heading.casefold()
        if key in seen:
            raise SectionError(f"{where}: heading `{spec.heading}` collides with `{seen[key]}` under casefold")
        seen[key] = spec.heading
        specs.append(spec)

    return TypeSections(
        sections=tuple(specs),
        additional_sections=additional,
        frontmatter=_frontmatter(name, data),
    )


def _index_sections(where: str, data: Mapping[str, Any], fragments: Mapping[str, str]) -> TypeSections:
    """One directory's declaration.

    **Frontmatter ownership is refused rather than ignored.** §8 gives a
    bundle-root index exactly one legal key, `okf_version`; widening that is
    a spec question, not a declaration's. Silently dropping a `frontmatter:`
    block here would leave an author believing a grant they do not have.
    """
    if "frontmatter" in data:
        raise SectionError(
            f"{where}: `frontmatter` is not declarable for a directory index -- OKF v0.2 §8 gives a "
            f"bundle-root index exactly one legal key, `okf_version`"
        )
    return _type_sections(where, data, fragments)


def _directories(name: str, data: Mapping[str, Any], fragments: Mapping[str, str]) -> dict[str, TypeSections]:
    """The `directories:` block of one file, keyed by normalized directory id.

    The id is stripped of whitespace and slashes so `"packages/"` keys
    `"packages"` -- the key `okf_io.Bundle.indexes` itself uses. Two spellings
    of one directory in a single file are refused rather than silently
    collapsed, for the reason a duplicate `owned` key is.
    """
    raw = data.get("directories", {})
    if not isinstance(raw, Mapping):
        raise SectionError(f"{name}: `directories` must be a mapping, got {type(raw).__name__}")
    found: dict[str, TypeSections] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise SectionError(f"{name}: directory id {key!r} must be a string, got {type(key).__name__}")
        directory = key.strip().strip("/")
        where = f"{name}: directories[{directory!r}]"
        if directory in found:
            raise SectionError(f"{where}: this directory is already declared in this file")
        if not isinstance(value, Mapping):
            raise SectionError(f"{where}: must be a mapping, got {type(value).__name__}")
        found[directory] = _index_sections(where, value, fragments)
    return found


def load_sections(path: str | Path) -> SectionSet:
    """Read every declaration file directly under *path*.

    Accepts a `str` as well as a `Path`, coerced first -- the forgiveness
    `load_vocabulary` and `load_schemas` both extend. The directory is read
    non-recursively: a declaration set is a directory of declarations, not a
    tree.

    An **underscore-prefixed file is a fragment source, never a type**. It is
    read so a `placeholder_ref` resolves and kept out of the dispatch table so
    no concept can match `type: _common` -- exactly what `load_schemas` does
    with `_base.schema.yaml`.

    Raises `SectionError`, naming the file, for anything wrong with a
    declaration's encoding, syntax or shape, for two files claiming one type
    name, for two files declaring one fragment name, and for a directory
    holding no declaration files at all -- overwhelmingly a wrong path, and
    raising keeps `section_rule` free of an empty-set special case.
    Propagates `OSError` for a path that is not there: a missing directory is
    a different caller error and should not be dressed up as a format one.
    """
    root = Path(path)
    parsed: list[tuple[str, Mapping[str, Any]]] = []
    fragments: dict[str, str] = {}
    fragment_sources: dict[str, str] = {}

    for entry in sorted(root.iterdir()):
        name = entry.name
        if _type_name(name) is None or not entry.is_file():
            continue
        data = _read(entry)
        if not isinstance(data, Mapping):
            raise SectionError(f"{name}: a declaration must be a mapping, got {type(data).__name__}")
        for key, value in _file_fragments(name, data).items():
            # Not in the spec's refusal list, and deliberate: a fragment
            # silently overwritten by directory iteration order is exactly the
            # drift §3.5 says the fragment mechanism exists to make
            # structurally impossible.
            if key in fragment_sources:
                raise SectionError(f"{name}: fragment `{key}` is already declared by `{fragment_sources[key]}`")
            fragments[key] = value
            fragment_sources[key] = name
        parsed.append((name, data))

    if not parsed:
        raise SectionError(f"{root}: no declaration files found; expected one or more of {list(SECTION_SUFFIXES)}")

    # Every `_`-prefixed file's `directories:` merges into one mapping, so the
    # three tier-3 packages that share a bundle can each declare their own
    # root section without arbitrating a single shared `_index.yaml` -- the
    # `tags.yaml` problem, not repeated. Two files claiming one
    # `(directory, heading)` pair is refused at load, the disjointness move
    # `FrontmatterOwnership` already makes for `owned`/`provenance`.
    #
    # `additional_sections` is not merged: it defaults to `True` and no file
    # may claim "no additional sections" over an index it shares with another.
    indexes: dict[str, list[SectionSpec]] = {}
    index_sources: dict[tuple[str, str], str] = {}
    for name, data in parsed:
        if "directories" not in data:
            continue
        if not name.startswith("_"):
            raise SectionError(
                f"{name}: `directories` may only be declared by an underscore-prefixed file -- a typed "
                f"file's sections belong to its type, so a directory declaration here is ambiguous"
            )
        for directory, declaration in _directories(name, data, fragments).items():
            bucket = indexes.setdefault(directory, [])
            for spec in declaration.sections:
                index_key = (directory, spec.heading.casefold())
                if index_key in index_sources:
                    raise SectionError(
                        f"{name}: heading `{spec.heading}` for directory `{directory}` is already declared "
                        f"by `{index_sources[index_key]}`; two files may not declare one section of one index"
                    )
                index_sources[index_key] = name
                bucket.append(spec)

    types: dict[str, TypeSections] = {}
    sources: dict[str, str] = {}
    for name, data in parsed:
        if name.startswith("_"):
            continue
        type_name = _type_name(name)
        assert type_name is not None  # every name in `parsed` already passed this
        if type_name in sources:
            raise SectionError(f"{name}: type `{type_name}` is already claimed by `{sources[type_name]}`")
        types[type_name] = _type_sections(name, data, fragments)
        sources[type_name] = name

    return SectionSet(
        types=MappingProxyType(dict(sorted(types.items()))),
        sources=MappingProxyType(dict(sorted(sources.items()))),
        fragments=MappingProxyType(dict(sorted(fragments.items()))),
        root=root,
        indexes=MappingProxyType(
            {directory: TypeSections(sections=tuple(specs)) for directory, specs in sorted(indexes.items())}
        ),
    )
