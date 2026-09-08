"""Helpers every topic module shares. Not a rule module: the leading underscore
keeps it out of the registry."""

from __future__ import annotations

from collections.abc import Iterator

from okf_io.document import Document
from okf_io.validate import RuleContext


def member_path(concept_id: str) -> str:
    """The bundle-relative posix member a concept id names."""
    return f"{concept_id}.md"


def in_scope(ctx: RuleContext, member: str) -> bool:
    """Whether *member* is inside this pass's scope. `None` means everything."""
    return ctx.scope is None or member in ctx.scope


def concepts(ctx: RuleContext) -> Iterator[tuple[str, Document]]:
    """Every in-scope concept, in id order. Nothing in this package iterates a
    mapping raw.

    This is the single per-document chokepoint for every topic module that
    walks documents, which is what makes `RuleContext.scope` enforceable rather
    than advisory: a rule that wants a document goes through here, and a rule
    that reasons across documents deliberately does not.
    `test_rule_iteration_boundary.py` pins that.
    """
    for concept_id in sorted(ctx.bundle.concepts):
        if not in_scope(ctx, member_path(concept_id)):
            continue
        yield concept_id, ctx.bundle.concepts[concept_id]
