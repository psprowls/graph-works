"""The registry: protocol conformance, role resolution, and the two refusals."""

from __future__ import annotations

import json
import tomllib
from importlib import resources
from pathlib import Path

import pytest
from graph_works_core.query import commands as q
from graph_works_core.query.adapters import LOOP_REGISTRY, REGISTRY
from graph_works_core.workspace.layout import layout_for
from subagents_io import Adapter, LoopAdapter, RunContext


def _roles() -> set[str]:
    with resources.files("graph_works_core").joinpath("models.toml").open("rb") as handle:
        return set(tomllib.load(handle)["roles"])


def test_the_registries_hold_exactly_c5s_adapters():
    assert set(REGISTRY) == {"librarian", "synthesizer"}
    assert set(LOOP_REGISTRY) == {"query_orchestrator"}


def test_every_single_shot_entry_satisfies_the_adapter_protocol():
    for name, factory in REGISTRY.items():
        adapter = factory()
        assert isinstance(adapter, Adapter), name
        assert adapter.name == name


def test_every_loop_entry_satisfies_the_loop_adapter_protocol():
    for name, factory in LOOP_REGISTRY.items():
        adapter = factory()
        assert isinstance(adapter, LoopAdapter), name
        assert adapter.name == name


def test_every_registry_key_names_a_packaged_role():
    roles = _roles()
    for factory in (*REGISTRY.values(), *LOOP_REGISTRY.values()):
        assert factory().role in roles


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(workspace=tmp_path, repo_root=tmp_path, wiki=tmp_path / "okf")


@pytest.mark.parametrize("name", ["librarian", "synthesizer"])
def test_items_raises_for_a_single_query_adapter(tmp_path, name):
    with pytest.raises(ValueError, match="single-query"):
        REGISTRY[name]().items(_ctx(tmp_path))


async def test_the_synthesizer_with_excerpts_performs_no_retrieval(tmp_path, monkeypatch):
    from graph_works_core.query.adapters.synthesizer import SynthesizerAdapter

    def fail(*a, **k):
        raise AssertionError("retrieval must not run when excerpts were supplied")

    monkeypatch.setattr(q, "_prepare_query_retrieval", fail)
    excerpts = tmp_path / "excerpts.md"
    excerpts.write_text("[concepts/auth]\nRefresh rotates the token.\n", encoding="utf-8")

    prepared = await SynthesizerAdapter(excerpts_path=excerpts).prepare(_ctx(tmp_path), "rotation?")
    assert "Refresh rotates the token." in prepared.human
    assert prepared.note is not None
    assert "retrieval skipped" in prepared.note


async def test_the_librarian_builds_the_standard_human_message(tmp_path, monkeypatch):
    from datetime import date

    from graph_works_core import apply_init, plan_init
    from graph_works_core.query.adapters.librarian import LibrarianAdapter

    layout = apply_init(plan_init(tmp_path, today=date(2026, 8, 16), topic="Adapter tests", repo_root=tmp_path)).layout
    root = layout.bundle_dir
    (root / "concepts").mkdir(parents=True)
    (root / "concepts" / "auth.md").write_text("---\ntitle: Auth\n---\n\nRefresh rotates.\n", encoding="utf-8")

    monkeypatch.setattr("graph_works_core.query.adapters.librarian.resolve", lambda **kw: layout)
    monkeypatch.setattr(q, "default_embedder", lambda: _FixedEmbedder())

    prepared = await LibrarianAdapter().prepare(_ctx(tmp_path), "how does refresh work?")
    assert prepared.human.startswith("Query: how does refresh work?")
    assert "Page (" in prepared.human
    assert prepared.item_id == "how does refresh work?"


