"""The four planners, every refusal kind, and the count reconciliation."""

from __future__ import annotations

import unicodedata
from urllib.parse import unquote

import ext_helpers
import pytest
from okf_ext.moves import plan as plan_module
from okf_ext.moves.model import Move
from okf_ext.moves.plan import plan_move, plan_move_dir, plan_move_many, plan_repair
from okf_io import load_bundle
from okf_io.bundle import canonical_id


@pytest.fixture
def linked():
    return ext_helpers.linked_bundle()


def kinds(plan):
    return {refusal.kind for refusal in plan.refusals}


def edit_for(plan, member, old):
    return next(e for e in plan.edits if e.member == member and e.old == old)


# --- the four planners over one engine ---


def test_plan_move_is_the_one_entry_mapping(linked):
    one = plan_move(linked, "concepts/beta.md", "pages/beta.md")
    many = plan_move_many(linked, {"concepts/beta.md": "pages/beta.md"})
    assert one.moves == many.moves
    assert one.edits == many.edits


def test_plan_move_dir_enumerates_the_directory(linked):
    plan = plan_move_dir(linked, "concepts", "pages")
    sources = {move.source for move in plan.moves}
    expected = {m for m in ext_helpers.LINKED_MEMBERS if m.startswith("concepts/")}
    assert {canonical_id(s) for s in sources} == {canonical_id(m) for m in expected}
    assert all(move.dest.startswith("pages/") for move in plan.moves)


def test_a_directory_move_rebases_a_reference_between_two_moved_members(linked):
    """Spec §4's third reason for a batch: a relative reference between two
    members that both move cannot be computed without both endpoints known.

    Under `plan_move_dir(linked, "concepts", "pages")`, `concepts/alpha.md` ->
    `pages/alpha.md` and `concepts/beta.md` -> `pages/beta.md` move together,
    so alpha's `./beta.md` reference to beta re-resolves to *exactly* the same
    text: both climbed out of `concepts/` and back into `pages/` in lockstep,
    so the relative path between two siblings never changes. That produces no
    `RefEdit` at all -- there is nothing to rewrite -- and the absence of one
    is only trustworthy because `plan.ok` is asserted too: if the engine had
    instead computed this against a *stale* view of beta (still at
    `concepts/beta.md`, as a naive per-member plan would), the rewritten text
    would differ from what's on disk and the count reconciliation would catch
    the mismatch as `unlocatable-reference`.

    Gamma does *not* move. Alpha's climb to it, `../notes/gamma.md`, is
    recomputed against alpha's *new* base (`pages/`) rather than left alone --
    and because `pages/` sits at the same depth as `concepts/` (both are
    single-segment, top-level directories), the recomputed climb is also
    textually identical to what was already there. So this reference produces
    no edit either, for the same reason and with the same proof: `plan.ok`
    being true is what shows the recomputation actually ran and agreed with
    the original text, rather than the reference simply being skipped.
    """
    plan = plan_move_dir(linked, "concepts", "pages")
    assert plan.ok, plan.refusals
    # The alpha -> beta sibling reference: recomputed, found identical, no edit.
    assert not any(e.member == "concepts/alpha.md" and e.old == "./beta.md" for e in plan.edits)
    # The alpha -> gamma climb: gamma didn't move, but alpha's base did; the
    # recomputed climb is still textually identical, so again no edit.
    assert not any(e.member == "concepts/alpha.md" and e.old == "../notes/gamma.md" for e in plan.edits)
    # The one alpha reference that *does* visibly change is the root-absolute
    # one: it repoints at beta's new location regardless of who else moved.
    absolute = edit_for(plan, "concepts/alpha.md", "/concepts/beta.md")
    assert absolute.new == "/pages/beta.md"


# --- validation refusals ---


def test_moving_onto_an_existing_member_is_refused(linked):
    plan = plan_move(linked, "concepts/alpha.md", "concepts/beta.md")
    assert "dest-exists" in kinds(plan)
    assert not plan.ok


def test_two_moves_claiming_one_destination_are_refused(linked):
    plan = plan_move_many(linked, {"concepts/alpha.md": "pages/x.md", "concepts/beta.md": "pages/x.md"})
    assert "dest-exists" in kinds(plan)


def test_a_reserved_source_is_refused(linked):
    plan = plan_move(linked, "index.md", "pages/index.md")
    assert "reserved-source" in kinds(plan)


