"""The regeneration corpus is what it claims to be.

A fixture directory that drifts from `GENERATED_EXPECTED` would leave the
plan and apply suites quietly asserting the old corpus -- the failure
`SCHEMA_EXPECTED` was introduced to prevent, one capability along.
"""

from __future__ import annotations

from ext_helpers import GENERATED, GENERATED_DIR, GENERATED_EXPECTED, generated_bundle, generated_copy, read
from okf_ext.body import sections
from okf_ext.shape import load_sections


def test_every_concept_on_disk_is_named_in_the_expectation_table():
    on_disk = {p.stem for p in GENERATED.glob("*.md")}
    assert on_disk == set(GENERATED_EXPECTED)


def test_the_bundle_walks_with_nothing_unreadable():
    bundle = generated_bundle()
    assert bundle.unreadable == {}
    assert set(bundle.concepts) == set(GENERATED_EXPECTED)


def test_exactly_one_concept_carries_a_parse_error():
    bundle = generated_bundle()
    broken = [name for name, doc in bundle.concepts.items() if doc.parse_error is not None]
    assert broken == ["broken"]


def test_the_declaration_set_declares_every_ownership_class():
    declaration = load_sections(GENERATED_DIR).types["Entity"]
    assert {spec.ownership for spec in declaration.sections} == {"prose", "generated", "template"}
    assert declaration.frontmatter.owned == ("title", "sources")
    assert declaration.frontmatter.provenance == ("content_hash",)


def test_every_committed_fixture_is_lf_with_a_trailing_newline():
    """`.gitattributes` marks only okf-io's fixtures `-text`, so a CRLF file
    here would be silently normalised on someone else's checkout. The
    encoding-survival property builds its variants in code instead; this
    asserts the committed state it builds them from."""
    for path in sorted(p for p in GENERATED.rglob("*") if p.is_file()):
        text = read(path)
        rel_path = path.relative_to(GENERATED).as_posix()
        assert "\r" not in text, f"{rel_path} carries a CR; see this test's docstring"
        assert text.endswith("\n"), f"{rel_path} lost its trailing newline"


def test_empty_sections_fixture_genuinely_has_body_start_greater_than_stop():
    """The `empty_sections.md` fixture claims to exercise the `body_start > stop`
    splice path (a heading with no body — the next heading starts immediately).
    This assertion pins that property: at least one declared owned section must
    have body_start > stop, not body_start == stop (which is a blank line, not
    nothing). Silently adding a blank line would regress the fixture without
    failing this test."""
    parsed = {section.heading: section for section in sections(generated_bundle().concepts["empty_sections"].body)}

    # Only a `generated` or `template` section is ever spliced, so only those
    # can exercise the path -- a prose section with an empty body would satisfy
    # a looser test while leaving the real one unproven.
    declaration = load_sections(GENERATED_DIR).types["Entity"]
    owned_headings = {
        spec.heading
        for spec in declaration.sections
        if spec.ownership in ("generated", "template") and spec.heading in parsed
    }

    has_gt_case = any(parsed[heading].body_start > parsed[heading].stop for heading in owned_headings)
    assert has_gt_case, "No owned section has body_start > stop; the fixture does not exercise the claimed path"


def test_a_copy_is_byte_identical(tmp_path):
    target = generated_copy(tmp_path)
    for path in sorted(GENERATED.rglob("*")):
        if path.is_file():
            assert read(target / path.relative_to(GENERATED)) == read(path)
