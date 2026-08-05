"""Line-range splicing for body writes.

Internal module, and the body-level counterpart of ``document._splice``. The
writers compute their edits as ranges over the original lines and copy
everything outside them, never re-rendering a region in order to change one
line inside it -- which is how a writer destroys what it did not mean to
touch. Prose, blank lines, comments and the file's own line endings survive
because they are never re-emitted.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from okf_io._md import ListItem
from okf_io._yaml import _lines

#: A bullet list marker at the start of a line, per CommonMark's 0-3 space
#: indent allowance. Ordered lists deliberately do not match: this exists to
#: reproduce a file's own marker, and there is nothing to reproduce.
_MARKER_RE = re.compile(r"^ {0,3}([*+\-])[ \t]")


@dataclass(frozen=True, slots=True)
class Edit:
    """A replacement of the 1-based, inclusive line range ``[start, end]``.

    An insertion is the empty range ``end == start - 1``: the new lines land
    before line ``start`` and nothing is removed. A deletion carries no lines.
    Each string in ``lines`` carries its own terminator.
    """

    start: int
    end: int
    lines: tuple[str, ...] = ()


def newline_of(body: str) -> str:
    """The line ending to give a line this body did not already have."""
    return "\r\n" if "\r\n" in body else "\n"


def line_count(body: str) -> int:
    """How many lines *body* has. A final line needs no terminator to count."""
    return len(_lines(body))


def line_at(body: str, line: int) -> str:
    """Line *line* of *body*, 1-based, with its terminator."""
    return _lines(body)[line - 1]


def bullet_marker(body: str, items: Sequence[ListItem], default: str) -> str:
    """The list marker *body* already uses, or *default*.

    Taken from the source line of a parsed list item rather than from a bare
    line scan, so a ``*`` inside a fenced code block cannot answer for the
    file. §8's example uses ``*`` and ``acme_retail/log.md`` uses ``-``; a
    writer that picked one would rewrite the other's file on every append.

    Note: *items* must have been parsed from the same *body* (a mismatch will
    IndexError).
    """
    lines = _lines(body)
    for item in items:
        match = _MARKER_RE.match(lines[item.line - 1])
        if match is not None:
            return match.group(1)
    return default


def _validate_edit(edit: Edit, body_line_count: int) -> None:
    """Raise ValueError if *edit* is malformed or outside the body's bounds.

    Valid ranges are:
    - start >= 1
    - end >= start - 1 (insertions have end == start - 1)
    - end <= body_line_count
    """
    if edit.start < 1 or edit.end < edit.start - 1 or edit.end > body_line_count:
        raise ValueError(f"Invalid edit range: start={edit.start}, end={edit.end}, body has {body_line_count} lines")


def insert_after(body: str, line: int, lines: Sequence[str]) -> Edit:
    """An :class:`Edit` inserting *lines* after 1-based *line*.

    ``line == 0`` inserts at the top. When *line* is the final line of *body*
    and carries no terminator, the edit replaces it with a terminated copy --
    otherwise the first inserted line would be glued onto the end of it.

    Raises ValueError if *line* is outside [0, line_count(body)].
    """
    existing = _lines(body)
    body_size = len(existing)
    if not (0 <= line <= body_size):
        raise ValueError(
            f"Invalid line number: line={line}, body has {body_size} lines (valid range: 0 to {body_size})"
        )
    if 0 < line == body_size and not existing[-1].endswith(("\n", "\r")):
        return Edit(line, line, (existing[-1] + newline_of(body), *lines))
    return Edit(line + 1, line, tuple(lines))


def apply(body: str, edits: Sequence[Edit]) -> str:
    """Apply *edits* to *body*, copying every unedited line verbatim.

    Raises ValueError if any edit is malformed (end < start - 1), outside the
    body's bounds, or overlaps with another. Multiple edits at the same
    position apply in the caller's argument order (Python's sort is stable).
    """
    lines = _lines(body)
    body_size = len(lines)
    out: list[str] = []
    cursor = 0
    for edit in sorted(edits, key=lambda item: (item.start, item.end)):
        _validate_edit(edit, body_size)
        if edit.start - 1 < cursor:
            raise ValueError(f"Overlapping edit at line {edit.start}")
        out.extend(lines[cursor : edit.start - 1])
        out.extend(edit.lines)
        cursor = edit.end
    out.extend(lines[cursor:])
    return "".join(out)
