"""The typing decision, validated rather than made.

The package owns the vocabulary, the validation and the placement, and never
the judgment (spec §4). An agent decides which of `RUBRIC`'s four a page is and
says why; this turns that decision into a `Classification` or refuses it.

Nothing here raises. A bad type is a value, matching okf-io's posture on the
content path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from okf_ext.schemas import SchemaSet

from doc_wiki_okf.diataxis.pages import default_concept_id
from doc_wiki_okf.diataxis.rubric import TYPE_NAMES

#: Why a decision was refused. Closed: every refusal is one of these five.
UnclassifiedReason = Literal[
    "unknown-type",
    "undeclared-type",
    "no-title",
    "no-rationale",
    "undecided",
]


@dataclass(frozen=True, slots=True)
class Classification:
    """A typing decision that survived validation.

    `decided_by` is carried, not interpreted: C3 names the deciding actor in the
    OKF §5.2 `verified` event `okf_ext.proposals` already writes. Nothing here
    writes one.
    """

    type_name: str
    concept_id: str
    title: str
    rationale: str
    decided_by: str


@dataclass(frozen=True, slots=True)
class Unclassified:
    """A decision that was refused, and why."""

    reason: UnclassifiedReason
    detail: str


def classify(
    schema_set: SchemaSet,
    *,
    type_name: str,
    title: str,
    rationale: str,
    decided_by: str,
    allowed_types: Sequence[str] = TYPE_NAMES,
) -> Classification | Unclassified:
    """Validate a typing decision and derive where the page goes.

    *allowed_types* is the closed vocabulary the type must come from: the four
    Diátaxis types by default. A caller that files into a lane with its own type
    (the ADR lane's `Adr`) passes that type too, rather than teaching the rubric
    a type it does not classify.

    An empty or whitespace-only *type_name* is the caller **declining to
    choose**, and comes back as `undecided`. An agent that cannot tell a
    `Reference` from an `Explanation` should say so and get a refusal it can act
    on, not pick one to satisfy a signature.

    A blank *rationale* is a refusal, not a warning. `effective_kind()`'s bug
    was that a blank value folded silently to `concept`, so an untyped page and
    a page typed on purpose were indistinguishable forever.
    """
    wanted = type_name.strip()
    if not wanted:
        return Unclassified(
            reason="undecided",
            detail=f"no type chosen; expected one of {list(TYPE_NAMES)} or an explicit decline",
        )
    if wanted not in allowed_types:
        return Unclassified(
            reason="unknown-type",
            detail=f"unknown type {wanted!r}; expected one of {list(allowed_types)}",
        )
    if wanted not in schema_set.schemas:
        return Unclassified(
            reason="undeclared-type",
            detail=(
                f"{wanted}: no schema in `{schema_set.root.name}`; "
                f"the declarations were never installed into this bundle"
            ),
        )
    if not title.strip():
        return Unclassified(reason="no-title", detail="a blank title derives no concept id")
    if not rationale.strip():
        return Unclassified(
            reason="no-rationale",
            detail=f"{wanted} was chosen with no rationale; say why, so the choice stays visible",
        )
    return Classification(
        type_name=wanted,
        concept_id=default_concept_id(schema_set, type_name=wanted, title=title),
        title=title.strip(),
        rationale=rationale.strip(),
        decided_by=decided_by.strip(),
    )


__all__ = ["Classification", "Unclassified", "UnclassifiedReason", "classify"]
