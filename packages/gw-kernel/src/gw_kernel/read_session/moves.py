"""Index-backed candidate selection for moving work items.

This is the reparent write path's exception to read_session's display-only rule
(epic D-108): the session hands the planner the walk its reconcile verified, the
Documents it parsed, and the names of members that may reference the moved
roots. It reads no body. Any session that cannot prove the index current
answers with a fallback reason instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from okf_io import Document
from okf_io.bundle import Walk, canonical_id

from gw_kernel.read_session.model import ReadSession


@dataclass(frozen=True, slots=True)
class MoveSelection:
    """The index-backed load selection for a move."""

    inventory: Walk
    candidates: frozenset[str]
    documents: Mapping[str, Document]


def move_selection(session: ReadSession, roots: Sequence[str]) -> MoveSelection | str:
    """The index-backed candidate set for moving *roots*, or the fallback reason."""
    accessor: object = getattr(session, "move_selection", None)
    if not callable(accessor):
        return getattr(session, "fallback", None) or "unavailable"
    result: object = accessor(roots)
    assert isinstance(result, (MoveSelection, str))
    return result


def needles(roots: Sequence[str]) -> list[str]:
    return [canonical_id(PurePosixPath(root).name) for root in roots]