def test_reserved_source_is_not_refused_in_repair_mode(tmp_path):
    """`reserved-source` is gated on `relocate`, symmetrically with
    `not-a-member`, `dest-exists`, and `reserved-dest`: in repair mode the
    mapping describes a move that already happened outside this capability,
    and `index.md`/`log.md` are not exempt from that -- something else can
    rename a reserved file into an ordinary concept just as easily as it can
    rename any other member. This physically renames `index.md` to
    `concepts/foo.md` on disk (matching "a move nothing planned"), reloads,
    and repairs -- which must succeed and must still find and fix the
    resulting dangling inbound reference.
    """
    root = tmp_path / "b"
    root.mkdir()
    (root / "index.md").write_text("# Index\n\n- [a](./a.md)\n", encoding="utf-8")
    (root / "a.md").write_text("---\ntitle: A\n---\n\n# A\n\nBack to [index](./index.md).\n", encoding="utf-8")
    load_bundle(root)  # sanity: builds cleanly before the external rename

    (root / "concepts").mkdir()
    (root / "index.md").rename(root / "concepts" / "foo.md")
    bundle = load_bundle(root)

    plan = plan_repair(bundle, {"index.md": "concepts/foo.md"})

    assert "reserved-source" not in kinds(plan)
    assert plan.ok, plan.refusals
    assert edit_for(plan, "a.md", "./index.md").new == "./concepts/foo.md"


def test_a_source_that_is_not_a_member_is_refused(linked):
    plan = plan_move(linked, "concepts/nope.md", "pages/nope.md")
    assert "not-a-member" in kinds(plan)


def test_a_path_escaping_the_root_is_refused(linked):
    plan = plan_move(linked, "concepts/alpha.md", "../outside.md")
    assert "escapes-root" in kinds(plan)


def test_a_move_to_the_same_path_is_refused(linked):
    plan = plan_move(linked, "concepts/alpha.md", "concepts/./alpha.md")
    assert "same-path" in kinds(plan)


def test_changing_a_concept_into_an_asset_is_refused(linked):
    plan = plan_move(linked, "concepts/alpha.md", "concepts/alpha.txt")
    assert "kind-change" in kinds(plan)


def test_a_move_onto_log_md_is_refused(linked):
    """`reserved-source` refuses `index.md`/`log.md` moving *away*; this is
    the same rule read backwards -- nothing may move *onto* one either. Left
    unguarded, an ordinary concept could become a directory's `log.md`
    (`kind-change` does not catch it: both ends are still `.md`), and
    `update_index()` would afterward reconcile the fabricated file as a
    genuine log."""
    plan = plan_move(linked, "concepts/café.md", "notes/log.md")
    assert not plan.ok
    assert "reserved-dest" in kinds(plan)


def test_a_move_onto_index_md_is_refused(linked):
    plan = plan_move(linked, "concepts/alpha.md", "notes/index.md")
    assert not plan.ok
    assert "reserved-dest" in kinds(plan)


def test_reserved_dest_is_not_refused_in_repair_mode(linked):
    """`plan_repair` describes a move that already happened -- the destination
    naming `index.md`/`log.md` is a state of the world by the time anyone
    calls it, not a proposal this call is making. Refusing it here would only
    stop the referrers from being repaired, with no fabrication left to
    prevent (whatever already happened, happened outside this capability)."""
    plan = plan_repair(linked, {"concepts/gone.md": "notes/index.md"})
    assert "reserved-dest" not in kinds(plan)


def test_every_refusal_kind_is_reachable(linked):
    """The closed vocabulary, checked as a set rather than one case at a time."""
    reached = set()
    reached |= kinds(plan_move(linked, "concepts/alpha.md", "concepts/beta.md"))
    reached |= kinds(plan_move(linked, "index.md", "pages/index.md"))
    reached |= kinds(plan_move(linked, "concepts/nope.md", "pages/nope.md"))
    reached |= kinds(plan_move(linked, "concepts/alpha.md", "../outside.md"))
    reached |= kinds(plan_move(linked, "concepts/alpha.md", "concepts/./alpha.md"))
    reached |= kinds(plan_move(linked, "concepts/alpha.md", "concepts/alpha.txt"))
    reached |= kinds(plan_move(linked, "concepts/alpha.md", "notes/index.md"))
    bad = ext_helpers.linked_bad_bundle()
    reached |= kinds(plan_move(bad, "target.md", "moved/target.md"))
    assert reached >= {
        "dest-exists",
        "reserved-source",
        "reserved-dest",
        "not-a-member",
        "escapes-root",
        "same-path",
        "kind-change",
        "parse-error",
        "unlocatable-reference",
        "reference-definition",
    }


# --- resolved-identity repair ---


def test_a_relative_inbound_reference_repoints(linked):
    """Alpha stays put; beta moves to `pages/`. `./beta.md` climbs out of
    `concepts/` and into `pages/`: `../pages/beta.md`. (`_rewrite_path`
    only ever prepends `./` when the recomputed path does *not* already start
    with `../` -- so `./../pages/beta.md` is not a value this code can
    produce, and asserting it as an acceptable alternative would let a real
    regression in the other direction pass unnoticed.)"""
    plan = plan_move(linked, "concepts/beta.md", "pages/beta.md")
    assert plan.ok
    assert edit_for(plan, "concepts/alpha.md", "./beta.md").new == "../pages/beta.md"


