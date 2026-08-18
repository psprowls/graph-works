"""The capped tool loop that decides which durable pages a source justifies.

Three properties of its seams are forced by what is underneath rather than
chosen here:

1. The catalog seam is C2's `build_catalog(bundle, lanes=…)` /
   `read_bounded_page(bundle, concept_id)`. A concept id is a `Bundle.concepts`
   key, so path-containment guards are unrepresentable here rather than
   reimplemented.
2. `graph_tools` arrives as an argument. Building them is C7's
   `graph_tools.py`, and a vertical importing another vertical is the risk the
   epic's §7 names for Wave C's parallelism. Absent, the reasoner runs on the
   catalog and the source chunks alone -- a narrower analysis, not a failure.
   The CLI wires C7's builder in at E7.
3. `make_llm` comes from C2's `roles`, and takes an optional layout so a
   workspace role override applies.

The four caps port as constants. They are tuned values from a dated sweep, not
incidental ones, and re-deriving them later is real work.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from doc_wiki_okf.proposals.lanes import LaneSet
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool
from okf_io import Bundle

from graph_works_core.agent_substrate.agent_loop import run_tool_loop
from graph_works_core.agent_substrate.agent_tools import (
    SourceChunks,
    build_catalog,
    chunk_text,
    filter_graph_tools,
    read_bounded_page,
    search_catalog,
    truncate_text,
)
from graph_works_core.agent_substrate.roles import make_llm
from graph_works_core.ingest.prompts.proposal_reasoner import build_proposal_reasoner_system
from graph_works_core.workspace.layout import WorkspaceLayout

#: A source at or under this many characters is inlined into the prompt whole.
FULL_SOURCE_MAX_CHARS = 120_000

#: How large each chunk is when it is not.
SOURCE_CHUNK_CHARS = 20_000

#: How many tool-call rounds the reasoner gets.
MAX_REASONER_ITERS = 5

#: The cap on one wiki page fetched through `read_wiki_page`.
MAX_WIKI_PAGE_CHARS = 40_000

_CATALOG_PROMPT_CHARS = 80_000

#: The only graph tools the reasoner may hold. C7 builds more; this filter is
#: what keeps a wider builder from widening this loop's surface by accident.
_ALLOWED_GRAPH_TOOL_NAMES = {"cg_find", "cg_describe"}


@dataclass(frozen=True)
class ProposalReasonerResult:
    """The loop's outcome, carried unchanged."""

    status: str
    analysis: str
    error: str | None = None


def build_source_chunks(source_text: str) -> SourceChunks:
    return chunk_text(source_text, max_chars=FULL_SOURCE_MAX_CHARS, chunk_chars=SOURCE_CHUNK_CHARS)


def build_reasoner_tools(
    *,
    bundle: Bundle,
    lanes: Sequence[str],
    chunks: list[str],
    graph_tools: Sequence[BaseTool],
) -> list[BaseTool]:
    """The reasoner's three own tools, plus whichever graph tools are allowed."""
    catalog = build_catalog(bundle, lanes=lanes)

    @tool
    def read_wiki_page(concept_id: str) -> str:
        """Read one wiki page by its bundle-relative id, bounded to a safe size."""
        return read_bounded_page(bundle, concept_id, max_chars=MAX_WIKI_PAGE_CHARS)

    @tool
    def read_source_chunk(index: int) -> str:
        """Read one source chunk by zero-based index when the source exceeded the prompt budget."""
        if index < 0 or index >= len(chunks):
            return f"ERROR: source chunk index out of range: {index}"
        return chunks[index]

    @tool
    def search_wiki_catalog(query: str, kind: str | None = None) -> str:
        """Search the wiki catalog by title, summary, or slug and return up to 20 JSON rows."""
        return json.dumps(search_catalog(catalog, query, kind=kind, limit=20), indent=2, sort_keys=True)

    return [
        read_wiki_page,
        read_source_chunk,
        search_wiki_catalog,
        *filter_graph_tools(list(graph_tools), _ALLOWED_GRAPH_TOOL_NAMES),
    ]


