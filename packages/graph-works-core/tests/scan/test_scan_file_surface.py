"""The out-of-process half: emit, hand off, read back, apply."""

from __future__ import annotations

import json

import pytest
from code_wiki_okf.config import load_config
from graph_works_core.scan import commands as scan_commands
from graph_works_core.scan.commands import (
    WORKLIST_FILENAME,
    apply_scan_worklist,
    brief_slug,
    build_scan_worklist,
    emit_scan_worklist,
    load_results_dir,
    scan_cache_dir,
)
from graph_works_core.scan.scan_contract import (
    ProseRefreshTask,
    ScanWorklist,
    UnsupportedWorklistSchema,
    worklist_from_payload,
)
from scan_helpers import AT, PACKAGE_URI, TODAY, make_workspace, seed_graph

FILLED = "Widgets is the demo package. It exists to exercise this pipeline."


@pytest.fixture
async def emitted(tmp_path):
    layout, repo = make_workspace(tmp_path)
    config = load_config(layout.bundle_dir, config_path=layout.repositories_path)
    seed_graph(config.graph_dir, repo)
    worklist, _ = await build_scan_worklist(layout, config, today=TODAY, at=AT, dry_run=False)
    out_dir = scan_cache_dir(layout)
    written = emit_scan_worklist(worklist, out_dir=out_dir)
    return layout, config, worklist, out_dir, written


async def test_the_emitted_artifact_lives_under_the_cache_dir(emitted):
    layout, _config, _worklist, out_dir, written = emitted
    assert out_dir.is_relative_to(layout.cache_dir)
    assert ".graph-wiki" not in str(out_dir)
    assert any(name.endswith(WORKLIST_FILENAME) for name in written)


async def test_one_brief_per_task_is_written(emitted):
    _layout, _config, worklist, out_dir, _written = emitted
    briefs = sorted((out_dir / "briefs").glob("*.md"))
    assert len(briefs) == len(worklist.prose_tasks)
    assert briefs[0].read_text(encoding="utf-8").strip()


async def test_the_worklist_reloads_equal(emitted):
    _layout, _config, worklist, out_dir, _written = emitted
    payload = json.loads((out_dir / WORKLIST_FILENAME).read_text(encoding="utf-8"))
    assert worklist_from_payload(payload) == worklist


async def test_a_schema_two_artifact_is_refused(emitted):
    _layout, _config, _worklist, out_dir, _written = emitted
    path = out_dir / WORKLIST_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(UnsupportedWorklistSchema):
        worklist_from_payload(json.loads(path.read_text(encoding="utf-8")))


def test_results_are_last_wins_by_sorted_filename(tmp_path):
    directory = tmp_path / "results"
    directory.mkdir()
    (directory / "a.json").write_text(json.dumps({"uri": "u", "sections": {"## Purpose": "first"}}), encoding="utf-8")
    (directory / "b.json").write_text(json.dumps({"uri": "u", "sections": {"## Purpose": "second"}}), encoding="utf-8")
    results = load_results_dir(directory)
    assert [r.sections["## Purpose"] for r in results.prose] == ["second"]


def test_an_unreadable_result_file_is_named_not_raised(tmp_path):
    directory = tmp_path / "results"
    directory.mkdir()
    (directory / "broken.json").write_text("{not json", encoding="utf-8")
    (directory / "nouri.json").write_text(json.dumps({"sections": {}}), encoding="utf-8")
    results = load_results_dir(directory)
    assert results.prose == ()
    assert len(results.provider_errors) == 2


def test_a_missing_results_directory_is_empty_not_an_error(tmp_path):
    results = load_results_dir(tmp_path / "nope")
    assert results.prose == ()


def test_brief_slug_strips_scheme_and_path_separators():
    assert brief_slug("pkg:acme/demo/widgets") == "pkg-acme-demo-widgets"


def test_brief_slug_falls_back_to_entity_for_an_all_unsafe_uri():
    assert brief_slug("::://") == "entity"


def _dep_task(uri: str) -> ProseRefreshTask:
    return ProseRefreshTask(
        uri=uri,
        kind="Dependency",
        name="foo",
        page_path=f"dependencies/{brief_slug(uri)}.md",
        entity_root="",
        trigger="first_fill",
        prose_sections={"## Purpose": ""},
    )


def test_emit_disambiguates_two_uris_that_collide_on_the_same_slug(tmp_path):
    """`dep:npm/@foo/bar` and `dep:npm/foo-bar` both sanitize to
    `dep-npm-foo-bar` -- the second must not silently overwrite the first."""
    colliding = ("dep:npm/@foo/bar", "dep:npm/foo-bar")
    assert brief_slug(colliding[0]) == brief_slug(colliding[1])
    worklist = ScanWorklist(prose_tasks=tuple(_dep_task(uri) for uri in colliding))
    written = emit_scan_worklist(worklist, out_dir=tmp_path / "scan")
    briefs = sorted((tmp_path / "scan" / "briefs").glob("*.md"))
    assert len(briefs) == 2
    assert len(written) == 3  # worklist.json + two distinct briefs


