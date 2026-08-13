"""Two callables this layer takes rather than imports.

`compute_state_gate` (is the scanned code graph stale?) and `match_entity`
(which package is this document about?) both land in `code-wiki-okf`, this
package's **sibling**. Importing them would stack two siblings into a layer,
which the epic's §1.2 rules out, so they arrive as optional callables behind
narrow protocols.

Absent, a brief reports `state_gate=None` and `NO_ENTITY` -- exactly what legacy
produced when `reader is None`, where the docstring already said the harness
"proceeds without a link". The seam existed; this makes it the package boundary.

`state_gate` is carried as an opaque `Mapping[str, Any]`. Its keys belong to the
scanner that produced it: this layer passes it through and never reads it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class EntityMatch:
    """The code entity a source is about, or two nulls."""

    uri: str | None = None
    entity_filename: str | None = None

    def as_data(self) -> dict[str, Any]:
        return {"uri": self.uri, "entity_filename": self.entity_filename}


#: No reader, or no hit. The value legacy returned as a bare dict.
NO_ENTITY = EntityMatch()


class StateGate(Protocol):
    """`compute_state_gate`'s shape. Positional-only so a conforming callable
    need not adopt our parameter name for the repo."""

    def __call__(self, repo: Path, /, *, workspace: Path) -> Mapping[str, Any]: ...


class EntityMatcher(Protocol):
    """`match_entity`'s shape, with the graph reader already bound by the caller."""

    def __call__(self, repo: Path, source: Path, title: str, /) -> EntityMatch: ...


def read_state_gate(state_gate: StateGate | None, repo: Path, workspace: Path) -> Mapping[str, Any] | None:
    """Call an optional gate. One place, so three builders cannot disagree."""
    return None if state_gate is None else state_gate(repo, workspace=workspace)
