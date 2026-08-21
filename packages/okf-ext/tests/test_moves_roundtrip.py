"""Corpus-wide properties, in their own module.

Mirrors okf-io's `test_roundtrip.py` for the same reason it is separate from
`test_document.py`: these are properties over every fixture, not cases.
"""

from __future__ import annotations

from pathlib import Path

import ext_helpers
import pytest
from okf_ext.body import split_lines
from okf_ext.moves.apply import apply
from okf_ext.moves.model import MovePlan, RefEdit
from okf_ext.moves.plan import plan_move, plan_move_dir
from okf_io import Bundle, build_link_graph, load_bundle
from okf_io.bundle import INDEX_NAME, LOG_NAME, canonical_id

#: Every move the properties run over. Each is a different shape: a leaf
#: rename, a climb into a deeper directory, an asset, a whole directory, and
#: a sideways move between sibling directories.
#:
#: The last entry exists to exercise `_relative`'s shared-prefix arithmetic
#: with a genuine partial match: `notes/sub/x.md` moving to
#: `notes/other/x.md` rebases its `./keep.md` sibling reference against
#: `_relative("notes/other", "notes/sub/keep.md")`, whose `from_dir` and
#: `target` share exactly one leading segment (`notes`) and then diverge
#: (`other` vs `sub`) -- the case none of the other four moves reach. Every
#: other move here either keeps `from_dir` and `target` in the same directory
#: (`shared == len(from_parts)`, zero climbs, the trivial case `_relative`
#: hits everywhere else) or shares no prefix at all (`shared == 0`).
MOVES = [
    ("concepts/beta.md", "pages/beta.md"),
    ("concepts/alpha.md", "deep/nested/alpha.md"),
    ("assets/diagram.png", "img/diagram.png"),
    ("notes/gamma.md", "gamma.md"),
    ("notes/sub/x.md", "notes/other/x.md"),
]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _body_line_offsets(bundle: Bundle) -> dict[str, int]:
    """Every markdown member's `Document.body_line_offset`, keyed by
    bundle-relative path -- reserved files included, mirroring
    `okf_ext.moves.plan._members`'s member map exactly (the same set `plan`
    walks when it computes a `RefEdit.line`, which is body-relative).

    `RefEdit.line` cannot be compared against a line index into the *whole
    file* without this: the frontmatter block that precedes `document.body`
    is never zero lines, so a body-relative line number and a whole-file line
    number name different lines for every member in this corpus.
    """
    offsets: dict[str, int] = {f"{cid}.md": doc.body_line_offset for cid, doc in bundle.concepts.items()}
    for directory, document in bundle.indexes.items():
        offsets[f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME] = document.body_line_offset
    for directory, document in bundle.logs.items():
        offsets[f"{directory}/{LOG_NAME}" if directory else LOG_NAME] = document.body_line_offset
    return offsets


@pytest.mark.parametrize(("source", "dest"), MOVES)
def test_every_line_without_a_planned_edit_is_byte_identical(tmp_path: Path, source: str, dest: str) -> None:
    """Property 1: byte fidelity. A move rewrites destinations, never bytes
    nobody planned to touch.

    A line "carries a planned edit" one of two ways: a body `RefEdit`, whose
    `line` is body-relative and is translated to a whole-file line via
    `_body_line_offsets` before comparison; or a frontmatter `RefEdit`, which
    carries no position at all by design (`moves` treats a frontmatter edit as
    rewriting a key wherever it lives, not a span -- see `RefEdit`'s own
    docstring), so a line is credited to it only when **both** (a) the line
    lies inside the frontmatter block (`number <= offset`, using the same
    `Document.body_line_offset` that places the body edits) and (b) the
    line's *original* text, with that edit's `old` value substituted for its
    `new` value, reproduces the line's *actual* new text exactly -- not merely
    "the old text appears somewhere on this line", which is weaker than it
    looks: restricted to the frontmatter block alone, an unedited prose
    mention of the same path in the *body* could still slip past a
    substring-only check, and a corruption of an unrelated frontmatter line
    happening to contain the same substring would too. The exact-substitution
    check catches both, because a change that is not literally "replace this
    edit's `old` with its `new`" fails it regardless of what substrings the
    line contains.

    **Residual, documented rather than assumed away.** Two distinct
    frontmatter edits on the same member that share an identical `(old, new)`
    pair (this corpus's own case: `beta.md`'s `resource` and
    `sources.0.resource` both move `../assets/diagram.png` ->
    `../img/diagram.png`) are indistinguishable to this check, but harmlessly
    so -- both lines are genuinely edited, so crediting either edit to either
    line reaches the same true verdict. The only way this check can still
    misfire is a frontmatter line that is *not* itself a moved reference yet
    (1) sits inside the frontmatter block, (2) contains some other edit's
    exact `old` text, (3) is corrupted by an independent bug, and (4) that
    corruption happens to land on precisely the bytes `old.replace(old, new,
    1)` would have produced -- a corruption that mimics the correct fix.
    Vanishingly unlikely and not a gap this heuristic can close without
    `RefEdit` carrying a position for frontmatter edits too, which the design
    deliberately does not do (see `RefEdit`'s docstring on why frontmatter
    edits stay position-free). Every other line -- for every member this plan
    touches, moved member included, compared under its destination path --
    must be byte-identical.
    """
    root = ext_helpers.linked_copy(tmp_path)
    bundle = load_bundle(root)
    before = _snapshot(root)
    offsets = _body_line_offsets(bundle)

    plan: MovePlan = plan_move(bundle, source, dest)
    assert plan.ok, [refusal.detail for refusal in plan.refusals]
    result = apply(bundle, plan)
    assert result.ok, result.failed

    relocated = {move.source: move.dest for move in plan.moves}
    edits_by_member: dict[str, list[RefEdit]] = {}
    for edit in plan.edits:
        edits_by_member.setdefault(edit.member, []).append(edit)

    after = _snapshot(root)
    for member, original in before.items():
        current_path = relocated.get(member, member)
        current = after.get(current_path)
        assert current is not None, f"{member} vanished"

        member_edits = edits_by_member.get(member, ())
        if not member_edits:
            assert current == original, f"{member} changed with no planned edit"
            continue

        old_lines = split_lines(original.decode("utf-8"))
        new_lines = split_lines(current.decode("utf-8"))
        assert len(old_lines) == len(new_lines), f"{member} gained or lost a line"

        offset = offsets.get(member, 0)
        touched_body_lines = {
            edit.line + offset for edit in member_edits if edit.where == "body" and edit.line is not None
        }
        frontmatter_edits = [edit for edit in member_edits if edit.where == "frontmatter"]

        for number, (old, new) in enumerate(zip(old_lines, new_lines, strict=True), start=1):
            if number in touched_body_lines:
                continue
            if number <= offset and any(
                edit.old in old and new == old.replace(edit.old, edit.new, 1) for edit in frontmatter_edits
            ):
                continue
            assert old == new, f"{member}:{number} (now {current_path}) changed without a planned edit"


