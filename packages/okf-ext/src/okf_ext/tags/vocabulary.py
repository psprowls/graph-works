"""The controlled vocabulary: its file format and its loader.

**Nothing is auto-discovered.** The caller passes a path or the `Vocabulary`
itself. `_tags.yaml` is a documented convention that tools may default to, not
magic this library performs.

Auto-discovery mirroring okf-schema's `_schema/` was rejected: it invents
format SPEC.md does not define and makes behaviour change because a file
appeared — a trap this project has been caught by before. Under this design
a bundle
carrying a `_tags.yaml` is an entirely ordinary OKF bundle to every other
reader, and a house rule only fires when someone names it.

The rule that turns a loaded `Vocabulary` into `Finding`s against a bundle
(`vocabulary_rule`) is a separate concern; this module only knows how to
read the file.

**Unknown keys are rejected, top level and per entry.** This is a small
hand-edited house-rule file with no schema and no editor support to catch a
typo, so an unrecognized key is far more likely `replace_by` for
`replaced_by` than a forward-compatible extension a future reader should
ignore. A silently-ignored key is the same silent-no-op failure class as the
`deprecated`-truthiness bug: the entry still loads, just not the way the
author meant. The cost of this strictness: adding a new field to the format
later is a breaking change for any file that already happens to use that
name for something else — accepted deliberately, in exchange for a typo
becoming an instant, legible error today.

**Tag names are compared exactly, never normalized.** `Metric` and `metric`
are two distinct entries as far as this loader is concerned, even though
`okf_ext.tags.normalize.canonical` would fold them together. The vocabulary
is the authority on exact spelling; a vocabulary that declares both is
itself the mess `vocabulary_rule` and normalization-driven renames exist to
surface, and canonicalizing here would quietly hide that
from the very tools meant to catch it.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

from okf_io import Finding, Rule, RuleContext
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from okf_ext.context import ExtContext
from okf_ext.tags.model import Vocabulary
from okf_ext.tags.normalize import canonical

#: The only format version this release reads. An unknown one raises rather
#: than degrading: a vocabulary the loader half-understands would silently
#: mis-validate a whole bundle.
SUPPORTED_VERSION = 1

#: The conventional filename. A default tools may offer, never one this module
#: reaches for.
VOCABULARY_FILENAME = "_tags.yaml"

#: The only keys a document may carry, top level and per entry. Anything else
#: is rejected — see the module docstring for why strictness is the default
#: here rather than ignoring what this loader does not recognize.
_TOP_LEVEL_KEYS = frozenset({"version", "tags"})
_ENTRY_KEYS = frozenset({"name", "description", "deprecated", "replaced_by"})


class VocabularyError(ValueError):
    """A vocabulary file the caller got wrong.

    Caller **configuration** is always an exception; bundle **content** never
    is (spec §10). Degrading a malformed vocabulary quietly would hide the
    caller's mistake and mis-validate every document in the bundle.

    Subclasses `ValueError` so a caller catching either works.
    """


def _reject_unknown_keys(mapping: Mapping[Any, Any], allowed_keys: frozenset[str], where: str, noun: str) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed_keys)
    if unknown:
        raise VocabularyError(
            f"{where}: unknown {noun} key(s) {unknown!r}; only {sorted(allowed_keys)!r} are recognized"
        )


def _entries(data: Any, source: str) -> Sequence[Any]:  # noqa: ANN401 -- arbitrary parsed YAML
    if not isinstance(data, Mapping):
        raise VocabularyError(f"{source}: must be a YAML mapping, got {type(data).__name__}")
    _reject_unknown_keys(data, _TOP_LEVEL_KEYS, source, "top-level")
    if "version" not in data:
        raise VocabularyError(f"{source}: missing required key `version`")
    version = data["version"]
    # `bool` is a subclass of `int` in Python, and `1.0 == 1`, so a naive
    # `!=` comparison would accept `version: true` and `version: 1.0` as the
    # integer `1`. Type first, compare second — the same reasoning already
    # applied to `deprecated` and `replaced_by`.
    if not isinstance(version, int) or isinstance(version, bool):
        raise VocabularyError(f"{source}: `version` must be an integer, got {version!r}")
    if version != SUPPORTED_VERSION:
        raise VocabularyError(
            f"{source}: unsupported vocabulary version {version!r}; this release reads version {SUPPORTED_VERSION}"
        )
    raw = data.get("tags", [])
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise VocabularyError(f"{source}: `tags` must be a sequence, got {type(raw).__name__}")
    return raw


def load_vocabulary(path: str | Path) -> Vocabulary:
    """Read the vocabulary at *path*.

    Accepts a `str` as well as a `Path`, coerced with `Path(path)` before
    anything else -- the most forgiving choice, and the one a caller passing
    `load_vocabulary("some/path.yaml")` actually expects. Every other caller
    error in this module surfaces as a legible `VocabularyError`; before this
    coercion, a `str` path raised a bare `AttributeError` at `path.name`
    instead.

    Raises `VocabularyError` for anything wrong with the file's content or
    shape — including bytes that are not valid UTF-8, which is a content
    problem like any other — and propagates `OSError` for a path that is not
    there — a missing file is a different caller error and should not be
    dressed up as a format one.

    Nothing here reads a path the caller did not give it: *path* is read
    exactly once, and no directory is scanned or defaulted to.
    """
    path = Path(path)
    source = path.name
    raw_bytes = path.read_bytes()
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VocabularyError(f"{source}: not valid UTF-8 at byte offset {exc.start}: {exc.reason}") from exc
    try:
        data = YAML(typ="safe").load(text)
    except YAMLError as exc:
        raise VocabularyError(f"{source}: not valid YAML: {exc}") from exc

    allowed: set[str] = set()
    deprecated: dict[str, str | None] = {}
    descriptions: dict[str, str] = {}
    replacements: dict[str, str] = {}

    for position, entry in enumerate(_entries(data, source)):
        where = f"{source}: tags[{position}]"
        if not isinstance(entry, Mapping):
            raise VocabularyError(f"{where}: must be a mapping, got {type(entry).__name__}")
        _reject_unknown_keys(entry, _ENTRY_KEYS, where, "entry")
        raw_name = entry.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise VocabularyError(f"{where}: missing a non-empty `name`")
        name = raw_name.strip()
        # This check is what makes `allowed`/`deprecated` disjoint: a name
        # already claimed by either dict below can never be claimed again,
        # so no tag can end up live and deprecated at once.
        if name in allowed or name in deprecated:
            raise VocabularyError(f"{where}: `{name}` is declared twice")

        description = entry.get("description")
        if description is not None:
            if not isinstance(description, str):
                raise VocabularyError(f"{where}: `description` must be a string, got {description!r}")
            descriptions[name] = description

        # `deprecated` gates a rename instruction that the vocabulary rule turns
        # into a `Finding` and the rename planner into an actual file rewrite, so
        # it must be a
        # real boolean. A truthiness test would let a hand-written or
        # tool-stringified `deprecated: "false"` (or `"no"`, or `0`/`1`)
        # silently deprecate a tag — the worst failure mode this package has.
        raw_deprecated = entry.get("deprecated", False)
        if not isinstance(raw_deprecated, bool):
            raise VocabularyError(f"{where}: `deprecated` must be true or false, got {raw_deprecated!r}")

        raw_replacement = entry.get("replaced_by")
        if raw_replacement is not None and not isinstance(raw_replacement, str):
            raise VocabularyError(f"{where}: `replaced_by` must be a string, got {raw_replacement!r}")
        replacement = raw_replacement.strip() if isinstance(raw_replacement, str) else None
        # An empty (or all-whitespace) `replaced_by` is not "replaced by
        # nothing" — that shape is spelled by omitting the key entirely — so
        # treat it the same as any other malformed value rather than as a
        # rename to `""`.
        if raw_replacement is not None and not replacement:
            raise VocabularyError(f"{where}: `replaced_by` must not be empty")

        if raw_deprecated:
            deprecated[name] = replacement
            if replacement is not None:
                replacements[name] = replacement
        elif replacement is not None:
            raise VocabularyError(
                f"{where}: `replaced_by` is only meaningful on a `deprecated: true` entry; "
                f"`{name}` is live, so the instruction would never fire"
            )
        else:
            allowed.add(name)

    for name, replacement in sorted(replacements.items()):
        if replacement not in allowed:
            raise VocabularyError(f"{source}: `{name}` is replaced_by `{replacement}`, which is not an allowed tag")

    return Vocabulary(
        allowed=frozenset(allowed),
        deprecated=MappingProxyType(dict(sorted(deprecated.items()))),
        descriptions=MappingProxyType(dict(sorted(descriptions.items()))),
        source=source,
    )


#: The topic prefix this rule set claims. Verified free: the built-in topics
#: are computation, frontmatter, legacy, lifecycle, links, provenance,
#: reserved and trust, and `validate()` raises the moment an external rule
#: emits a colliding prefix. The claim is both available and mechanically
#: protected.
TOPIC = "tags"

CODES = (
    "tags.unknown",  # tag absent from the vocabulary entirely
    "tags.deprecated",  # tag declared, but marked deprecated
    "tags.non-canonical",  # tag differs from its own canonical form
)

#: `rule()` below yields these, not the string literals in `CODES` directly --
#: unpacked from `CODES` itself rather than re-typed, so a code-string edit to
#: one cannot silently drift from the other. `test_tags_rule.py` also asserts
#: every code the rule can emit is a member of `CODES`, and that every member
#: of `CODES` starts with `TOPIC + "."`, as a second, independent guard.
_CODE_UNKNOWN, _CODE_DEPRECATED, _CODE_NON_CANONICAL = CODES


def vocabulary_rule(vocab: Vocabulary, ctx: ExtContext | None = None) -> Rule:
    """Build an `okf_io.Rule` that checks tags against *vocab*.

        validate(bundle, today=..., extra_rules=[vocabulary_rule(vocab)])

    Reads tags from the typed view (`doc.fm.tags`), never from `fm_raw` — the
    view already coerced them and a second reading is a second thing to keep in
    sync.

    **Every code is `warn`.** See the module docstring for why; the short
    version is that `Report.ok` is a claim about OKF v0.2 conformance, and a
    house rule has no business making a conformant bundle look otherwise.

    `tags.non-canonical` suppresses only `tags.unknown`, never `tags.deprecated`.
    When the canonical form is **allowed**, unknown is suppressed: `Data Quality`
    → `data-quality` has one problem, not two. But deprecation is never suppressed,
    because a retired tag is a separate fact the author needs regardless of
    spelling — whether the tag is `kpi` or `KPI`, the message must say it is
    retired and what to use instead.
    """
    policy = (ctx or ExtContext()).normalization

    def rule(context: RuleContext) -> Iterable[Finding]:
        for concept_id in sorted(context.bundle.concepts):
            document = context.bundle.concepts[concept_id]
            if document.parse_error is not None:
                continue
            path = f"{concept_id}.md"
            for tag in dict.fromkeys(document.fm.tags):
                form = canonical(tag, policy)
                if form != tag:
                    yield Finding(
                        code=_CODE_NON_CANONICAL,
                        severity="warn",
                        message=f"Tag `{tag}` is not canonical; its canonical form is `{form}`.",
                        spec=vocab.source,
                        path=path,
                    )
                # Check exact spelling first: a vocabulary can deprecate a specific
                # literal spelling (e.g., KPI) that is distinct from an allowed
                # canonical form (e.g., kpi). See load_vocabulary's exact-spelling policy.
                lookup = tag if tag in vocab.known else form
                if lookup in vocab.deprecated:
                    replacement = vocab.deprecated[lookup]
                    detail = f" Use `{replacement}` instead." if replacement is not None else " It has no replacement."
                    yield Finding(
                        code=_CODE_DEPRECATED,
                        severity="warn",
                        message=f"Tag `{tag}` is deprecated.{detail}",
                        spec=vocab.source,
                        path=path,
                    )
                elif lookup not in vocab.allowed:
                    message = f"Tag `{tag}` is not in the vocabulary."
                    # Suggest a close match if one exists
                    close = difflib.get_close_matches(form, sorted(vocab.known), n=1, cutoff=0.6)
                    if close:
                        message += f" Did you mean `{close[0]}`?"
                    yield Finding(
                        code=_CODE_UNKNOWN,
                        severity="warn",
                        message=message,
                        spec=vocab.source,
                        path=path,
                    )

    return rule
