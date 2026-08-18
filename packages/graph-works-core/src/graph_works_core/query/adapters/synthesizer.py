"""SynthesizerAdapter: answer synthesis from librarian excerpts."""

from __future__ import annotations

from pathlib import Path

from code_graph_io import GraphReader
from okf_io import load_bundle
from subagents_io import Prepared, RunContext

from graph_works_core.agent_substrate.agent_tools import read_bounded_page
from graph_works_core.query import commands as query_mod
from graph_works_core.query.prompts.synthesizer import SYNTHESIZER_SYSTEM
from graph_works_core.workspace.discovery import resolve

_MAX_CHARS = 24_000
_TOP_K = 5


class SynthesizerAdapter:
    """Synthesizer adapter.

    When *excerpts_path* is provided, the adapter loads pre-fetched librarian
    excerpts from that file and skips the retrieval + librarian step. When it
    is `None` (the default), the adapter runs `_prepare_query_retrieval` to
    find the top page, reads it as an excerpt, and builds the synthesizer
    human message — the same retrieval `LibrarianAdapter` runs, through the
    same module-object reference so a test can patch it.
    """

    name = "synthesizer"
    role = "synthesizer"
    selector = "query"
    supports_all = False

    def __init__(self, excerpts_path: Path | None = None) -> None:
        self.excerpts_path = excerpts_path

    async def prepare(self, ctx: RunContext[GraphReader], item: str) -> Prepared:
        if self.excerpts_path is not None:
            excerpts_text = self.excerpts_path.read_text(encoding="utf-8", errors="replace")
            note = "retrieval skipped — excerpts loaded from disk"
        else:
            layout = resolve(workspace=ctx.workspace)
            bundle = load_bundle(layout.bundle_dir)
            prepared = query_mod._prepare_query_retrieval(
                item, layout, bundle, top_k=_TOP_K, embedder=query_mod.default_embedder()
            )
            top_page = prepared.top_pages[0] if prepared.top_pages else ""
            page_text = read_bounded_page(bundle, top_page, max_chars=_MAX_CHARS) if top_page else ""
            excerpts_text = f"[{top_page}]\n{page_text}" if top_page else page_text
            note = None

        human = f"Query: {item}\n\nLibrarian excerpts:\n{excerpts_text}"

        return Prepared(
            item_id=item[:80],
            system=SYNTHESIZER_SYSTEM,
            human=human,
            parse=None,
            note=note,
        )

    def items(self, ctx: RunContext[GraphReader]) -> list[str]:
        raise ValueError("SynthesizerAdapter is a single-query adapter; use prepare() directly")
