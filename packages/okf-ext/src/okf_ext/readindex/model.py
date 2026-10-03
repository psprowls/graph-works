"""Read-index lifecycle errors and rebuild reasons."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from okf_io import ParseError
from okf_io.bundle import MemberKind


class IndexBusy(Exception):
    """A write lock timed out or the optimistic snapshot conflicted; nothing was written."""


class IndexUnavailable(Exception):
    """The cache could not be opened, or a stale publishing handle must be reopened."""


RebuildReason = Literal["created", "schema", "projection", "okf-io", "fingerprint", "patterns", "corrupt"]


@dataclass(frozen=True, slots=True)
class Reconcile:
    """Changes and settlement state of one index refresh."""

    generation: int
    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    parsed: int
    unsettled: tuple[str, ...]
    racy: tuple[str, ...]
    published: bool


@dataclass(frozen=True, slots=True)
class MemberRow:
    """One readable member's persisted projection, without its body."""

    id: str
    kind: MemberKind
    sha256: str | None
    type: str | None
    title: str | None
    status: str | None
    tags: tuple[str, ...]
    fm: Mapping[str, object] | None
    fm_exact: bool
    parse_error: ParseError | None
    coercion_failures: frozenset[str]

    @property
    def concept_id(self) -> str | None:
        return self.id[:-3] if self.kind == "concept" else None


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """Content failures and membership diagnostics for a pinned generation."""

    unreadable: Mapping[str, str]
    parse_errors: Mapping[str, ParseError]
    collisions: Mapping[str, tuple[str, ...]]
    pruned: frozenset[str]
