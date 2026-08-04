"""Helpers every topic module shares. Not a rule module: the leading underscore
keeps it out of the registry."""

from __future__ import annotations

from collections.abc import Iterator

from okf_io.document import Document
from okf_io.validate import RuleContext


def member_path(concept_id: str) -> str:
    """The bundle-relative posix member a concept id names."""
    return f"{concept_id}.md"


def concepts(ctx: RuleContext) -> Iterator[tuple[str, Document]]:
    """Every concept, in id order. Nothing in this package iterates a mapping raw."""
    for concept_id in sorted(ctx.bundle.concepts):
        yield concept_id, ctx.bundle.concepts[concept_id]
