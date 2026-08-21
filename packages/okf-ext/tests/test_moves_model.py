"""The frozen values, the fixture corpora, and the boundary wiring."""

from __future__ import annotations

import subprocess
import unicodedata
from pathlib import Path

import ext_helpers
import pytest
from okf_ext import moves
from okf_ext.moves.model import Move, MovePlan, MoveResult, RefEdit, Refusal, Unrebased
from okf_io import build_link_graph
from okf_io.bundle import canonical_id


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
    """A fixture change cannot leave a test quietly asserting the old corpus.

    Compared canonically, not by raw string: `readdir` may hand back
    `concepts/café.md` as NFC or NFD depending on how the working tree was
    materialized (spec §ADR-0027), and `LINKED_MEMBERS` is a hand-authored
    NFC literal set either way.
    """
    root = ext_helpers.LINKED
    found = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert {canonical_id(m) for m in found} == {canonical_id(m) for m in ext_helpers.LINKED_MEMBERS}


def test_the_crlf_fixture_is_uniformly_crlf():
    """`.gitattributes` marks this tree `-text`; without it, checkout on
    another machine would silently normalize these bytes away."""
    raw = (ext_helpers.LINKED / "concepts" / "crlf.md").read_bytes()
    assert b"\r\n" in raw
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0


def test_the_café_fixture_name_is_committed_as_nfc():
    """Guards §ADR-0027's D2 choice itself, not the working tree's current
    materialization -- a checkout is free to decompose an NFC name to NFD
    (that tolerance is the whole point of the fix, and is what
    `test_the_clean_corpus_is_exactly_what_the_helper_claims` accepts).
    `git ls-files` reports the tracked bytes, not `readdir`'s, so this is
    the one check immune to that and able to catch the fixture regressing
    to a committed NFD name -- which the repo-wide drift gate
    (`scripts/check_filename_normalization.py`) cannot: a name committed
    and checked out in the same (wrong) form carries no tracked/disk drift
    for it to see.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "fixtures/linked/concepts/"],
        cwd=Path(__file__).resolve().parent,
        check=True,
        capture_output=True,
    )
    tracked = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    (café,) = [name for name in tracked if "caf" in name.lower()]
    leaf = café.rsplit("/", 1)[-1]
    assert leaf == unicodedata.normalize("NFC", leaf)


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


def test_stranded_references_do_not_change_ok_or_is_empty():
    """A stranded reference is a fact about what the planner could not see, and
    never gates anything (2026-08-21 spec D-3, ADR-0004)."""
    from okf_ext.moves.model import Stranded

    entry = Stranded(member="notes/gamma.md", target="concepts/beta.md", line=9)
    plan = _plan(stranded=(entry,))
    assert plan.ok
    assert plan.is_empty
    assert plan.stranded == (entry,)


def test_a_plan_built_without_a_stranded_argument_still_works():
    """`migrate.py`'s empty-plan short circuit constructs a `MovePlan` directly."""
    assert _plan().stranded == ()
