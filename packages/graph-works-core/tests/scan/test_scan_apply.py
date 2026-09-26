"""Phase 3: what lands, what is refused, and what gets stamped."""

from __future__ import annotations

import pytest
from code_wiki_okf.config import load_config
from graph_works_core.scan.commands import (
    PROSE_ANCHOR_KEY,
    PROSE_ATTEMPTS_KEY,
    apply_scan_results,
    build_scan_worklist,
    splice_sections,
)
from graph_works_core.scan.scan_contract import (
    ProseRefreshResult,
    ProseRefreshTask,
    ScanResults,
    ScanWorklist,
)
from okf_io import load_bundle
from scan_helpers import AT, PACKAGE_URI, REPO_URI, TODAY, git, make_workspace, package_page, seed_graph, write_page

FILLED = "Widgets is the demo package. It exists to exercise this pipeline."


@pytest.fixture
async def synced(tmp_path):
    """A workspace whose structural pass has run, with a worklist in hand."""
    layout, repo = make_workspace(tmp_path)
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    seed_graph(config.graph_dir, repo)
    worklist, _ = await build_scan_worklist(layout, config, today=TODAY, at=AT, dry_run=False)
    return layout, config, repo, worklist


def _task(worklist: ScanWorklist) -> ProseRefreshTask:
    return next(task for task in worklist.prose_tasks if task.uri == PACKAGE_URI)


def _page(layout) -> str:
    return (layout.bundle_dir / "code-graph" / "demo" / "entities" / "packages" / "widgets.md").read_text(
        encoding="utf-8"
    )


def _results(*results: ProseRefreshResult) -> ScanResults:
    return ScanResults(prose=results)


async def test_a_splice_touches_only_its_own_section(synced):
    layout, config, _repo, worklist = synced
    before = _page(layout).splitlines()
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    after = _page(layout).splitlines()
    assert applied.sections_filled == 1
    assert FILLED in _page(layout)
    # Everything from `## Public API` onward is untouched.
    tail_before = before[before.index("## Public API") :]
    tail_after = after[after.index("## Public API") :]
    assert tail_before == tail_after


async def test_a_crlf_page_keeps_its_terminators(synced):
    layout, config, _repo, worklist = synced
    path = layout.bundle_dir / "code-graph" / "demo" / "entities" / "packages" / "widgets.md"
    path.write_bytes(path.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    raw = path.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


async def test_an_undeclared_heading_never_lands(synced):
    layout, config, _repo, worklist = synced
    apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Files": "hand-written!"})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert "hand-written!" not in _page(layout)


async def test_a_todo_shaped_body_never_lands(synced):
    layout, config, _repo, worklist = synced
    apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": "> TODO: still thinking"})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert "still thinking" not in _page(layout)


async def test_an_errored_result_stamps_nothing_and_is_reported(synced):
    layout, config, _repo, worklist = synced
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, error="model unavailable")),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert applied.stamped == 0
    assert applied.narrated == 0
    assert any("model unavailable" in message for message in applied.entity_errors)
    assert PROSE_ANCHOR_KEY not in _page(layout)


async def test_a_partial_fill_leaves_the_anchor_behind(synced):
    """One of two declared prose sections written: the page still holds a
    placeholder, so the next scan must retry it."""
    layout, config, _repo, worklist = synced
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert applied.narrated == 1
    assert applied.stamped == 0
    assert PROSE_ANCHOR_KEY not in _page(layout)


async def test_a_complete_fill_stamps_the_owning_repo_sha(synced):
    layout, config, repo, worklist = synced
    task = _task(worklist)
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={heading: FILLED for heading in task.prose_sections})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert applied.stamped == 1
    document = load_bundle(layout.bundle_dir).concepts["code-graph/demo/entities/packages/widgets"]
    assert document.fm_raw[PROSE_ANCHOR_KEY] == git(repo, "rev-parse", "HEAD")
    assert document.fm_raw[PROSE_ANCHOR_KEY] == document.fm_raw["last_updated_commit"]


async def test_a_page_with_no_last_updated_commit_is_narrated_but_not_stamped(synced):
    layout, config, _repo, worklist = synced
    write_page(layout, "code-graph/demo/entities/packages/widgets.md", package_page())
    task = _task(worklist)
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={heading: FILLED for heading in task.prose_sections})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert (applied.narrated, applied.stamped) == (1, 0)
    assert applied.entity_errors == ()