async def test_a_second_emit_leaves_no_brief_from_the_first(emitted):
    """F8: `worklist.json` stays authoritative either way, but an out-of-process
    agent globbing `briefs/*.md` picks up dead work. Within-run slug collisions
    were handled carefully; across runs there was nothing."""
    _layout, _config, worklist, out_dir, _written = emitted
    stale = out_dir / "briefs" / "dep-npm-ghost.md"
    stale.write_text("a brief from a previous run\n", encoding="utf-8")

    emit_scan_worklist(worklist, out_dir=out_dir)

    assert not stale.exists()
    assert len(sorted((out_dir / "briefs").glob("*.md"))) == len(worklist.prose_tasks)


async def test_pruning_touches_nothing_outside_the_briefs_directory(emitted):
    _layout, _config, worklist, out_dir, _written = emitted
    results = out_dir / "results"
    results.mkdir(exist_ok=True)
    (results / "widgets.json").write_text("{}", encoding="utf-8")
    keeper = out_dir / "briefs" / "notes.txt"
    keeper.write_text("not a brief\n", encoding="utf-8")

    emit_scan_worklist(worklist, out_dir=out_dir)

    assert (out_dir / WORKLIST_FILENAME).is_file()
    assert (results / "widgets.json").is_file()
    assert keeper.is_file()


def test_emit_into_a_fresh_directory_prunes_nothing(tmp_path):
    worklist = ScanWorklist(prose_tasks=(_dep_task("dep:npm/foo"),))
    written = emit_scan_worklist(worklist, out_dir=tmp_path / "scan")
    assert len(written) == 2


def test_emit_replaces_canonical_leaf_symlinks_without_touching_external_targets(tmp_path):
    """Artifact leaves must never redirect pruning or writes outside the cache."""
    out_dir = tmp_path / "scan"
    out_dir.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    brief_sentinel = external / "stale.md"
    brief_sentinel.write_text("external brief\n", encoding="utf-8")
    worklist_sentinel = external / "worklist.json"
    worklist_sentinel.write_text("external worklist\n", encoding="utf-8")
    (out_dir / "briefs").symlink_to(external, target_is_directory=True)
    (out_dir / WORKLIST_FILENAME).symlink_to(worklist_sentinel)
    worklist = ScanWorklist(prose_tasks=(_dep_task("dep:npm/foo"),))

    emit_scan_worklist(worklist, out_dir=out_dir)

    assert brief_sentinel.read_text(encoding="utf-8") == "external brief\n"
    assert worklist_sentinel.read_text(encoding="utf-8") == "external worklist\n"
    assert (out_dir / "briefs").is_dir()
    assert not (out_dir / "briefs").is_symlink()
    assert len(list((out_dir / "briefs").glob("*.md"))) == 1
    assert (out_dir / WORKLIST_FILENAME).is_file()
    assert not (out_dir / WORKLIST_FILENAME).is_symlink()


async def test_apply_scan_worklist_carries_provider_errors_through(emitted):
    """F11: an unreadable result file's error must survive `apply_scan_worklist`
    alongside whatever `apply_scan_results` itself reports, not be swallowed."""
    layout, config, _worklist, out_dir, _written = emitted
    results_dir = out_dir / "results"
    results_dir.mkdir(exist_ok=True)
    (results_dir / "broken.json").write_text("{not json", encoding="utf-8")
    applied = apply_scan_worklist(
        worklist_path=out_dir / WORKLIST_FILENAME,
        results_dir=results_dir,
        bundle_root=layout.bundle_dir,
        config=config,
        today=TODAY,
        dry_run=False,
    )
    assert any("unreadable result file" in message for message in applied.entity_errors)


async def test_apply_scan_worklist_lands_the_same_page_the_in_process_path_does(emitted):
    layout, config, worklist, out_dir, _written = emitted
    task = next(t for t in worklist.prose_tasks if t.uri == PACKAGE_URI)
    results_dir = out_dir / "results"
    results_dir.mkdir()
    (results_dir / "widgets.json").write_text(
        json.dumps({"uri": PACKAGE_URI, "sections": dict.fromkeys(task.prose_sections, FILLED)}),
        encoding="utf-8",
    )
    applied = apply_scan_worklist(
        worklist_path=out_dir / WORKLIST_FILENAME,
        results_dir=results_dir,
        bundle_root=layout.bundle_dir,
        config=config,
        today=TODAY,
        dry_run=False,
    )
    assert applied.narrated == 1
    assert applied.stamped == 1
    assert FILLED in (layout.bundle_dir / "repositories" / "demo" / "packages" / "widgets.md").read_text(
        encoding="utf-8"
    )


async def test_apply_scan_worklist_uses_a_supplied_worklist_after_the_artifact_changes(emitted, monkeypatch):
    """The caller's validated object, not a second path read, controls apply."""
    layout, config, worklist, out_dir, _written = emitted
    worklist_path = out_dir / WORKLIST_FILENAME
    worklist_path.write_text("{not valid JSON", encoding="utf-8")
    received: list[ScanWorklist] = []

    def fake_apply_scan_results(
        supplied: ScanWorklist,
        *_args: object,
        **_kwargs: object,
    ) -> scan_commands.ApplyResult:
        received.append(supplied)
        return scan_commands.ApplyResult()

    monkeypatch.setattr(scan_commands, "apply_scan_results", fake_apply_scan_results)

    result = apply_scan_worklist(
        worklist_path=worklist_path,
        worklist=worklist,
        results_dir=out_dir / "results",
        bundle_root=layout.bundle_dir,
        config=config,
        today=TODAY,
        dry_run=False,
    )

    assert result == scan_commands.ApplyResult()
    assert received == [worklist]
