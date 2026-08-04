"""Parsing and appending to ``log.md`` (OKF v0.2 §9).

A log is append-only by construction, so preserving what is already written
follows from the operation rather than from any convention: an append inserts
lines and copies every other byte. Frontmatter included -- §8 restricts what an
``index.md`` may carry and §9 says nothing of the sort, and
``acme_retail/log.md`` legitimately carries ``type: Log``.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from okf_io import _edit
from okf_io._md import parse_body
from okf_io.document import Document, rendered_with_body

#: §9's heading level for a dated section.
_SECTION_LEVEL = 2


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One list item from a §9 section.

    Note: nested sub-bullets yield one ``LogEntry`` per bullet, so
    ``len(section.entries)`` counts bullets, not logical entries.
    """

    text: str  # the item's inline source
    line: int  # 1-based, body-relative
    end: int  # 1-based, body-relative, inclusive


@dataclass(frozen=True, slots=True)
class LogSection:
    """One ``##`` section. ``date`` is ``None`` for a heading that is not ISO 8601."""

    date: date | None
    heading: str
    line: int  # 1-based, body-relative
    entries: tuple[LogEntry, ...]


@dataclass(frozen=True, slots=True)
class Log:
    sections: tuple[LogSection, ...]

    def on(self, day: date) -> LogSection | None:
        return next((section for section in self.sections if section.date == day), None)


@dataclass(frozen=True, slots=True)
class LogAppend:
    """What appending one entry would do. Same shape as ``IndexUpdate``.

    ``changed`` compares and renders nothing; ``diff()`` renders on demand and
    writes nothing. The symmetry with the index writer is deliberate: two
    writers, one result vocabulary.
    """

    path: str | None
    before: str
    after: str
    entry: str
    section_created: bool

    @property
    def changed(self) -> bool:
        return self.after != self.before

    def diff(self) -> str:
        name = self.path or "log.md"
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
            )
        )


#: §9 permits exactly `YYYY-MM-DD`. The shape is enforced before delegating to
#: `fromisoformat`, which on 3.11+ also accepts basic format (20260701) and ISO
#: week dates (2026-W27-3).
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def iso_date(text: str) -> date | None:
    """Parse a §9 date heading, or ``None`` when it is not one.

    Shared with ``_rules.reserved``, which reports the headings this rejects.
    One parser, so the rule and the writer can never disagree about what
    counts as a dated section.
    """
    stripped = text.strip()
    if not _ISO_DATE_RE.fullmatch(stripped):
        return None
    try:
        return date.fromisoformat(stripped)
    except ValueError:
        return None


def parse(document: Document) -> Log:
    """Read *document* as a §9 log. Never raises and never rejects.

    A ``##`` heading that is not an ISO 8601 date yields ``date=None`` rather
    than an error: the ``reserved.log-heading-not-date`` rule already reports
    that shape, and a parser that refused it would leave the writer unable to
    append to the very file the rule is warning about.

    Entries are attributed to a section by line, not by heading text, so two
    sections that happen to carry the same heading do not merge.

    **Document order is the contract:** sections are returned in the order they
    appear in the document, not sorted by date. Callers needing chronological
    order must sort themselves.

    **Only ``##`` headings are recognized as dated sections:** headings at any
    other level (``#``, ``###``, etc.) are invisible to this parser.
    """
    index = parse_body(document.body)
    starts = [h for h in index.headings if h.level == _SECTION_LEVEL]
    sections: list[LogSection] = []
    for position, heading in enumerate(starts):
        stop = starts[position + 1].line if position + 1 < len(starts) else None
        entries = tuple(
            LogEntry(item.text, item.line, item.end)
            for item in index.list_items
            if item.line > heading.line and (stop is None or item.line < stop)
        )
        sections.append(LogSection(iso_date(heading.text), heading.text, heading.line, entries))
    return Log(sections=tuple(sections))


def _spaced(body: str, anchor: int, lines: Sequence[str], newline: str) -> list[str]:
    """*lines*, with a blank line added on either side only where one is missing.

    A block insertion has to sit apart from its neighbours; adding the blank
    unconditionally would double one that is already there, and adding none
    would glue a new ``##`` heading onto the line above it.
    """
    out = list(lines)
    if anchor > 0 and _edit.line_at(body, anchor).strip():
        out.insert(0, newline)
    if anchor < _edit.line_count(body) and _edit.line_at(body, anchor + 1).strip():
        out.append(newline)
    return out


def _section_end(log: Log, position: int, body: str) -> int:
    """The last line of the section at *position*: the line before the next
    heading, or the last line of the body."""
    if position + 1 < len(log.sections):
        return log.sections[position + 1].line - 1
    return _edit.line_count(body)


def _first_inversion(
    dated: Sequence[tuple[int, LogSection]],
) -> tuple[LogSection, LogSection] | None:
    """The first adjacent pair, in document order, where the earlier section
    is older than the one that follows it -- the first break in newest-first
    order. ``None`` if the dated sections are already non-increasing.
    """
    previous: LogSection | None = None
    for _, section in dated:
        if (
            previous is not None
            and previous.date is not None
            and section.date is not None
            and previous.date < section.date
        ):
            return previous, section
        previous = section
    return None


