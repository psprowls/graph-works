"""The wiki-page archive vertical: plan, apply, reconcile every touched lane."""

from __future__ import annotations

import importlib.resources
import unicodedata
from dataclasses import replace

import pytest
from doc_wiki_okf.archive import (
    ARCHIVE_IGNORE,
    IGNORE,
    PROPOSALS_DIRECTORY,
    WIKI_LANE_TYPES,
    _lane_of,
    apply_archive,
    plan_archive,
    wiki_lanes,
)
from doc_wiki_okf.diataxis.pages import directory_for
from doc_wiki_okf.resources import seeded_schema_set
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
target: docs/tutorials/new-idea.md
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
    """The on-disk path of *token*, whose lane may be multi-segment
    (`docs/tutorials/foo`): a page sits directly in its lane, so the slug is
    the last segment and everything before it is the lane."""
    lane, slug = token.rsplit("/", 1)
    dirname = f"{lane}/_archive" if archived else lane
    return root / dirname / f"{slug}.md"


def _schema_set():
    assets = importlib.resources.files("doc_wiki_okf") / "assets" / "schema"
    return load_schemas(str(assets))


#: The lanes every call site in this file plans against: the real derivation,
#: so these tests exercise the schema set rather than a parallel constant.
_LANES = wiki_lanes(_schema_set())


def test_a_targeted_archive_moves_the_page_regardless_of_status(tmp_path):
    bundle = _build(tmp_path, {"docs/tutorials/foo": _PAGE.format(title="Foo")})

    plan = plan_archive(bundle, ["docs/tutorials/foo"], lanes=_LANES)
    result = apply_archive(bundle, plan)

    assert result.archived == ("docs/tutorials/foo",)
    assert result.ok is True
    assert not _page_path(tmp_path, "docs/tutorials/foo").exists()
    assert _page_path(tmp_path, "docs/tutorials/foo", archived=True).is_file()
    assert any(index.path == "docs/tutorials/index.md" for index in result.indexes)
    assert any(index.path == "docs/tutorials/_archive/index.md" for index in result.indexes)


def test_a_targeted_token_with_a_differently_normalized_query_resolves_to_the_raw_disk_id(tmp_path):
    """`_select` must key off the raw disk id, not the human-typed query --
    see work/tech-debt-has-member-callers-raw-id."""
    nfd = unicodedata.normalize("NFD", "café")
    nfc = unicodedata.normalize("NFC", "café")
    bundle = _build(tmp_path, {f"docs/tutorials/{nfd}": _PAGE.format(title="Café")})

    plan = plan_archive(bundle, [f"docs/tutorials/{nfc}"], lanes=_LANES)

    assert plan.ok is True
    assert plan.tokens == (f"docs/tutorials/{nfd}",)
    assert [move.source for move in plan.moves.moves] == [f"docs/tutorials/{nfd}.md"]
    assert [move.dest for move in plan.moves.moves] == [f"docs/tutorials/_archive/{nfd}.md"]


def test_a_targeted_token_naming_no_page_is_unknown_member(tmp_path):
    bundle = _build(tmp_path, {"docs/tutorials/foo": _PAGE.format(title="Foo")})

    plan = plan_archive(bundle, ["docs/tutorials/missing"], lanes=_LANES)

    assert plan.tokens == ()
    assert [(s.token, s.reason) for s in plan.skipped] == [("docs/tutorials/missing", "unknown-member")]


def test_a_targeted_token_naming_an_excluded_lane_is_unknown_member(tmp_path):
    bundle = _build(tmp_path, {"docs/tutorials/foo": _PAGE.format(title="Foo")})

    plan = plan_archive(bundle, ["entities/foo", "work/foo", "not-a-lane"], lanes=_LANES)

    assert plan.tokens == ()
    assert {s.token for s in plan.skipped} == {"entities/foo", "work/foo", "not-a-lane"}
    assert all(s.reason == "unknown-member" for s in plan.skipped)


def test_an_already_archived_token_is_skipped(tmp_path):
    bundle = _build(tmp_path, {"docs/tutorials/foo": _PAGE.format(title="Foo")})
    apply_archive(bundle, plan_archive(bundle, ["docs/tutorials/foo"], lanes=_LANES))
    reloaded = _build(tmp_path, {})  # reload: the page now sits under _archive/

    plan = plan_archive(reloaded, ["docs/tutorials/foo"], lanes=_LANES)

    assert plan.tokens == ()
    assert [(s.token, s.reason) for s in plan.skipped] == [("docs/tutorials/foo", "already-archived")]


