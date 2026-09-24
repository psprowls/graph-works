"""Phase 1: what the front half decides is stale, and why."""

from __future__ import annotations

import pytest
from code_wiki_okf.config import load_config
from code_wiki_okf.placement import PlacementError
from graph_works_core.scan.commands import (
    PROSE_ANCHOR_KEY,
    PROSE_ATTEMPTS_KEY,
    apply_scan_results,
    build_scan_worklist,
    entity_refs,
    is_unfilled,
    prose_specs,
)
from graph_works_core.scan.scan_contract import ProseRefreshResult, ScanResults
from okf_ext.bundle import SECTIONS_DIRNAME
from scan_helpers import (
    APP_URI,
    AT,
    DEPENDENCY_URI,
    DOTTED_PACKAGE_URI,
    PACKAGE_URI,
    REPO_URI,
    TODAY,
    commit_change,
    entity_page,
    git,
    make_workspace,
    package_page,
    seed_graph,
    write_page,
)

FILLED = "Widgets is the demo package. It exists to exercise this pipeline."


@pytest.fixture
def scanned(tmp_path):
    """A workspace whose graph is seeded and whose structural pass has not run yet."""
    layout, repo = make_workspace(tmp_path)
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    seed_graph(config.graph_dir, repo)
    return layout, config, repo


async def _worklist(layout, config, **kwargs):
    kwargs.setdefault("dry_run", False)
    worklist, summary = await build_scan_worklist(layout, config, today=TODAY, at=AT, **kwargs)
    return worklist, summary


def _package_task(worklist):
    return next(task for task in worklist.prose_tasks if task.uri == PACKAGE_URI)


async def test_a_placeholder_body_is_a_first_fill(scanned):
    layout, config, _repo = scanned
    worklist, summary = await _worklist(layout, config)
    task = _package_task(worklist)
    assert task.trigger == "first_fill"
    assert set(task.prose_sections) == {"## Purpose", "## Public API"}
    assert task.page_path == "code-graph/demo/entities/packages/widgets.md"
    assert summary.entities.written  # the structural pass ran and wrote pages


async def test_equal_anchors_on_a_written_page_are_not_stale(scanned):
    layout, config, repo = scanned
    await _worklist(layout, config)  # let the structural pass create the page
    head = git(repo, "rev-parse", "HEAD")
    write_page(
        layout,
        "code-graph/demo/entities/packages/widgets.md",
        package_page(purpose=FILLED, public_api=FILLED, last_updated_commit=head, prose_refreshed_commit=head),
    )
    worklist, _ = await _worklist(layout, config)
    # Not a whole-worklist emptiness check: the structural pass also scaffolds
    # `code-graph/demo.md` with its own placeholder prose (§3.2's own
    # `first_fill` rule, correctly applied to a page this suite never fills),
    # so it legitimately stays in every worklist below. The property under
    # test is the *package* page's own staleness classification.
    assert PACKAGE_URI not in [task.uri for task in worklist.prose_tasks]


async def test_differing_anchors_with_an_empty_scoped_diff_are_not_stale(scanned):
    layout, config, repo = scanned
    await _worklist(layout, config)
    old = git(repo, "rev-parse", "HEAD")
    commit_change(repo, "README.md", "outside the entity root\n")
    write_page(
        layout,
        "code-graph/demo/entities/packages/widgets.md",
        package_page(purpose=FILLED, public_api=FILLED, last_updated_commit=old, prose_refreshed_commit=old),
    )
    # The page's own anchors are equal here, so re-stamp `last_updated_commit`
    # the way the structural pass does before asking again.
    worklist, _ = await _worklist(layout, config)
    # See the note in test_equal_anchors_on_a_written_page_are_not_stale: the
    # repository page's own placeholder prose keeps it in every worklist here,
    # so the property under test is scoped to the package page.
    assert PACKAGE_URI not in [task.uri for task in worklist.prose_tasks]


