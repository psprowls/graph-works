"""The wiki-page archive vertical: plan, apply, reconcile every touched lane."""

from __future__ import annotations

import importlib.resources

from doc_wiki_okf.archive import ARCHIVE_IGNORE, IGNORE, WIKI_LANES, apply_archive, plan_archive
from doc_wiki_okf.diataxis.pages import directory_for
from doc_wiki_okf.proposals.lanes import ADR_DIRECTORY
from okf_ext.schemas import load_schemas
from okf_io import load_bundle, update_index

_PAGE = """---
title: {title}
description: d
---

## Summary
d
"""

_PROPOSAL = """---
type: Proposal
target: tutorials/new-idea.md
title: New idea
description: d
page_status: {status}
---

## Summary
d
"""


def _build(root, pages):
    """Write *pages* (concept id -> full page text) under *root* and load it
    through `ARCHIVE_IGNORE`, the lens `plan_archive`/`apply_archive` want."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    for concept_id, text in pages.items():
        target = root / f"{concept_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return load_bundle(root, ignore=ARCHIVE_IGNORE)


def _page_path(root, token, *, archived=False):
    lane, slug = token.split("/", 1)
    dirname = f"{lane}/_archive" if archived else lane
    return root / dirname / f"{slug}.md"


def test_a_targeted_archive_moves_the_page_regardless_of_status(tmp_path):
    bundle = _build(tmp_path, {"tutorials/foo": _PAGE.format(title="Foo")})

    plan = plan_archive(bundle, ["tutorials/foo"])
    result = apply_archive(bundle, plan)

    assert result.archived == ("tutorials/foo",)
    assert result.ok is True
    assert not _page_path(tmp_path, "tutorials/foo").exists()
    assert _page_path(tmp_path, "tutorials/foo", archived=True).is_file()
    assert any(index.path == "tutorials/index.md" for index in result.indexes)
    assert any(index.path == "tutorials/_archive/index.md" for index in result.indexes)


def test_a_targeted_token_naming_no_page_is_unknown_member(tmp_path):
    bundle = _build(tmp_path, {"tutorials/foo": _PAGE.format(title="Foo")})

    plan = plan_archive(bundle, ["tutorials/missing"])

    assert plan.tokens == ()
    assert [(s.token, s.reason) for s in plan.skipped] == [("tutorials/missing", "unknown-member")]


def test_a_targeted_token_naming_an_excluded_lane_is_unknown_member(tmp_path):
    bundle = _build(tmp_path, {"tutorials/foo": _PAGE.format(title="Foo")})

    plan = plan_archive(bundle, ["entities/foo", "work/foo", "not-a-lane"])

    assert plan.tokens == ()
    assert {s.token for s in plan.skipped} == {"entities/foo", "work/foo", "not-a-lane"}
    assert all(s.reason == "unknown-member" for s in plan.skipped)


def test_an_already_archived_token_is_skipped(tmp_path):
    bundle = _build(tmp_path, {"tutorials/foo": _PAGE.format(title="Foo")})
    apply_archive(bundle, plan_archive(bundle, ["tutorials/foo"]))
    reloaded = _build(tmp_path, {})  # reload: the page now sits under _archive/

    plan = plan_archive(reloaded, ["tutorials/foo"])

    assert plan.tokens == ()
    assert [(s.token, s.reason) for s in plan.skipped] == [("tutorials/foo", "already-archived")]


def test_sweep_archives_only_proposals_past_proposed(tmp_path):
    bundle = _build(
        tmp_path,
        {
            "proposals/still-open": _PROPOSAL.format(status="proposed"),
            "proposals/approved-one": _PROPOSAL.format(status="approved"),
            "proposals/rejected-one": _PROPOSAL.format(status="rejected"),
            "tutorials/untouched": _PAGE.format(title="Untouched"),
        },
    )

    plan = plan_archive(bundle)
    result = apply_archive(bundle, plan)

    assert result.archived == ("proposals/approved-one", "proposals/rejected-one")
    assert _page_path(tmp_path, "proposals/still-open").is_file()
    assert _page_path(tmp_path, "tutorials/untouched").is_file()
    assert _page_path(tmp_path, "proposals/approved-one", archived=True).is_file()
    assert _page_path(tmp_path, "proposals/rejected-one", archived=True).is_file()


def test_a_sources_page_takes_its_reference_companion_with_it(tmp_path):
    _build(tmp_path, {"sources/2026-08-foo": _PAGE.format(title="Foo")})
    references = tmp_path / "sources" / "references"
    references.mkdir(parents=True, exist_ok=True)
    (references / "2026-08-foo.txt").write_text("material\n", encoding="utf-8")
    reloaded = _build(tmp_path, {})

    plan = plan_archive(reloaded, ["sources/2026-08-foo"])
    result = apply_archive(reloaded, plan)

    assert result.archived == ("sources/2026-08-foo",)
    assert not (references / "2026-08-foo.txt").exists()
    assert (references / "_archive" / "2026-08-foo.txt").is_file()


def test_a_refused_plan_applies_nothing(tmp_path):
    bundle = _build(
        tmp_path,
        {
            "tutorials/foo": _PAGE.format(title="Foo"),
            "tutorials/_archive/foo": _PAGE.format(title="Stray"),
        },
    )

    plan = plan_archive(bundle, ["tutorials/foo"])
    result = apply_archive(bundle, plan)

    assert plan.ok is False
    assert result.archived == ()
    assert result.refusals != ()
    assert result.ok is False
    assert _page_path(tmp_path, "tutorials/foo").is_file()
    assert plan.diff().startswith("! tutorials/foo.md: dest-exists")


def test_plan_diff_and_changed_render_the_preview(tmp_path):
    bundle = _build(tmp_path, {"tutorials/foo": _PAGE.format(title="Foo")})

    plan = plan_archive(bundle, ["tutorials/foo", "unknown/x"])

    assert plan.changed is True
    diff = plan.diff()
    assert "unknown/x: skipped (unknown-member)" in diff
    assert "tutorials/foo.md -> tutorials/_archive/foo.md" in diff

    empty_plan = plan_archive(bundle, ["unknown/x"])
    assert empty_plan.changed is False
    assert empty_plan.tokens == ()


def test_plan_diff_renders_a_reference_rebase(tmp_path):
    bundle = _build(
        tmp_path,
        {
            "tutorials/foo": _PAGE.format(title="Foo"),
            "tutorials/other": _PAGE.format(title="Other") + "\nSee [Foo](foo.md) for background.\n",
        },
    )

    plan = plan_archive(bundle, ["tutorials/foo"])

    assert any(edit.member == "tutorials/other.md" for edit in plan.moves.edits)
    assert "~ tutorials/other.md:" in plan.diff()


def test_sweep_with_no_eligible_proposals_touches_nothing(tmp_path):
    bundle = _build(tmp_path, {"proposals/still-open": _PROPOSAL.format(status="proposed")})

    plan = plan_archive(bundle)
    result = apply_archive(bundle, plan)

    assert plan.tokens == ()
    assert result.archived == ()
    assert result.indexes == ()


def test_reference_companions_are_matched_by_stem_and_top_level_only(tmp_path):
    _build(tmp_path, {"sources/2026-08-foo": _PAGE.format(title="Foo")})
    references = tmp_path / "sources" / "references"
    references.mkdir(parents=True, exist_ok=True)
    (references / "2026-08-foo.txt").write_text("material\n", encoding="utf-8")
    (references / "2026-08-bar.txt").write_text("unrelated\n", encoding="utf-8")
    nested = references / "nested"
    nested.mkdir()
    (nested / "2026-08-foo.txt").write_text("nested copy\n", encoding="utf-8")
    reloaded = _build(tmp_path, {})

    plan = plan_archive(reloaded, ["sources/2026-08-foo"])
    result = apply_archive(reloaded, plan)

    assert result.archived == ("sources/2026-08-foo",)
    assert not (references / "2026-08-foo.txt").exists()
    assert (references / "_archive" / "2026-08-foo.txt").is_file()
    assert (references / "2026-08-bar.txt").is_file()  # different stem: left alone
    assert (nested / "2026-08-foo.txt").is_file()  # nested: not a top-level companion


def test_the_authors_words_survive_the_lane_crossing(tmp_path):
    bundle = _build(tmp_path, {"tutorials/foo": _PAGE.format(title="Foo")})
    update_index(bundle, directories=["tutorials"], create_missing=True, dry_run=False)

    index_path = tmp_path / "tutorials" / "index.md"
    original = index_path.read_text(encoding="utf-8")
    assert " - d" in original
    index_path.write_text(original.replace(" - d", " - Hand-authored blurb."), encoding="utf-8")

    reloaded = _build(tmp_path, {})
    plan = plan_archive(reloaded, ["tutorials/foo"])
    result = apply_archive(reloaded, plan)

    assert result.archived == ("tutorials/foo",)
    archived_index = (tmp_path / "tutorials" / "_archive" / "index.md").read_text(encoding="utf-8")
    assert "Hand-authored blurb." in archived_index


#: Written out literally, not recomputed from the tier-2 defaults (C1-D). The
#: composition is this package's exported contract, so it must break loudly if
#: either default moves -- recomputing it here would make the test agree with
#: whatever okf-ext happens to say today.
_EXPECTED_IGNORE = (
    "sources/references/*",
    "*/sources/references/*",
    "*/.DS_Store",
    "schema/*",
    "*/schema/*",
    "sections/*",
    "*/sections/*",
)

#: Written out literally for the same reason `_EXPECTED_IGNORE` is: this is
#: the package's second exported recipe, and recomputing it from the tier-2
#: defaults would make the test agree with whatever okf-ext says today.
_EXPECTED_ARCHIVE_IGNORE = (
    "schema/*",
    "*/schema/*",
    "sections/*",
    "*/sections/*",
)


def test_ignore_is_the_composed_recipe_written_out() -> None:
    assert IGNORE == _EXPECTED_IGNORE


def test_archive_ignore_is_the_composed_recipe_written_out() -> None:
    assert ARCHIVE_IGNORE == _EXPECTED_ARCHIVE_IGNORE


def test_the_two_recipes_differ_by_exactly_the_move_blind_patterns() -> None:
    """Every entry in the delta is there for one reason: `okf_ext.moves` never
    reads `bundle.ignored`, so anything hidden from the archive's own lens is
    left behind by the move -- a `sources/` page's `references/` companions in
    the first two cases, and in the third a file that keeps `apply` from
    pruning the directory it emptied."""
    assert tuple(pattern for pattern in IGNORE if pattern not in ARCHIVE_IGNORE) == (
        "sources/references/*",
        "*/sources/references/*",
        "*/.DS_Store",
    )
    assert tuple(pattern for pattern in ARCHIVE_IGNORE if pattern not in IGNORE) == ()


def _schema_set():
    assets = importlib.resources.files("doc_wiki_okf") / "assets" / "schema"
    return load_schemas(str(assets))


_LANE_TYPES = {
    "tutorials": "Tutorial",
    "how-tos": "HowTo",
    "references": "Reference",
    "explanations": "Explanation",
    "sources": "Source",
}


def test_wiki_lanes_matches_the_schema_and_module_source_of_truth() -> None:
    schema_set = _schema_set()
    for lane, type_name in _LANE_TYPES.items():
        assert lane in WIKI_LANES
        assert lane == directory_for(schema_set, type_name).rstrip("/")
    assert "adrs" in WIKI_LANES
    assert ADR_DIRECTORY.rstrip("/") == "adrs"
    # "proposals" is the one genuinely fixed convention with no other module
    # constant to check against, matching the module's own docstring rationale.
    assert "proposals" in WIKI_LANES


# --- the stranded-wikilink count (2026-08-21 spec §4.4) ---------------------

_CITING = """---
title: Citing
description: d
---

## Summary
See [[tutorials/foo]] for the rest.
"""

_QUIET = """---
title: Citing
description: d
---

## Summary
Nothing points anywhere.
"""


def test_a_wikilink_only_vault_and_a_quiet_vault_produce_different_plans(tmp_path):
    """The item's done-when, as a test: the count is what tells the two apart."""
    loud = _build(tmp_path / "loud", {"tutorials/foo": _PAGE.format(title="Foo"), "reference/citing": _CITING})
    quiet = _build(tmp_path / "quiet", {"tutorials/foo": _PAGE.format(title="Foo"), "reference/citing": _QUIET})

    loud_plan = plan_archive(loud, ["tutorials/foo"])
    quiet_plan = plan_archive(quiet, ["tutorials/foo"])

    assert loud_plan.ok and quiet_plan.ok  # never a reason a plan is not ok
    assert [entry.target for entry in loud_plan.moves.stranded] == ["tutorials/foo.md"]
    assert quiet_plan.moves.stranded == ()
    assert loud_plan.diff() != quiet_plan.diff()
    assert "1 inbound [[wikilink]]" in loud_plan.diff()
    assert "inbound [[wikilink]]" not in quiet_plan.diff()
