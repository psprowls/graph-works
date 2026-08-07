"""Read a directory of section declarations. The only filesystem access here.

**Nothing is auto-discovered.** `load_sections(path)` reads the directory the
caller names and no other, following `load_vocabulary` and `load_schemas`.
`_sections/` remains a documented convention tools may default to --
`DEFAULT_SECTIONS_DIRNAME` and `okf_ext.sections.DEFAULT_IGNORE` exist for
callers who want it -- not magic this library performs. Because the path is
explicit, declarations need not live inside the bundle they describe.

**Unknown keys are tolerated, unlike `load_vocabulary`.** That loader rejects
them because a typo in a hand-edited house-rule file is likelier than a
forward-compatible extension. Here the opposite is true and stated: the filed
`generators` capability adds an ownership field to `SectionSpec`, and the
design spec calls that an *additive* change -- which it is not if a
declaration written today against a newer field refuses to load. Type errors
on keys this loader *does* know are still refused.

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

from okf_ext.sections.model import SectionError, SectionSet, SectionSpec, TypeSections

#: The conventional directory name. A default tools may offer, never one this
#: module reaches for.
DEFAULT_SECTIONS_DIRNAME = "_sections"

#: Recognised suffixes. The filename stem is the type name -- there is no
#: `type:` key, matching `_schema/<type>.schema.yaml`.
SECTION_SUFFIXES = (".yaml", ".yml")

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

    return SectionSpec(
        heading=raw_heading.strip(),
        level=level,
        required=required,
        seeded_is_complete=seeded,
        placeholder=placeholder,
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

    return TypeSections(sections=tuple(specs), additional_sections=additional)


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
    )
