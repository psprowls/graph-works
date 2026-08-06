"""The two acceptance properties, over every fixture.

Line-ending and trailing-newline variants are built **in code**, never from a
fixture on disk: `.gitattributes` marks only `packages/okf-io/tests/fixtures/**`
as `-text`, so a CRLF file under okf-ext would be silently normalized on
someone else's checkout -- the exact failure that rule exists to prevent.
Constructing the bytes here keeps the regression honest without opening a
second byte-exact fixture zone.
"""

from __future__ import annotations

import pytest
from ext_helpers import TABLED, read
from okf_ext.body import split_lines
from okf_ext.tables import Column, TableSpec, splice_text

PLAN = TableSpec(columns=(Column("action"), Column("done_when", synonyms=("done when",)), Column("rationale")))
ROW = {"action": "a new action", "done_when": "it lands", "rationale": "because"}

FIXTURES = sorted(p.name for p in TABLED.glob("*.md"))


def _body(name: str) -> str:
    """The fixture's body -- everything after the frontmatter block."""
    text = read(TABLED / name)
    return text.split("---\n", 2)[2] if text.startswith("---\n") else text


def _variants(body: str):
    yield "lf", body
    yield "crlf", body.replace("\n", "\r\n")
    yield "no-trailing", body.rstrip("\n")


def _matches_outside_a_forced_terminator(before: tuple[str, ...], after: tuple[str, ...]) -> bool:
    """*before* and *after* agree, or agree everywhere but their last element,
    where *after*'s is *before*'s plus exactly one line terminator.

    Insertion at the true end of a body with no trailing terminator forces
    `_insert` to give the previously-last line one -- otherwise the new
    content would run onto it (`splice.py`'s `_insert`, and the `TextSplice`
    docstring, both explain why). That line's bytes do change, but only its
    terminator, never its text, and only because the body's own
    trailing-newline state forced it -- not because the splice chose to
    rewrite it. Tolerating exactly that one difference here, rather than
    widening `written` or `line` to cover it, keeps both fully meaningful:
    `written` stays the count of genuinely new lines, and `line` stays the
    first line of the block the splice actually composed.
    """
    if before == after:
        return True
    if not before or len(before) != len(after) or before[:-1] != after[:-1]:
        return False
    added = after[-1][len(before[-1]) :]
    return after[-1].startswith(before[-1]) and added in ("\n", "\r\n", "\r")


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("on_conflict", ["skip", "update"])
@pytest.mark.parametrize("heading", ["Plan", "Entries"])
def test_splicing_twice_equals_splicing_once(name, on_conflict, heading):
    for label, body in _variants(_body(name)):
        once = splice_text(body, heading, PLAN, ROW, key="action", on_conflict=on_conflict).after
        twice = splice_text(once, heading, PLAN, ROW, key="action", on_conflict=on_conflict).after
        assert twice == once, f"{name} [{label}] is not idempotent"


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("heading", ["Plan", "Entries"])
def test_a_splice_changes_only_the_lines_it_claims(name, heading):
    for label, body in _variants(_body(name)):
        result = splice_text(body, heading, PLAN, ROW, key="action")
        if not result.changed:
            assert result.after == body
            continue
        before = split_lines(body)
        after = split_lines(result.after)
        first = result.line - 1
        written = len(after) - len(before)
        assert written > 0, f"{name} [{label}] claimed a change that added no line"
        assert _matches_outside_a_forced_terminator(before[:first], after[:first]), (
            f"{name} [{label}] changed a line above the span"
        )
        assert after[first + written :] == before[first:], f"{name} [{label}] changed a line below the span"


@pytest.mark.parametrize("name", FIXTURES)
@pytest.mark.parametrize("heading", ["Plan", "Entries"])
def test_an_in_place_update_changes_exactly_one_line(name, heading):
    """The minimal-change property's `on_conflict="update"` arm.

    `test_a_splice_changes_only_the_lines_it_claims` above always calls
    `splice_text` with the default `on_conflict="skip"`, and `ROW`'s key is
    absent from every fixture, so that property only ever exercises
    `action="append"` -- a present key under `"skip"` returns unchanged, and
    an absent one has nothing to update. `_update_row` never runs there.

    Seed the row in first (an append, since no fixture already carries its
    key), then splice a variant of the same row back in under
    `on_conflict="update"`: that second call must match the row just
    inserted, rewrite its non-key cells in place, add no line, and leave
    every other line byte-identical -- the same minimal-change contract,
    for the rewrite path instead of the insert path.
    """
    for label, body in _variants(_body(name)):
        seeded = splice_text(body, heading, PLAN, ROW, key="action").after
        updated_row = {**ROW, "done_when": "it landed", "rationale": "because it changed"}
        result = splice_text(seeded, heading, PLAN, updated_row, key="action", on_conflict="update")
        assert result.action == "update", f"{name} [{label}] did not take the update path"
        before = split_lines(seeded)
        after = split_lines(result.after)
        assert len(after) == len(before), f"{name} [{label}] update changed the line count"
        changed = [i for i in range(len(before)) if before[i] != after[i]]
        assert changed == [result.line - 1], f"{name} [{label}] update touched more than one line"


@pytest.mark.parametrize("name", FIXTURES)
def test_a_crlf_fixture_stays_uniformly_crlf(name):
    body = _body(name).replace("\n", "\r\n")
    after = splice_text(body, "Plan", PLAN, ROW, key="action").after
    assert "\n" not in after.replace("\r\n", "")


@pytest.mark.parametrize("name", FIXTURES)
def test_the_trailing_newline_state_is_preserved(name):
    body = _body(name)
    assert splice_text(body, "Plan", PLAN, ROW, key="action").after.endswith("\n")
    stripped = body.rstrip("\n")
    assert not splice_text(stripped, "Plan", PLAN, ROW, key="action").after.endswith("\n")