def test_a_root_absolute_reference_stays_root_absolute(linked):
    plan = plan_move(linked, "concepts/beta.md", "pages/beta.md")
    assert edit_for(plan, "concepts/alpha.md", "/concepts/beta.md").new == "/pages/beta.md"


def test_a_percent_encoded_destination_repairs(linked):
    """`Link.raw` is markdown-it's normalized destination, so string matching
    could never find this one -- resolution is what makes it ordinary.

    Asserted canonically: `edit.target` and `edit.new` carry whatever
    Unicode normalization form `café.md` actually has on disk (NFC or NFD,
    spec §ADR-0027), not necessarily the NFC this literal is written in.
    """
    plan = plan_move(linked, "concepts/café.md", "pages/café.md")
    assert plan.ok
    edit = edit_for(plan, "concepts/encoded.md", "./caf%C3%A9.md")
    assert unicodedata.normalize("NFC", edit.target) == "concepts/café.md"
    assert unicodedata.normalize("NFC", unquote(edit.new)) == "../pages/café.md"


def test_plan_move_accepts_an_nfc_source_against_an_nfd_named_member(tmp_path):
    """The same scenario as `test_a_percent_encoded_destination_repairs`, but
    materialized so the member's *disk* name is NFD while every reference to
    it -- the caller's `source` argument and the link content -- is NFC, the
    form a byte-exact filesystem would hand back from a git checkout that
    normalized on write. `plan_move` must accept the NFC source and repair
    the NFC-encoded inbound reference either way."""
    nfd = unicodedata.normalize("NFD", "café")
    bundle = ext_helpers.write_bundle(
        tmp_path,
        {
            f"concepts/{nfd}.md": "---\ntype: Concept\ntitle: Café\n---\n\n# Café\n",
            "concepts/encoded.md": ("---\ntype: Concept\ntitle: Encoded\n---\n\nA link: [café](./caf%C3%A9.md).\n"),
        },
    )
    plan = plan_move(bundle, "concepts/café.md", "pages/café.md")
    assert plan.ok
    assert plan.moves == (Move(source=f"concepts/{nfd}.md", dest="pages/café.md", is_asset=False),)
    edit = edit_for(plan, "concepts/encoded.md", "./caf%C3%A9.md")
    assert "%C3%A9" in edit.new


def test_an_angle_bracket_destination_repairs_unencoded(linked):
    plan = plan_move(linked, "concepts/spaced name.md", "pages/spaced name.md")
    assert plan.ok
    edit = edit_for(plan, "concepts/encoded.md", "./spaced name.md")
    assert " " in edit.new and "%20" not in edit.new


def test_a_fragment_rides_through_untouched(linked):
    plan = plan_move(linked, "concepts/alpha.md", "pages/alpha.md")
    edit = edit_for(plan, "concepts/frag.md", "./alpha.md#alpha")
    assert edit.new.endswith("#alpha")


def test_an_image_reference_repairs(linked):
    plan = plan_move(linked, "assets/diagram.png", "img/diagram.png")
    assert plan.ok
    assert edit_for(plan, "concepts/alpha.md", "../assets/diagram.png").new == "../img/diagram.png"


def test_an_asset_move_is_flagged_as_one(linked):
    plan = plan_move(linked, "assets/diagram.png", "img/diagram.png")
    assert [move.is_asset for move in plan.moves] == [True]


def test_a_fenced_decoy_is_never_edited(linked):
    plan = plan_move(linked, "concepts/alpha.md", "pages/alpha.md")
    decoy = [e for e in plan.edits if e.member == "concepts/decoy.md"]
    assert len(decoy) == 1, "only the real link, never the fenced/indented/inline ones"


def test_all_four_links_in_one_paragraph_are_planned(linked):
    """`multi.md` cites alpha twice inside one paragraph -- the trap
    `MdLink.line` sets, since markdown-it reports the containing block's
    first line for every link in it. The two occurrences land on the *same*
    line, so what actually distinguishes them is `column`: the locator must
    place each at a distinct offset rather than collapsing them into one."""
    plan = plan_move(linked, "concepts/alpha.md", "pages/alpha.md")
    multi = [e for e in plan.edits if e.member == "concepts/multi.md"]
    assert len(multi) == 2, "multi.md points at alpha twice"
    assert {e.line for e in multi} == {multi[0].line}, "both occurrences are on the same line"
    columns = {e.column for e in multi}
    assert len(columns) == 2, "the two occurrences must land at two distinct columns"


