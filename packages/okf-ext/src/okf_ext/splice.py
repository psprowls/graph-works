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
    "dominant_newline",
    "has_trailing_newline",
    "insert",
    "needs_gap",
]