async def test_a_result_with_no_task_is_reported_and_skipped(synced):
    layout, config, _repo, worklist = synced
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri="pkg:acme/demo/ghost", sections={"## Purpose": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert applied.narrated == 0
    assert any("ghost" in message for message in applied.entity_errors)


async def test_one_bad_result_does_not_block_a_good_one_in_the_same_batch(synced):
    """Two real entities in one call: the repository's result errors, the
    package's result lands anyway -- isolation, not all-or-nothing."""
    layout, config, _repo, worklist = synced
    applied = apply_scan_results(
        worklist,
        _results(
            ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED}),
            ProseRefreshResult(uri=REPO_URI, error="model unavailable"),
        ),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert applied.narrated == 1
    assert FILLED in _page(layout)
    assert any("model unavailable" in message for message in applied.entity_errors)


async def test_one_log_entry_per_apply_that_wrote_something(synced):
    layout, config, _repo, worklist = synced
    log = layout.bundle_dir / "log.md"
    before = log.read_text(encoding="utf-8")
    apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    after = log.read_text(encoding="utf-8")
    assert after != before
    assert after.count("prose refresh") == 1


async def test_an_apply_that_wrote_nothing_writes_no_log_entry(synced):
    layout, config, _repo, worklist = synced
    log = layout.bundle_dir / "log.md"
    before = log.read_text(encoding="utf-8")
    apply_scan_results(worklist, ScanResults(), layout.bundle_dir, config, today=TODAY, dry_run=False)
    assert log.read_text(encoding="utf-8") == before


def test_a_level_three_key_splices_into_the_level_three_heading():
    """F4: `_section_body` and `_still_placeholder` both pass `level=spec.level`;
    the splice called `find_section` with no level, which is documented
    level-agnostic. The first `level: 3` prose section would have made phase 1
    read one section and phase 3 write a different one."""
    body = "## Notes\n\nlevel two body\n\n### Notes\n\nlevel three body\n"
    spliced, filled = splice_sections(body, {"### Notes": "written by the model"})
    assert filled == 1
    assert "level two body" in spliced
    assert "level three body" not in spliced
    assert "written by the model" in spliced


def test_a_level_two_key_still_splices_into_the_level_two_heading():
    """Deliberately *not* the level-three test's body: `sections()` runs a
    section to the next heading of the same or shallower level, so nesting the
    level-three "Notes" inside the level-two one's span (as the level-three
    test does) would make the level-two splice legitimately consume it too --
    that would be `sections()` working as documented, not this function
    failing to match by level. Placing the level-three heading *before* the
    level-two one keeps the two spans disjoint, so this exercises the level
    match in isolation."""
    body = "### Notes\n\nlevel three body\n\n## Notes\n\nlevel two body\n"
    spliced, filled = splice_sections(body, {"## Notes": "written by the model"})
    assert filled == 1
    assert "level two body" not in spliced
    assert "level three body" in spliced


def test_a_heading_at_no_matching_level_splices_nothing():
    body = "## Notes\n\nlevel two body\n"
    spliced, filled = splice_sections(body, {"#### Notes": "written by the model"})
    assert filled == 0
    assert spliced == body


async def test_a_partial_first_fill_increments_the_attempt_counter(synced):
    """F2 write half: this is the page that would otherwise cost one LLM call
    every scan forever."""
    layout, config, _repo, worklist = synced
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert applied.stamped == 0
    document = load_bundle(layout.bundle_dir).concepts["code-graph/demo/entities/packages/widgets"]
    assert document.fm_raw[PROSE_ATTEMPTS_KEY] == 1


async def test_a_second_partial_first_fill_increments_again(synced):
    layout, config, _repo, worklist = synced
    for _ in range(2):
        apply_scan_results(
            worklist,
            _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED})),
            layout.bundle_dir,
            config,
            today=TODAY,
            dry_run=False,
        )
    document = load_bundle(layout.bundle_dir).concepts["code-graph/demo/entities/packages/widgets"]
    assert document.fm_raw[PROSE_ATTEMPTS_KEY] == 2


async def test_a_complete_fill_clears_the_attempt_counter(synced):
    layout, config, _repo, worklist = synced
    task = _task(worklist)
    apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={heading: FILLED for heading in task.prose_sections})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert applied.stamped == 1
    document = load_bundle(layout.bundle_dir).concepts["code-graph/demo/entities/packages/widgets"]
    assert PROSE_ATTEMPTS_KEY not in document.fm_raw


