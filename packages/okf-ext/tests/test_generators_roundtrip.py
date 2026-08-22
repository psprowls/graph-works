"""The three acceptance properties, over every fixture body, every declared
type, and every derived encoding variant -- plus two supporting tests that
pin cases the properties' own boundaries cannot see.

okf-io round-trip property 2, two layers up: a mutation is minimal, and every
byte it does not claim survives untouched. `sections` and `moves` each found
an interaction bug at 100% branch coverage, which is the argument for
properties over per-function tests.

Line-ending, trailing-newline and BOM variants are built **in code**, never
from a fixture on disk: `.gitattributes` marks only
`packages/okf-io/tests/fixtures/**` as `-text`, so a CRLF file under okf-ext
would be silently normalized on someone else's checkout -- the exact failure
that rule exists to prevent.
"""

from __future__ import annotations

import pytest
from ext_helpers import GENERATED, GENERATED_DIR, read
from okf_ext.body import find_section, split_lines
from okf_ext.body import sections as body_sections
from okf_ext.generators.regenerate import regenerate_body
from okf_ext.shape import TypeSections, load_sections

#: The invisible U+FEFF byte-order mark, spelled as an escape so it is
#: reviewable in a diff -- the character itself would not be. Not imported
#: from `okf_io._yaml.BOM`: that module is private, and a test reaching into
#: it would trade one coupling for a worse one.
BOM = "\ufeff"

SECTION_SET = load_sections(GENERATED_DIR)

#: Every declared type, parametrized rather than read as a single module
#: constant -- the pattern `test_sections_roundtrip.py` and
#: `test_moves_roundtrip.py` both use (`DECLARATIONS = sorted(SECTION_SET.types)`).
#: A single `DECLARATION = SECTION_SET.types["Entity"]` would extend to a
#: second declared type only if someone remembered to update every test by
#: hand; parametrizing over the declaration set means a type added to
#: `generated/sections/` is covered automatically, with no test to edit.
DECLARATIONS = sorted(SECTION_SET.types)

#: Every concept, including the ones whose frontmatter is broken: this
#: operates on bodies, and a body is readable even when its frontmatter is not.
FIXTURES = sorted(p.name for p in GENERATED.glob("*.md"))

#: What a run supplies. Chosen to differ from every fixture's on-disk content
#: so property 2 has a real edit to measure, and reused verbatim by property 1
#: after the first pass has made it the on-disk content.
SUPPLIED = {"Sources": "- [[alpha]]\n- [[beta]]"}

#: The four encoding states every property runs over. Named and parametrized
#: (rather than looped over inside the test body) so a failing id reads
#: `test_fn[Entity-full.md-crlf]` -- the exact (type, fixture, variant) triple
#: -- instead of `test_fn[Entity-full.md]`, which leaves the variant legible
#: only one layer deeper, inside the traceback.
VARIANT_LABELS = ("lf", "crlf", "no-trailing", "bom")


def _body(name: str) -> str:
    """The fixture's body -- everything after the frontmatter block.

    `broken.md`'s frontmatter never closes, so there is no second `---\\n` to
    split on; `okf_io`'s own reader treats that the same way, reporting an
    empty `document.body` for an unterminated block (confirmed against
    `Document.body` directly), rather than raising. Matching that here is
    what lets `broken.md` stay in `FIXTURES` per the module docstring's
    promise that a body is readable even when its frontmatter is not.
    """
    text = read(GENERATED / name)
    if not text.startswith("---\n"):
        return text
    parts = text.split("---\n", 2)
    return parts[2] if len(parts) == 3 else ""


def _variant(body: str, label: str) -> str:
    """*body* transformed into the encoding state named *label*.

    The one place every property and every support test builds a variant --
    the three single-variant tests below call this too, rather than each
    re-deriving its own crlf/no-trailing/bom transform. Two independent
    copies of, say, the crlf transform agree today but could silently
    diverge under a future change (bare `\\r` handling, say); one copy
    cannot.
    """
    if label == "lf":
        return body
    if label == "crlf":
        return body.replace("\n", "\r\n")
    if label == "no-trailing":
        return body.rstrip("\n")
    if label == "bom":
        return BOM + body
    raise ValueError(f"unknown variant label: {label!r}")


def _owned_headings(declaration: TypeSections) -> set[str]:
    return {spec.heading.casefold() for spec in declaration.sections if spec.ownership in ("generated", "template")}


