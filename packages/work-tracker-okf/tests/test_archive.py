"""The archive path's acceptance surface — spec §9's twelve entries."""

import shutil
from pathlib import Path

import pytest
from okf_ext import moves
from okf_io import build_link_graph, load_bundle, update_index
from work_helpers import make_terminal
from work_tracker_okf import archive
from work_tracker_okf.items import ARCHIVE_IGNORE, IGNORE, load_items
from work_tracker_okf.paths import item_page

_FEATURE = "2026-03-02-epic-feature-filing-writer"
_SPIKE = "2026-03-03-spike-path-layout-questions"
_OPEN_BUG = "2026-03-04-bug-slug-prefix-mismatch"
_ARCHIVED = "2026-03-07-epic-feature-archived-child"


def _archive_bundle(root: Path):
    return load_bundle(root, ignore=ARCHIVE_IGNORE)


def test_the_conformant_vault_is_already_index_reconciled(conformant_root: Path) -> None:
    """Spec §8. The fixture was authored before anything reconciled it;
    `update_index` wants a `# Subdirectories` entry for `_archive` whether or
    not an archive ever runs, so the fixture's steady state is made its
    reconciled state. Without this, every archive assertion would have to
    tolerate one unrelated addition."""
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    updates = update_index(bundle, directories=["work", "work/_archive"])
    assert [u.path for u in updates if u.changed] == []


def test_sweep_selects_every_terminal_item_and_reports_no_skips(conformant_root: Path) -> None:
    """C4-G. `work_io` appended a skip for every non-terminal page it walked
    past; a sweep's non-candidates were never candidates."""
    plan = archive.plan_archive(_archive_bundle(conformant_root))
    assert plan.slugs == (_SPIKE,)
    assert plan.skipped == ()


def test_sweep_picks_up_an_item_made_terminal(conformant_root: Path) -> None:
    make_terminal(conformant_root, _FEATURE)
    plan = archive.plan_archive(_archive_bundle(conformant_root))
    assert plan.slugs == (_FEATURE, _SPIKE)


def test_targeted_mode_answers_every_slug_it_was_given(conformant_root: Path) -> None:
    """Spec test 11: the three reasons, and one record per named slug."""
    plan = archive.plan_archive(
        _archive_bundle(conformant_root),
        slugs=[_SPIKE, _OPEN_BUG, _ARCHIVED, "no-such-slug"],
    )
    assert plan.slugs == (_SPIKE,)
    assert {(s.slug, s.reason) for s in plan.skipped} == {
        (_OPEN_BUG, "not-terminal"),
        (_ARCHIVED, "already-archived"),
        ("no-such-slug", "unknown-slug"),
    }
    assert all(s.detail for s in plan.skipped)
    rendered = plan.diff()
    for skip in plan.skipped:
        assert f"- {skip.slug}: skipped ({skip.reason})" in rendered


def test_the_lens_regression(conformant_root: Path) -> None:
    """Spec test 1 — the reason §2 exists, and not redundant with the tests
    below: planned through `IGNORE` the plan reports `ok` and the archive is
    silently half done."""
    make_terminal(conformant_root, _FEATURE)

    narrow = archive.plan_archive(load_bundle(conformant_root, ignore=IGNORE), slugs=[_FEATURE])
    assert len(narrow.moves.moves) == 1
    assert [e for e in narrow.moves.edits if e.where == "frontmatter"] == []

    wide = archive.plan_archive(_archive_bundle(conformant_root), slugs=[_FEATURE])
    assert len(wide.moves.moves) == 4
    assert len([e for e in wide.moves.edits if e.where == "frontmatter"]) == 3
    rendered = wide.diff()
    assert any(f"~ {edit.member}:" in rendered for edit in wide.moves.edits)