def _new_section_anchor(parsed: Log, day: date, body: str) -> int:
    """The line a new section for *day* goes after, newest first per §9.

    Positions are computed against **dated** sections only, so an undated
    heading is never reordered around: it keeps whatever place it holds.

    ``parse()`` guarantees document order only, never date order -- nothing
    validates that a log's dated sections are newest-first (see
    ``test_parse_preserves_document_order`` in ``test_log.py``).

    **When the dated sections are non-increasing**, the log is well-formed
    and the ordinary walk below -- return before the first section older
    than *day* -- is fully trustworthy.

    **When they are not**, the walk's answer generally cannot be trusted: a
    later, un-walked section could be newer than *day* and end up stranded
    below an entry inserted ahead of it. (An earlier version of this guard
    scoped the check to only the sections walked before an anchor was found,
    reasoning that nothing past the anchor could affect it -- true for the
    anchor's own position, but not for whether a *later* section needed to
    sort ahead of *it*. That let exactly this kind of misfile through.)
    Exactly one placement stays correct no matter how disordered the rest of
    the log is: *day* strictly newer than every dated section belongs at the
    very top, since nothing before that position could ever need to precede
    it. Every other case is refused with ``ValueError`` naming the offending
    pair, rather than guessed.
    """
    dated = [(position, s) for position, s in enumerate(parsed.sections) if s.date is not None]
    inversion = _first_inversion(dated)
    if inversion is not None:
        dates = [s.date for _, s in dated if s.date is not None]
        if dates and day > max(dates):
            # Newer than everything: the top is unambiguous regardless of
            # whatever disorder sits further down, so this is always safe.
            return dated[0][1].line - 1
        previous, section = inversion
        raise ValueError(
            f"log's dated sections are not newest-first: {previous.heading!r} "
            f"(line {previous.line}) is older than {section.heading!r} "
            f"(line {section.line}), which follows it in the document; "
            "refusing to guess where a new section belongs"
        )

    for _, section in dated:
        if section.date is not None and section.date < day:
            return section.line - 1
    if dated:
        return _section_end(parsed, dated[-1][0], body)
    return _edit.line_count(body)


def append(
    document: Document,
    text: str,
    *,
    on: date | None = None,
    today: date | None = None,
    dry_run: bool = True,
) -> LogAppend:
    """Append one entry to *document*, preserving every other byte.

    The target date is *on*, defaulting to *today*, which is **injected**:
    supplying neither raises ``ValueError`` rather than reading a clock. An
    existing section for that date gains the bullet at the end of its list;
    otherwise a new section is inserted in date order among the dated ones.

    When two sections share the same date, :meth:`Log.on` returns the first
    one in document order, so the second is unreachable to an automated
    append. That is the only sane default absent a rule flagging duplicate
    dates, and it loses no data -- the entry still lands in *a* section
    carrying that date.

    *text* is written as given. §9 is explicit that a leading bold word
    (``**Update**``, ``**Creation**``) is a convention, not a requirement, so
    the core does not impose one.

    Raises ``ValueError`` if the document's dated sections are not
    newest-first and *day* does not beat every one of them (see
    :func:`_new_section_anchor`): scanning document order for an insertion
    point would otherwise silently strand a later, newer section below the
    one just inserted. A *day* newer than every dated section is the one
    placement that stays unambiguous no matter how disordered the rest of
    the log is, so that case alone is allowed through.

    **``dry_run`` defaults to ``True``**, matching :func:`okf_io.index.update`:
    the default call plans, and only an explicit ``dry_run=False`` touches the
    disk.
    """
    day = on or today
    if day is None:
        raise ValueError("append() needs `on=` or `today=`; okf-io never reads the clock")

    body = document.body
    parsed = parse(document)
    index = parse_body(body)
    marker = _edit.bullet_marker(body, index.list_items, "-")
    newline = _edit.newline_of(body)
    bullet = f"{marker} {text.strip()}"

    section = parsed.on(day)
    if section is None:
        anchor = _new_section_anchor(parsed, day, body)
        lines = _spaced(
            body,
            anchor,
            (f"{'#' * _SECTION_LEVEL} {day.isoformat()}{newline}", newline, f"{bullet}{newline}"),
            newline,
        )
        created = True
    elif section.entries:
        anchor = max(entry.end for entry in section.entries)
        lines = [f"{bullet}{newline}"]
        created = False
    else:
        anchor = section.line
        lines = _spaced(body, anchor, (f"{bullet}{newline}",), newline)
        created = False

    edited = _edit.apply(body, (_edit.insert_after(body, anchor, lines),))
    after = rendered_with_body(document, edited)
    result = LogAppend(
        path=document.path.as_posix() if document.path is not None else None,
        before=document.raw_text,
        after=after,
        entry=bullet,
        section_created=created,
    )
    # append() always inserts at least one line, so `result.changed` is always
    # True here -- the check is structural, mirroring index.update()'s
    # dry_run gating, not a defensive no-op guard for this writer.
    if not dry_run and result.changed:
        if document.path is None:
            raise ValueError("Document has no path; cannot write an append")
        document.path.write_bytes(result.after.encode("utf-8"))
    return result