def test_a_link_inside_a_table_row_is_repaired_and_a_bare_path_is_not(linked):
    """Table rows are prose, not code: `body.prose_lines` excludes only fences,
    indented blocks and raw HTML, so the locator reaches inside a table and a
    link there repairs like any other. The bare `alpha.md` in the same file is
    not a link destination, so resolved-identity matching (spec §5) never makes
    it a candidate -- the same rule that keeps `[[wikilinks]]` out of scope
    (§3), seen from the other side.

    Both halves matter: if a table token ever joined `body._CODE_TOKENS`, every
    link in every table would silently stop being repaired, and nothing else in
    this corpus would catch it.
    """
    plan = plan_move(linked, "concepts/alpha.md", "pages/alpha.md")
    tabled = [e for e in plan.edits if e.member == "concepts/tabled.md"]
    assert len(tabled) == 1, "the table-row link is repaired; the bare path in prose is not"
    assert tabled[0].old == "./alpha.md"


def test_a_reserved_file_body_is_repaired(linked):
    """Spec §7.2: without this pass every move loses hand-authored index text,
    because `update_index()` would prune the dead entry and generate a new one."""
    plan = plan_move(linked, "concepts/alpha.md", "pages/alpha.md")
    assert any(e.member == "index.md" for e in plan.edits)
    assert any(e.member == "log.md" for e in plan.edits)


# --- the count reconciliation ---


def test_a_reference_style_link_refuses_the_plan():
    """`LinkGraph` sees the edge; the locator cannot place its text. The
    shortfall is the capability's central safety property: a document is never
    half-repaired, and any future locator gap fails closed."""
    bundle = ext_helpers.linked_bad_bundle()
    plan = plan_move(bundle, "target.md", "moved/target.md")
    assert not plan.ok
    assert "unlocatable-reference" in kinds(plan)


def test_a_reference_definition_into_the_moved_set_refuses():
    bundle = ext_helpers.linked_bad_bundle()
    plan = plan_move(bundle, "target.md", "moved/target.md")
    assert "reference-definition" in kinds(plan)
    detail = next(r.detail for r in plan.refusals if r.kind == "reference-definition")
    assert "tgt" in detail


def test_a_touched_parse_error_document_refuses():
    bundle = ext_helpers.linked_bad_bundle()
    plan = plan_move(bundle, "target.md", "moved/target.md")
    assert "parse-error" in kinds(plan)


# --- known limitations ---


def test_reference_link_with_trailing_parenthetical_is_a_false_positive(tmp_path):
    """**Known limitation, not a bug fixed here.** `locate._scan` does not
    understand full reference-link grammar. For body text
    `[a][ref](notlink.md)`, markdown-it parses one reference link -- `[a][ref]`
    resolving to whatever `[ref]:` defines -- with `(notlink.md)` as trailing
    literal text; nothing in the real document links to `notlink.md` at all.
    `_scan`, however, matches the bracket-then-paren shape on sight and yields
    `notlink.md` as its own candidate destination.

    The failure mode this produces is a false `unlocatable-reference` refusal
    on a document that never cited the moved file: `LinkGraph` correctly
    reports zero references from this document into the moved set, the
    locator (wrongly) placed one, and the count reconciliation reads that
    *excess* as "the locator matched text the graph never saw" and refuses
    the whole plan -- blocking a legitimate, unrelated move.

    This fails **closed**, not corrupt: no reference is silently mismatched
    or dropped, a real move is merely refused when it should have gone
    through. Fixing the root cause needs `locate._scan` to understand
    `[text][label]` lookahead, which is deliberately out of scope here --
    `locate.py` has already been through three review rounds and two
    regression cycles, and a fourth pass to add reference-link lookahead
    risks more than it buys at this stage. This test exists to pin the
    current behaviour so it cannot silently change (for better or worse)
    without a test noticing, and to carry the limitation forward into the
    package README.
    """
    root = tmp_path / "b"
    root.mkdir()
    (root / "a.md").write_text(
        "---\ntitle: A\n---\n\n# A\n\nA reference-style link: [a][ref](notlink.md)\n\n[ref]: target.md\n",
        encoding="utf-8",
    )
    (root / "target.md").write_text("---\ntitle: Target\n---\n\n# Target\n", encoding="utf-8")
    (root / "notlink.md").write_text("---\ntitle: Notlink\n---\n\n# Notlink\n", encoding="utf-8")
    bundle = load_bundle(root)

    plan = plan_move(bundle, "notlink.md", "moved/notlink.md")

    assert not plan.ok
    assert "unlocatable-reference" in kinds(plan)
    detail = next(r.detail for r in plan.refusals if r.kind == "unlocatable-reference")
    assert "the excess means the locator matched text the graph never saw" in detail


def test_an_untouched_parse_error_document_does_not_refuse(tmp_path):
    """A bundle with one broken unrelated file must still be movable."""
    root = tmp_path / "b"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("---\ntitle: A\n---\n\n# A\n", encoding="utf-8")
    (root / "sub" / "unrelated.md").write_text("---\ntitle: X\n\n# X\n", encoding="utf-8")
    bundle = load_bundle(root)
    plan = plan_move(bundle, "a.md", "moved/a.md")
    assert plan.ok, plan.refusals


# --- unrebased reporting (spec §7.1) ---


