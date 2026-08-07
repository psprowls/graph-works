"""Frozen values the generators capability passes between its stages.

Frozen, slotted and `MappingProxyType`-backed, matching `okf_ext.tags.model`,
`okf_ext.sections.model` and the core.

**The caller hands over parts, not a document.** A whole-document API --
render a fresh page, merge it onto the one on disk -- is closer to how the
reference entity writer works, and was rejected because it must silently
discard everything it is not allowed to write: a section rendered but
declared `prose` would vanish with no error and no report. Handing over parts
makes the same mistake loud, because everything supplied is either written or
raises -- with one stated exception. Content supplied for a `template`
section is granted rather than refused (`plan.py`'s `_WRITABLE` includes it)
and then silently discarded (`regenerate.py`'s `_content` never reads it): a
template section's content is always its declared placeholder, so there is
nothing for supplied content to mean there. That exception is deliberate and
tested -- see `test_a_template_section_ignores_whatever_the_caller_supplied`
-- not a hole in the invariant above it.

This module imports the shared layer and nothing else from its own package.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from okf_ext.writing import Skipped

_EMPTY_FM: Mapping[str, Any] = MappingProxyType({})
_EMPTY_SECTIONS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Render:
    """What a generator computed for one concept.

    **The capability owns the merge, never the render.** What a `## Sources`
    list should say is tier 3's question and depends on a code graph this
    package cannot see. What may be written into the document, and how it
    lands without disturbing a neighbouring byte, is this package's question.

    Both fields default to empty. Against a concept with **no usable
    declaration** -- no `type`, or a type the section set does not cover --
    `plan_regenerate` grants nothing and computes nothing there, so
    `Render()` is a legal no-op.

    Against a **declared** type it is not a no-op: `key_edits` rewrites
    every `owned` frontmatter key exhaustively, so an owned key `Render()`
    does not supply reads as "the run has nothing to say about this any
    more" and is **deleted** -- the
    same omission-is-the-signal rule that lets a dependency no longer
    computed disappear from the document on its own. Running a bare
    `Render()` against a declared concept deletes every one of its owned
    keys from disk.

    The way to leave a declared concept alone is to **omit it from
    `renders` entirely** -- not to hand over an empty `Render` for it.
    """

    frontmatter: Mapping[str, Any] = _EMPTY_FM
    sections: Mapping[str, str] = _EMPTY_SECTIONS


@dataclass(frozen=True, slots=True)
class KeyEdit:
    """One frontmatter key, and what happens to it.

    `value` is unread for a `delete` and is `None` by convention there --
    carrying the doomed value would invite a caller to read it as "what it
    will become".
    """

    key: str
    action: Literal["set", "delete"]
    value: Any = None


@dataclass(frozen=True, slots=True)
class SectionEdit:
    """One rewritten section. `line` is the first line the edit claims --
    the section's `body_start`, never its heading, which this never touches."""

    heading: str
    line: int  # 1-based, body-relative


@dataclass(frozen=True, slots=True)
class Regeneration:
    """One document's whole regeneration, ready to write.

    `after` carries the whole new body for `SectionSplice`'s stated reason: it
    makes `apply` a pure digest-check-then-write, with no chance of the plan
    and the apply disagreeing about what an edit means.

    `digest` is over the **whole** body, for `RowSplice`'s other stated
    reason: the splice is a deterministic function of all of it, and a change
    anywhere can move the lines an edit lands on, so "the section still looks
    the same" is a weaker check than it appears. There is deliberately no
    frontmatter digest -- see `okf_ext.generators.apply`.
    """

    concept_id: str
    path: str  # bundle-relative posix
    key_edits: tuple[KeyEdit, ...]
    section_edits: tuple[SectionEdit, ...]  # document order
    digest: str
    after: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RegenerationPlan:
    """A preview you can inspect and filter before anything is written.

    A value rather than a `dry_run=True` flag, for the reason `RenamePlan`
    gives and `SectionPlan` repeats: "apply 38 of these 40" is a thing a
    boolean cannot express. **Idempotence surfaces as an empty plan** -- a
    document already carrying every value the run computed contributes no
    `Regeneration` at all, and `is_empty` is the signal.
    """

    root: Path  # the bundle this was planned against
    regenerations: tuple[Regeneration, ...]
    skipped: tuple[Skipped, ...]

    @property
    def is_empty(self) -> bool:
        return not self.regenerations

    @property
    def concept_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.concept_id for item in self.regenerations}))
