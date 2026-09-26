"""`run_work_queue`: every active, non-terminal item, routed as a dry-run `next` would route it."""

from __future__ import annotations

from pathlib import Path

from graph_works_core.work import commands as work
from test_run_dispatch_explain import _dispatch
from test_run_next import CHILD, EPIC, _layout, _spec, _write

READY = "work/feature-ready"
DESIGN = "work/feature-design"
UNSIZED = "work/testgap-unsized"


def _edit(layout, path: str, old: str, new: str) -> None:
    page = layout.bundle_dir / f"{path}.md"
    text = page.read_text(encoding="utf-8")
    assert old in text
    page.write_text(text.replace(old, new), encoding="utf-8")


def _fixture(layout) -> None:
    _write(layout, READY, phase="plan")
    _spec(layout, READY)
    _write(layout, DESIGN)
    _write(layout, UNSIZED, type="TestGap")
    _edit(layout, UNSIZED, "phase: design\neffort: medium\n", "")
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD, phase="plan")


def test_each_entry_is_the_dry_run_next_for_its_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _fixture(layout)

    entries = work.run_work_queue(layout)

    assert [entry.item.path for entry in entries] == sorted([CHILD, DESIGN, EPIC, READY, UNSIZED])
    assert all(entry.result.guidance is None for entry in entries)
    for entry in entries:
        nexted = work.run_next(layout, entry.item.path, dry_run=True)
        assert entry.result.selected_path == entry.item.path
        assert entry.result.route == nexted.route
        assert entry.result.dispatch_resolution == nexted.dispatch_resolution
        assert entry.result.dispatch_preflight == nexted.dispatch_preflight
    by_path = {entry.item.path: entry.result for entry in entries}
    assert by_path[READY].dispatch_resolution is not None
    design = by_path[DESIGN].dispatch_resolution
    assert design is not None and design.profile.mode == "attend"
    assert by_path[EPIC].route.dispatch is None and by_path[EPIC].route.blockers
    assert any("effort required" in blocker for blocker in by_path[UNSIZED].route.blockers)


def test_archived_and_terminal_items_are_left_out(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-live")
    _write(layout, "work/feature-done")
    _edit(layout, "work/feature-done", "work_status: open", "work_status: resolved")
    _write(layout, "work/_archive/feature-old")

    assert [entry.item.path for entry in work.run_work_queue(layout)] == ["work/feature-live"]


def test_a_malformed_dispatch_file_is_each_dispatchable_items_preflight(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _fixture(layout)
    _dispatch(layout, shared="  - match: {}\n    colour: red\n")

    entries = work.run_work_queue(layout)

    by_path = {entry.item.path: entry.result for entry in entries}
    assert by_path[READY].dispatch_preflight is not None
    assert "dispatch.yaml: rule 0" in by_path[READY].dispatch_preflight
    assert by_path[READY].dispatch_preflight == work.run_next(layout, READY, dry_run=True).dispatch_preflight
    assert by_path[EPIC].dispatch_preflight is None


def test_the_bundle_and_the_dispatch_config_are_each_loaded_once(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _fixture(layout)
    calls = {"bundle": 0, "config": 0}
    load_bundle, load_config = work.load_bundle, work.load_dispatch_config

    def counting_bundle(*args, **kwargs):
        calls["bundle"] += 1
        return load_bundle(*args, **kwargs)

    def counting_config(*args, **kwargs):
        calls["config"] += 1
        return load_config(*args, **kwargs)

    monkeypatch.setattr(work, "load_bundle", counting_bundle)
    monkeypatch.setattr(work, "load_dispatch_config", counting_config)

    work.run_work_queue(layout)

    assert calls == {"bundle": 1, "config": 1}


def test_the_queue_writes_nothing_when_a_design_source_needs_repair(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    _spec(layout, DESIGN)
    page = layout.bundle_dir / f"{DESIGN}.md"
    before = page.read_bytes()

    work.run_work_queue(layout)

    assert page.read_bytes() == before