async def test_differing_anchors_with_a_real_diff_carry_every_prose_heading(scanned):
    layout, config, repo = scanned
    await _worklist(layout, config)
    old = git(repo, "rev-parse", "HEAD")
    commit_change(repo, "packages/widgets/src/a.py", "def alpha():\n    return 2\n")
    write_page(
        layout,
        "code-graph/demo/entities/packages/widgets.md",
        package_page(purpose=FILLED, public_api=FILLED, prose_refreshed_commit=old),
    )
    worklist, _ = await _worklist(layout, config)
    task = _package_task(worklist)
    assert task.trigger == "diff"
    assert set(task.prose_sections) == {"## Purpose", "## Public API"}
    assert task.changed_files == ("packages/widgets/src/a.py",)


async def test_an_unknown_anchor_is_a_diff_with_no_diff(scanned):
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(
        layout,
        "code-graph/demo/entities/packages/widgets.md",
        package_page(purpose=FILLED, public_api=FILLED, prose_refreshed_commit="0" * 40),
    )
    worklist, _ = await _worklist(layout, config)
    task = _package_task(worklist)
    assert (task.trigger, task.diff) == ("diff", None)


async def test_an_absent_anchor_on_written_prose_is_not_staleness(scanned):
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(layout, "code-graph/demo/entities/packages/widgets.md", package_page(purpose=FILLED, public_api=FILLED))
    worklist, _ = await _worklist(layout, config)
    # Still not a task -- but no longer *frozen*: `entities.sync` reconciles
    # this page (its `resource:` still resolves) and stamps `last_updated_commit`
    # to HEAD unconditionally as part of that pass, so by the time phase 1 reads
    # it back the page has an anchor-less, filled, dated page -- exactly the
    # adoption case (D5), not a silent skip.
    assert PACKAGE_URI not in [task.uri for task in worklist.prose_tasks]
    assert "code-graph/demo/entities/packages/widgets.md" in worklist.adopted


async def test_max_entities_truncates_from_the_tail_and_reports_the_drop(scanned):
    layout, config, _repo = scanned
    full, _ = await _worklist(layout, config)
    assert len(full.prose_tasks) >= 2
    bounded, _ = await _worklist(layout, config, max_entities=1)
    assert len(bounded.prose_tasks) == 1
    assert bounded.truncated == len(full.prose_tasks) - 1
    assert bounded.prose_tasks[0].uri == full.prose_tasks[0].uri


async def test_a_prose_section_added_to_a_declaration_enters_the_worklist(scanned):
    """The load-bearing property (spec §3.2): the declaration decides the
    surface, so this passes with no change to any module in this package."""
    layout, config, _repo = scanned
    declaration = config.declarations_dir / SECTIONS_DIRNAME / "Package.yaml"
    declaration.write_text(
        declaration.read_text(encoding="utf-8")
        + "  - heading: Operational notes\n    placeholder: |\n      > TODO: <how this is run>\n",
        encoding="utf-8",
    )
    worklist, _ = await _worklist(layout, config)
    assert "## Operational notes" in _package_task(worklist).prose_sections


async def test_an_unbuildable_graph_refuses(tmp_path):
    from graph_works_core.workspace.errors import ScanError

    layout, _repo = make_workspace(tmp_path)
    # No `code.db` and no repository git dir the builder can read: the build
    # comes back non-SUCCESS and phase 1 refuses rather than narrating nothing.
    layout.manifest_path.write_text(
        "version: 1\nrepositories: {}\nstate_gate:\n  enabled: false\n",
        encoding="utf-8",
    )
    with pytest.raises(ScanError):
        await build_scan_worklist(
            layout,
            load_config(
                layout.bundle_dir,
                config_path=layout.manifest_path,
                graph_dir=layout.cache_dir,
                declarations_dir=layout.config_dir,
            ),
            today=TODAY,
            at=AT,
            dry_run=False,
        )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("packages/widgets", "packages/widgets"),
        ("packages/foo.bar", "packages/foo.bar"),
        ("plugins/demo-plugin", "plugins/demo-plugin"),
        (".claude-plugin", ".claude-plugin"),
        ("tests", "tests"),
        ("", ""),
        (".", ""),
        (None, ""),
    ],
)
def test_a_lane_nodes_path_is_used_as_the_directory_it_is(path, expected):
    from code_graph_io import NodeRecord
    from graph_works_core.scan.commands import _entity_relative_root

    node = NodeRecord(kind="package", name="x", path=path, line=None, attrs={})
    assert _entity_relative_root(node) == expected