def test_sweep_archives_only_proposals_past_proposed(tmp_path):
    bundle = _build(
        tmp_path,
        {
            "proposals/still-open": _PROPOSAL.format(status="proposed"),
            "proposals/approved-one": _PROPOSAL.format(status="approved"),
            "proposals/rejected-one": _PROPOSAL.format(status="rejected"),
            "docs/tutorials/untouched": _PAGE.format(title="Untouched"),
        },
    )

    plan = plan_archive(bundle, lanes=_LANES)
    result = apply_archive(bundle, plan)

    assert result.archived == ("proposals/approved-one", "proposals/rejected-one")
    assert _page_path(tmp_path, "proposals/still-open").is_file()
    assert _page_path(tmp_path, "docs/tutorials/untouched").is_file()
    assert _page_path(tmp_path, "proposals/approved-one", archived=True).is_file()
    assert _page_path(tmp_path, "proposals/rejected-one", archived=True).is_file()


def test_a_sources_page_takes_its_reference_companion_with_it(tmp_path):
    _build(tmp_path, {"sources/2026-08-foo": _PAGE.format(title="Foo")})
    references = tmp_path / "sources" / "references"
    references.mkdir(parents=True, exist_ok=True)
    (references / "2026-08-foo.txt").write_text("material\n", encoding="utf-8")
    reloaded = _build(tmp_path, {})

    plan = plan_archive(reloaded, ["sources/2026-08-foo"], lanes=_LANES)
    result = apply_archive(reloaded, plan)

    assert result.archived == ("sources/2026-08-foo",)
    assert not (references / "2026-08-foo.txt").exists()
    assert (references / "_archive" / "2026-08-foo.txt").is_file()


def test_a_refused_plan_applies_nothing(tmp_path):
    bundle = _build(
        tmp_path,
        {
            "docs/tutorials/foo": _PAGE.format(title="Foo"),
            "docs/tutorials/_archive/foo": _PAGE.format(title="Stray"),
        },
    )

    plan = plan_archive(bundle, ["docs/tutorials/foo"], lanes=_LANES)
    result = apply_archive(bundle, plan)

    assert plan.ok is False
    assert result.archived == ()
    assert result.refusals != ()
    assert result.ok is False
    assert _page_path(tmp_path, "docs/tutorials/foo").is_file()
    assert plan.diff().startswith("! docs/tutorials/foo.md: dest-exists")


def test_plan_diff_and_changed_render_the_preview(tmp_path):
    bundle = _build(tmp_path, {"docs/tutorials/foo": _PAGE.format(title="Foo")})

    plan = plan_archive(bundle, ["docs/tutorials/foo", "unknown/x"], lanes=_LANES)

    assert plan.changed is True
    diff = plan.diff()
    assert "unknown/x: skipped (unknown-member)" in diff
    assert "docs/tutorials/foo.md -> docs/tutorials/_archive/foo.md" in diff

    empty_plan = plan_archive(bundle, ["unknown/x"], lanes=_LANES)
    assert empty_plan.changed is False
    assert empty_plan.tokens == ()


def test_plan_diff_renders_a_reference_rebase(tmp_path):
    bundle = _build(
        tmp_path,
        {
            "docs/tutorials/foo": _PAGE.format(title="Foo"),
            "docs/tutorials/other": _PAGE.format(title="Other") + "\nSee [Foo](foo.md) for background.\n",
        },
    )

    plan = plan_archive(bundle, ["docs/tutorials/foo"], lanes=_LANES)

    assert any(edit.member == "docs/tutorials/other.md" for edit in plan.moves.edits)
    assert "~ docs/tutorials/other.md:" in plan.diff()


def test_sweep_with_no_eligible_proposals_touches_nothing(tmp_path):
    bundle = _build(tmp_path, {"proposals/still-open": _PROPOSAL.format(status="proposed")})

    plan = plan_archive(bundle, lanes=_LANES)
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

    plan = plan_archive(reloaded, ["sources/2026-08-foo"], lanes=_LANES)
    result = apply_archive(reloaded, plan)

    assert result.archived == ("sources/2026-08-foo",)
    assert not (references / "2026-08-foo.txt").exists()
    assert (references / "_archive" / "2026-08-foo.txt").is_file()
    assert (references / "2026-08-bar.txt").is_file()  # different stem: left alone
    assert (nested / "2026-08-foo.txt").is_file()  # nested: not a top-level companion