def build_reasoner_prompt(
    *,
    bundle: Bundle,
    lanes: Sequence[str],
    material: Path,
    source_text: str,
    source_page: str,
    source_kind: str,
    origin: str,
    page_text: str,
    entity_uri: str | None,
    entity_page: str | None,
) -> str:
    """The human message: catalog, the composed page, and the source or its manifest.

    *page_text* is the page's composed **body** -- it has not been written yet
    (spec §4.6), and it is not the whole page: a reader of the landed page also
    sees its frontmatter, and the reasoner cannot, because `proposal_status` --
    one of that frontmatter's fields -- is what this very phase computes.
    `compose_frontmatter` runs after `plan_suggestions` returns, so at reasoner
    time there is no composed frontmatter to pass.

    The identity fields that *do* exist at this point are passed as their own
    lines instead: *source_kind* -- validated against the bundle's own `Source`
    vocabulary, not the caller's hint -- plus *origin* and the entity pair.
    Those are the ones worth weighing when choosing a lane.
    """
    source_chunks = build_source_chunks(source_text)
    catalog_json = json.dumps(build_catalog(bundle, lanes=lanes), indent=2, sort_keys=True)

    if source_chunks.full_text is None:
        chunk_lines = "\n".join(
            f"- chunk {index}: {len(chunk)} chars" for index, chunk in enumerate(source_chunks.chunks)
        )
        raw_source_section = (
            "Raw source text exceeded prompt budget and was split into chunks. "
            "Use read_source_chunk(index) for targeted inspection.\n"
            f"{chunk_lines}"
        )
    else:
        raw_source_section = source_chunks.full_text

    return (
        f"Source material: {material}\n"
        f"Source wiki page: {source_page}\n"
        f"Source kind: {source_kind}\n"
        f"Origin: {origin}\n"
        f"Entity URI: {entity_uri or '(none)'}\n"
        f"Entity page: {entity_page or '(none)'}\n\n"
        "Wiki catalog JSON (truncated if needed):\n"
        f"{truncate_text(catalog_json, _CATALOG_PROMPT_CHARS)}\n\n"
        "Source page text (truncated if needed):\n"
        f"{truncate_text(page_text, MAX_WIKI_PAGE_CHARS)}\n\n"
        "Raw source text or chunk manifest:\n"
        f"{raw_source_section}\n\n"
        "Produce up to 10 candidate analyses. For each candidate include: lane, the page it "
        "argues for, title, source evidence, existing pages considered, reasoning summary, "
        "potential conflicts, implementation notes, confidence, rank, and why that lane is the "
        "right one. Return no candidates if the source does not justify durable wiki changes."
    )


async def run_proposal_reasoner(
    *,
    bundle: Bundle,
    lanes: Sequence[str],
    lane_set: LaneSet,
    material: Path,
    source_text: str,
    source_page: str,
    source_kind: str,
    origin: str,
    page_text: str,
    entity_uri: str | None,
    entity_page: str | None,
    graph_tools: Sequence[BaseTool] = (),
    layout: WorkspaceLayout | None = None,
    model_override: str | None = None,
) -> ProposalReasonerResult:
    """Run the capped loop once and return its outcome verbatim.

    This never raises for a model failure -- `run_tool_loop` returns a `failed`
    status instead -- but a constructor failure in `make_llm` does propagate,
    and `plan_suggestions` is where that is caught.

    *lane_set* is the proposal `LaneSet`, and it is **not** *lanes*: *lanes* is
    `catalog_lanes`' wider set -- the proposal lanes plus the entity lanes plus
    `sources/` -- which is what the catalog tool covers, not what the model may
    propose into. Reusing it for the system prompt would offer lanes no
    suggestion can be filed under.
    """
    chunks = build_source_chunks(source_text).chunks
    tools = build_reasoner_tools(bundle=bundle, lanes=lanes, chunks=chunks, graph_tools=graph_tools)
    messages = [
        SystemMessage(content=build_proposal_reasoner_system(lane_set=lane_set)),
        HumanMessage(
            content=build_reasoner_prompt(
                bundle=bundle,
                lanes=lanes,
                material=material,
                source_text=source_text,
                source_page=source_page,
                source_kind=source_kind,
                origin=origin,
                page_text=page_text,
                entity_uri=entity_uri,
                entity_page=entity_page,
            )
        ),
    ]
    loop_result = await run_tool_loop(
        llm=make_llm("proposal_reasoner", layout=layout, model_override=model_override),
        tools=tools,
        messages=messages,
        max_iterations=MAX_REASONER_ITERS,
        cap_label="reasoner",
    )
    return ProposalReasonerResult(status=loop_result.status, analysis=loop_result.final_text, error=loop_result.error)


__all__ = [
    "FULL_SOURCE_MAX_CHARS",
    "MAX_REASONER_ITERS",
    "MAX_WIKI_PAGE_CHARS",
    "SOURCE_CHUNK_CHARS",
    "ProposalReasonerResult",
    "build_reasoner_prompt",
    "build_reasoner_tools",
    "build_source_chunks",
    "run_proposal_reasoner",
]