async def test_a_dotted_package_directory_scopes_its_diff_to_itself(scanned):
    """F6: `packages/foo.bar` is a directory, not a manifest. A `path.suffix`
    heuristic reads `.bar` as an extension and widens the scope to `packages/`,
    which makes every commit under any sibling package read as staleness."""
    layout, config, repo = scanned
    await _worklist(layout, config)
    old = git(repo, "rev-parse", "HEAD")
    # A change under a *sibling* package. Scoped correctly this is invisible to
    # `packages/foo.bar`; scoped to `packages/` it is staleness.
    commit_change(repo, "packages/widgets/src/a.py", "def alpha():\n    return 2\n")
    write_page(
        layout,
        "code-graph/demo/entities/packages/foobar.md",
        entity_page(
            "Package",
            title="foobar",
            resource=DOTTED_PACKAGE_URI,
            bodies={"Purpose": FILLED, "Public API": FILLED},
            prose_refreshed_commit=old,
        ),
    )
    worklist, _ = await _worklist(layout, config)
    assert DOTTED_PACKAGE_URI not in [task.uri for task in worklist.prose_tasks]


async def test_a_dotted_package_directory_still_sees_its_own_changes(scanned):
    """The other half: correct scoping must not become no scoping."""
    layout, config, repo = scanned
    await _worklist(layout, config)
    old = git(repo, "rev-parse", "HEAD")
    commit_change(repo, "packages/foo.bar/src/b.py", "def beta():\n    return 2\n")
    write_page(
        layout,
        "code-graph/demo/entities/packages/foobar.md",
        entity_page(
            "Package",
            title="foobar",
            resource=DOTTED_PACKAGE_URI,
            bodies={"Purpose": FILLED, "Public API": FILLED},
            prose_refreshed_commit=old,
        ),
    )
    worklist, _ = await _worklist(layout, config)
    task = next(t for t in worklist.prose_tasks if t.uri == DOTTED_PACKAGE_URI)
    assert task.trigger == "diff"
    assert task.changed_files == ("packages/foo.bar/src/b.py",)


def _repo_page(layout, *, prose_refreshed_commit=None):
    write_page(
        layout,
        "code-graph/demo.md",
        entity_page(
            "Repository",
            title="demo",
            resource=REPO_URI,
            bodies={"Overview": FILLED, "Layout": FILLED},
            prose_refreshed_commit=prose_refreshed_commit,
        ),
    )


async def test_a_change_deep_inside_a_package_does_not_make_the_repository_stale(scanned):
    """F7: `Repository.yaml` declares `Overview` and `Layout`. Neither is
    affected by an edit inside a package, so "did anything in this repo change"
    is the wrong gate for them."""
    layout, config, repo = scanned
    await _worklist(layout, config)
    old = git(repo, "rev-parse", "HEAD")
    commit_change(repo, "packages/widgets/src/a.py", "def alpha():\n    return 2\n")
    _repo_page(layout, prose_refreshed_commit=old)
    worklist, _ = await _worklist(layout, config)
    assert REPO_URI not in [task.uri for task in worklist.prose_tasks]


async def test_a_root_level_change_does_make_the_repository_stale(scanned):
    layout, config, repo = scanned
    await _worklist(layout, config)
    old = git(repo, "rev-parse", "HEAD")
    commit_change(repo, "README.md", "# demo\n\nnow with prose\n")
    _repo_page(layout, prose_refreshed_commit=old)
    worklist, _ = await _worklist(layout, config)
    task = next(t for t in worklist.prose_tasks if t.uri == REPO_URI)
    assert task.trigger == "diff"
    assert task.changed_files == ("README.md",)