async def test_a_partial_diff_refresh_does_not_increment(synced):
    """The counter bounds a *decline* on a first fill. A diff refresh that
    lands one of two sections is not the same failure."""
    layout, config, _repo, worklist = synced
    from dataclasses import replace as dc_replace

    diff_worklist = dc_replace(
        worklist,
        prose_tasks=tuple(dc_replace(t, trigger="diff") if t.uri == PACKAGE_URI else t for t in worklist.prose_tasks),
    )
    apply_scan_results(
        diff_worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    document = load_bundle(layout.bundle_dir).concepts["code-graph/demo/entities/packages/widgets"]
    assert PROSE_ATTEMPTS_KEY not in document.fm_raw


async def test_a_narrated_page_refreshes_the_index_of_its_own_parent_directory(synced, monkeypatch):
    layout, config, _repo, worklist = synced
    seen: list[list[str]] = []

    def capture(bundle, *, directories, create_missing, dry_run):
        seen.append(list(directories))
        return ()

    monkeypatch.setattr("graph_works_core.scan.commands.update_index", capture)
    task = _task(worklist)
    apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={heading: FILLED for heading in task.prose_sections})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert seen == [["code-graph/demo/entities/packages"]]


def test_the_index_directory_of_a_repository_page_is_the_code_graph_index():
    from graph_works_core.scan.commands import _index_directory

    assert _index_directory("code-graph/demo.md") == "code-graph"
    assert _index_directory("code-graph/demo/entities/packages/widgets.md") == "code-graph/demo/entities/packages"


async def test_an_over_cap_purpose_never_lands_and_the_page_keeps_its_own(synced):
    """I1: the sanitizer emptied the answer entirely, but the task is a first
    fill, so it still counts as a decline -- only the body is untouched."""
    layout, config, _repo, worklist = synced
    task = _task(worklist)
    assert task.trigger == "first_fill"
    before_body = load_bundle(layout.bundle_dir).concepts["code-graph/demo/entities/packages/widgets"].body
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": "word " * 151})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert any("no usable section survived sanitizing" in error for error in applied.entity_errors)
    document = load_bundle(layout.bundle_dir).concepts["code-graph/demo/entities/packages/widgets"]
    assert document.body == before_body
    assert document.fm_raw[PROSE_ATTEMPTS_KEY] == 1


async def test_three_over_cap_declines_exhaust_the_attempts(synced):
    """I1: without this, a model that always overshoots the cap on a first
    fill is re-dispatched on every scan with no limit."""
    layout, config, _repo, worklist = synced
    for _ in range(3):
        apply_scan_results(
            worklist,
            _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": "word " * 151})),
            layout.bundle_dir,
            config,
            today=TODAY,
            dry_run=False,
        )
    next_worklist, _ = await build_scan_worklist(layout, config, today=TODAY, at=AT, dry_run=False)
    assert PACKAGE_URI not in [task.uri for task in next_worklist.prose_tasks]
    assert ("code-graph/demo/entities/packages/widgets.md", "attempts-exhausted") in [
        (s.page, s.reason) for s in next_worklist.skipped
    ]


async def test_a_diff_triggered_over_cap_answer_does_not_increment(synced):
    """The counter bounds a first-fill *decline*. An emptied-out diff refresh
    is a different failure and stays uncounted, mirroring
    `test_a_partial_diff_refresh_does_not_increment`."""
    layout, config, _repo, worklist = synced
    from dataclasses import replace as dc_replace

    diff_worklist = dc_replace(
        worklist,
        prose_tasks=tuple(dc_replace(t, trigger="diff") if t.uri == PACKAGE_URI else t for t in worklist.prose_tasks),
    )
    applied = apply_scan_results(
        diff_worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": "word " * 151})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert any("no usable section survived sanitizing" in error for error in applied.entity_errors)
    document = load_bundle(layout.bundle_dir).concepts["code-graph/demo/entities/packages/widgets"]
    assert PROSE_ATTEMPTS_KEY not in document.fm_raw


async def test_an_at_cap_purpose_lands(synced):
    layout, config, _repo, worklist = synced
    body = " ".join(["word"] * 150)
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": body})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert applied.sections_filled == 1
    assert body in _page(layout)