async def test_the_synthesizer_without_excerpts_runs_retrieval(tmp_path, monkeypatch):
    from graph_works_core.query.adapters.synthesizer import SynthesizerAdapter

    root = tmp_path / "okf"
    (root / "concepts").mkdir(parents=True)
    (root / "concepts" / "auth.md").write_text(
        "---\ntitle: Auth\n---\n\nRefresh rotates the token.\n", encoding="utf-8"
    )
    layout = layout_for(tmp_path)

    monkeypatch.setattr("graph_works_core.query.adapters.synthesizer.resolve", lambda **kw: layout)
    monkeypatch.setattr(q, "default_embedder", lambda: _FixedEmbedder())

    prepared = await SynthesizerAdapter().prepare(_ctx(tmp_path), "how does refresh work?")
    assert prepared.human.startswith("Query: how does refresh work?")
    assert "concepts/auth" in prepared.human
    assert prepared.note is None


async def test_the_query_orchestrator_loop_adapter_maps_the_result_onto_a_loop_outcome(tmp_path, monkeypatch):
    from graph_works_core.query.adapters.query_orchestrator import QueryOrchestratorLoopAdapter
    from graph_works_core.query.query_orchestrator import (
        AnswerEvidenceMap,
        OrchestratorEvidence,
        OrchestratorOutput,
        QueryOrchestratorResult,
        _freeze_mapping,
    )

    root = tmp_path / "okf"
    (root / "concepts").mkdir(parents=True)
    (root / "concepts" / "auth.md").write_text(
        "---\ntitle: Auth\n---\n\nRefresh rotates the token.\n", encoding="utf-8"
    )
    layout = layout_for(tmp_path, repo_root=tmp_path)

    monkeypatch.setattr("graph_works_core.query.adapters.query_orchestrator.resolve", lambda **kw: layout)
    monkeypatch.setattr(q, "default_embedder", lambda: _FixedEmbedder())
    monkeypatch.setattr(q, "_load_query_graph_tools", lambda target: (None, []))

    output = OrchestratorOutput(
        answer_markdown="Refresh rotates the token.",
        citations=["concepts/auth"],
        evidence=[
            OrchestratorEvidence(
                id="e1",
                source_type="wiki",
                path="concepts/auth",
                freshness="fresh",
                staleness_reason=None,
                excerpt="Refresh rotates the token.",
                line_refs=[],
            )
        ],
        answer_evidence_map=[AnswerEvidenceMap(claim="Refresh rotates the token.", evidence_ids=["e1"])],
        worker_plan=(_freeze_mapping({"worker": "librarian", "task_id": "t1"}),),
        worker_results=(_freeze_mapping({"worker": "librarian", "task_id": "t1", "status": "ok"}),),
        gaps=[],
        confidence="high",
    )

    seen_layouts = []

    async def scripted(**kwargs):
        # Reverting `layout=layout` at this adapter's own `run_query_orchestrator`
        # call site MUST make `seen_layouts` come back `[None]` instead of
        # `[layout]` — that is the whole point of recording it.
        seen_layouts.append(kwargs.get("layout"))
        return QueryOrchestratorResult(output=output, trace_metadata={"status": "ok", "worker_batches": 0})

    monkeypatch.setattr(q, "run_query_orchestrator", scripted)

    outcome = await QueryOrchestratorLoopAdapter().run(_ctx(tmp_path), "how does refresh work?")
    assert seen_layouts == [layout]
    assert outcome.item_id == "how does refresh work?"
    assert outcome.role == "query_orchestrator"
    assert outcome.answer == "Refresh rotates the token."
    assert outcome.structured["citations"] == ["concepts/auth"]
    assert outcome.structured["worker_plan"] == [{"worker": "librarian", "task_id": "t1"}]
    assert json.dumps(outcome.structured)  # a populated output must survive json.dumps, not merely have keys
    assert outcome.trace_metadata == {"status": "ok", "worker_batches": 0}
    assert outcome.note is not None and "loop adapter" in outcome.note


class _FixedEmbedder:
    model_id = "fake-embed-v1"

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.5, 0.25]