def _owned_span(body: str, declaration: TypeSections) -> tuple[int, int] | None:
    """The `(first, last)` 1-based body-line bounds spanning every owned,
    present section -- heading excluded, and the only lines a regeneration is
    allowed to move.

    Tracked as the min `body_start` and max `stop` across owned sections,
    never as a set of member lines unioned from `range(body_start, stop+1)`:
    a section with `body_start > stop` -- a heading with no body at all --
    contributes an *empty* range under that approach and would vanish from
    the set entirely, even though it owns a real insertion point at
    `body_start` and even though `regenerate_body` can and does write there.
    `empty_sections.md`'s `Sources` section is exactly this case: it is the
    one edited section in that fixture, and a membership-set version of this
    helper silently excludes it from `first`/`last`, understating the
    protected span and producing a false failure. Tracking bounds directly
    fixes that regardless of how many owned sections are empty, so no
    separate guard is needed for "every owned section happens to be empty" --
    `min`/`max` here run over `body_start`/`stop` values, which exist even
    for an empty section, never over an expanded (and possibly empty) range.

    Returns `None` when no owned heading is present in *body* at all, which
    only happens when `regenerate_body` planned no edit either -- the caller
    checks that first.
    """
    levels = {spec.level for spec in declaration.sections}
    owned = _owned_headings(declaration)
    firsts: list[int] = []
    lasts: list[int] = []
    for section in body_sections(body):
        if section.level in levels and section.heading.strip().casefold() in owned:
            firsts.append(section.body_start)
            lasts.append(section.stop)
    if not firsts:
        return None
    return min(firsts), max(lasts)


# ---------------------------------------------------------------------------
# The three acceptance properties: idempotence, whole-diff containment, and
# encoding survival (the last split into three single-facet tests below).
# Each runs over every declared type, every fixture, and every derived
# encoding variant. What follows this block are two supporting tests that
# pin cases these properties' own boundaries cannot see -- see the
# cross-references in `test_a_regeneration_changes_only_lines_inside_owned_sections`
# and `test_no_prose_section_is_ever_touched`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", VARIANT_LABELS)
@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_regenerating_with_the_values_already_on_disk_changes_nothing(type_name, name, label):
    """Property 1 -- the idempotence contract, and the one a generator
    re-running on a schedule depends on. The design spec states this
    property in bytes ("changes nothing, byte for byte"), which is what is
    asserted here -- not that the second pass's `edits` tuple is empty.

    Those two are not the same thing for a body with no trailing newline:
    `regenerate_body`'s own defensive backstop (see its docstring, the
    comment starting "A defensive backstop, not the primary fix") can undo
    the one line a splice just added, so the second pass reports a
    `SectionEdit` whose net effect on the assembled body is nothing.
    `plan.py` documents and works around this exact interaction --
    "Checking the whole body here is what keeps property 1 ... true for a
    body with no trailing newline" -- by squashing `section_edits` to `()`
    whenever `after == document.body`. This test applies the same standard
    at the layer it is actually testing.
    """
    declaration = SECTION_SET.types[type_name]
    body = _variant(_body(name), label)
    once, _, _ = regenerate_body(body, declaration, SUPPLIED)
    twice, _, _ = regenerate_body(once, declaration, SUPPLIED)
    assert twice == once, f"{name} [{label}/{type_name}] is not idempotent"


