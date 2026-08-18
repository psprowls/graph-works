"""The subagent adapters this vertical owns, and the two registries.

C5's three only. `prose_refresher` arrives with a later sibling epic child and
two guidance adapters with another — each child adds its own module and its
own entry here. The mapping file is touched by three children over time, which
is the accepted cost of every vertical owning its adapters.

The base types come from `subagents_io`, which ships all five. The reference's
`adapters/base.py` is a retirement, not a port. Both protocols are
`runtime_checkable`, which turns conformance into an assertion rather than a
convention — see `tests/test_adapters.py`.

The mappings deliberately do **not** live in `subagents-io`:
`LibrarianAdapter.prepare` reaches `commands.query._prepare_query_retrieval`,
so a band-1 registry would import band 3.
"""

from __future__ import annotations

from collections.abc import Callable

from code_graph_io import GraphReader
from subagents_io import Adapter, LoopAdapter

from graph_works_core.query.adapters.librarian import LibrarianAdapter
from graph_works_core.query.adapters.query_orchestrator import QueryOrchestratorLoopAdapter
from graph_works_core.query.adapters.synthesizer import SynthesizerAdapter

#: Adapter name → zero-argument constructor. `SynthesizerAdapter` takes an
#: optional `excerpts_path`; the registry entry is its no-argument form.
REGISTRY: dict[str, Callable[[], Adapter[GraphReader]]] = {
    "librarian": LibrarianAdapter,
    "synthesizer": SynthesizerAdapter,
}

#: Tool-loop adapters, kept separate so the single-shot path stays untouched.
LOOP_REGISTRY: dict[str, Callable[[], LoopAdapter[GraphReader]]] = {
    "query_orchestrator": QueryOrchestratorLoopAdapter,
}

__all__ = [
    "LOOP_REGISTRY",
    "REGISTRY",
    "LibrarianAdapter",
    "QueryOrchestratorLoopAdapter",
    "SynthesizerAdapter",
]