def test_a_moved_members_climb_out_of_the_bundle_is_reported_not_rewritten(tmp_path):
    """One of the two branches that produce `Unrebased`: a moved member's own
    relative reference resolves *outside* the bundle root entirely
    (`resolve_path` returns `None`). A move cannot make a broken link less
    broken, but it can silently make it mean something different by rebasing
    it against a new location it was never written against -- so this is
    reported, not rewritten, and the plan still succeeds (a pre-existing
    broken reference elsewhere in the file is not this move's problem)."""
    root = tmp_path / "b"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.md").write_text(
        "---\ntitle: A\n---\n\n# A\n\nA broken climb: [gone](../../outside.md).\n",
        encoding="utf-8",
    )
    bundle = load_bundle(root)

    plan = plan_move(bundle, "sub/a.md", "moved/a.md")

    assert plan.ok, plan.refusals
    assert [(u.member, u.raw) for u in plan.unrebased] == [("sub/a.md", "../../outside.md")]
    assert not any(e.old == "../../outside.md" for e in plan.edits)


def test_a_moved_members_reference_to_a_non_member_is_reported_not_rewritten(tmp_path):
    """The other `Unrebased` branch: the reference resolves to a real
    in-bundle path (it does not escape the root), but nothing lives there --
    `bundle.has_member(target)` is false. Same conclusion as the escaping
    case, for the same reason: reported, not rewritten, plan still ok."""
    root = tmp_path / "b"
    root.mkdir()
    (root / "a.md").write_text(
        "---\ntitle: A\n---\n\n# A\n\nA dangling reference: [gone](./missing.md).\n",
        encoding="utf-8",
    )
    bundle = load_bundle(root)

    plan = plan_move(bundle, "a.md", "deep/a.md")

    assert plan.ok, plan.refusals
    assert [(u.member, u.raw) for u in plan.unrebased] == [("a.md", "./missing.md")]
    assert not any(e.old == "./missing.md" for e in plan.edits)


def test_a_moved_members_frontmatter_climb_out_of_the_bundle_is_reported_not_rewritten(tmp_path):
    """The frontmatter-side twin of
    `test_a_moved_members_climb_out_of_the_bundle_is_reported_not_rewritten`:
    the same escapes-the-root outcome, but for a §6.2 path-valued frontmatter
    key rather than a body destination. `_frontmatter_edits` must report
    through the same `Unrebased` channel `_body_edits` does, not drop it
    silently -- there is no body link here to mask the omission this test
    guards against."""
    root = tmp_path / "b"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.md").write_text(
        "---\ntitle: A\nresource: ../../outside.md\n---\n\n# A\n",
        encoding="utf-8",
    )
    bundle = load_bundle(root)

    plan = plan_move(bundle, "sub/a.md", "moved/a.md")

    assert plan.ok, plan.refusals
    assert [(u.member, u.raw) for u in plan.unrebased] == [("sub/a.md", "../../outside.md")]
    assert not any(e.old == "../../outside.md" for e in plan.edits)


def test_a_moved_members_frontmatter_reference_to_a_non_member_is_reported_not_rewritten(tmp_path):
    """The frontmatter-side twin of
    `test_a_moved_members_reference_to_a_non_member_is_reported_not_rewritten`:
    the reference resolves to a real in-bundle path (it does not escape the
    root), but nothing lives there. Same guard as the escaping case above:
    no body link is present, so nothing else could mask `_frontmatter_edits`
    silently dropping this."""
    root = tmp_path / "b"
    root.mkdir()
    (root / "a.md").write_text(
        "---\ntitle: A\nresource: ./missing.md\n---\n\n# A\n",
        encoding="utf-8",
    )
    bundle = load_bundle(root)

    plan = plan_move(bundle, "a.md", "deep/a.md")

    assert plan.ok, plan.refusals
    assert [(u.member, u.raw) for u in plan.unrebased] == [("a.md", "./missing.md")]
    assert not any(e.old == "./missing.md" for e in plan.edits)


def test_a_fragment_only_reference_is_not_a_candidate_in_either_form(tmp_path):
    """A fragment-only destination (`#section`) has no path component to
    resolve at all, so it is not a candidate for `Unrebased` -- or for a
    rewrite -- in either form: a body destination or a §6.2 frontmatter
    value. `_body_edits` already screens this out before resolution
    (`if external or not path_part: continue`); `_frontmatter_edits` needed
    the same early-out, since an unguarded fall-through into
    `resolve_reference` (which itself returns `None` for an empty
    destination) would otherwise misreport it as a reference that "resolves
    outside the bundle root" -- which it does not, having no path at all.
    Both are asserted together on one moved member so the two sources'
    agreement is visible in one place."""
    root = tmp_path / "b"
    root.mkdir()
    (root / "a.md").write_text(
        "---\ntitle: A\nresource: '#section'\n---\n\n# A\n\nAn anchor: [here](#section).\n",
        encoding="utf-8",
    )
    bundle = load_bundle(root)

    plan = plan_move(bundle, "a.md", "deep/a.md")

    assert plan.ok, plan.refusals
    assert plan.unrebased == ()
    assert plan.edits == ()


