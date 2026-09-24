"""Phase 2 and the composition. No live model call."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from code_wiki_okf.config import load_config
from graph_works_core.scan import commands as scan
from graph_works_core.scan.scan_contract import ProseRefreshResult, ScanResults
from scan_helpers import AT, TODAY, FakeLLM, FakeResponse, make_workspace, seed_graph

FILLED = "Widgets is the demo package. It exists to exercise this pipeline."


@pytest.fixture
def ready(tmp_path):
    layout, repo = make_workspace(tmp_path)
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    seed_graph(config.graph_dir, repo)
    return layout, config


def _answer(*headings: str) -> str:
    return json.dumps({"sections": dict.fromkeys(headings, FILLED)})


async def test_narrate_false_stops_after_phase_one(ready, monkeypatch):
    layout, config = ready

    def _no_model(*args, **kwargs):
        raise AssertionError("narrate=False must not construct a model")

    monkeypatch.setattr(scan, "role_binding", _no_model)
    result = await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)
    assert result.structural.entities.written
    assert result.worklist.prose_tasks
    assert result.applied == scan.ApplyResult()


async def test_phase_one_calls_the_composite_sync_once(ready, monkeypatch):
    layout, config = ready
    real_sync_bundle = scan.sync_bundle
    calls = 0

    def counted_sync_bundle(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_sync_bundle(*args, **kwargs)

    monkeypatch.setattr(scan, "sync_bundle", counted_sync_bundle)

    result = await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)

    assert calls == 1
    assert result.structural.entities.written
    assert result.structural.mirror.results


async def test_narrate_false_reports_structural_catalog_declines(ready, monkeypatch):
    """An early structural-only return must still be a failed scan result."""
    layout, config = ready
    real_build = scan.build_scan_worklist

    async def build_with_decline(*args, **kwargs):
        worklist, structural = await real_build(*args, **kwargs)
        entities = replace(structural.entities, catalog_declined=(("packages/broken.md", "parse-error"),))
        return worklist, replace(structural, entities=entities)

    def _no_model(*args, **kwargs):
        raise AssertionError("narrate=False must not construct a model")

    monkeypatch.setattr(scan, "build_scan_worklist", build_with_decline)
    monkeypatch.setattr(scan, "role_binding", _no_model)

    result = await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)

    assert result.errors == ("packages/broken.md: parse-error",)
    assert not result.ok


async def test_narrate_false_reports_incomplete_entity_writes(ready, monkeypatch):
    layout, config = ready
    real_build = scan.build_scan_worklist

    async def build_with_incomplete_entity(*args, **kwargs):
        worklist, structural = await real_build(*args, **kwargs)
        entities = replace(
            structural.entities,
            skipped=("code-graph/demo/entities/packages/widgets.md: commit-error: disk full",),
        )
        return worklist, replace(structural, entities=entities)

    monkeypatch.setattr(scan, "build_scan_worklist", build_with_incomplete_entity)

    result = await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)

    assert result.errors == (
        "entity sync incomplete: code-graph/demo/entities/packages/widgets.md: commit-error: disk full",
    )
    assert not result.ok


async def test_narrated_scan_keeps_structural_declines_before_provider_and_apply_errors(ready, monkeypatch):
    """The final narrated return must preserve every phase's error in order."""
    layout, config = ready
    real_build = scan.build_scan_worklist

    async def build_with_decline(*args, **kwargs):
        worklist, structural = await real_build(*args, **kwargs)
        entities = replace(structural.entities, catalog_declined=(("packages/broken.md", "parse-error"),))
        return worklist, replace(structural, entities=entities)

    async def fake_fan_out(*args, **kwargs):
        return ScanResults(provider_errors=("pkg:widgets: provider unavailable",))

    monkeypatch.setattr(scan, "build_scan_worklist", build_with_decline)
    monkeypatch.setattr(scan, "run_prose_fan_out", fake_fan_out)
    monkeypatch.setattr(
        scan,
        "apply_scan_results",
        lambda *args, **kwargs: scan.ApplyResult(entity_errors=("packages/widgets.md: write-error",)),
    )

    result = await scan.run_scan(layout, config, today=TODAY, at=AT, dry_run=False)

    assert result.errors == (
        "packages/broken.md: parse-error",
        "pkg:widgets: provider unavailable",
        "packages/widgets.md: write-error",
    )
    assert not result.ok


async def test_an_empty_worklist_short_circuits_before_constructing_a_model(ready, monkeypatch):
    """Distinct from `narrate=False`: here `narrate` is left at its default
    `True`, but every page is already fully written and stamped, so the
    worklist itself is empty and there is nothing to construct a model for."""
    layout, config = ready

    def _no_model(*args, **kwargs):
        raise AssertionError("an empty worklist must not construct a model")

    worklist, _summary = await scan.build_scan_worklist(layout, config, today=TODAY, at=AT, dry_run=False)
    assert worklist.prose_tasks
    results = ScanResults(
        prose=tuple(
            ProseRefreshResult(uri=task.uri, sections=dict.fromkeys(task.prose_sections, FILLED))
            for task in worklist.prose_tasks
        )
    )
    applied = scan.apply_scan_results(worklist, results, layout.bundle_dir, config, today=TODAY, dry_run=False)
    # Every task narrates; not every task can be *stamped*. No owning repo means
    # no head, which means `sync` writes no `last_updated_commit`, which means
    # the refill gate has no SHA to anchor -- `owning_short_head is None` is
    # that whole chain in one field. `Dependency` is today's only entity kind
    # with no owning repository, which is why `Dependency.yaml` declares no
    # `prose_refreshed_commit` at all; the old `stamped == len(prose_tasks)`
    # held only because the fixture was a single package with no such entity.
    assert applied.narrated == len(worklist.prose_tasks)
    assert applied.stamped == len([task for task in worklist.prose_tasks if task.owning_short_head is not None])

    monkeypatch.setattr(scan, "role_binding", _no_model)
    result = await scan.run_scan(layout, config, today=TODAY, at=AT, dry_run=False)
    assert result.worklist.prose_tasks == ()
    assert result.applied == scan.ApplyResult()