async def test_a_new_manifest_anywhere_makes_the_repository_stale(scanned):
    """A package appearing or disappearing is what "the layout changed" looks
    like on disk -- the one case `Layout` exists to describe."""
    layout, config, repo = scanned
    await _worklist(layout, config)
    old = git(repo, "rev-parse", "HEAD")
    commit_change(repo, "packages/gadgets/pyproject.toml", "[project]\nname = 'gadgets'\n")
    _repo_page(layout, prose_refreshed_commit=old)
    worklist, _ = await _worklist(layout, config)
    task = next(t for t in worklist.prose_tasks if t.uri == REPO_URI)
    assert task.changed_files == ("packages/gadgets/pyproject.toml",)


def test_a_change_set_cut_at_the_cap_says_so():
    """F10: character truncation is signalled by `truncate_text`; the file-count
    cut was not, so the model read a partial change set as complete.

    The marker leads the rendered diff rather than trailing it: the prompt's
    own `truncate_text(..., MAX_PROMPT_DIFF_CHARS)` cuts from the end, so a
    trailing marker would be discarded in exactly the case that produces it.
    """
    from graph_works_core.scan.commands import MAX_TASK_DIFF_FILES, _render_diff

    changed = [f"src/f{n}.py" for n in range(MAX_TASK_DIFF_FILES + 31)]
    rendered, kept = _render_diff(changed)
    assert len(kept) == MAX_TASK_DIFF_FILES
    assert rendered.startswith(f"[TRUNCATED after {MAX_TASK_DIFF_FILES} of {MAX_TASK_DIFF_FILES + 31} changed files]")
    assert rendered.endswith(f"\nsrc/f{MAX_TASK_DIFF_FILES - 1}.py")


def test_a_change_set_under_the_cap_carries_no_marker():
    from graph_works_core.scan.commands import _render_diff

    rendered, kept = _render_diff(["src/a.py", "src/b.py"])
    assert rendered == "src/a.py\nsrc/b.py"
    assert kept == ("src/a.py", "src/b.py")
    assert "TRUNCATED" not in rendered


async def test_a_page_whose_resource_no_longer_resolves_is_reported_not_dropped(scanned):
    """F5: a drifted `resource:` used to be indistinguishable from an
    up-to-date page -- both simply absent from the worklist.

    Filled prose, not the default placeholders: `entities.sync`'s own
    `prune_lane` deletes a lane page outright the moment its `resource:`
    fails to resolve, *unless* a declared prose section holds real content
    (`entities/delete.py`'s `_decline_reason`) -- in which case it declines
    the delete and leaves the page for phase 1 to find. A placeholder-only
    ghost page never reaches `_classify_pages` at all; it is gone before phase 1
    runs, which is the correct behaviour for a ghost with nothing to lose.
    """
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(
        layout,
        "code-graph/demo/entities/packages/ghost.md",
        entity_page(
            "Package", title="ghost", resource="pkg:acme/demo/ghost", bodies={"Purpose": FILLED, "Public API": FILLED}
        ),
    )
    worklist, _ = await _worklist(layout, config)
    assert ("code-graph/demo/entities/packages/ghost.md", "unresolved-resource") in [
        (s.page, s.reason) for s in worklist.skipped
    ]


async def test_a_non_code_wiki_type_under_a_code_wiki_directory_is_ignored(scanned):
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(
        layout,
        "code-graph/demo/entities/packages/odd.md",
        '---\ntype: Concept\ntitle: "odd"\ndescription: ""\n---\n\n## Purpose\n\nprose\n',
    )
    worklist, _ = await _worklist(layout, config)
    assert not [s for s in worklist.skipped if s.page == "code-graph/demo/entities/packages/odd.md"]


async def test_a_page_outside_the_entity_lanes_is_never_reported(scanned):
    """Every concept, ADR and source page in a real vault would otherwise land
    in `skipped` on every scan, which is noise, not diagnosis."""
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(
        layout,
        "concepts/some-idea.md",
        '---\ntype: Concept\ntitle: "some idea"\ndescription: ""\n---\n\n## Summary\n\nprose\n',
    )
    worklist, _ = await _worklist(layout, config)
    assert not [s for s in worklist.skipped if s.page.startswith("concepts/")]


