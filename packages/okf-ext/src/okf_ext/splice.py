"""Line-list splice primitives, shared by every capability that inserts text.

**Shared layer, not a capability.** `okf_ext.sections` needs the same
newline detection, line assembly and insertion `okf_ext.tables` already had,
and the independence contract forbids a capability importing a sibling -- so
these living private inside `tables/splice.py` would force `sections` to
duplicate them. That would make this the third instance of a pattern the
README already flags, and the duplicated code is precisely the code whose
value is the byte-fidelity promise it makes: two copies is how two
capabilities end up promising two different things by the same word.

**A new module rather than an addition to `okf_ext.body`.** `body` is
memoized, parse-once and read-only; stapling write primitives onto it muddies
a module whose character is worth keeping. This is `body`'s write-side
counterpart. `okf_ext.writing` stays what it is -- the file-level probe ->
stage -> commit engine, one layer below.

Imports stdlib only. Nothing here reads a file, and nothing here knows what a
bundle or a document is.
"""

from __future__ import annotations

from collections.abc import Sequence

#: The three line terminators `okf_ext.body.split_lines` can leave on a line.
CRLF, LF, CR = "\r\n", "\n", "\r"

#: What a terminated line ends with. `CRLF` is redundant against `LF` for an
#: `endswith` test and is deliberately left out, so this stays a two-element
#: tuple every call site can pass straight to `str.endswith`.
TERMINATORS = (LF, CR)


def dominant_newline(body: str) -> str:
    """The body's own line ending. Ties prefer CRLF, then LF, then CR.

    A body with no terminator at all answers `LF`: something has to be
    chosen for the first line ever appended to it, and LF is the corpus
    default.
    """
    crlf = body.count(CRLF)
    lf = body.count(LF) - crlf
    cr = body.count(CR) - crlf
    count, _, newline = max(
        [(crlf, 0, CRLF), (lf, 1, LF), (cr, 2, CR)],
        key=lambda item: (item[0], -item[1]),
    )
    return newline if count else LF


def has_trailing_newline(lines: Sequence[str]) -> bool:
    """Whether the body *lines* came from ended with a terminator.

    An empty body counts as trailing: there is no unterminated last line to
    preserve, and answering `False` would make `assemble` strip a terminator
    off the first line ever written into it.
    """
    return not lines or lines[-1].endswith(TERMINATORS)


def assemble(lines: Sequence[str], newline: str, trailing: bool) -> str:
    """Join lines-with-terminators, restoring the body's trailing state."""
    text = "".join(lines)
    if not trailing and text.endswith(newline):
        text = text[: -len(newline)]
    return text


def bare_lines(text: str) -> list[str]:
    """*text* as bare, unterminated lines, blank ends dropped.

    A YAML `|` block scalar always ends in a newline and a hand-written one
    may open with a blank; neither should stack a second blank against the
    ones a caller writes itself. Line endings are normalised here because
    every caller re-terminates each line with the newline it asked for.
    **The order of terminator normalisation is correctness-critical: CRLF must
    collapse before bare CR, else `\\r\\n` becomes `\\n\\n` and CRLF documents
    grow spurious blank lines.**

    Shared rather than private, because this is the **second** instance --
    `sections/scaffold.py` composed a section body with it and
    `okf_ext.generators` composes a replacement body with the same shape, and
    the two must agree byte for byte or a document scaffolded and then
    regenerated differs from one regenerated directly.
    """
    flat = text.replace(CRLF, LF).replace(CR, LF)
    lines = flat.split(LF)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def insert(lines: Sequence[str], at: int, new: Sequence[str], newline: str) -> list[str]:
    """Insert bare *new* lines before 1-based line *at*, terminating each.

    When *at* is one past the end, the previous last line gets a terminator
    if it lacked one -- otherwise the first inserted line would run onto it.
    That previous last line's bytes do change (it gains a terminator), but
    only ever the terminator: `at` being one past the end also means nothing
    in the body follows it, so `line=at` still marks a span with no
    unclaimed line below it, and the one line above it that changed differs
    by exactly a terminator, forced by the body's own trailing-newline state
    rather than chosen by the splice.
    """
    head = list(lines)
    if at > len(head) and head and not head[-1].endswith(TERMINATORS):
        head[-1] = head[-1] + newline
    return [*head[: at - 1], *(item + newline for item in new), *head[at - 1 :]]


def replace(lines: Sequence[str], start: int, stop: int, new: Sequence[str], newline: str) -> list[str]:
    """Replace 1-based lines *start* through *stop* **inclusive** with bare
    *new* lines, terminating each.

    *start* and *stop* are 1-based; *start* is expected to be >= 1. Behaviour
    for `start <= 0` is undefined and will not raise: negative indexing causes
    Python's slice arithmetic to wrap around, silently duplicating content.
    This is not guarded because callers pass `Section.body_start`, which cannot
    be less than 1; a runtime check would only pay for a caller that does not
    exist.

    `stop < start` claims no existing line and is therefore exactly
    `insert(lines, start, new, newline)` -- the path a section whose heading
    carries no body takes, where `body_start > stop`. That equivalence is a
    property in `test_splice.py`, not a coincidence to be rediscovered.

    Carries `insert`'s terminator discipline for the same reason: when
    *start* is one past the end, a previous last line lacking a terminator
    gets one, or the first new line would run onto it. The body's own
    trailing-newline state is restored by `assemble`, never here.
    """
    head = list(lines)
    if start > len(head) and head and not head[-1].endswith(TERMINATORS):
        head[-1] = head[-1] + newline
    return [*head[: start - 1], *(item + newline for item in new), *head[max(stop, start - 1) :]]


def needs_gap(lines: Sequence[str], at: int) -> bool:
    """Whether a separating blank line belongs above an insert at *at*.

    One is needed exactly when the line the insert lands under is non-blank:
    a heading written directly beneath a paragraph would otherwise read as a
    lazy continuation of it. Skipped when that line is already blank, so the
    splice never stacks a second blank on an existing one, and skipped at the
    very start of a body, where there is nothing above to separate from.
    """
    return bool(lines) and at > 1 and bool(lines[at - 2].strip())


#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = [
    "CR",
    "CRLF",
    "LF",
    "TERMINATORS",
    "assemble",
    "bare_lines",
    "dominant_newline",
    "has_trailing_newline",
    "insert",
    "needs_gap",
    "replace",
]