def test_one_batch_carries_the_page_and_every_working_directory_member(conformant_root: Path) -> None:
    """C4-H. Spec test 3, at plan level."""
    make_terminal(conformant_root, _FEATURE)
    plan = archive.plan_archive(_archive_bundle(conformant_root), slugs=[_FEATURE])
    assert {(m.source, m.dest) for m in plan.moves.moves} == {
        (f"work/{_FEATURE}.md", f"work/_archive/{_FEATURE}.md"),
        (
            f"work/{_FEATURE}/references/01-design-spec.md",
            f"work/_archive/{_FEATURE}/references/01-design-spec.md",
        ),
        (
            f"work/{_FEATURE}/references/02-plan-plan.md",
            f"work/_archive/{_FEATURE}/references/02-plan-plan.md",
        ),
        (
            f"work/{_FEATURE}/references/03-execute-transcript.jsonl",
            f"work/_archive/{_FEATURE}/references/03-execute-transcript.jsonl",
        ),
    }


def test_the_lane_indexes_are_filtered_out_of_the_plan(conformant_root: Path) -> None:
    """C4-B. `moves` scans index.md deliberately, so the unfiltered plan does
    carry an edit for it — which is why the filter is a real subtraction and
    not a no-op."""
    make_terminal(conformant_root, _FEATURE)
    bundle = _archive_bundle(conformant_root)

    unfiltered = moves.plan_move(bundle, f"work/{_FEATURE}.md", f"work/_archive/{_FEATURE}.md")
    assert any(e.member == "work/index.md" for e in unfiltered.edits)

    plan = archive.plan_archive(bundle, slugs=[_FEATURE])
    assert not any(e.member in {"work/index.md", "work/_archive/index.md"} for e in plan.moves.edits)
    assert plan.ok


def test_a_pre_existing_twin_refuses_the_plan(conformant_root: Path) -> None:
    """Spec test 9, plan half."""
    twin = item_page(_SPIKE, archived=True).path(conformant_root)
    twin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(item_page(_SPIKE).path(conformant_root), twin)

    plan = archive.plan_archive(_archive_bundle(conformant_root), slugs=[_SPIKE])
    assert not plan.ok
    assert {r.kind for r in plan.moves.refusals} == {"dest-exists"}
    rendered = plan.diff()
    assert any(f"! {refusal.path}: {refusal.kind}" in rendered for refusal in plan.moves.refusals)


def test_an_empty_plan_is_ok_and_unchanged(conformant_root: Path) -> None:
    plan = archive.plan_archive(_archive_bundle(conformant_root), slugs=[_OPEN_BUG])
    assert plan.slugs == ()
    assert plan.ok
    assert not plan.changed


def test_diff_renders_and_writes_nothing(conformant_root: Path) -> None:
    before = sorted(p.relative_to(conformant_root).as_posix() for p in conformant_root.rglob("*") if p.is_file())
    plan = archive.plan_archive(_archive_bundle(conformant_root))
    rendered = plan.diff()
    assert _SPIKE in rendered
    after = sorted(p.relative_to(conformant_root).as_posix() for p in conformant_root.rglob("*") if p.is_file())
    assert before == after


def _archive(root: Path, slugs: list[str] | None = None) -> archive.ArchiveResult:
    bundle = _archive_bundle(root)
    return archive.apply_archive(bundle, archive.plan_archive(bundle, slugs))


def test_both_paths_move_and_every_source_is_repaired(conformant_root: Path) -> None:
    """Spec tests 3 and 4."""
    make_terminal(conformant_root, _FEATURE)
    result = _archive(conformant_root, [_FEATURE])
    assert result.ok, result.move.failed
    assert result.archived == (_FEATURE,)

    assert not (conformant_root / "work" / f"{_FEATURE}.md").exists()
    assert (conformant_root / "work" / "_archive" / f"{_FEATURE}.md").is_file()
    for name in ("01-design-spec.md", "02-plan-plan.md", "03-execute-transcript.jsonl"):
        assert (conformant_root / "work" / "_archive" / _FEATURE / "references" / name).is_file()

    bundle = load_bundle(conformant_root, ignore=IGNORE)
    item = next(i for i in load_items(bundle) if i.slug == _FEATURE)
    assert item.archived
    assert [s.resource for s in item.sources] == [
        f"/work/_archive/{_FEATURE}/references/01-design-spec.md",
        f"/work/_archive/{_FEATURE}/references/02-plan-plan.md",
        f"/work/_archive/{_FEATURE}/references/03-execute-transcript.jsonl",
    ]
    for source in item.sources:
        assert bundle.has_member(source.resource[1:])