async def test_a_lane_typed_page_outside_the_entity_lanes_is_never_reported(scanned):
    """The `in_lane` gate covers all four skip reasons uniformly, not just
    `parse-error` and `unknown-type`: a page whose `type` IS a lane type but
    which lives outside every entity lane -- here with a `resource:` that also
    fails to resolve -- must still produce no `SkippedPage`. It was never a
    candidate for this vertical regardless of which check it would otherwise
    fail."""
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(
        layout,
        "concepts/stray.md",
        entity_page("Package", title="stray", resource="pkg:acme/demo/does-not-exist"),
    )
    worklist, _ = await _worklist(layout, config)
    assert not [s for s in worklist.skipped if s.page.startswith("concepts/")]


async def test_a_written_page_with_no_anchor_is_adopted_without_a_model_call(scanned):
    """F3/D5: the gate used to freeze a hand-written page forever, and the
    literal §5.2 reading would have rewritten every one of them wholesale."""
    layout, config, repo = scanned
    await _worklist(layout, config)
    head = git(repo, "rev-parse", "HEAD")
    write_page(
        layout,
        "code-graph/demo/entities/packages/widgets.md",
        package_page(purpose=FILLED, public_api=FILLED, last_updated_commit=head),
    )
    worklist, _ = await _worklist(layout, config)
    assert "code-graph/demo/entities/packages/widgets.md" in worklist.adopted
    assert PACKAGE_URI not in [task.uri for task in worklist.prose_tasks]

    from okf_io import load_bundle

    document = load_bundle(layout.bundle_dir).concepts["code-graph/demo/entities/packages/widgets"]
    assert document.fm_raw[PROSE_ANCHOR_KEY] == head
    assert FILLED in document.body  # the prose itself is untouched


async def test_adoption_clears_a_stale_attempt_counter(scanned):
    """Adoption is the recovery path for a page that stalled out mid-refresh
    (F2's attempt cap) and was then finished by hand. Leaving
    `prose_refresh_attempts` behind would let a later declared-section change
    (`entities.sync` scaffolding a fresh placeholder) find the page already
    exhausted, with zero attempts left for the new section."""
    layout, config, repo = scanned
    await _worklist(layout, config)
    head = git(repo, "rev-parse", "HEAD")
    write_page(
        layout,
        "code-graph/demo/entities/packages/widgets.md",
        package_page(purpose=FILLED, public_api=FILLED, last_updated_commit=head, prose_refresh_attempts=3),
    )
    worklist, _ = await _worklist(layout, config)
    assert "code-graph/demo/entities/packages/widgets.md" in worklist.adopted

    from okf_io import load_bundle

    document = load_bundle(layout.bundle_dir).concepts["code-graph/demo/entities/packages/widgets"]
    assert document.fm_raw[PROSE_ANCHOR_KEY] == head
    assert PROSE_ATTEMPTS_KEY not in document.fm_raw


async def test_an_adopted_page_diff_tracks_on_the_next_commit(scanned):
    layout, config, repo = scanned
    await _worklist(layout, config)
    head = git(repo, "rev-parse", "HEAD")
    write_page(
        layout,
        "code-graph/demo/entities/packages/widgets.md",
        package_page(purpose=FILLED, public_api=FILLED, last_updated_commit=head),
    )
    await _worklist(layout, config)  # adoption run
    commit_change(repo, "packages/widgets/src/a.py", "def alpha():\n    return 3\n")
    worklist, _ = await _worklist(layout, config)
    task = _package_task(worklist)
    assert task.trigger == "diff"
    assert task.changed_files == ("packages/widgets/src/a.py",)


async def test_a_page_with_no_anchor_and_an_unfilled_section_still_first_fills(scanned):
    layout, config, repo = scanned
    await _worklist(layout, config)
    head = git(repo, "rev-parse", "HEAD")
    write_page(
        layout, "code-graph/demo/entities/packages/widgets.md", package_page(purpose=FILLED, last_updated_commit=head)
    )
    worklist, _ = await _worklist(layout, config)
    assert "code-graph/demo/entities/packages/widgets.md" not in worklist.adopted
    assert _package_task(worklist).trigger == "first_fill"


