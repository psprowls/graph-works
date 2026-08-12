"""The two-class section model, at the body level. Pure -- no bundle here."""

from __future__ import annotations

from ext_helpers import GENERATED_DIR, generated_bundle
from okf_ext.generators.regenerate import regenerate_body
from okf_ext.shape import SectionSpec, TypeSections, load_sections

DECLARATION = TypeSections(
    sections=(
        SectionSpec(heading="Summary", required=True),
        SectionSpec(heading="Sources", ownership="generated"),
        SectionSpec(heading="How this synthesis has changed", ownership="prose"),
        SectionSpec(
            heading="About this page", ownership="template", placeholder="Generated.\n", seeded_is_complete=True
        ),
    )
)

BODY = (
    "## Summary\n"
    "\n"
    "Hand-written.\n"
    "\n"
    "## Sources\n"
    "\n"
    "- [[old]]\n"
    "\n"
    "## How this synthesis has changed\n"
    "\n"
    "Prose nothing may touch.\n"
    "\n"
    "## About this page\n"
    "\n"
    "A human wrote over the template.\n"
)


def test_a_prose_section_is_never_touched():
    """A substring check on the sentence alone would pass even if the
    section's heading, its surrounding blank lines, or a line's trailing
    whitespace were disturbed. Assert the whole span -- heading through the
    blank line that separates it from its neighbour -- byte for byte,
    trailing whitespace included, since that is exactly the kind of damage a
    substring check cannot see."""
    body = BODY.replace("Prose nothing may touch.\n", "Prose nothing may touch.   \n")
    prose_span = "## How this synthesis has changed\n\nProse nothing may touch.   \n\n"
    after, edits, missing = regenerate_body(body, DECLARATION, {"Sources": "- [[new]]"})
    assert prose_span in after
    assert "How this synthesis has changed" not in [edit.heading for edit in edits]
    assert missing == ()


def test_a_supplied_generated_section_is_replaced():
    after, edits, _ = regenerate_body(BODY, DECLARATION, {"Sources": "- [[new]]"})
    assert "- [[new]]\n" in after
    assert "- [[old]]" not in after
    assert "Sources" in [edit.heading for edit in edits]


def test_an_omitted_generated_section_is_left_alone():
    """Deliberately not symmetric with the frontmatter rule: a deleted key is
    invisible, but an emptied section leaves a bare heading over nothing --
    and a section's existence is governed by required/optional, not by what a
    run supplies."""
    after, edits, _ = regenerate_body(BODY, DECLARATION, {})
    assert "- [[old]]\n" in after
    assert "Sources" not in [edit.heading for edit in edits]


def test_an_empty_string_is_how_a_run_says_the_section_is_now_empty():
    after, edits, _ = regenerate_body(BODY, DECLARATION, {"Sources": ""})
    assert "- [[old]]" not in after
    assert "Sources" in [edit.heading for edit in edits]


def test_a_template_section_is_reset_from_its_placeholder():
    after, edits, _ = regenerate_body(BODY, DECLARATION, {})
    assert "Generated.\n" in after
    assert "A human wrote over the template." not in after
    assert "About this page" in [edit.heading for edit in edits]


def test_a_template_section_ignores_whatever_the_caller_supplied():
    """`ownership="template"` always wins with its declared placeholder --
    even a run that supplies its own content for the heading is overridden,
    because a template section's content is the declaration's, not the
    generator's."""
    after, _, _ = regenerate_body(BODY, DECLARATION, {"About this page": "A run tried to supply this."})
    assert "Generated.\n" in after
    assert "A run tried to supply this." not in after


def test_a_missing_declared_section_is_reported_and_never_created():
    body = "## Summary\n\nOnly this.\n"
    after, edits, missing = regenerate_body(body, DECLARATION, {"Sources": "- [[a]]"})
    assert "## Sources" not in after
    assert missing == ("Sources", "About this page")
    assert edits == ()