# --- edge cases in the engine's private helpers ---


def test_a_within_bounds_climb_in_a_mapping_path_is_collapsed(tmp_path):
    """`_normalize`'s `..` branch has two outcomes: escaping the root (already
    covered by `test_a_path_escaping_the_root_is_refused`) and collapsing
    back within bounds, which nothing else here exercises. `notes/sub/../moved.md`
    collapses to `notes/moved.md` -- a perfectly ordinary destination -- and
    the move must plan normally rather than reading the raw `..` as anything
    unusual."""
    root = tmp_path / "b"
    root.mkdir()
    (root / "alpha.md").write_text("---\ntitle: A\n---\n\n# A\n", encoding="utf-8")
    bundle = load_bundle(root)

    plan = plan_move(bundle, "alpha.md", "notes/sub/../moved.md")

    assert plan.ok, plan.refusals
    assert plan.moves == (Move(source="alpha.md", dest="notes/moved.md", is_asset=False),)


def test_read_key_never_raises_on_a_malformed_shape():
    """`_read_key`'s three defensive branches uphold "content never raises on
    the read path" for a dotted key whose actual shape does not match: a
    digit segment indexing something that is not a sequence, a digit segment
    indexing past the end of one, and a non-digit segment reached through
    something that is not a mapping.

    Unlike the other private-helper edge cases in this section, this is
    exercised by calling `_read_key` directly rather than through `plan_move`:
    `_frontmatter_edits` only ever generates a `sources.<n>.resource` path for
    an `n` actually present in the document (`_reference_key_paths`), so the
    first two branches below are not reachable through that call pattern at
    all -- there is no live document shape that would produce them. The third
    (a `sources` list mixing a bare string with a valid mapping) *is*
    reachable in principle and is the shape the reviewer hand-verified: the
    malformed entry is skipped and the valid one still repairs, covered by
    `test_a_sources_resource_repairs_by_index` sharing a list position with
    nothing malformed -- so it is pinned here too, directly, for the exact
    return value.
    """
    # A digit segment indexing something that is not a sequence at all.
    assert plan_module._read_key({"sources": {"resource": "x"}}, "sources.0.resource") is None
    # A digit segment indexing past the end of a real sequence.
    assert plan_module._read_key({"sources": ["only-one"]}, "sources.5.resource") is None
    # A non-digit segment reached through something that is not a mapping --
    # the "bare string in a sources list" shape.
    assert plan_module._read_key({"sources": ["just a string"]}, "sources.0.resource") is None


def test_an_external_body_destination_is_skipped_by_the_locator_walk(tmp_path):
    """`_body_edits` scans every located candidate, external ones included --
    `locate.destinations` does not resolve, it locates. The `external`
    branch that skips them is real code, but nothing in the corpus so far
    happens to contain an external markdown link, so it was never actually
    exercised."""
    root = tmp_path / "b"
    root.mkdir()
    (root / "other.md").write_text(
        "---\ntitle: Other\n---\n\n# Other\n\nAn external link: [x](https://example.com).\n",
        encoding="utf-8",
    )
    (root / "target.md").write_text("---\ntitle: Target\n---\n\n# Target\n", encoding="utf-8")
    bundle = load_bundle(root)

    plan = plan_move(bundle, "target.md", "moved/target.md")

    assert plan.ok, plan.refusals
    assert not any(e.member == "other.md" for e in plan.edits)


def test_an_already_broken_reference_in_an_unmoved_document_is_ignored(tmp_path):
    """The `target is None` branch (a relative reference already escaping the
    bundle root) is reported via `Unrebased` only when the *referring*
    document is itself being moved (`rebase=True`) -- covered by
    `test_a_moved_members_climb_out_of_the_bundle_is_reported_not_rewritten`.
    This is the other half: the same already-broken reference sitting in a
    document that is *not* moving. It is neither this move's job to fix nor
    to report -- it was broken before this plan and stays exactly as broken
    after it, silently."""
    root = tmp_path / "b"
    root.mkdir()
    (root / "other.md").write_text(
        "---\ntitle: Other\n---\n\n# Other\n\nAlready broken: [gone](../../outside.md).\n",
        encoding="utf-8",
    )
    (root / "target.md").write_text("---\ntitle: Target\n---\n\n# Target\n", encoding="utf-8")
    bundle = load_bundle(root)

    plan = plan_move(bundle, "target.md", "moved/target.md")

    assert plan.ok, plan.refusals
    assert not any(e.member == "other.md" for e in plan.edits)
    assert not any(u.member == "other.md" for u in plan.unrebased)


