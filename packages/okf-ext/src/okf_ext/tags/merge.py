"""Merging a package's tag definitions into a vault's `_tags.yaml`.

**A text splice, not a dump.** `_tags.yaml` is human-owned in the strongest
sense this codebase has: the scaffold seeds it with an explanatory comment
block, and from that moment it is the vault's file -- hand-edited, re-grouped,
commented. ADR-0009 already answers this shape of problem for `index.md`:
`update_index()` reconciles, it does not regenerate. The machine owns *which*
entries appear; the human owns *what they say*; every other byte is copied
through. This module is that rule applied to the vocabulary.

A safe-load -> mutate -> `dump()` would destroy the human's comments and
formatting on the first merge, and a ruamel round-trip would add a second
ruamel mode, inherit the emitter as an unstable contract, and reformat the
file in ways nobody asked for. Neither is worth a merge whose whole payload is
two entries.

**Where the refusals differ from okf-io's splice.** okf-io falls back to
re-emitting a whole frontmatter block when it cannot anchor the edited lines.
This module does not: the unit here is the entire file, it belongs to the
human, and a machine that cannot read its shape has no business rewriting it.
Stated as a contract rather than left as an accident: *a vocabulary merge is
byte-exact outside the entries it adds, or it does not happen.*

**Sixth module in the capability**, alongside `inventory`, `model`,
`normalize`, `rename` and `vocabulary`. It imports the shared `okf_ext.body`,
`okf_ext.splice` and `okf_ext.writing` layers and its own capability's model
and loader. It imports no sibling capability, and never the top-level
`okf_ext` package -- which is why this lives here rather than in
`okf_ext.bundle`, whose install path cannot reach `load_vocabulary` at all.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from okf_ext.body import split_lines
from okf_ext.splice import assemble, dominant_newline, has_trailing_newline, insert, replace
from okf_ext.tags.model import TagDefinition, TagDrift, Vocabulary, VocabularyPlan
from okf_ext.tags.vocabulary import VocabularyError, load_vocabulary
from okf_ext.writing import ApplyResult, PendingWrite, WriteFailure, write_all

#: A value safe to write as a YAML plain scalar. Deliberately narrow rather
#: than exhaustive: it excludes `:` and `#` entirely -- the two characters
#: that make an unquoted scalar mean something else -- and requires an
#: alphanumeric first character so nothing is read as an indicator. Anything
#: outside it is `json.dumps`ed, which is always a valid YAML double-quoted
#: scalar. A description with a comma is quoted unnecessarily; that costs two
#: characters and buys never having to reason about flow context.
_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._/'-]*")

#: The words YAML 1.1 reads back as a bool or a null. Compared case-folded,
#: because `No`, `NO` and `no` are all the boolean.
_RESERVED = frozenset({"y", "yes", "n", "no", "true", "false", "on", "off", "null", "none", "~"})

#: A `tags:` key at column 0, and whatever follows it on the same line. Only
#: two remainders are recognized: nothing (a block sequence follows) and `[]`
#: (the scaffold's flow-style empty). `tags: [something]` is a shape a human
#: chose, and rewriting it into block style is a reformat nobody asked for.
_TAGS_KEY = re.compile(r"^tags:[ \t]*(\[\])?[ \t]*$")

#: The default sequence indentation, used only when the block carries no
#: entry to copy it from. Two spaces is what `_tags.yaml` fixtures and the
#: scaffold's own commented example both use.
_DEFAULT_INDENT = "  "


@dataclass(frozen=True, slots=True)
class _Anchor:
    """Where a merge's new entries go, and how they are laid out.

    Private: the anchor is an implementation detail of the splice, and a
    caller inspecting one would be reading a shape this module reserves the
    right to change.
    """

    key_line: int  # 1-based line carrying `tags:`
    insert_line: int  # 1-based line the new entries are inserted *before*
    indent: str  # the indentation the existing entries use
    flow_empty: bool  # whether `key_line` reads `tags: []` and must be rewritten


def _locate(text: str) -> _Anchor | None:
    """Where new entries belong in *text*, or `None` when its shape is one
    this merge will not touch.

    Declining is a refusal, not a fallback. See the module docstring: the
    unit here is the whole file, and a shape the locator cannot read is a
    shape it must not rewrite.
    """
    lines = split_lines(text)
    key_index: int | None = None
    for index, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        if index == 0:
            # A UTF-8 BOM sits ahead of the first line's own bytes. Stripping
            # it here rather than in the caller keeps the anchor's line
            # numbers the file's own -- the splice re-attaches the BOM by
            # never touching line 1's leading bytes at all.
            bare = bare.lstrip("﻿")
        match = _TAGS_KEY.match(bare)
        if match is not None:
            key_index = index
            flow_empty = match.group(1) is not None
            break
    if key_index is None:
        return None

    if flow_empty:
        return _Anchor(key_line=key_index + 1, insert_line=key_index + 2, indent=_DEFAULT_INDENT, flow_empty=True)

    last = key_index
    indent: str | None = None
    for index in range(key_index + 1, len(lines)):
        bare = lines[index].rstrip("\r\n")
        if not bare.strip():
            # A blank line may sit between two groups the human wrote. It
            # does not extend the block on its own -- only real content does
            # -- so a trailing blank stays below the insert point.
            continue
        if not bare[0].isspace():
            break  # column 0: the mapping moved on, and so has the block
        stripped = bare.lstrip()
        if stripped.startswith("#"):
            # An indented comment sitting under the last entry is a note
            # about it, not another entry. Skipping it keeps new entries
            # above the human's commentary rather than orphaning it.
            continue
        if indent is None and stripped.startswith("- "):
            indent = bare[: len(bare) - len(stripped)]
        last = index

    return _Anchor(
        key_line=key_index + 1,
        insert_line=last + 2,
        indent=indent if indent is not None else _DEFAULT_INDENT,
        flow_empty=False,
    )


def _scalar(value: str) -> str:
    """*value* as a YAML scalar that reads back as itself.

    Plain when it safely can be -- this file is read by humans, and a
    vocabulary of quoted strings is worse to edit than one without them --
    and `json.dumps` otherwise. That call is the one `code_wiki_okf.init`
    already makes for `_repositories.yaml`, and for the reason documented
    there: an unquoted plain scalar breaks on an ordinary string containing
    `" #"` (read back truncated at the comment) or `": "` (unparseable YAML).
    """
    if _PLAIN.fullmatch(value) and not value.endswith(" ") and value.lower() not in _RESERVED:
        return value
    return json.dumps(value)


def _render_entry(definition: TagDefinition, indent: str) -> list[str]:
    """*definition* as bare, unterminated lines at *indent*.

    Field order is fixed -- `name`, `description`, `deprecated`,
    `replaced_by` -- so two runs of the same merge produce the same bytes.
    `deprecated: false` and an absent `replaced_by` are omitted: the loader
    already treats both as defaults, and writing them would be noise in a
    file a human reads.

    *indent* is the indentation of the `- ` marker itself; continuation keys
    sit two columns further in, which is where `- ` leaves them.
    """
    lines = [
        f"{indent}- name: {_scalar(definition.name)}",
        f"{indent}  description: {_scalar(definition.description)}",
    ]
    if definition.deprecated:
        lines.append(f"{indent}  deprecated: true")
    if definition.replaced_by is not None:
        lines.append(f"{indent}  replaced_by: {_scalar(definition.replaced_by)}")
    return lines


def _refusal(path: str, error: str) -> WriteFailure:
    """A merge refusal. Always `foreign-content`: every refusal this module
    makes is "a file I own exists with content I did not write" -- narrowed
    to one tag or widened to the whole file."""
    return WriteFailure(path=path, kind="foreign-content", error=error)


def _check_definitions(definitions: Sequence[TagDefinition]) -> None:
    """Reject definitions that would render a file `load_vocabulary` rejects.

    Caller **configuration** is always an exception in this capability; bundle
    **content** never is. These three are configuration: a package's own
    constant, wrong. Writing them would leave a `_tags.yaml` the very next
    install refuses whole -- our own output breaking our own idempotence --
    so they fail here, loudly, at the call that would have written them.
    """
    seen: set[str] = set()
    for position, definition in enumerate(definitions):
        where = f"definitions[{position}]"
        if not definition.name.strip():
            raise VocabularyError(f"{where}: `name` must be non-empty")
        if definition.name in seen:
            raise VocabularyError(f"{where}: `{definition.name}` is declared twice")
        seen.add(definition.name)
        if definition.replaced_by is not None and not definition.deprecated:
            raise VocabularyError(
                f"{where}: `replaced_by` is only meaningful on a `deprecated: true` entry; "
                f"`{definition.name}` is live, so the instruction would never fire"
            )


def _classify(
    definition: TagDefinition,
    vocab: Vocabulary,
) -> tuple[Literal["added", "unchanged", "refused"], TagDrift | WriteFailure | None]:
    """One definition against the file: `"added"`, `"unchanged"` or `"refused"`.

    The verdict is a `Literal`, not a bare `str`, so a call site pairing
    `"unchanged"` with a `WriteFailure` -- or `"refused"` with anything but
    one -- is a type error mypy can catch, not a bug that ships silently.

    Per-field, deliberately (D-4). `description` is prose a human reads;
    `deprecated` and `replaced_by` are machine instructions. Treating all
    three alike has a bad failure mode: reword the description of a
    package-contributed tag in your own file, and every subsequent install of
    that package refuses that tag forever.
    """
    name = definition.name
    if name not in vocab.known:
        return "added", None

    theirs_deprecated = name in vocab.deprecated
    theirs_replaced_by = vocab.deprecated.get(name)
    if theirs_deprecated != definition.deprecated or theirs_replaced_by != definition.replaced_by:
        return "refused", _refusal(
            name,
            f"`{name}` is already declared in the vault with deprecated={theirs_deprecated!r}, "
            f"replaced_by={theirs_replaced_by!r}, but this package contributes deprecated="
            f"{definition.deprecated!r}, replaced_by={definition.replaced_by!r}; "
            "the vocabulary is the vault's -- resolve it by hand",
        )

    theirs_description = vocab.descriptions.get(name, "")
    if theirs_description != definition.description:
        return "unchanged", TagDrift(name=name, ours=definition.description, theirs=theirs_description)
    return "unchanged", None


def plan_vocabulary_merge(
    path: str | Path,
    definitions: Sequence[TagDefinition],
) -> VocabularyPlan:
    """Preview merging *definitions* into the vocabulary at *path*.

    Named `plan_vocabulary_merge` and not `plan_merge`: that name is taken by
    `okf_ext.tags.rename.plan_merge`, which merges several *tags* into one by
    rewriting concepts. Two functions called `plan_merge` in one capability,
    meaning two unrelated things, is a collision worth four extra characters.

    Writes nothing -- `apply_vocabulary` does that.

    Raises `OSError` for a *path* that is not there and `VocabularyError` for
    a malformed *definitions* sequence. Both are caller error. Nothing about
    the file's **content** raises: a malformed vocabulary, or one whose shape
    the splice cannot anchor, comes back as a single refusal with
    `after == before`.
    """
    _check_definitions(definitions)
    path = Path(path)

    def refuse_whole(before: str, error: str) -> VocabularyPlan:
        return VocabularyPlan(
            path=path,
            before=before,
            after=before,
            added=(),
            unchanged=(),
            drift=(),
            refusals=(_refusal(path.name, error),),
        )

    try:
        vocab = load_vocabulary(path)
    except VocabularyError as exc:
        # `load_vocabulary` already read *path* once and knows why it failed
        # -- including bytes that are not valid UTF-8, a content problem like
        # any other (see its own docstring). There is no trustworthy strict
        # decode to fall back on here, so `errors="replace"` gives a
        # presentable `before`/`after` for a refusal that writes nothing and
        # whose text is therefore never re-encoded.
        before = path.read_bytes().decode("utf-8", errors="replace")
        return refuse_whole(before, f"{exc}; a malformed vocabulary is the vault's to fix")

    # `load_vocabulary` just decoded these exact bytes as UTF-8 successfully,
    # so this cannot raise `UnicodeDecodeError` -- unlike a decode attempted
    # *before* that validation had run, which is the bug this replaced.
    before = path.read_bytes().decode("utf-8")

    anchor = _locate(before)
    if anchor is None:
        return refuse_whole(
            before,
            "no `tags:` block this merge can anchor to; a vocabulary merge is byte-exact "
            "outside the entries it adds, or it does not happen",
        )

    to_add: list[TagDefinition] = []
    unchanged: list[str] = []
    drift: list[TagDrift] = []
    refusals: list[WriteFailure] = []
    for definition in definitions:
        verdict, detail = _classify(definition, vocab)
        if verdict == "added":
            to_add.append(definition)
        elif verdict == "refused":
            assert isinstance(detail, WriteFailure)  # `_classify`'s own contract
            refusals.append(detail)
        else:
            unchanged.append(definition.name)
            if isinstance(detail, TagDrift):
                drift.append(detail)

    after = _splice(before, anchor, to_add) if to_add else before
    return VocabularyPlan(
        path=path,
        before=before,
        after=after,
        added=tuple(definition.name for definition in to_add),
        unchanged=tuple(unchanged),
        drift=tuple(drift),
        refusals=tuple(refusals),
    )


def _splice(before: str, anchor: _Anchor, to_add: Sequence[TagDefinition]) -> str:
    """*before* with *to_add* rendered into its `tags:` block, and nothing else.

    Comments, blank lines, the human's grouping and ordering, the file's
    dominant line ending and its trailing-newline state all survive because
    nothing here rewrites a line it did not add -- with exactly one exception,
    the scaffold's `tags: []`, which has to become `tags:` before a block
    sequence can follow it.
    """
    # A BOM is not part of any line's content. Split it off before the line
    # work and put it back after, so line 1 is the same string here as it was
    # to the locator.
    prefix, body = (before[:1], before[1:]) if before.startswith("﻿") else ("", before)

    lines = list(split_lines(body))
    newline = dominant_newline(body)
    trailing = has_trailing_newline(lines)
    entries = [line for definition in to_add for line in _render_entry(definition, anchor.indent)]

    if anchor.flow_empty:
        edited = replace(lines, anchor.key_line, anchor.key_line, ["tags:", *entries], newline)
    else:
        edited = insert(lines, anchor.insert_line, entries, newline)
    return prefix + assemble(edited, newline, trailing)


def _nothing_to_sync() -> None:
    """`PendingWrite.on_written`'s hook, deliberately empty.

    The other capabilities use it to keep an in-memory `Document` in step with
    disk after its own commit. This one holds no such state: a vocabulary is
    read fresh from the file on every plan, so there is nothing to swap and
    nothing that could fall out of step.
    """


def apply_vocabulary(plan: VocabularyPlan) -> ApplyResult:
    """Write *plan*, or write nothing when it adds nothing.

    A separate function rather than an overload of `okf_ext.tags.apply`:
    `okf_ext.bundle.apply` accepts a union because `ScaffoldPlan` and
    `InstallPlan` share all five of their fields, and `RenamePlan` and
    `VocabularyPlan` share none of theirs.

    The plan's own refusals -- per-tag conflicts, or a whole-merge refusal --
    are merged into the result rather than reported separately, so a caller
    has one list of everything that did not land.
    """
    if plan.is_empty:
        # Includes every whole-merge refusal: those set `after == before` and
        # add nothing, so there is no write to attempt and the refusal is the
        # entire result.
        return ApplyResult(written=(), failed=plan.refusals, skipped=())

    pending = [
        PendingWrite(
            member="_tags.yaml",
            path=plan.path,
            rendered=plan.after,
            on_written=_nothing_to_sync,
        )
    ]
    return write_all(pending, failed=plan.refusals)