def test_two_owned_sections_both_land():
    """The second is planned against the body as amended by the first, so
    line numbers stay meaningful as the body shifts under them.

    A stale-offset implementation (both sections located once against the
    original body, then spliced in sequence against the mutating line list)
    passes every substring and edit-heading check this test could make --
    the reviewer confirmed it by direct execution. It still corrupts the
    document: growing the `Sources` list by two lines shifts `About this
    page` two lines out from under its own stale offset, so the placeholder
    lands over the *next* section's tail, deletes the real `## About this
    page` heading, and leaves the stale human prose beneath it untouched.
    Only an exact-body comparison catches that -- which is why this asserts
    the whole returned string rather than pieces of it.
    """
    after, edits, _ = regenerate_body(BODY, DECLARATION, {"Sources": "- [[a]]\n- [[b]]\n- [[c]]"})
    expected = (
        "## Summary\n"
        "\n"
        "Hand-written.\n"
        "\n"
        "## Sources\n"
        "\n"
        "- [[a]]\n"
        "- [[b]]\n"
        "- [[c]]\n"
        "\n"
        "## How this synthesis has changed\n"
        "\n"
        "Prose nothing may touch.\n"
        "\n"
        "## About this page\n"
        "\n"
        "Generated.\n"
    )
    assert after == expected
    assert [edit.heading for edit in edits] == ["Sources", "About this page"]


def test_a_splice_producing_identical_bytes_is_not_an_edit():
    once, _, _ = regenerate_body(BODY, DECLARATION, {"Sources": "- [[new]]"})
    twice, edits, _ = regenerate_body(once, DECLARATION, {"Sources": "- [[new]]"})
    assert twice == once
    assert edits == ()


def test_a_heading_matches_stripped_and_casefolded():
    body = "##   SOURCES  \n\n- [[old]]\n"
    after, _, missing = regenerate_body(body, DECLARATION, {"Sources": "- [[new]]"})
    assert "- [[new]]\n" in after
    assert missing == ("About this page",)


def test_a_heading_at_the_wrong_level_does_not_match():
    """`level` is part of the declaration; `find_section`'s matching rule is
    deliberately level-agnostic and this is not."""
    body = "### Sources\n\n- [[old]]\n"
    after, _, missing = regenerate_body(body, DECLARATION, {"Sources": "- [[new]]"})
    assert after == body
    assert "Sources" in missing


def test_a_section_with_no_body_at_all_is_filled():
    """`body_start > stop`, which `splice.replace` handles as a pure insert."""
    body = "## Sources\n## How this synthesis has changed\n\nKept.\n"
    after, edits, _ = regenerate_body(body, DECLARATION, {"Sources": "- [[a]]"})
    assert "- [[a]]\n" in after
    assert "Kept.\n" in after
    assert [edit.heading for edit in edits] == ["Sources"]


def test_a_crlf_body_stays_uniformly_crlf():
    body = BODY.replace("\n", "\r\n")
    after, _, _ = regenerate_body(body, DECLARATION, {"Sources": "- [[new]]"})
    assert "\n" not in after.replace("\r\n", "")


def test_a_body_with_no_trailing_newline_still_has_none():
    """The §5.4 hazard: the sentinel would otherwise stack a second
    terminator that `assemble` only ever undoes one of."""
    body = BODY.rstrip("\n")
    after, _, _ = regenerate_body(body, DECLARATION, {"Sources": "- [[new]]"})
    assert not after.endswith("\n")


def test_the_edit_line_is_the_sections_body_start():
    after, edits, _ = regenerate_body(BODY, DECLARATION, {"Sources": "- [[new]]"})
    lines = after.splitlines(keepends=True)
    edit = next(item for item in edits if item.heading == "Sources")
    assert lines[edit.line - 2].strip() == "## Sources"


