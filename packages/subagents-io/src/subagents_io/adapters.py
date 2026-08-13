"""Adapter protocol, run context, and the prepared-invocation payload.

`RunContext` is generic over its reader rather than declaring a structural
`GraphReaderLike`. Two of the four reader methods the concrete adapters call
return `code_graph_io` records whose attributes those adapters read, so a
narrow Protocol here would have to mirror them — a shadow model of the graph,
living in the package defined by knowing nothing about graphs. The type
parameter carries the same guarantee with no model at all: band 1 knows only
that a reader closes.

Python 3.12 has no PEP 696 type-parameter defaults and `mypy --strict` implies
`disallow_any_generics`, so a bare `RunContext` is a type error. Every
annotation is parameterized — `RunContext[GraphReader]` in core,
`RunContext[FakeReader]` in this package's tests. An adapter that never
touches the graph still builds a parameterized context, with `open_reader=None`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class Closeable(Protocol):
    """The single thing this package knows about a graph reader: it closes.

    Public because it bounds `RunContext`'s type parameter — a caller
    annotating against the generic cannot name the bound without it.
    """

    def close(self) -> None: ...


class NoGraphReader(RuntimeError):
    """A RunContext built without an opener was asked for a graph reader."""


@dataclass
class RunContext[ReaderT: Closeable]:
    """Resolved workspace paths plus a lazily-opened, caller-supplied reader."""

    workspace: Path
    repo_root: Path
    wiki: Path
    open_reader: Callable[[Path], ReaderT] | None = None
    _reader: ReaderT | None = field(default=None, repr=False)

    def graph_reader(self) -> ReaderT:
        if self._reader is None:
            if self.open_reader is None:
                raise NoGraphReader(
                    "this RunContext was built without a graph reader; "
                    "pass open_reader= to use adapters that query the graph"
                )
            self._reader = self.open_reader(self.workspace)
        return self._reader

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


@dataclass
class Prepared:
    """One subagent invocation's real inputs: the genuine prompt + parser."""

    item_id: str
    system: str
    human: str
    parse: Callable[[str], Any] | None = None
    note: str | None = None


@runtime_checkable
class Adapter[ReaderT: Closeable](Protocol):
    name: str
    role: str  # resolves to a RoleSpec; see subagents_io.roles
    selector: str  # "file" | "package" | "query"
    supports_all: bool  # True only for worklist adapters

    async def prepare(self, ctx: RunContext[ReaderT], item: str) -> Prepared: ...

    def items(self, ctx: RunContext[ReaderT]) -> list[str]:
        """Real worklist for --all; raises for single-query adapters."""
        ...


@dataclass
class LoopOutcome:
    """Result of one tool-loop adapter run (real agentic orchestration).

    Unlike RunOutcome there is no token/cost footer: the orchestration spans
    many model calls across worker batches and returns no aggregate usage.

    `region` is `str | None` rather than `str` because `RoleSpec.region`
    defaults to None — the "us-east-1" fallback is a Bedrock fact and lives in
    `models_io.make_bedrock_llm`, not here.
    """

    item_id: str
    role: str
    model_id: str
    region: str | None
    answer: str  # OrchestratorOutput.answer_markdown
    structured: dict[str, Any] | None  # OrchestratorOutput as a plain dict
    trace_metadata: Mapping[str, Any]
    latency_s: float
    trace_path: str | None
    note: str | None = None


@runtime_checkable
class LoopAdapter[ReaderT: Closeable](Protocol):
    name: str
    role: str  # resolves to a RoleSpec; see subagents_io.roles
    selector: str  # "query" for query_orchestrator

    async def run(self, ctx: RunContext[ReaderT], item: str) -> LoopOutcome: ...