@pytest.mark.parametrize(("source", "dest"), MOVES)
def test_a_move_adds_no_broken_link(tmp_path: Path, source: str, dest: str) -> None:
    """Property 2: no new breakage."""
    root = ext_helpers.linked_copy(tmp_path)
    bundle = load_bundle(root)
    before = {(link.source, link.raw) for link in build_link_graph(bundle).broken}

    plan = plan_move(bundle, source, dest)
    assert plan.ok, [refusal.detail for refusal in plan.refusals]
    assert apply(bundle, plan).ok

    after = {(link.source, link.raw) for link in build_link_graph(load_bundle(root)).broken}
    assert after <= before, f"new broken links: {sorted(after - before)}"


def plan_target(target: str, plan: MovePlan) -> str:
    for move in plan.moves:
        if move.source == target:
            return move.dest
    return target


@pytest.mark.parametrize(("source", "dest"), MOVES)
def test_a_moved_members_resolved_targets_are_unchanged(tmp_path: Path, source: str, dest: str) -> None:
    """Property 3: meaning preserved.

    This is the one that catches a rebasing bug properties 1 and 2 both pass:
    a reference can stay well-formed, resolve to a real member, and still point
    somewhere new.
    """
    if not source.endswith(".md"):
        pytest.skip("an asset has no outbound references")

    root = ext_helpers.linked_copy(tmp_path)
    bundle = load_bundle(root)
    old_id = source[: -len(".md")]
    graph = build_link_graph(bundle)
    before = {link.target for link in graph.out.get(old_id, ()) if not link.external}

    plan = plan_move(bundle, source, dest)
    assert plan.ok, [refusal.detail for refusal in plan.refusals]
    assert apply(bundle, plan).ok

    new_id = dest[: -len(".md")]
    after = {link.target for link in build_link_graph(load_bundle(root)).out.get(new_id, ()) if not link.external}
    # A target that itself moved in this batch is compared under its new path.
    expected = {plan_target(before_target, plan) for before_target in before}
    assert after == expected


def test_a_directory_move_preserves_every_members_meaning(tmp_path: Path) -> None:
    """Property 3 again, over the case that has to get it right for two moved
    endpoints at once -- spec §4's third reason for a batch."""
    root = ext_helpers.linked_copy(tmp_path)
    bundle = load_bundle(root)
    graph = build_link_graph(bundle)
    before = {cid: {link.target for link in graph.out.get(cid, ()) if not link.external} for cid in bundle.concepts}

    plan = plan_move_dir(bundle, "concepts", "pages")
    assert plan.ok, [refusal.detail for refusal in plan.refusals]
    assert apply(bundle, plan).ok

    moved = {move.source: move.dest for move in plan.moves}
    # `targets` is content-derived (a link destination) and `moved` is keyed
    # by raw disk ids (§ADR-0027) -- the two may disagree about Unicode
    # normalization form for `café.md` alone, so the lookup below goes
    # through `canonical_id` rather than by raw string.
    canonical_moved = {canonical_id(k): v for k, v in moved.items()}
    after_graph = build_link_graph(load_bundle(root))
    for cid, targets in before.items():
        new_cid = moved.get(f"{cid}.md", f"{cid}.md")[: -len(".md")]
        expected = {canonical_moved.get(canonical_id(target), target) for target in targets}
        actual = {link.target for link in after_graph.out.get(new_cid, ()) if not link.external}
        assert actual == expected, f"{cid} changed meaning"