@pytest.mark.parametrize("label", VARIANT_LABELS)
@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_a_regeneration_changes_only_lines_inside_owned_sections(type_name, name, label):
    """Property 2 -- asserted against the **whole diff**, not just the
    intended lines. okf-io's `test_roundtrip.py` property 2 makes the same
    choice for the same reason: a weaker assertion lets a writer quietly
    rewrite neighbours.

    **This checks only the two edges of the owned envelope**: every line
    above the first owned section's start, and every line below the last
    owned section's end (mod the shift the edits introduced). It does
    **not** check the interior. A prose section sandwiched *between* two
    owned sections -- `Entity.yaml`'s declaration order is exactly
    `Summary -> Sources (generated) -> How this synthesis has changed
    (prose) -> About this page (template)` -- sits inside `[first, last]`
    and is invisible to this check by construction: the whole point of the
    two-edge assertion is to tolerate an owned section shifting the lines
    after it, and an interior prose section shifts along with everything
    else without this test being able to tell a legitimate shift from a
    rewrite. `test_no_prose_section_is_ever_touched`, below, is what covers
    that interior gap. Deleting it would leave interior prose unguarded even
    though this property would keep passing.
    """
    declaration = SECTION_SET.types[type_name]
    body = _variant(_body(name), label)
    after_text, edits, _ = regenerate_body(body, declaration, SUPPLIED)
    if not edits:
        assert after_text == body, f"{name} [{label}/{type_name}] changed bytes while planning no edit"
        return
    span = _owned_span(body, declaration)
    # An edit requires `_locate` to have found the spec's heading in the
    # document, which is exactly what makes that heading appear in
    # `_owned_span`'s walk -- so `edits` non-empty guarantees `span` is
    # not `None`. Guarded anyway: `min`/`max` raising on an empty
    # sequence is a worse failure mode than an assertion naming the
    # fixture, and a `None` here would be a real, reportable surprise
    # rather than dead code to trust blindly.
    if span is None:
        assert after_text == body, f"{name} [{label}/{type_name}] planned an edit with no owned heading present"
        return
    first, last = span
    before = split_lines(body)
    after = split_lines(after_text)
    # Everything above the first claimed line is untouched, and everything
    # below the last claimed line is untouched modulo the shift the edits
    # introduced. Both ends are checked, which is what makes this a
    # whole-diff assertion rather than a spot check.
    shift = len(after) - len(before)
    assert before[: first - 1] == after[: first - 1], (
        f"{name} [{label}/{type_name}] changed a line above every owned section"
    )
    assert before[last:] == after[last + shift :], (
        f"{name} [{label}/{type_name}] changed a line below every owned section"
    )


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_a_crlf_body_stays_uniformly_crlf(type_name, name):
    """Property 3a -- encoding survival, the CRLF facet."""
    declaration = SECTION_SET.types[type_name]
    body = _variant(_body(name), "crlf")
    after, _, _ = regenerate_body(body, declaration, SUPPLIED)
    assert "\n" not in after.replace("\r\n", ""), f"{name} [{type_name}] introduced a bare LF into a CRLF body"


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_the_trailing_newline_state_is_preserved(type_name, name):
    """Property 3b -- encoding survival, the trailing-newline facet. What
    pins the §5.4 sentinel hazard.

    Asserted as "matches the input's own state" rather than "always ends
    with a newline, then never does": every committed fixture body does end
    with `\\n`, but `broken.md`'s derived body is the empty string (its
    frontmatter never closes, so `_body` -- matching `Document.body` for an
    unterminated block -- reports no body at all), and `"".endswith("\\n")`
    is `False`. The state-matching form holds for that case the same way it
    holds for every canonical fixture, with no special-case needed.
    """
    declaration = SECTION_SET.types[type_name]
    body = _variant(_body(name), "lf")
    after = regenerate_body(body, declaration, SUPPLIED)[0]
    assert after.endswith("\n") == body.endswith("\n"), f"{name} [{type_name}] changed the trailing-newline state"
    stripped = _variant(_body(name), "no-trailing")
    after_stripped = regenerate_body(stripped, declaration, SUPPLIED)[0]
    assert after_stripped.endswith("\n") == stripped.endswith("\n"), (
        f"{name} [{type_name}] changed the trailing-newline state"
    )


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_a_bom_survives(type_name, name):
    """Property 3c -- encoding survival, the BOM facet. A BOM is body
    content as far as this capability is concerned, and content it was not
    granted must come through untouched."""
    declaration = SECTION_SET.types[type_name]
    body = _variant(_body(name), "bom")
    after, _, _ = regenerate_body(body, declaration, SUPPLIED)
    assert after.startswith(BOM), f"{name} [{type_name}] dropped the BOM"


# ---------------------------------------------------------------------------
# Supporting test: pins the one case property 2's two-edge check cannot see
# by construction (see that test's docstring for why).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", VARIANT_LABELS)
@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_no_prose_section_is_ever_touched(type_name, name, label):
    """Not redundant with property 2. `test_a_regeneration_changes_only_lines_inside_owned_sections`
    checks only the two edges outside the owned envelope; a prose section
    sitting *between* two owned sections is inside that envelope and
    invisible to it. This test reads that interior section directly instead,
    so the editorial model's central promise -- human prose survives a
    regeneration byte for byte -- is asserted rather than left as an
    unverified consequence of a check that cannot actually see it.
    """
    declaration = SECTION_SET.types[type_name]
    body = _variant(_body(name), label)
    before = find_section(body, "How this synthesis has changed")
    after_text, _, _ = regenerate_body(body, declaration, SUPPLIED)
    after = find_section(after_text, "How this synthesis has changed")
    if before is None:
        assert after is None, f"{name} [{label}/{type_name}] created a prose section"
        return
    assert after is not None
    assert after.slice(after_text) == before.slice(body), f"{name} [{label}/{type_name}] rewrote prose"