def test_a_moved_members_root_absolute_dangling_reference_is_not_reported(tmp_path):
    """`Unrebased` only ever names a *relative* reference (spec §7.1's
    concern is a reference whose meaning silently shifts when rebased against
    a new location -- a root-absolute one is never rebased in the first
    place, so it cannot shift). A moved member's own root-absolute reference
    to a path that simply does not exist is left exactly as written, with no
    edit and no `Unrebased` entry -- it was already broken, and root-absolute
    breakage is not this capability's problem to surface."""
    root = tmp_path / "b"
    root.mkdir()
    (root / "a.md").write_text(
        "---\ntitle: A\n---\n\n# A\n\nAlready broken: [gone](/somewhere/missing.md).\n",
        encoding="utf-8",
    )
    bundle = load_bundle(root)

    plan = plan_move(bundle, "a.md", "deep/a.md")

    assert plan.ok, plan.refusals
    assert plan.edits == ()
    assert plan.unrebased == ()


def test_a_parse_error_documents_external_and_unrelated_references_are_skipped(tmp_path):
    """`_mentions_moved_set` exists to catch a *hidden* citation of the moved
    path inside a document whose `body` came back empty. It must not become
    a blanket "any link at all makes this touched": an external destination
    is skipped outright, and a relative one that resolves to something other
    than the moved path leaves the document untouched too, exactly as a
    normal, parseable document would be."""
    root = tmp_path / "b"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("---\ntitle: A\n---\n\n# A\n", encoding="utf-8")
    (root / "sub" / "unrelated.md").write_text(
        "---\ntitle: X\n\nAn external one: [x](https://example.com) and an unrelated one: [y](./unrelated-target.md)\n",
        encoding="utf-8",
    )
    bundle = load_bundle(root)

    plan = plan_move(bundle, "a.md", "moved/a.md")

    assert plan.ok, plan.refusals


def test_an_external_reference_definition_is_skipped(tmp_path):
    """The reference-definition scan (spec §6) refuses only a definition that
    resolves *into the moved set*; `is_external` screens out one that plainly
    cannot, such as a URL. Pairing it with a real, locatable link into the
    moved set on the same document proves the external definition is
    skipped specifically, not that the whole scan was never reached."""
    root = tmp_path / "b"
    root.mkdir()
    (root / "a.md").write_text(
        '---\ntitle: A\n---\n\n# A\n\nA real link: [real](./target.md)\n\n[ext]: https://example.com/x "Ext"\n',
        encoding="utf-8",
    )
    (root / "target.md").write_text("---\ntitle: Target\n---\n\n# Target\n", encoding="utf-8")
    bundle = load_bundle(root)

    plan = plan_move(bundle, "target.md", "moved/target.md")

    assert plan.ok, plan.refusals
    assert "reference-definition" not in kinds(plan)
    assert edit_for(plan, "a.md", "./target.md").new == "./moved/target.md"


# --- plan_repair ---


def test_plan_repair_needs_no_surviving_source(linked):
    plan = plan_repair(linked, {"concepts/gone.md": "pages/gone.md"})
    assert "not-a-member" not in kinds(plan)


def test_plan_repair_relocates_nothing(linked):
    plan = plan_repair(linked, {"concepts/beta.md": "pages/beta.md"})
    assert plan.relocate is False
    assert any(e.member == "concepts/alpha.md" for e in plan.edits)


def test_plan_repair_is_not_refused_by_its_own_destination(tmp_path):
    """`plan_repair`'s *primary* documented use case: the destination already
    exists on disk, because the move it is repairing after already happened
    -- either `apply` relocated the file and then failed partway through the
    referrer writes, or something outside this capability moved it entirely.
    In both cases `bundle.has_member(dest)` is true by construction, and a
    `dest-exists` refusal here would mean `plan_repair` refuses the exact
    situation its own docstring calls its recovery path. This physically
    moves `concepts/beta.md` to `pages/beta.md` on disk (no `apply` involved,
    matching "a move nothing planned"), reloads, and repairs -- which must
    succeed and must still find and fix the inbound referrer.
    """
    root = ext_helpers.linked_copy(tmp_path)
    (root / "pages").mkdir(parents=True, exist_ok=True)
    (root / "concepts" / "beta.md").rename(root / "pages" / "beta.md")
    bundle = load_bundle(root)

    plan = plan_repair(bundle, {"concepts/beta.md": "pages/beta.md"})

    assert plan.ok, plan.refusals
    assert edit_for(plan, "concepts/alpha.md", "./beta.md").new == "../pages/beta.md"
    assert edit_for(plan, "concepts/alpha.md", "/concepts/beta.md").new == "/pages/beta.md"


def test_a_plan_carries_a_digest_for_every_member_it_edits(linked):
    plan = plan_move(linked, "concepts/alpha.md", "pages/alpha.md")
    for edit in plan.edits:
        assert edit.member in plan.digests


def test_the_reference_keys_are_the_documented_set():
    assert plan_module.REFERENCE_KEYS == (
        "resource",
        "computation",
        "executor.resource",
        "attester.resource",
    )
    assert plan_module.SOURCES_KEY == "sources"