async def test_a_dry_run_reports_an_adoption_without_writing_it(scanned):
    layout, config, repo = scanned
    await _worklist(layout, config)
    head = git(repo, "rev-parse", "HEAD")
    path = write_page(
        layout,
        "code-graph/demo/entities/packages/widgets.md",
        package_page(purpose=FILLED, public_api=FILLED, last_updated_commit=head),
    )
    before = path.read_text(encoding="utf-8")
    worklist, _ = await _worklist(layout, config, dry_run=True)
    assert "code-graph/demo/entities/packages/widgets.md" in worklist.adopted
    assert path.read_text(encoding="utf-8") == before


async def test_a_page_at_the_attempt_cap_stops_being_dispatched(scanned):
    """F2/D1: the system prompt tells the model to omit a heading it cannot say
    something true about, and the refill gate refuses to stamp while any section
    is still a placeholder -- so without a bound the page costs one LLM call
    every scan, forever."""
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(
        layout, "code-graph/demo/entities/packages/widgets.md", package_page(purpose=FILLED, prose_refresh_attempts=3)
    )
    worklist, _ = await _worklist(layout, config)
    assert PACKAGE_URI not in [task.uri for task in worklist.prose_tasks]
    assert ("code-graph/demo/entities/packages/widgets.md", "attempts-exhausted") in [
        (s.page, s.reason) for s in worklist.skipped
    ]


async def test_a_page_below_the_attempt_cap_is_still_dispatched(scanned):
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(
        layout, "code-graph/demo/entities/packages/widgets.md", package_page(purpose=FILLED, prose_refresh_attempts=2)
    )
    worklist, _ = await _worklist(layout, config)
    assert _package_task(worklist).trigger == "first_fill"


async def test_a_real_diff_dispatches_a_page_even_at_the_cap(scanned):
    """The counter bounds *retrying a decline*, not refreshing a change."""
    layout, config, repo = scanned
    await _worklist(layout, config)
    old = git(repo, "rev-parse", "HEAD")
    commit_change(repo, "packages/widgets/src/a.py", "def alpha():\n    return 4\n")
    write_page(
        layout,
        "code-graph/demo/entities/packages/widgets.md",
        package_page(purpose=FILLED, prose_refreshed_commit=old, prose_refresh_attempts=9),
    )
    worklist, _ = await _worklist(layout, config)
    assert PACKAGE_URI in [task.uri for task in worklist.prose_tasks]


async def test_a_malformed_attempt_counter_reads_as_zero(scanned):
    layout, config, _repo = scanned
    await _worklist(layout, config)
    page = package_page(purpose=FILLED).replace(
        "---\n\n## Purpose", 'prose_refresh_attempts: "many"\n---\n\n## Purpose'
    )
    write_page(layout, "code-graph/demo/entities/packages/widgets.md", page)
    worklist, _ = await _worklist(layout, config)
    assert _package_task(worklist).trigger == "first_fill"


async def test_dependency_refs_are_repository_owned(scanned):
    """A dependency carries its repository's path and HEAD (D-004)."""
    from code_graph_io import open_reader
    from graph_works_core.graph import commands as graph

    layout, config, _repo = scanned
    await _worklist(layout, config)
    reader = open_reader(graph_dir=graph.graph_target(layout).graph_dir)
    try:
        refs = entity_refs(reader, config)
    finally:
        reader.close()

    ref = refs[DEPENDENCY_URI]
    assert ref.type_name == "Dependency"
    assert ref.repo_path == config.repos[0].path
    assert ref.head is not None
    assert ref.relative_root == ""
    assert ref.describe_identifier == DEPENDENCY_URI.removeprefix("dependency:")


async def test_an_unfilled_dependency_page_first_fills_against_its_repository(scanned):
    """A dependency page with nothing filled is a first fill, and the task
    is scoped to the owning repository's root and HEAD (D-004)."""
    layout, config, repo = scanned
    await _worklist(layout, config)
    write_page(
        layout,
        "code-graph/demo/entities/dependencies/pypi/httpx.md",
        entity_page("Dependency", title="httpx", resource=DEPENDENCY_URI),
    )
    worklist, _ = await _worklist(layout, config)
    task = next(t for t in worklist.prose_tasks if t.uri == DEPENDENCY_URI)
    assert task.trigger == "first_fill"
    assert task.entity_root == str(repo)
    assert task.owning_short_head is not None