def test_a_whitespace_only_unterminated_last_line_does_not_grow_a_newline():
    """The one line of `regenerate_body` the property suite structurally
    cannot reach: the defensive backstop right before the return, inherited
    from `sections/scaffold.py:201`.

    The property suite's `no-trailing` variant is always `body.rstrip("\\n")`,
    which can never leave a *blank* line unterminated -- `rstrip` eats
    trailing whitespace-only lines along with the newline. This body has no
    trailing newline **and** an unterminated, whitespace-only last line, a
    shape only a hand-built body reaches.

    Without the backstop, `_needs_trailing_sentinel` reads that stray blank
    line as "restore the existing blank" and `replace` -- which unconditionally
    terminates every line it writes -- turns the sentinel into a standalone
    `"\\n"` entry stacked onto the one `assemble` only ever undoes once. The
    result is a body that gained a trailing newline nobody asked for, even
    though the input had none.

    Confirmed by direct execution with the backstop removed: the same
    `regenerate_body` call below returns `'## Sources\\n\\n- [[a]]\\n'`
    (trailing newline) instead of `'## Sources\\n\\n- [[a]]'` (none) -- i.e.
    `after.endswith("\\n")` flips from `False` to `True`, which is exactly
    what the assertion below pins.
    """
    body = "## Sources\n\n- [[a]]\n\n   "
    after, edits, _ = regenerate_body(body, DECLARATION, {"Sources": "- [[a]]"})
    assert after == "## Sources\n\n- [[a]]"
    assert not after.endswith("\n")
    assert [edit.heading for edit in edits] == ["Sources"]


def test_full_md_regenerates_to_itself_with_zero_edits():
    """`full.md` is the corpus's idempotence case: every ownership class is
    already exactly what a matching run would write, including its
    `template` section running to the true end of the body with no trailing
    blank. This is the regression that would have caught the §5.4 shift
    (dropping the blank after a heading, then manufacturing one at end of
    file) at this task rather than at Task 8, where combining this function
    with a real bundle first exposed it -- this module's other tests are all
    hand-built bodies, and none of them happened to end a section at the true
    end of the document with no trailing blank to restore."""
    section_set = load_sections(GENERATED_DIR)
    document = generated_bundle().concepts["full"]
    declaration = section_set.types["Entity"]
    after, edits, missing = regenerate_body(document.body, declaration, {"Sources": "- [[a]]"})
    assert after == document.body
    assert edits == ()
    assert missing == ()


def test_create_missing_appends_a_granted_section_that_is_absent():
    declaration = TypeSections(sections=(SectionSpec(heading="Repositories", ownership="generated", required=True),))
    after, edits, missing = regenerate_body(
        "# Bundle\n", declaration, {"Repositories": "- [a](/repositories/a.md)"}, create_missing=True
    )
    assert after == "# Bundle\n\n## Repositories\n\n- [a](/repositories/a.md)\n"
    assert [e.heading for e in edits] == ["Repositories"]
    assert missing == ()


def test_create_missing_is_off_by_default():
    declaration = TypeSections(sections=(SectionSpec(heading="Repositories", ownership="generated", required=True),))
    after, edits, missing = regenerate_body("# Bundle\n", declaration, {"Repositories": "- [a](/a.md)"})
    assert after == "# Bundle\n"
    assert edits == ()
    assert missing == ("Repositories",)


def test_a_created_section_is_idempotent_on_the_next_run():
    """The property acceptance criterion 3 rests on: creating and then
    regenerating the same content must plan nothing the second time."""
    declaration = TypeSections(sections=(SectionSpec(heading="Repositories", ownership="generated", required=True),))
    supplied = {"Repositories": "- [a](/repositories/a.md)"}
    after, _edits, _missing = regenerate_body("# Bundle\n", declaration, supplied, create_missing=True)
    again, edits, missing = regenerate_body(after, declaration, supplied, create_missing=True)
    assert again == after
    assert edits == ()
    assert missing == ()
