"""The declaration types every consumer of a `_sections/` directory reads.

**Shared layer, not a capability.** `okf_ext.sections` seeds and validates
against this declaration; `okf_ext.generators` regenerates from it. The
independence contract forbids a capability importing a sibling, so leaving
these inside `sections` would force `generators` to duplicate the very types
whose whole value is that both capabilities agree about what a declaration
means -- and two copies is how two capabilities end up promising two
different things by the same word. That is the fourth hoist of its kind,
after `okf_ext.body`, `okf_ext.writing` and `okf_ext.splice`.

**`SectionSet` keeps its name and `_sections/` keeps its convention**, both
of which now understate the file: it declares frontmatter keys as well as
body sections. Renaming would churn a just-landed public type, a documented
directory convention, every vendored fixture declaration and every citation
in the README, and buy a better name and nothing else.

Imports stdlib only. Nothing here reads a file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class SectionError(ValueError):
    """A declaration set the caller got wrong.

    Caller **configuration** is always an exception; bundle **content** never
    is. One complaint at load beats the same confusion repeated against every
    concept in the bundle.

    Subclasses `ValueError` so a caller catching either works, mirroring
    `okf_ext.tags.VocabularyError` and `okf_ext.schemas.SchemaError`.
    """


#: Who writes a section's content, orthogonal to required/optional/undeclared.
#: That axis answers *must this exist*; this one answers *who writes it*, and
#: the two are independent -- a required section may be `prose`, an optional
#: one `generated`.
#:
#: `prose` is the **default**, and the direction of that choice is the point:
#: a declaration that says nothing about a section gets the behaviour that
#: cannot destroy anything, so the machine can only claim territory by
#: explicit grant. It is also what keeps the field additive.
Ownership = Literal["prose", "generated", "template"]


@dataclass(frozen=True, slots=True)
class FrontmatterOwnership:
    """Which frontmatter keys a generator may claim, and how.

    `owned` keys are **exhaustively rewritten**: the run's values are the
    whole truth, so an owned key present on disk that the run omits is
    deleted. That is what lets a dependency that no longer applies disappear
    on its own.

    `provenance` keys are **written when supplied and left alone when not**.
    This class exists because the reference implementation found it necessary:
    a run that does not recompute a hash must not wipe it, and folding
    provenance into `owned` makes exactly that failure silent.

    Everything undeclared is **the human's** and is never read, written or
    deleted. Defining the human set by omission makes the disjointness
    structural -- there is no second list to drift. The one disjointness that
    can still be got wrong, a key named in both lists, is refused at load,
    which is strictly stronger than a test.
    """

    owned: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SectionSpec:
    """One declared section.

    `placeholder` is **already resolved**: no `placeholder_ref` survives
    loading, which is what lets `render_skeleton` be a pure function of its
    argument with no registry to consult.

    `ownership` is `prose` unless a declaration says otherwise. A `template`
    section always loads with `seeded_is_complete=True`: its content *is* its
    placeholder by construction, so without that every required template
    section would report `sections.unfilled` forever, for being in exactly
    the state it is supposed to be in.
    """

    heading: str
    level: int = 2
    required: bool = False
    seeded_is_complete: bool = False
    placeholder: str = ""
    ownership: Ownership = "prose"


@dataclass(frozen=True, slots=True)
class TypeSections:
    """What one `type` declares. `sections` is in declaration order, which is
    what decides where an insert lands -- and nothing else (sections spec
    §5.4: reordering sections is house style, not a defect)."""

    sections: tuple[SectionSpec, ...]
    additional_sections: bool = True
    frontmatter: FrontmatterOwnership = FrontmatterOwnership()


@dataclass(frozen=True, slots=True)
class SectionSet:
    """A directory of declarations, read once.

    `types` is the dispatch table; `sources` answers "what says so" so a
    `Finding` can cite `feature.yaml` by name -- the move `Vocabulary.source`
    and `SchemaSet.sources` both make. `fragments` is the union of every
    `fragments:` mapping in the directory, `_`-prefixed files included, and is
    kept after loading so a caller can see what a `placeholder_ref` resolved
    to.
    """

    types: Mapping[str, TypeSections]  # type name -> declaration
    sources: Mapping[str, str]  # type name -> filename
    fragments: Mapping[str, str]  # fragment name -> text
    root: Path  # the directory this was read from

    @property
    def type_names(self) -> tuple[str, ...]:
        """Named `type_names` rather than `types` -- unlike `SchemaSet.types`,
        which is the property, `types` here is the mapping field the spec
        names."""
        return tuple(sorted(self.types))