async def test_a_repository_absent_from_the_graph_contributes_no_refs(tmp_path):
    """`entity_refs` skips it, matching `entities.sync`'s own skip."""
    from code_graph_io import open_reader
    from code_wiki_okf.config import load_config as _load
    from graph_works_core.graph import commands as graph

    layout, repo = make_workspace(tmp_path)
    config = _load(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    seed_graph(config.graph_dir, repo)
    # Rename the declared repository so no graph node answers to it.
    text = layout.manifest_path.read_text(encoding="utf-8")
    layout.manifest_path.write_text(text.replace("demo:", "ghost:"), encoding="utf-8")
    renamed = _load(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    graph.build(graph.graph_target(layout))
    reader = open_reader(graph_dir=graph.graph_target(layout).graph_dir)
    try:
        refs = entity_refs(reader, renamed)
    finally:
        reader.close()
    assert not [uri for uri in refs if uri.startswith("pkg:acme/")]


def test_a_dependency_node_with_no_uri_contributes_no_ref(tmp_path):
    """The dependency loop's own `if not uri: continue` -- distinct from a
    repository absent from the graph, this is a dependency node the graph
    itself never gave a `uri` attribute."""
    from code_graph_io import open_reader
    from code_graph_io.testing import raw_conn

    layout, repo = make_workspace(tmp_path)
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    seed_graph(config.graph_dir, repo)
    conn = raw_conn(config.graph_dir / "code.db", create=True)
    try:
        with conn:
            conn.execute(
                "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri, repo) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                (99, "dependency", "orphan", "dependency:acme/demo:pypi:orphan", "{}", None, None),
            )
    finally:
        conn.close()
    reader = open_reader(graph_dir=config.graph_dir)
    try:
        refs = entity_refs(reader, config)
    finally:
        reader.close()
    assert not [ref for ref in refs.values() if ref.name == "orphan"]


def test_prose_specs_is_empty_for_an_undeclared_type():
    from pathlib import Path

    from okf_ext.shape import SectionSet

    section_set = SectionSet(types={}, sources={}, fragments={}, root=Path())
    assert prose_specs(section_set, "Nonesuch") == ()


def test_a_section_absent_from_the_page_is_not_unfilled():
    """Scaffolding a missing declared section is `entities.sync`'s job, and
    phase 3 could not splice into a heading that is not there."""
    assert is_unfilled(None, "> TODO: x") is False


def test_a_type_that_declares_no_prose_sections_yields_no_task(tmp_path):
    """`_classify_pages`'s "specs empty" branch: every shipped declaration
    currently carries at least one `ownership: prose` section, so this branch
    can only be reached by a declaration set built for the purpose."""
    from graph_works_core.scan.commands import _classify_pages, _EntityRef
    from okf_ext.shape import SectionSet, SectionSpec, TypeSections
    from okf_io import load_bundle

    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "packages").mkdir(parents=True)
    (bundle_dir / "packages" / "widgets.md").write_text(
        '---\ntype: Package\ntitle: "widgets"\nresource: "pkg:acme/demo/widgets"\ndescription: ""\n---\n\n'
        "## Files\n\n_(none)_\n",
        encoding="utf-8",
    )
    bundle = load_bundle(bundle_dir)
    section_set = SectionSet(
        types={"Package": TypeSections(sections=(SectionSpec(heading="Files", ownership="generated"),))},
        sources={},
        fragments={},
        root=bundle_dir,
    )
    ref = _EntityRef(
        uri="pkg:acme/demo/widgets",
        type_name="Package",
        describe_kind="package",
        describe_identifier="widgets",
        name="widgets",
        repo_path=None,
        relative_root="",
        head=None,
    )
    classification = _classify_pages(
        bundle,
        section_set,
        {"pkg:acme/demo/widgets": ref},
        reader=None,  # never touched: "specs empty" returns before any reader read
        bundle_root=bundle_dir,
    )
    assert classification.tasks == ()
    assert classification.skipped == ()


async def test_a_page_whose_type_does_not_match_its_resource_refuses_sync(scanned):
    """The composite preflight refuses a duplicate resource before any write."""
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(
        layout,
        "code-graph/demo/entities/apps/mismatch.md",
        entity_page(
            "App",
            title="mismatch",
            resource=PACKAGE_URI,
            bodies={
                "Purpose": FILLED,
                "Platform & runtime": FILLED,
                "Routes / screens": FILLED,
                "Provider chain": FILLED,
            },
        ),
    )
    with pytest.raises(PlacementError, match="duplicate resource"):
        await _worklist(layout, config)


async def test_an_unparseable_page_is_not_claimed_by_its_directory(scanned):
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(layout, "code-graph/demo/entities/packages/broken.md", "---\ntype: [Package\n---\n\n## Purpose\n\nx\n")
    worklist, _ = await _worklist(layout, config)
    assert not [s for s in worklist.skipped if s.page == "code-graph/demo/entities/packages/broken.md"]


async def test_a_parse_error_outside_the_entity_lanes_is_never_reported(scanned):
    """The `in_lane` gate covers `parse-error` too, not only the other three
    reasons -- a page outside every entity lane was never a candidate regardless
    of which check would otherwise fail it."""
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(layout, "concepts/broken.md", "---\ntype: [Concept\n---\n\n## Summary\n\nx\n")
    worklist, _ = await _worklist(layout, config)
    assert not [s for s in worklist.skipped if s.page.startswith("concepts/")]


async def test_a_misplaced_code_wiki_type_refuses_composite_sync(scanned):
    layout, config, _repo = scanned
    await _worklist(layout, config)
    write_page(
        layout,
        "concepts/mismatch.md",
        entity_page("Package", title="mismatch", resource=APP_URI),
    )
    with pytest.raises(PlacementError, match="duplicate resource"):
        await _worklist(layout, config)


def test_an_unopenable_graph_raises_scan_error(tmp_path):
    from graph_works_core.graph.commands import GraphTarget
    from graph_works_core.scan.commands import _open_reader
    from graph_works_core.workspace.errors import ScanError

    with pytest.raises(ScanError, match="cannot open the code graph"):
        _open_reader(GraphTarget(graph_dir=tmp_path / "nope"))


def _results(*results: ProseRefreshResult) -> ScanResults:
    return ScanResults(prose=results)


async def test_a_result_for_a_missing_page_is_reported(scanned):
    layout, config, _repo = scanned
    worklist, _ = await _worklist(layout, config)
    (layout.bundle_dir / "code-graph" / "demo" / "entities" / "packages" / "widgets.md").unlink()
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert any("missing or did not parse" in message for message in applied.entity_errors)


async def test_a_result_whose_headings_are_not_on_the_page_is_reported(scanned):
    """Sanitizing keeps a declared heading; the splice then cannot find it
    because the page's body no longer carries it."""
    layout, config, _repo = scanned
    worklist, _ = await _worklist(layout, config)
    path = layout.bundle_dir / "code-graph" / "demo" / "entities" / "packages" / "widgets.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("## Purpose", "## Rationale"), encoding="utf-8")
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert any("no declared section could be located" in message for message in applied.entity_errors)


async def test_a_missing_log_document_skips_the_log_append_without_erroring(scanned):
    """The `reconciled.logs.get("") is None` branch: a bundle that carries no
    root `log.md` at apply time must still land the page and stamp the anchor,
    it just has nowhere to append a line."""
    layout, config, _repo = scanned
    worklist, _ = await _worklist(layout, config)
    (layout.bundle_dir / "log.md").unlink()
    applied = apply_scan_results(
        worklist,
        _results(ProseRefreshResult(uri=PACKAGE_URI, sections={"## Purpose": FILLED, "## Public API": FILLED})),
        layout.bundle_dir,
        config,
        today=TODAY,
        dry_run=False,
    )
    assert applied.narrated == 1
    assert applied.stamped == 1
    assert not (layout.bundle_dir / "log.md").exists()
