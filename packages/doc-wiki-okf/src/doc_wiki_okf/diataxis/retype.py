"""Retyping a page: a lane move and a `type:` rewrite, planned then applied.

Epic §4.4 accepted that retyping is a move rather than an edit, so
`okf_ext.moves` is a permanent dependency of this lane. The move is only half of
it: the file changes lane **and** its `type:` key changes, and neither `moves`
nor okf-io does both.

Plan-and-apply as two calls, refusals as data on the plan, an `ok` property, and
an `apply` that raises on a non-ok plan: `MovePlan`'s contract, matched exactly,
so a caller holding both does not have to hold two mental models.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Literal

from okf_ext.moves import MovePlan, MoveResult, WriteFailure, plan_move
from okf_ext.moves import apply as apply_move
from okf_ext.schemas import SchemaSet
from okf_io import Bundle
from ruamel.yaml.error import YAMLError

from doc_wiki_okf.diataxis.pages import directory_for
from doc_wiki_okf.diataxis.rubric import TYPE_NAMES

#: Why a retype was refused, by this module. Small and ours: the move's own
#: refusals keep their own vocabulary on `plan.move.refusals`, where it already
#: lives, and `ok` folds them in rather than copying them up.
RetypeRefusalKind = Literal["not-a-member", "unknown-type", "undeclared-type", "same-type"]


@dataclass(frozen=True, slots=True)
class RetypeRefusal:
    """One reason this retype will not be applied."""

    concept_id: str
    kind: RetypeRefusalKind
    detail: str


@dataclass(frozen=True, slots=True)
class RetypePlan:
    """A preview you can inspect before anything is written."""

    concept_id: str
    from_type: str
    to_type: str
    dest: str
    move: MovePlan
    refusals: tuple[RetypeRefusal, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals and self.move.ok

    def diff(self) -> str:
        """Render the plan. Writes nothing."""
        if self.refusals:
            first = self.refusals[0]
            return f"{self.concept_id}: refused ({first.kind}) -- {first.detail}"
        if not self.move.ok:
            first_move = self.move.refusals[0]
            return f"{self.concept_id}: move refused ({first_move.kind}) -- {first_move.detail}"
        return (
            f"{self.concept_id}.md -> {self.dest}\n"
            f"  type: {self.from_type} -> {self.to_type}\n"
            f"  references repaired: {len(self.move.edits)}"
        )


@dataclass(frozen=True, slots=True)
class RetypeResult:
    """What happened, with the two halves of §5.1 told apart.

    Adds no vocabulary of its own: `move` is `moves.apply`'s own result, and
    `type_written` is the one extra fact this pair produces.
    """

    move: MoveResult
    type_written: bool

    @property
    def ok(self) -> bool:
        return self.type_written and self.move.ok


def _empty_move(bundle: Bundle) -> MovePlan:
    """The `MovePlan` a refused retype carries: nothing to move, nothing refused.

    `RetypePlan.ok` is already `False` from our own refusals, so this must not
    add a second, confusing one.
    """
    return MovePlan(
        root=bundle.root,
        moves=(),
        edits=(),
        refusals=(),
        unrebased=(),
        digests=MappingProxyType({}),
    )


def plan_retype(bundle: Bundle, schema_set: SchemaSet, concept_id: str, new_type: str) -> RetypePlan:
    """Plan *concept_id*'s move into *new_type*'s lane. Writes nothing.

    A page okf-io could not parse is refused by `plan_move`'s own `parse-error`,
    not by a fifth kind here -- the vocabulary already exists and duplicating it
    would give a caller two words for one fact.
    """
    document = bundle.concepts.get(concept_id)
    if document is None:
        return RetypePlan(
            concept_id=concept_id,
            from_type="",
            to_type=new_type,
            dest="",
            move=_empty_move(bundle),
            refusals=(
                RetypeRefusal(
                    concept_id=concept_id,
                    kind="not-a-member",
                    detail=f"{concept_id}: not a concept in this bundle",
                ),
            ),
        )

    from_type = (document.fm.type or "").strip()
    wanted = new_type.strip()
    refusals: list[RetypeRefusal] = []

    if wanted not in TYPE_NAMES:
        refusals.append(
            RetypeRefusal(
                concept_id=concept_id,
                kind="unknown-type",
                detail=f"unknown type {wanted!r}; expected one of {list(TYPE_NAMES)}",
            )
        )
    elif wanted not in schema_set.schemas:
        refusals.append(
            RetypeRefusal(
                concept_id=concept_id,
                kind="undeclared-type",
                detail=f"{wanted}: no schema in `{schema_set.root.name}`; the declarations were never installed",
            )
        )
    if from_type == wanted:
        refusals.append(
            RetypeRefusal(
                concept_id=concept_id,
                kind="same-type",
                detail=f"{concept_id} is already `{wanted}`",
            )
        )

    if refusals:
        return RetypePlan(
            concept_id=concept_id,
            from_type=from_type,
            to_type=wanted,
            dest="",
            move=_empty_move(bundle),
            refusals=tuple(refusals),
        )

    dest = f"{directory_for(schema_set, wanted)}{PurePosixPath(concept_id).name}.md"
    return RetypePlan(
        concept_id=concept_id,
        from_type=from_type,
        to_type=wanted,
        dest=dest,
        move=plan_move(bundle, f"{concept_id}.md", dest),
        refusals=(),
    )


def apply_retype(bundle: Bundle, plan: RetypePlan) -> RetypeResult:
    """Write the `type:` change, then move the file.

    **The `type:` rewrite lands first** (spec §5.1). `MovePlan.digests` is over
    the **body**, and a frontmatter edit does not change the body, so rewriting
    `type:` cannot invalidate the move plan it was computed alongside. It is also
    the right ordering on failure: a correctly-typed page in its old lane is a
    legal page under ADR-0012, while a page in `explanations/` still claiming to
    be something else is a lie in the field every reader trusts. The durable half
    lands first; the cosmetic half second.

    Raises `ValueError` on a non-ok plan -- `moves.apply`'s own contract.
    """
    if not plan.ok:
        kinds: list[str] = [refusal.kind for refusal in plan.refusals]
        kinds.extend(refusal.kind for refusal in plan.move.refusals)
        raise ValueError(f"refused plan must not be applied ({', '.join(kinds)}): {plan.concept_id} -> {plan.to_type}")

    document = bundle.concepts[plan.concept_id]
    document.set("type", plan.to_type)
    try:
        document.save()
    except (YAMLError, ValueError, RecursionError) as exc:
        return RetypeResult(
            move=MoveResult(
                moved=(),
                written=(),
                failed=(
                    WriteFailure(
                        path=f"{plan.concept_id}.md",
                        kind="serialize-error",
                        error=f"the `type:` rewrite did not land, so the move was not attempted: {exc}",
                    ),
                ),
                pruned=(),
            ),
            type_written=False,
        )
    except OSError as exc:
        return RetypeResult(
            move=MoveResult(
                moved=(),
                written=(),
                failed=(
                    WriteFailure(
                        path=f"{plan.concept_id}.md",
                        kind="commit-error",
                        error=f"the `type:` rewrite did not land, so the move was not attempted: {exc}",
                    ),
                ),
                pruned=(),
            ),
            type_written=False,
        )

    return RetypeResult(move=apply_move(bundle, plan.move), type_written=True)


__all__ = [
    "RetypePlan",
    "RetypeRefusal",
    "RetypeRefusalKind",
    "RetypeResult",
    "apply_retype",
    "plan_retype",
]
