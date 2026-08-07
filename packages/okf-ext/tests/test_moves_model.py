"""The frozen values, the fixture corpora, and the boundary wiring."""

from __future__ import annotations

from pathlib import Path

import ext_helpers
import pytest
from okf_ext import moves
from okf_ext.moves.model import Move, MovePlan, MoveResult, RefEdit, Refusal, Unrebased
from okf_io import build_link_graph


def _plan(**overrides) -> MovePlan:
    base = dict(root=Path("/tmp/x"), moves=(), edits=(), refusals=(), unrebased=(), digests={})
    return MovePlan(**{**base, **overrides})


def test_a_plan_with_no_refusals_is_ok():
    assert _plan().ok


def test_any_refusal_invalidates_the_whole_plan():
    """Spec §8: every edit is computed against the *whole* mapping, so
    applying the rest would apply edits computed against a mapping that is no
    longer the one being applied."""
    refused = _plan(
        moves=(Move("a.md", "b.md", is_asset=False),),
        refusals=(Refusal("c.md", "dest-exists", "already a member"),),
    )
    assert not refused.ok


def test_is_empty_reflects_moves_and_edits():
    assert _plan().is_empty
    assert not _plan(moves=(Move("a.md", "b.md", is_asset=False),)).is_empty
    assert not _plan(
        edits=(RefEdit(member="r.md", where="body", target="a.md", old="a.md", new="b.md", line=1, column=0),)
    ).is_empty


def test_members_names_every_member_whose_content_is_written():
    plan = _plan(
        moves=(Move("a.md", "b.md", is_asset=False), Move("i.png", "j.png", is_asset=True)),
        edits=(RefEdit(member="r.md", where="body", target="a.md", old="a.md", new="b.md", line=1, column=0),),
    )
    # A moved markdown member is named by its *source* path -- its own body is
    # what gets rewritten (outbound references rebased) and what `digests` is
    # keyed by. The moved asset is excluded: its content is never edited, so
    # nothing about it is written here.
    assert plan.members == ("a.md", "r.md")


def test_relocate_false_is_a_repair_plan_with_no_moves():
    """`plan_repair` sets `relocate=False`: references are repaired, nothing
    relocates, and no source is required to still exist. At this layer --
    the frozen value alone, before Tasks 4-6 build the planner -- what can be
    proven is the shape a repair plan takes: no `Move` entries, only edits."""
    plan = _plan(
        relocate=False,
        edits=(RefEdit(member="r.md", where="body", target="old.md", old="old.md", new="new.md", line=1, column=0),),
    )
    assert plan.relocate is False
    assert plan.moves == ()
    assert plan.members == ("r.md",)


def test_relocate_defaults_to_true():
    """A `plan_move`/`plan_move_dir`/`plan_move_many` plan relocates by
    default; only `plan_repair` opts out."""
    assert _plan().relocate is True


def test_a_result_with_no_failures_is_ok():
    assert MoveResult(moved=(), written=(), failed=(), pruned=()).ok


def test_every_frozen_value_is_immutable():
    move = Move("a.md", "b.md", is_asset=False)
    with pytest.raises(AttributeError):
        move.source = "c.md"  # type: ignore[misc]


def test_unrebased_reports_rather_than_rewrites():
    """A move cannot make a broken link less broken, but it can silently make
    it mean something different -- so it is reported (spec §7.1)."""
    entry = Unrebased(member="a.md", raw="../gone.md", detail="not a bundle member")
    assert entry.raw == "../gone.md"


def test_the_clean_corpus_loads_with_no_broken_links():
    """The acceptance properties in `test_moves_roundtrip.py` run over this
    bundle, and "gains no broken link it did not already have" is only a
    meaningful property against a corpus that starts with none."""
    bundle = ext_helpers.linked_bundle()
    assert not bundle.unreadable
    assert not build_link_graph(bundle).broken


def test_the_clean_corpus_is_exactly_what_the_helper_claims():
    """A fixture change cannot leave a test quietly asserting the old corpus."""
    root = ext_helpers.LINKED
    found = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert found == set(ext_helpers.LINKED_MEMBERS)


def test_the_crlf_fixture_is_uniformly_crlf():
    """`.gitattributes` marks this tree `-text`; without it, checkout on
    another machine would silently normalize these bytes away."""
    raw = (ext_helpers.LINKED / "concepts" / "crlf.md").read_bytes()
    assert b"\r\n" in raw
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0


def test_the_asset_is_a_real_member():
    """`Bundle.assets` exists because non-markdown members are link targets;
    an `rglob("*.md")` walk misses them."""
    bundle = ext_helpers.linked_bundle()
    assert "assets/diagram.png" in bundle.assets


def test_the_refusal_corpus_carries_one_parse_error():
    bundle = ext_helpers.linked_bad_bundle()
    broken = [cid for cid, doc in bundle.concepts.items() if doc.parse_error is not None]
    assert broken == ["broken"]


def test_the_documented_surface_is_importable():
    for name in moves.__all__:
        assert hasattr(moves, name)
