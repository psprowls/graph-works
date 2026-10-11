"""`run_work_queue`: every active, non-terminal item, routed as a dry-run `next` would route it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from _display_fixture import disable_index
from graph_works_core.read_session import open as read_session_open
from graph_works_core.work import commands as work
from graph_works_core.workspace import dispatch_artifacts
from okf_io import Document
from test_run_dispatch_explain import _dispatch
from test_run_next import CHILD, EPIC, _layout, _spec, _write
from test_stage_artifacts_renamed import _set_pipeline

READY = "work/feature-ready"
DESIGN = "work/feature-design"
UNSIZED = "work/testgap-unsized"


def test_routing_items_titles_through_a_supplied_body(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    _spec(layout, DESIGN)
    with read_session_open.open_read_session(layout) as session:
        items = session.work_snapshot()
    asked = []

    def body_of(ref):
        asked.append(ref.rel)
        return "# Supplied\n"

    definition = work._definition_of(work._load_config(layout))
    found = dispatch_artifacts.missing_design_source(
        layout.bundle_dir,
        next(item for item in items if item.path == DESIGN),
        definition=definition,
        body_of=body_of,
    )
    planned = dispatch_artifacts.routing_items(layout.bundle_dir, items, definition=definition, body_of=body_of)

    assert found is not None and found[1] == "Supplied"
    assert asked == [f"{DESIGN}/references/01-design.md"] * 2
    assert next(item for item in planned if item.path == DESIGN).has_design_artifact


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
        assert entry.result.normalizations == nexted.normalizations
    by_path = {entry.item.path: entry.result for entry in entries}
    assert by_path[READY].dispatch_resolution is not None
    design = by_path[DESIGN].dispatch_resolution
    assert design is not None and design.profile.mode == "attend"
    assert by_path[EPIC].route.dispatch is None and by_path[EPIC].route.blockers
    assert any("effort required" in blocker.message for blocker in by_path[UNSIZED].route.blockers)
    assert [change.path for change in by_path[READY].normalizations] == [READY]


def test_archived_and_terminal_items_are_left_out(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-live")
    _write(layout, "work/feature-done")
    _edit(layout, "work/feature-done", "work_status: open", "work_status: resolved")
    _write(layout, "work/_archive/feature-old")

    assert [entry.item.path for entry in work.run_work_queue(layout)] == ["work/feature-live"]


@pytest.mark.parametrize("inactive_items", [False, True])
def test_an_empty_queue_never_builds_a_routing_overlay(tmp_path: Path, monkeypatch, inactive_items: bool) -> None:
    layout = _layout(tmp_path)
    if inactive_items:
        _write(layout, "work/feature-done")
        _edit(layout, "work/feature-done", "work_status: open", "work_status: resolved")
        _write(layout, "work/_archive/feature-old")

    def unexpected_overlay(*args, **kwargs):
        raise AssertionError("an empty queue must not probe design artifacts")

    monkeypatch.setattr(work, "routing_items", unexpected_overlay)

    assert work.run_work_queue(layout) == ()


def test_a_malformed_dispatch_file_blocks_offered_transitions_only(tmp_path: Path) -> None:
    """D-004: dispatches and transitions are blocked; waiting items remain visible."""
    layout = _layout(tmp_path)
    _fixture(layout)
    _dispatch(layout, shared="  - match: {}\n    colour: red\n")

    entries = work.run_work_queue(layout)

    by_path = {entry.item.path: entry.result for entry in entries}
    assert by_path[READY].dispatch_preflight is not None
    assert "dispatch.yaml: rule 0" in by_path[READY].dispatch_preflight
    assert by_path[READY].dispatch_preflight == work.run_next(layout, READY, dry_run=True).dispatch_preflight
    assert by_path[EPIC].route.on_complete is None
    assert by_path[EPIC].dispatch_preflight is None
    assert by_path[UNSIZED].dispatch_preflight is None


def test_the_session_and_the_dispatch_config_are_each_read_once(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _fixture(layout)
    calls = {"session": 0, "config": 0}
    open_session, load_config = read_session_open.open_read_session, work.load_dispatch_config

    def counting_session(*args, **kwargs):
        calls["session"] += 1
        return open_session(*args, **kwargs)

    def counting_config(*args, **kwargs):
        calls["config"] += 1
        return load_config(*args, **kwargs)

    monkeypatch.setattr(read_session_open, "open_read_session", counting_session)
    monkeypatch.setattr(work, "load_dispatch_config", counting_config)

    work.run_work_queue(layout)

    assert calls == {"session": 1, "config": 1}


def test_the_queue_writes_nothing_when_a_design_source_needs_repair(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    _spec(layout, DESIGN)
    page = layout.bundle_dir / f"{DESIGN}.md"
    before = page.read_bytes()

    work.run_work_queue(layout)

    assert page.read_bytes() == before


def _count(monkeypatch, module, name: str) -> dict[str, int]:
    calls = {"n": 0}
    original = getattr(module, name)

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, counting)
    return calls


def test_the_routing_overlay_is_built_once_per_pass(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _fixture(layout)
    calls = _count(monkeypatch, work, "routing_items")

    entries = work.run_work_queue(layout)

    assert len(entries) == 5
    assert calls["n"] == 1


def _probes(tmp_path: Path, monkeypatch, active: int) -> int:
    layout = _layout(tmp_path)
    for index in range(active):
        _write(layout, f"work/feature-{index:02d}")
    overlay = _count(monkeypatch, dispatch_artifacts, "missing_design_source")
    normalization = _count(monkeypatch, work, "missing_design_source")
    assert len(work.run_work_queue(layout)) == active
    return overlay["n"] + normalization["n"]


def test_design_probes_grow_linearly_with_active_items(tmp_path: Path, monkeypatch) -> None:
    small = _probes(tmp_path / "small", monkeypatch, 4)
    monkeypatch.undo()
    large = _probes(tmp_path / "large", monkeypatch, 8)

    assert small == 4 + 4
    assert large == 8 + 8


def test_each_pass_builds_a_fresh_overlay(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    calls = _count(monkeypatch, work, "routing_items")

    first = work.run_work_queue(layout)
    _spec(layout, DESIGN)
    second = work.run_work_queue(layout)

    assert calls["n"] == 2
    assert first[0].result.normalizations == ()
    assert [change.path for change in second[0].result.normalizations] == [DESIGN]
    assert second[0].result.route == work.run_next(layout, DESIGN, dry_run=True).route


@contextmanager
def _parses(monkeypatch) -> Iterator[list]:
    seen: list = []
    real = Document.parse

    def parse(cls, text, *, path=None):
        seen.append(path)
        return real(text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(parse))
    yield seen


def _prime(layout) -> None:
    with read_session_open.open_read_session(layout):
        pass


def test_a_warm_queue_parses_nothing(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _fixture(layout)
    _write(layout, DESIGN + "-unstamped")
    _spec(layout, DESIGN + "-unstamped")
    _prime(layout)
    with _parses(monkeypatch) as seen:
        entries = work.run_work_queue(layout)
    assert seen == []
    assert len(entries) == 6


def test_one_unrelated_edit_parses_one_file(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _fixture(layout)
    _prime(layout)
    _edit(layout, DESIGN, "## Summary\nd\n", "## Summary\nedited\n")
    with _parses(monkeypatch) as seen:
        work.run_work_queue(layout)
    assert [p.name for p in seen if p is not None] == ["feature-design.md"]
    assert len(seen) == 1


TITLE_BODIES = {
    "closing-hashes": "# A *bold* title ###\n",
    "setext": "Setext title\n===\n",
    "indented": "  # Indented title\n",
    "fenced": "```\n# Inside fence\n```\n# Real\n",
    "frontmatter-comment": "---\ntype: Spec\n# not a title\n---\n# After frontmatter\n",
    "crlf": "---\r\ntype: Spec\r\n---\r\n# Windows\r\n",
    "bom": "﻿# With BOM\n",
    "malformed-yaml": "---\ntype: [unclosed\n---\n# Despite YAML\n",
    "no-h1": "Just prose.\n",
}


@pytest.mark.parametrize("body", TITLE_BODIES.values(), ids=TITLE_BODIES.keys())
@pytest.mark.parametrize("backend", ["index", "bundle"])
def test_queue_titles_equal_next_titles(tmp_path: Path, body: str, backend: str) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    _spec(layout, DESIGN).write_bytes(body.encode("utf-8"))
    if backend == "bundle":
        disable_index(layout)
    _prime(layout)

    (entry,) = work.run_work_queue(layout)
    nexted = work.run_next(layout, DESIGN, dry_run=True)

    assert entry.result.normalizations == nexted.normalizations
    assert [c.title for c in entry.result.normalizations] == [c.title for c in nexted.normalizations]


def test_artifact_created_after_open_is_still_titled(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    real = read_session_open.open_read_session

    @contextmanager
    def open_then_create(*args, **kwargs):
        with real(*args, **kwargs) as session:
            _spec(layout, DESIGN).write_text("# Late\n", encoding="utf-8")
            yield session

    monkeypatch.setattr(read_session_open, "open_read_session", open_then_create)
    (entry,) = work.run_work_queue(layout)
    assert [c.title for c in entry.result.normalizations] == ["Late"]


def test_undecodable_design_raises_like_next(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    _spec(layout, DESIGN).write_bytes(b"# \xff\xfe broken\n")
    with pytest.raises(Exception) as from_next:
        work.run_next(layout, DESIGN, dry_run=True)
    with pytest.raises(type(from_next.value)):
        work.run_work_queue(layout)


def test_a_supplied_session_serves_bodies_and_stays_open(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    _spec(layout, DESIGN)
    with read_session_open.open_read_session(layout) as session:
        asked = []
        content = session.content
        monkeypatch.setattr(session, "content", lambda mid: (asked.append(mid), content(mid))[1])
        monkeypatch.setattr(
            read_session_open, "open_read_session", lambda *a, **k: pytest.fail("opened a second session")
        )
        (entry,) = work.run_work_queue(layout, session=session)
        assert asked == [f"{DESIGN}/references/01-design.md"]
        assert [c.title for c in entry.result.normalizations] == ["Design"]
        assert session.work_snapshot()  # still usable: the queue did not close it


def test_a_design_heading_edit_is_visible_on_the_next_pass(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    spec = _spec(layout, DESIGN)
    first = work.run_work_queue(layout)
    spec.write_text("# Renamed\n", encoding="utf-8")
    second = work.run_work_queue(layout)
    assert [c.title for c in first[0].result.normalizations] == ["Design"]
    assert [c.title for c in second[0].result.normalizations] == ["Renamed"]


@pytest.mark.parametrize("backend", ["index", "bundle"])
def test_configured_design_artifacts_keep_authoritative_titles(tmp_path: Path, backend: str) -> None:
    filename = "spec.md"
    layout = _layout(tmp_path)
    _set_pipeline(layout, artifacts={"design": {"file": filename}})
    _write(layout, DESIGN)
    canonical = _spec(layout, DESIGN)
    canonical.write_text("# Wrong canonical title\n", encoding="utf-8", newline="\n")
    configured = canonical.with_name(filename)
    configured.write_text("---\ntype: Spec\n# YAML comment\n---\n# Configured\n", encoding="utf-8", newline="\n")
    if backend == "bundle":
        disable_index(layout)
    _prime(layout)

    (entry,) = work.run_work_queue(layout)
    nexted = work.run_next(layout, DESIGN, dry_run=True)

    assert entry.result.normalizations == nexted.normalizations
    assert [(c.ref.resource, c.title) for c in entry.result.normalizations] == [
        (f"/{DESIGN}/references/{filename}", "Configured")
    ]


@pytest.mark.parametrize("deleted", [False, True])
def test_missing_or_deleted_design_is_not_normalized(tmp_path: Path, deleted: bool) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    if deleted:
        artifact = _spec(layout, DESIGN)
    _prime(layout)
    if deleted:
        artifact.unlink()

    (entry,) = work.run_work_queue(layout)
    nexted = work.run_next(layout, DESIGN, dry_run=True)

    assert entry.result.normalizations == nexted.normalizations == ()
    assert entry.result.route == nexted.route


def test_a_design_deleted_from_a_supplied_session_is_not_normalized(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    artifact = _spec(layout, DESIGN)
    with read_session_open.open_read_session(layout) as session:
        artifact.unlink()
        (entry,) = work.run_work_queue(layout, session=session)
    assert entry.result.normalizations == ()
    assert entry.result.route == work.run_next(layout, DESIGN, dry_run=True).route


@pytest.mark.parametrize("backend", ["index", "bundle"])
def test_authored_design_sources_are_preserved_without_probing_artifact_bodies(
    tmp_path: Path, monkeypatch, backend: str
) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    _spec(layout, DESIGN)
    _edit(
        layout, DESIGN, "affects:\n", "sources:\n- id: design\n  resource: /authored.md\n  title: Authored\naffects:\n"
    )
    before = (layout.bundle_dir / f"{DESIGN}.md").read_bytes()
    if backend == "bundle":
        disable_index(layout)
    with read_session_open.open_read_session(layout) as session:
        monkeypatch.setattr(session, "content", lambda mid: pytest.fail(f"authored source probed {mid}"))
        (entry,) = work.run_work_queue(layout, session=session)
    nexted = work.run_next(layout, DESIGN, dry_run=True)
    assert entry.result.normalizations == nexted.normalizations == ()
    assert entry.result.route == nexted.route
    assert [(s.id, s.resource, s.title) for s in entry.item.sources] == [("design", "/authored.md", "Authored")]
    assert (layout.bundle_dir / f"{DESIGN}.md").read_bytes() == before