def test_a_prune_failure_is_tolerated_and_the_slug_stays_out_of_pruned(
    conformant_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `except OSError: continue` arm of `_prune_working_directories` --
    matching `moves._prune`'s own best-effort posture (C4-I). A directory that
    fails to remove simply does not appear in `pruned`; it is not a failure of
    the archive as a whole."""
    make_terminal(conformant_root, _FEATURE)
    target = conformant_root / "work" / _FEATURE
    real_rmdir = Path.rmdir

    def flaky_rmdir(self: Path) -> None:
        if self == target:
            raise OSError("simulated: cannot remove directory")
        real_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", flaky_rmdir)

    result = _archive(conformant_root, [_FEATURE])
    assert result.ok
    assert result.archived == (_FEATURE,)
    assert result.pruned == ()
    assert target.is_dir()


def test_the_working_directory_is_gone_not_merely_its_references_child(conformant_root: Path) -> None:
    """Spec test 7. C4-I: `moves._prune` declines proper ancestors by design,
    so `work/<slug>/` survives its own emptying and this package removes it."""
    make_terminal(conformant_root, _FEATURE)
    result = _archive(conformant_root, [_FEATURE])
    assert result.move.pruned == (f"work/{_FEATURE}/references",)
    assert result.pruned == (f"work/{_FEATURE}",)
    assert not (conformant_root / "work" / _FEATURE).exists()


def test_the_item_is_listed_once_under_archive_and_not_at_all_under_work(conformant_root: Path) -> None:
    """Spec test 5 — the §3.1 regression. Unfiltered, `moves` repairs the
    active entry's link and `update_index` has no reason to prune it, so the
    item ends up listed in both files."""
    make_terminal(conformant_root, _FEATURE)
    _archive(conformant_root, [_FEATURE])

    active = (conformant_root / "work" / "index.md").read_text(encoding="utf-8")
    archived = (conformant_root / "work" / "_archive" / "index.md").read_text(encoding="utf-8")
    assert _FEATURE not in active
    assert archived.count(_FEATURE) == 1


def test_the_authors_words_survive_the_lane_crossing(conformant_root: Path) -> None:
    """Spec test 6. C4-C: filtering the index edits would otherwise re-introduce
    exactly the loss `moves`' index scan exists to prevent."""
    make_terminal(conformant_root, _FEATURE)
    _archive(conformant_root, [_FEATURE])
    archived = (conformant_root / "work" / "_archive" / "index.md").read_text(encoding="utf-8")
    assert "the filing writer. A conformant fixture item." in archived


def test_a_refused_plan_returns_its_refusals_and_writes_nothing(conformant_root: Path) -> None:
    """Spec test 9, apply half. C4-F: `moves.apply`'s `ValueError` is
    unreachable through this package's API."""
    twin = item_page(_SPIKE, archived=True).path(conformant_root)
    twin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(item_page(_SPIKE).path(conformant_root), twin)

    before = {
        p.relative_to(conformant_root).as_posix(): p.read_bytes() for p in conformant_root.rglob("*") if p.is_file()
    }
    bundle = _archive_bundle(conformant_root)
    plan = archive.plan_archive(bundle, slugs=[_SPIKE])
    result = archive.apply_archive(bundle, plan)

    assert not result.ok
    assert result.archived == ()
    assert result.indexes == ()
    assert {r.kind for r in result.refusals} == {"dest-exists"}
    after = {
        p.relative_to(conformant_root).as_posix(): p.read_bytes() for p in conformant_root.rglob("*") if p.is_file()
    }
    assert after == before


def test_the_archived_vault_still_validates_with_zero_errors(conformant_root: Path) -> None:
    from okf_ext.schemas import load_schemas, schema_rule
    from okf_ext.sections import section_rule
    from okf_ext.shape import load_sections
    from okf_io import validate
    from work_helpers import CONFORMANT_TODAY

    make_terminal(conformant_root, _FEATURE)
    assert _archive(conformant_root, [_FEATURE]).ok

    bundle = load_bundle(conformant_root, ignore=IGNORE)
    report = validate(
        bundle,
        today=CONFORMANT_TODAY,
        extra_rules=(
            schema_rule(load_schemas(conformant_root / "_schema"), severity="error"),
            section_rule(load_sections(conformant_root / "_sections"), severity="error"),
        ),
    )
    assert [f"{f.code} {f.path}: {f.message}" for f in report.errors] == []


def test_cross_item_references_rebase_in_one_sweep(conformant_root: Path) -> None:
    """Spec test 8. C4-H's correctness argument: planned separately, the first
    plan would rebase a reference to a path the second plan is about to move.

    Both ends sit in `work/`, so both are relative to the *same* directory
    before and after — which is why the destinations come out unchanged, and
    why an empty `broken` slice is the assertion that actually has teeth here.
    """
    make_terminal(conformant_root, _FEATURE)
    page = conformant_root / "work" / f"{_SPIKE}.md"
    page.write_text(
        page.read_text(encoding="utf-8")
        + f"\nSee [the filing writer]({_FEATURE}.md) and its [spec]({_FEATURE}/references/01-design-spec.md).\n",
        encoding="utf-8",
    )

    result = _archive(conformant_root)
    assert result.ok, result.move.failed
    assert result.archived == (_FEATURE, _SPIKE)

    moved = (conformant_root / "work" / "_archive" / f"{_SPIKE}.md").read_text(encoding="utf-8")
    assert f"({_FEATURE}.md)" in moved
    assert f"({_FEATURE}/references/01-design-spec.md)" in moved

    graph = build_link_graph(load_bundle(conformant_root, ignore=IGNORE))
    assert [link.raw for link in graph.broken if link.source == f"work/_archive/{_SPIKE}"] == []


def test_create_missing_conjures_the_archive_index(conformant_root: Path) -> None:
    """Spec test 10a. C4-D: `init` scaffolds neither lane index, so a first
    archive into a fresh `_archive/` would otherwise write none at all."""
    index = conformant_root / "work" / "_archive" / "index.md"
    index.unlink()

    result = _archive(conformant_root, [_SPIKE])
    assert result.ok
    assert index.is_file()
    assert _SPIKE in index.read_text(encoding="utf-8")
    assert [u.path for u in result.indexes if u.created] == ["work/_archive/index.md"]


def test_harvesting_tolerates_a_vault_with_no_active_index_yet(conformant_root: Path) -> None:
    """`_harvest`'s guard: a bundle that has never run `update_index` on
    `work/` yet (no `work/index.md`) has nothing to harvest, not an error --
    the archived entry then falls back to the document's own `description`."""
    (conformant_root / "work" / "index.md").unlink()

    result = _archive(conformant_root, [_SPIKE])
    assert result.ok
    archived = (conformant_root / "work" / "_archive" / "index.md").read_text(encoding="utf-8")
    assert "Path layout questions. A conformant fixture item." in archived


def test_a_no_op_archive_touches_neither_index(conformant_root: Path) -> None:
    """Spec test 10b. Otherwise a sweep finding nothing would still rewrite
    `work/index.md`."""
    active = conformant_root / "work" / "index.md"
    archived = conformant_root / "work" / "_archive" / "index.md"
    before = (active.read_bytes(), archived.read_bytes())

    result = _archive(conformant_root, [_OPEN_BUG])
    assert result.archived == ()
    assert result.indexes == ()
    assert (active.read_bytes(), archived.read_bytes()) == before


def test_a_wikilink_is_left_exactly_as_written(conformant_root: Path) -> None:
    """Spec test 12 and §11's first limitation, pinned so it is a recorded
    decision rather than an omission. A wikilink is not a link form OKF v0.2
    defines, `okf_io` does not see one, and nothing in `moves` can repair one.
    Binding on the live-vault migration item, which inherits it as a
    prerequisite to its first archive."""
    page = conformant_root / "work" / f"{_SPIKE}.md"
    page.write_text(
        page.read_text(encoding="utf-8") + f"\nSee [[work/{_FEATURE}]] for the writer.\n",
        encoding="utf-8",
    )

    assert _archive(conformant_root, [_SPIKE]).ok
    moved = (conformant_root / "work" / "_archive" / f"{_SPIKE}.md").read_text(encoding="utf-8")
    assert f"[[work/{_FEATURE}]]" in moved
