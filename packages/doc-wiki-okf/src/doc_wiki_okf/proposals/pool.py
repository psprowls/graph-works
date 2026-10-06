"""The proposal pool: the types a proposal may target, read off the schema set.

`okf_ext.schemas.declared_proposables` reads the annotations; this wraps the
result for the three things a proposal needs from a type -- where a new one
lands (`target_for`), which type an existing one targets (`type_for`), and
whether a named type can be filed into at all (`lookup`, `__getitem__`). No
type name is written down here: the pool is whatever the schemas flag.

**Directory is not type.** All seven work types share `work/`, so a proposal
records `target_type`, and `type_for` falls back to the directory only when
exactly one pool type owns it. It never guesses.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from okf_ext.proposals import Proposal, Refusal
from okf_ext.schemas import Proposables, ProposableType, SchemaSet, declared_proposables
from okf_io import Finding, Rule, RuleContext

from doc_wiki_okf.reading import slugify

#: Where a type name stands with respect to the pool.
TypeState = Literal["pool", "locked", "refused", "unknown"]

#: The lint code for a flagged type the pool excluded.
REFUSED_TYPE = "proposals.refused-type"


class PoolError(KeyError):
    """A type the pool cannot file into -- locked, refused or unknown.

    Caller input, so it raises; a `KeyError` so every caller already mapping
    an unknown lane's `KeyError` to a usage error keeps doing so. `str()` is
    the plain message, not `KeyError`'s quoted repr.
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


@dataclass(frozen=True, slots=True)
class ProposalPool:
    """The pool, with lookup by type name and type recovery for a proposal."""

    proposables: Proposables

    @property
    def types(self) -> tuple[ProposableType, ...]:
        return self.proposables.types

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.proposables.types)

    @property
    def locked(self) -> tuple[str, ...]:
        return self.proposables.locked

    @property
    def refused(self) -> tuple[tuple[str, str], ...]:
        return self.proposables.refused

    def lookup(self, name: str) -> tuple[TypeState, str]:
        """*name*'s state and its canonical spelling. Case-insensitive; never raises."""
        wanted = name.strip()
        folded = wanted.casefold()
        for entry in self.proposables.types:
            if entry.name.casefold() == folded:
                return "pool", entry.name
        for locked in self.proposables.locked:
            if locked.casefold() == folded:
                return "locked", locked
        for refused, _reason in self.proposables.refused:
            if refused.casefold() == folded:
                return "refused", refused
        return "unknown", wanted

    def _why(self, state: TypeState, name: str) -> str:
        if state == "locked":
            return f"{name}: locked -- its schema sets x-okf-accept-proposals: false"
        if state == "refused":
            return f"{name}: refused from the proposal pool ({dict(self.proposables.refused)[name]})"
        return f"{name}: not a proposal type; expected one of {list(self.names)}"

    def __getitem__(self, name: str) -> ProposableType:
        """The pool type *name* names. Raises `PoolError` naming why it cannot be filed into."""
        state, canonical = self.lookup(name)
        if state == "pool":
            return next(entry for entry in self.proposables.types if entry.name == canonical)
        raise PoolError(self._why(state, canonical))

    def target_for(self, name: str, title: str, *, on: date | None = None) -> str:
        """Where a page of *name* titled *title* lands: undated, unless *on* is given
        and the type's promotion is `dated`. Raises `PoolError` like `__getitem__`."""
        entry = self[name]
        slug = slugify(title)
        if on is not None and entry.promotion is not None and entry.promotion.dated:
            return f"{entry.directory}{on.isoformat()}-{slug}.md"
        return f"{entry.directory}{slug}.md"

    def type_for(self, proposal: Proposal) -> ProposableType | Refusal:
        """The pool type *proposal* targets, or the refusal saying why there is none.

        `target_type` first. Without one, the longest pool directory prefixing
        the target, only when exactly one pool type owns it.
        """
        if proposal.target_type is not None:
            state, canonical = self.lookup(proposal.target_type)
            if state == "pool":
                return self[canonical]
            return Refusal(path=proposal.member, kind="type-unavailable", detail=self._why(state, canonical))
        matches = [entry for entry in self.proposables.types if proposal.target.startswith(entry.directory)]
        if matches:
            longest = max(len(entry.directory) for entry in matches)
            matches = [entry for entry in matches if len(entry.directory) == longest]
        if len(matches) == 1:
            return matches[0]
        if matches:
            detail = (
                f"target {proposal.target!r} records no target_type and its directory is shared by "
                f"{', '.join(entry.name for entry in matches)}; this layer will not guess one"
            )
        else:
            directories = ", ".join(sorted({entry.directory for entry in self.proposables.types}))
            detail = (
                f"target {proposal.target!r} records no target_type and is in none of the proposal "
                f"types' directories ({directories})"
            )
        return Refusal(path=proposal.member, kind="malformed-proposal", detail=detail)


def proposal_pool(schema_set: SchemaSet) -> ProposalPool:
    """The pool *schema_set* declares."""
    return ProposalPool(declared_proposables(schema_set))


def refused_type_rule(proposables: Proposables) -> Rule:
    """One `proposals.refused-type` warning per flagged type the pool excluded.

    A fact about the schema set, not about a member, so `path` is None and a
    scoped validation (one proposal under review) reports nothing.
    """
    refused = proposables.refused

    def rule(context: RuleContext) -> Iterable[Finding]:
        if context.scope is not None:
            return
        for type_name, reason in refused:
            yield Finding(
                code=REFUSED_TYPE,
                severity="warn",
                message=f"{type_name} excluded from the proposal pool: {reason}",
                spec="x-okf-accept-proposals",
                path=None,
            )

    return rule


__all__ = ["REFUSED_TYPE", "PoolError", "ProposalPool", "TypeState", "proposal_pool", "refused_type_rule"]
