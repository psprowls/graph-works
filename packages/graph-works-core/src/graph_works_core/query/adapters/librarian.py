"""LibrarianAdapter: single-page excerpt extraction for one query."""

from __future__ import annotations

from code_graph_io import GraphReader
from code_wiki_okf.config import load_config
from okf_ext.schemas import load_schemas
from okf_io import load_bundle
from subagents_io import Prepared, RunContext

from graph_works_core.agent_substrate.agent_tools import read_bounded_page
from graph_works_core.query import commands as query_mod
from graph_works_core.query.prompts.librarian import build_librarian_system
from graph_works_core.workspace.discovery import resolve

_MAX_CHARS = 24_000
_TOP_K = 5


class LibrarianAdapter:
    """Retrieval → top page → the standard `Query: … / Page (…): …` message.

    Reaches `_prepare_query_retrieval` through the module object rather than by
    name: that private function is why this adapter lives in band 3 at all, and
    the module-object reference is what lets a test patch it.
    """

    name = "librarian"
    role = "librarian"
    selector = "query"
    supports_all = False

    async def prepare(self, ctx: RunContext[GraphReader], item: str) -> Prepared:
        layout = resolve(workspace=ctx.workspace)
        bundle = load_bundle(layout.bundle_dir)
        config = load_config(layout.bundle_dir)
        schema_set = load_schemas(config.declarations_dir / "_schema")
        prepared = query_mod._prepare_query_retrieval(
            item, layout, bundle, top_k=_TOP_K, embedder=query_mod.default_embedder()
        )
        top_page = prepared.top_pages[0] if prepared.top_pages else ""
        page_text = read_bounded_page(bundle, top_page, max_chars=_MAX_CHARS) if top_page else ""
        return Prepared(
            item_id=item[:80],
            system=build_librarian_system(schema_set=schema_set),
            human=f"Query: {item}\n\nPage ({top_page}):\n{page_text}",
            parse=None,
        )

    def items(self, ctx: RunContext[GraphReader]) -> list[str]:
        raise ValueError("LibrarianAdapter is a single-query adapter; use prepare() directly")