def test_the_authors_words_survive_the_lane_crossing(tmp_path):
    bundle = _build(tmp_path, {"docs/tutorials/foo": _PAGE.format(title="Foo")})
    update_index(bundle, directories=["docs/tutorials"], create_missing=True, dry_run=False)

    index_path = tmp_path / "docs" / "tutorials" / "index.md"
    original = index_path.read_text(encoding="utf-8")
    assert " - d" in original
    index_path.write_text(original.replace(" - d", " - Hand-authored blurb."), encoding="utf-8")

    reloaded = _build(tmp_path, {})
    plan = plan_archive(reloaded, ["docs/tutorials/foo"], lanes=_LANES)
    result = apply_archive(reloaded, plan)

    assert result.archived == ("docs/tutorials/foo",)
    archived_index = (tmp_path / "docs" / "tutorials" / "_archive" / "index.md").read_text(encoding="utf-8")
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


# --- the stranded-wikilink count (2026-08-21 spec §4.4) ---------------------

_CITING = """---
title: Citing
description: d
---

## Summary
See [[docs/tutorials/foo]] for the rest.
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
    loud = _build(tmp_path / "loud", {"docs/tutorials/foo": _PAGE.format(title="Foo"), "reference/citing": _CITING})
    quiet = _build(tmp_path / "quiet", {"docs/tutorials/foo": _PAGE.format(title="Foo"), "reference/citing": _QUIET})

    loud_plan = plan_archive(loud, ["docs/tutorials/foo"], lanes=_LANES)
    quiet_plan = plan_archive(quiet, ["docs/tutorials/foo"], lanes=_LANES)

    assert loud_plan.ok and quiet_plan.ok  # never a reason a plan is not ok
    assert [entry.target for entry in loud_plan.moves.stranded] == ["docs/tutorials/foo.md"]
    assert quiet_plan.moves.stranded == ()
    assert loud_plan.diff() != quiet_plan.diff()
    assert "1 inbound [[wikilink]]" in loud_plan.diff()
    assert "inbound [[wikilink]]" not in quiet_plan.diff()


# --- the schema-derived lane vocabulary ------------------------------------


def test_wiki_lanes_derives_every_declared_lane_from_the_schema_set() -> None:
    """The drift guard: the archive vocabulary and the schemas cannot disagree,
    because there is only one of them."""
    schema_set = _schema_set()
    lanes = wiki_lanes(schema_set)
    for type_name in WIKI_LANE_TYPES:
        assert directory_for(schema_set, type_name).rstrip("/") in lanes


def test_wiki_lanes_carries_proposals_which_no_shipped_schema_declares() -> None:
    """`doc-wiki-okf` ships six schemas, not seven (`resources.SEED_RELATIVE_PATHS`):
    `proposals/` is a fixed convention, named once, like `ADR_DIRECTORY`."""
    assert "Proposal" not in _schema_set().schemas
    assert PROPOSALS_DIRECTORY.rstrip("/") in wiki_lanes(_schema_set())


def test_wiki_lanes_is_ordered_longest_first() -> None:
    """A prefix lookup over an unsorted tuple can match `docs` before
    `docs/explanations`. The producer sorts so no consumer has to."""
    lanes = wiki_lanes(_schema_set())
    assert list(lanes) == sorted(lanes, key=len, reverse=True)


def test_wiki_lanes_raises_for_a_schema_set_missing_a_wiki_type() -> None:
    """Configuration, not bundle content -- the same contract `directory_for`
    and `lane_set` already state."""
    partial = replace(_schema_set(), schemas={"Tutorial": {"x-okf-directory": "docs/tutorials/"}})
    with pytest.raises(KeyError):
        wiki_lanes(partial)


def test_the_seeded_schema_set_is_this_packages_own_assets() -> None:
    """The fallback `run_archive` uses when a workspace has no `.gw/schema/`."""
    assert tuple(sorted(seeded_schema_set().schemas)) == tuple(sorted(_schema_set().schemas))


# --- multi-segment lanes ---------------------------------------------------

_DOCS_LANES = ("docs/explanations", "docs/how-tos", "docs/tutorials", "docs/reference", "proposals", "sources", "adrs")


def test_lane_of_resolves_a_multi_segment_lane() -> None:
    assert _lane_of("docs/explanations/foo", _DOCS_LANES) == "docs/explanations"


def test_lane_of_rejects_a_token_deeper_than_its_lane() -> None:
    """A page sits directly in its lane. That contract is preserved, not
    relaxed, by the move to a prefix match."""
    assert _lane_of("docs/explanations/a/b", _DOCS_LANES) is None


def test_lane_of_rejects_an_unknown_first_segment() -> None:
    assert _lane_of("repositories/foo", _DOCS_LANES) is None
    assert _lane_of("work/foo", _DOCS_LANES) is None
    assert _lane_of("docs/foo", _DOCS_LANES) is None


def test_lane_of_rejects_a_bare_word_and_a_trailing_slash() -> None:
    assert _lane_of("explanations", _DOCS_LANES) is None
    assert _lane_of("docs/explanations/", _DOCS_LANES) is None


def test_lane_of_prefers_the_longer_lane_whatever_order_it_is_given() -> None:
    """`_lane_of` sorts defensively: a caller-supplied tuple need not already
    be longest-first for the lookup to be right."""
    unsorted = ("docs", "docs/explanations")
    assert _lane_of("docs/explanations/foo", unsorted) == "docs/explanations"


def test_a_targeted_archive_round_trips_in_a_multi_segment_lane(tmp_path) -> None:
    """The whole point: under the `docs/` layout the page lands in the lane's
    own `_archive/`, and both the lane index and its `_archive/` form
    reconcile -- not `docs/_archive/` and not `docs/index.md`.

    The lane index also carries a link to the moved page, so `moves` itself
    would want to rewrite that link (a `RefEdit` on `docs/explanations/index.md`)
    were `plan_archive`'s lane-index filter not dropping it first --
    `update_index` owns index content end to end, and a leftover moves-edit on
    top of it is exactly the double-write `_lane_of`'s filter exists to
    prevent (the same reason `work_tracker_okf.archive` filters
    `_LANE_INDEXES`)."""
    bundle = _build(
        tmp_path,
        {
            "docs/explanations/foo": _PAGE.format(title="Foo"),
            "docs/explanations/index": _PAGE.format(title="Explanations") + "\n[Foo](/docs/explanations/foo.md)\n",
        },
    )

    plan = plan_archive(bundle, ["docs/explanations/foo"], lanes=_DOCS_LANES)
    result = apply_archive(bundle, plan)

    assert result.archived == ("docs/explanations/foo",)
    assert result.ok
    assert (tmp_path / "docs/explanations/_archive/foo.md").is_file()
    assert not (tmp_path / "docs/explanations/foo.md").exists()
    assert not (tmp_path / "docs/_archive").exists()
    written = {update.path for update in result.indexes}
    assert "docs/explanations/index.md" in written
    assert "docs/explanations/_archive/index.md" in written
    assert "docs/index.md" not in written
    assert all(edit.member != "docs/explanations/index.md" for edit in plan.moves.edits)


def test_the_plan_carries_the_lanes_it_was_given(tmp_path) -> None:
    bundle = _build(tmp_path, {"docs/explanations/foo": _PAGE.format(title="Foo")})
    plan = plan_archive(bundle, ["docs/explanations/foo"], lanes=_DOCS_LANES)
    assert plan.lanes == tuple(_DOCS_LANES)


def test_a_sweep_skips_a_proposal_that_sits_in_no_lane(tmp_path) -> None:
    """`list_proposals` enumerates by `type:`, not by path, so a `Proposal`
    outside every lane can come back. It has no lane to build an `_archive/`
    path under, so it is not a sweep candidate -- previously it computed a
    nonsense destination from its first path segment."""
    bundle = _build(
        tmp_path,
        {
            "proposals/good": _PROPOSAL.format(status="approved"),
            "unknown/stray": _PROPOSAL.format(status="approved"),
        },
    )
    plan = plan_archive(bundle, lanes=_DOCS_LANES)
    assert plan.tokens == ("proposals/good",)
    assert plan.skipped == ()


def test_the_diataxis_lanes_live_under_docs() -> None:
    """The relocation, asserted once at the source of truth. `reference` is
    singular; `how-tos` keeps its hyphen."""
    assert wiki_lanes(_schema_set()) == (
        "docs/explanations",
        "docs/tutorials",
        "docs/reference",
        "docs/how-tos",
        "proposals",
        "sources",
        "adrs",
    )
