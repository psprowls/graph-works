"""The acceptance property, over every fixture body.

okf-io round-trip property 2, one layer up: a mutation is minimal, and every
byte it does not claim survives untouched.

Line-ending and trailing-newline variants are built **in code**, never from a
fixture on disk: `.gitattributes` marks only `packages/okf-io/tests/fixtures/**`
as `-text`, so a CRLF file under okf-ext would be silently normalized on
someone else's checkout -- the exact failure that rule exists to prevent.
"""

from __future__ import annotations

import pytest
from ext_helpers import SECTIONED, SECTIONS_DIR, read
from okf_ext.body import split_lines
from okf_ext.sections import load_sections
from okf_ext.sections.scaffold import _scaffold

SECTION_SET = load_sections(SECTIONS_DIR)
FIXTURES = sorted(p.name for p in SECTIONED.glob("*.md"))

#: The declarations the corpus carries. Every body is run against both, so a
#: fixture written for one type still exercises the other's insertion paths.
DECLARATIONS = sorted(SECTION_SET.types)


def _body(name: str) -> str:
    """The fixture's body -- everything after the frontmatter block."""
    text = read(SECTIONED / name)
    return text.split("---\n", 2)[2] if text.startswith("---\n") else text


def _variants(body: str):
    yield "lf", body
    yield "crlf", body.replace("\n", "\r\n")
    yield "no-trailing", body.rstrip("\n")


def _matches_outside_a_forced_terminator(before: tuple[str, ...], after: tuple[str, ...]) -> bool:
    """*before* and *after* agree, or agree everywhere but their last element,
    where *after*'s is *before*'s plus exactly one line terminator.

    Insertion at the true end of a body with no trailing terminator forces
    `okf_ext.splice.insert` to give the previously-last line one -- otherwise
    the new content would run onto it. That line's bytes do change, but only
    its terminator, never its text, and only because the body's own
    trailing-newline state forced it. Tolerating exactly that one difference
    keeps `line` fully meaningful as the first line of the block the scaffold
    actually composed.
    """
    if before == after:
        return True
    if not before or len(before) != len(after) or before[:-1] != after[:-1]:
        return False
    added = after[-1][len(before[-1]) :]
    return after[-1].startswith(before[-1]) and added in ("\n", "\r\n", "\r")


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_scaffolding_twice_equals_scaffolding_once(name, type_name):
    declaration = SECTION_SET.types[type_name]
    for label, body in _variants(_body(name)):
        once, _ = _scaffold(body, declaration)
        twice, inserts = _scaffold(once, declaration)
        assert twice == once, f"{name} [{label}/{type_name}] is not idempotent"
        assert inserts == (), f"{name} [{label}/{type_name}] planned a second time"


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_a_scaffold_changes_only_the_lines_it_claims(name, type_name):
    """Every insert lands contiguously, so the span from the first insert's
    line through the end of the last block is the whole change."""
    declaration = SECTION_SET.types[type_name]
    for label, body in _variants(_body(name)):
        after_text, inserts = _scaffold(body, declaration)
        if not inserts:
            assert after_text == body
            continue
        before = split_lines(body)
        after = split_lines(after_text)
        first = inserts[0].line - 1
        written = len(after) - len(before)
        assert written > 0, f"{name} [{label}/{type_name}] claimed a change that added no line"
        assert _matches_outside_a_forced_terminator(before[:first], after[:first]), (
            f"{name} [{label}/{type_name}] changed a line above the span"
        )
        assert after[first + written :] == before[first:], f"{name} [{label}/{type_name}] changed a line below the span"


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_a_crlf_body_stays_uniformly_crlf(name, type_name):
    body = _body(name).replace("\n", "\r\n")
    after, _ = _scaffold(body, SECTION_SET.types[type_name])
    assert "\n" not in after.replace("\r\n", "")


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_the_trailing_newline_state_is_preserved(name, type_name):
    declaration = SECTION_SET.types[type_name]
    body = _body(name)
    assert _scaffold(body, declaration)[0].endswith("\n")
    stripped = body.rstrip("\n")
    assert not _scaffold(stripped, declaration)[0].endswith("\n")


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("type_name", DECLARATIONS)
def test_every_inserted_block_is_reachable_from_its_own_line(name, type_name):
    """`SectionInsert.line` is the first line of the block, gap included, so
    the heading is on that line or the one below it -- never further down."""
    declaration = SECTION_SET.types[type_name]
    for label, body in _variants(_body(name)):
        after_text, inserts = _scaffold(body, declaration)
        lines = split_lines(after_text)
        for item in inserts:
            window = "".join(lines[item.line - 1 : item.line + 1])
            assert item.heading in window, f"{name} [{label}/{type_name}]: {item.heading} not at line {item.line}"