# --- frontmatter references ---


def fm_edit(plan, member, key):
    return next(e for e in plan.edits if e.member == member and e.key == key)


def test_a_top_level_resource_repairs(linked):
    plan = plan_move(linked, "assets/diagram.png", "img/diagram.png")
    edit = fm_edit(plan, "concepts/beta.md", "resource")
    assert edit.where == "frontmatter"
    assert edit.line is None and edit.column is None
    assert edit.new == "../img/diagram.png"


def test_a_sources_resource_repairs_by_index(linked):
    plan = plan_move(linked, "assets/diagram.png", "img/diagram.png")
    edit = fm_edit(plan, "concepts/beta.md", "sources.0.resource")
    assert edit.new == "../img/diagram.png"


def test_an_external_sources_resource_is_untouched(linked):
    """The key set is generous because the match is strict -- an external
    value never resolves to a moved member."""
    plan = plan_move(linked, "assets/diagram.png", "img/diagram.png")
    assert not any(e.key == "sources.1.resource" for e in plan.edits)


def test_a_non_path_resource_is_untouched(tmp_path):
    """A top-level `resource` may be a table URI and a `sources[].resource` a
    §5.1 scope descriptor; neither resolves to a member."""
    root = tmp_path / "b"
    root.mkdir()
    (root / "a.md").write_text(
        "---\ntitle: A\nresource: bigquery://project.dataset.table\n---\n\n# A\n\n[t](./t.md)\n",
        encoding="utf-8",
    )
    (root / "t.md").write_text("---\ntitle: T\n---\n\n# T\n", encoding="utf-8")
    bundle = load_bundle(root)
    plan = plan_move(bundle, "t.md", "moved/t.md")
    assert plan.ok
    assert not any(e.where == "frontmatter" for e in plan.edits)


def test_the_nested_executor_and_attester_keys_repair(tmp_path):
    root = tmp_path / "b"
    root.mkdir()
    (root / "a.md").write_text(
        "---\ntitle: A\nexecutor:\n  resource: ./t.md\nattester:\n  resource: ./t.md\n---\n\n# A\n",
        encoding="utf-8",
    )
    (root / "t.md").write_text("---\ntitle: T\n---\n\n# T\n", encoding="utf-8")
    bundle = load_bundle(root)
    plan = plan_move(bundle, "t.md", "moved/t.md")
    keys = {e.key for e in plan.edits if e.where == "frontmatter"}
    assert keys == {"executor.resource", "attester.resource"}


# --- outbound rebasing ---


def test_a_moved_member_rebases_its_own_relative_references(linked):
    """`resolve_path` resolves file-relative destinations against the
    containing file's directory, so a member that changes directory breaks its
    own references (spec §7.1)."""
    plan = plan_move(linked, "concepts/alpha.md", "deep/nested/alpha.md")
    assert plan.ok
    edit = edit_for(plan, "concepts/alpha.md", "../notes/gamma.md")
    assert edit.target == "notes/gamma.md"
    assert edit.new == "../../notes/gamma.md"


def test_a_moved_members_root_absolute_reference_does_not_change(linked):
    plan = plan_move(linked, "concepts/alpha.md", "deep/nested/alpha.md")
    assert not any(e.member == "concepts/alpha.md" and e.old == "/concepts/beta.md" for e in plan.edits)


def test_a_moved_members_own_image_rebases(linked):
    plan = plan_move(linked, "concepts/alpha.md", "deep/nested/alpha.md")
    assert edit_for(plan, "concepts/alpha.md", "../assets/diagram.png").new == "../../assets/diagram.png"


def test_rebasing_does_not_inflate_the_inbound_count(linked):
    """A rebase edit for an *unmoved* target is not an inbound reference, and
    `RefEdit.target` is what keeps the two apart. If it did count, this plan
    would refuse with `unlocatable-reference`."""
    plan = plan_move(linked, "concepts/alpha.md", "deep/nested/alpha.md")
    assert plan.ok, plan.refusals


# NOTE: the plan text's `test_a_relative_reference_to_a_non_member_is_reported_not_rewritten`
# is a near-duplicate of `test_a_moved_members_reference_to_a_non_member_is_reported_not_rewritten`
# above (Task 4) -- same shape (a lone relative reference to a path with no
# member), differing only in the literal filename used ("missing.md" vs
# "gone.md") and in the fact that the existing test additionally asserts
# `plan.ok`. Per instructions, the stronger existing test is kept and this one
# is not duplicated.


def test_an_unmoved_member_is_never_rebased(linked):
    """Its base did not change, so only its inbound references may move."""
    plan = plan_move(linked, "concepts/beta.md", "pages/beta.md")
    alpha = [e for e in plan.edits if e.member == "concepts/alpha.md"]
    assert {e.target for e in alpha} == {"concepts/beta.md"}
