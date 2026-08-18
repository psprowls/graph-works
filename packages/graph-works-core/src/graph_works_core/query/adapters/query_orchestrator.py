"""QueryOrchestratorLoopAdapter: run the real query-orchestration loop end-to-end.

Unlike the single-shot adapters this executes `graph_works_core`'s production
`run_query_orchestrator` — it plans over bounded bundle/graph tools and fans
out librarian + code_reader worker batches. The production seams are
referenced via the `query` module object (not `from … import`) so tests can
patch them, matching `LibrarianAdapter` and `SynthesizerAdapter`.
"""

from __future__ import annotations

from code_graph_io import GraphReader
from okf_io import load_bundle
from subagents_io import LoopOutcome, RunContext

from graph_works_core.graph.commands import graph_target
from graph_works_core.query import commands as query_mod
from graph_works_core.query.query_orchestrator import orchestrator_output_as_dict
from graph_works_core.workspace.discovery import resolve
from graph_works_core.workspace.provenance import head_sha

_LOOP_NOTE = "loop adapter: no token/cost aggregation (multi-call orchestration); see trace file"


class QueryOrchestratorLoopAdapter:
    """Runs the real orchestration and maps its output onto a `LoopOutcome`.

    `model_id`, `region`, `latency_s` and `trace_path` are left empty on
    purpose: `subagents_io.run_loop` overlays all four via
    `dataclasses.replace`. `note` records that no token or cost footer exists —
    the orchestration spans many model calls and aggregates none.
    """

    name = "query_orchestrator"
    role = "query_orchestrator"
    selector = "query"

    def __init__(self, top_k: int = 5) -> None:
        self.top_k = top_k

    async def run(self, ctx: RunContext[GraphReader], item: str) -> LoopOutcome:
        layout = resolve(workspace=ctx.workspace)
        bundle = load_bundle(layout.bundle_dir)
        # `_prepare_query_retrieval` already raises `ValueError` outside
        # `[3, 10]`; the reference's own `top_k` bounds check is not repeated
        # here — one check is better than two that can disagree.
        prepared = query_mod._prepare_query_retrieval(
            item, layout, bundle, top_k=self.top_k, embedder=query_mod.default_embedder()
        )
        reader, graph_tools = query_mod._load_query_graph_tools(graph_target(layout))
        try:
            result = await query_mod.run_query_orchestrator(
                query=item,
                bundle=bundle,
                repo_root=layout.repo_root,
                initial_candidates=query_mod._initial_candidates(
                    prepared, repo_head=head_sha(layout.repo_root) if layout.repo_root is not None else None
                ),
                graph_tools=graph_tools,
                trace_dir=layout.cache_dir / "traces",
                workspace_root=layout.root,
                layout=layout,
            )
        finally:
            if reader is not None:
                reader.close()

        return LoopOutcome(
            item_id=item[:80],
            role=self.role,
            model_id="",
            region="",
            answer=result.output.answer_markdown,
            structured=orchestrator_output_as_dict(result.output),
            trace_metadata=dict(result.trace_metadata),
            latency_s=0.0,
            trace_path=None,
            note=_LOOP_NOTE,
        )