async def test_the_factory_is_called_once_per_item(ready, monkeypatch):
    layout, config = ready
    made: list[object] = []

    class _Binding:
        class spec:  # a stand-in for RoleSpec's two read fields
            model_id = "fake-model"
            max_concurrency = 4

        @staticmethod
        def make_llm():
            llm = FakeLLM(FakeResponse(_answer("## Purpose", "## Public API")))
            made.append(llm)
            return llm

    monkeypatch.setattr(scan, "role_binding", lambda *a, **k: _Binding())
    result = await scan.run_scan(layout, config, today=TODAY, at=AT, dry_run=False)
    assert len(made) == len(result.worklist.prose_tasks)
    assert result.applied.narrated >= 1


async def test_one_raising_item_does_not_cancel_its_siblings(ready, monkeypatch):
    layout, config = ready
    calls = {"n": 0}

    class _Binding:
        class spec:
            model_id = "fake-model"
            max_concurrency = 2

        @staticmethod
        def make_llm():
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeLLM(fail=True)
            return FakeLLM(FakeResponse(_answer("## Purpose", "## Public API")))

    monkeypatch.setattr(scan, "role_binding", lambda *a, **k: _Binding())
    result = await scan.run_scan(layout, config, today=TODAY, at=AT, dry_run=False)
    # The failing item comes back as a per-item error, never as a raise.
    assert result.errors
    assert not result.ok
    assert len(result.worklist.prose_tasks) > 1


async def test_traces_land_under_the_cache_dir(ready, monkeypatch):
    layout, config = ready

    class _Binding:
        class spec:
            model_id = "fake-model"
            max_concurrency = 1

        @staticmethod
        def make_llm():
            return FakeLLM(FakeResponse(_answer("## Purpose", "## Public API")))

    monkeypatch.setattr(scan, "role_binding", lambda *a, **k: _Binding())
    await scan.run_scan(layout, config, today=TODAY, at=AT, dry_run=False)
    traces = layout.cache_dir / "traces"
    assert traces.is_dir()
    assert list(traces.glob("*.jsonl"))


async def test_a_dry_run_writes_nothing_and_reports_what_it_would_do(ready, monkeypatch):
    """The composite preview reports both structural halves without writes,
    model construction, page changes, or log entries."""
    layout, config = ready

    def _no_model(*args, **kwargs):
        raise AssertionError("a dry run must not construct a model")

    monkeypatch.setattr(scan, "role_binding", _no_model)
    before = sorted(p.name for p in layout.bundle_dir.rglob("*.md"))
    result = await scan.run_scan(layout, config, today=TODAY, at=AT, dry_run=True)
    after = sorted(p.name for p in layout.bundle_dir.rglob("*.md"))

    assert after == before
    assert result.structural.entities.created
    assert result.structural.entities.written == result.structural.entities.created
    assert result.structural.mirror.plans
    assert result.structural.mirror.results == ()
    assert result.applied == scan.ApplyResult()


async def test_dry_run_is_the_default_on_run_scan(ready, monkeypatch):
    layout, config = ready

    def _no_model(*args, **kwargs):
        raise AssertionError("the default must not construct a model")

    monkeypatch.setattr(scan, "role_binding", _no_model)
    result = await scan.run_scan(layout, config, today=TODAY, at=AT)
    assert result.applied == scan.ApplyResult()


async def test_apply_previews_its_counts_without_touching_a_file(ready):
    layout, config = ready
    worklist, _ = await scan.build_scan_worklist(layout, config, today=TODAY, at=AT, dry_run=False)
    task = next(t for t in worklist.prose_tasks if t.page_path == "code-graph/demo/entities/packages/widgets.md")
    page = layout.bundle_dir / "code-graph" / "demo" / "entities" / "packages" / "widgets.md"
    log = layout.bundle_dir / "log.md"
    before_page = page.read_text(encoding="utf-8")
    before_log = log.read_text(encoding="utf-8")

    results = ScanResults(
        prose=(ProseRefreshResult(uri=task.uri, sections=dict.fromkeys(task.prose_sections, FILLED)),)
    )
    applied = scan.apply_scan_results(worklist, results, layout.bundle_dir, config, today=TODAY, dry_run=True)

    assert applied.dry_run is True
    assert applied.narrated == 1
    assert applied.sections_filled == len(task.prose_sections)
    assert applied.stamped == 1
    assert page.read_text(encoding="utf-8") == before_page
    assert log.read_text(encoding="utf-8") == before_log
