"""The reasoner: its tools, its prompt, and its two loop outcomes."""

from __future__ import annotations

import json
from pathlib import Path

from graph_works_core.ingest.proposal_reasoner import (
    FULL_SOURCE_MAX_CHARS,
    MAX_REASONER_ITERS,
    MAX_WIKI_PAGE_CHARS,
    SOURCE_CHUNK_CHARS,
    ProposalReasonerResult,
    build_reasoner_prompt,
    build_reasoner_tools,
    run_proposal_reasoner,
)
from ingest_helpers import FakeLLM, FakeResponse
from langchain_core.tools import tool


def _bundle(tmp_path: Path):
    from okf_io import load_bundle

    root = tmp_path / "okf"
    (root / "explanations").mkdir(parents=True)
    (root / "explanations" / "why.md").write_text(
        "---\ntype: Explanation\ntitle: Why\ndescription: The reason\n---\n\nBecause.\n",
        encoding="utf-8",
    )
    return load_bundle(root)


def _lanes(tmp_path: Path):
    """A real `LaneSet`, seeded in its own directory.

    Not `tmp_path / "okf"` -- that is `_bundle`'s, and `_bundle` calls
    `mkdir(parents=True)` without `exist_ok`, so seeding over it raises.
    """
    from doc_wiki_okf.proposals.lanes import lane_set
    from ingest_helpers import declarations
    from suggest_fixtures import make_bundle

    schema_set, _ = declarations(make_bundle(tmp_path / "lanes"))
    return lane_set(schema_set)


def test_the_caps_carry_the_tuned_values():
    assert (FULL_SOURCE_MAX_CHARS, SOURCE_CHUNK_CHARS, MAX_REASONER_ITERS, MAX_WIKI_PAGE_CHARS) == (
        120_000,
        20_000,
        5,
        40_000,
    )


def test_only_the_two_allowed_graph_tools_reach_the_loop(tmp_path):
    @tool
    def cg_find(query: str) -> str:
        """find"""
        return query

    @tool
    def cg_write(query: str) -> str:
        """write"""
        return query

    tools = build_reasoner_tools(
        bundle=_bundle(tmp_path), lanes=("explanations",), chunks=[], graph_tools=[cg_find, cg_write]
    )
    names = {each.name for each in tools}
    assert "cg_find" in names
    assert "cg_write" not in names
    assert {"read_wiki_page", "read_source_chunk", "search_wiki_catalog"} <= names


def test_read_wiki_page_is_a_key_lookup(tmp_path):
    tools = {
        each.name: each
        for each in build_reasoner_tools(bundle=_bundle(tmp_path), lanes=("explanations",), chunks=[], graph_tools=[])
    }
    assert "Because." in tools["read_wiki_page"].invoke({"concept_id": "explanations/why"})
    assert tools["read_wiki_page"].invoke({"concept_id": "../../etc/passwd"}).startswith("ERROR:")


def test_read_source_chunk_bounds_its_index(tmp_path):
    tools = {
        each.name: each
        for each in build_reasoner_tools(
            bundle=_bundle(tmp_path), lanes=("explanations",), chunks=["a", "b"], graph_tools=[]
        )
    }
    assert tools["read_source_chunk"].invoke({"index": 1}) == "b"
    assert tools["read_source_chunk"].invoke({"index": 2}).startswith("ERROR:")
    assert tools["read_source_chunk"].invoke({"index": -1}).startswith("ERROR:")


def test_search_wiki_catalog_returns_json_rows(tmp_path):
    tools = {
        each.name: each
        for each in build_reasoner_tools(bundle=_bundle(tmp_path), lanes=("explanations",), chunks=[], graph_tools=[])
    }
    rows = json.loads(tools["search_wiki_catalog"].invoke({"query": "why"}))
    assert rows and rows[0]["slug"] == "why"


def test_a_source_under_budget_is_inlined(tmp_path):
    prompt = build_reasoner_prompt(
        bundle=_bundle(tmp_path),
        lanes=("explanations",),
        material=Path("/tmp/thing.md"),
        source_text="short source",
        source_page="sources/2026-08-thing.md",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="---\ntitle: Thing\n---\n\nbody",
        entity_uri=None,
        entity_page=None,
    )
    assert "short source" in prompt
    assert "chunk manifest" not in prompt.lower() or "chunk 0" not in prompt


def test_a_source_over_budget_becomes_a_chunk_manifest(tmp_path):
    prompt = build_reasoner_prompt(
        bundle=_bundle(tmp_path),
        lanes=("explanations",),
        material=Path("/tmp/thing.md"),
        source_text="x" * (FULL_SOURCE_MAX_CHARS + 1),
        source_page="sources/2026-08-thing.md",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="body",
        entity_uri="pkg:okf-io",
        entity_page="packages/okf-io",
    )
    assert "read_source_chunk(index)" in prompt
    assert "chunk 0:" in prompt
    assert "pkg:okf-io" in prompt


async def test_a_clean_run_returns_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "graph_works_core.ingest.proposal_reasoner.make_llm",
        lambda *args, **kwargs: FakeLLM(FakeResponse("the analysis")),
    )
    result = await run_proposal_reasoner(
        bundle=_bundle(tmp_path),
        lanes=("explanations",),
        lane_set=_lanes(tmp_path),
        material=Path("/tmp/thing.md"),
        source_text="short",
        source_page="sources/2026-08-thing.md",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="body",
        entity_uri=None,
        entity_page=None,
        graph_tools=(),
    )
    assert result == ProposalReasonerResult(status="ok", analysis="the analysis", error=None)


async def test_an_empty_response_is_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "graph_works_core.ingest.proposal_reasoner.make_llm",
        lambda *args, **kwargs: FakeLLM(FakeResponse("")),
    )
    result = await run_proposal_reasoner(
        bundle=_bundle(tmp_path),
        lanes=("explanations",),
        lane_set=_lanes(tmp_path),
        material=Path("/tmp/thing.md"),
        source_text="short",
        source_page="sources/2026-08-thing.md",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="body",
        entity_uri=None,
        entity_page=None,
        graph_tools=(),
    )
    assert result.status == "failed"
    assert result.analysis == ""


def test_the_prompt_names_the_validated_source_kind_and_the_resolved_origin(tmp_path):
    """D3: the reasoner is told the two identity fields that exist at its own
    call time. The page's composed frontmatter is not among them and cannot
    be -- it is composed after this phase returns.
    """
    prompt = build_reasoner_prompt(
        bundle=_bundle(tmp_path),
        lanes=("explanations",),
        material=Path("/tmp/thing.md"),
        source_text="short source",
        source_page="sources/2026-08-thing.md",
        source_kind="spec",
        origin="https://example.invalid/spec",
        page_text="body",
        entity_uri=None,
        entity_page=None,
    )
    assert "Source kind: spec" in prompt
    assert "Origin: https://example.invalid/spec" in prompt
