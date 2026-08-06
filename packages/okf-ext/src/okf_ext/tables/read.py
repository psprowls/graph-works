"""Table reads: generic here, spec-driven below it.

Two levels, because the three consumers want different things. The entity
lane's file maps and the Diátaxis entry gate want positional
`(headers, rows)` from a table whose headers they do not know in advance; the
work lane's plan tables want typed rows, header synonyms, and the four-state
result. A generic reader with no expectations serves the first two, and the
spec-driven reader layers the rest on top.

**This module imports the shared `okf_ext.body` layer and nothing else from
its own package.** It never imports `okf_ext` itself, nor a sibling
capability.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from functools import lru_cache
from types import MappingProxyType

from okf_ext.body import Section, find_section, prose_lines, split_lines
from okf_ext.tables.model import SectionRead, Table, TableSpec

#: A `|` that is not escaped. `\\|` is a cell's own content; a bare `|` is the
#: separator. `\\\\|` -- an escaped backslash followed by a real separator --
#: reads here as an escaped pipe; neither reference implementation handles it
#: either, and no corpus row carries one.
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")

#: One cell of a delimiter row: `---`, `:---`, `---:`, `:---:`.
_DELIMITER_CELL_RE = re.compile(r"^:?-+:?$")


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def _split_cells(line: str) -> tuple[str, ...]:
    """*line* split on unescaped pipes, one structural pipe stripped each end.

    Stripped **structurally** -- one leading and one trailing `|` -- rather
    than by dropping empty cells afterwards, which would also swallow a
    legitimately empty first or last cell (`wiki_io._split_pipes`'s documented
    reason). A leading `|` cannot itself be escaped, since nothing precedes
    it; a trailing one can, so `\\|` at the end is content, not structure.

    Cells are stripped of surrounding whitespace and `\\|` is decoded; the
    splice re-escapes on write.

    The leading pipe is stripped unconditionally, with no `startswith("|")`
    guard: every caller reaches this through `_scan`'s `_is_table_line`
    filter or through `okf_ext.tables.splice`'s line numbers, both of which
    already guarantee one, and this function is private to the package (its
    name-mangled `_` prefix, not an access check) -- no caller can reach it
    with a line that lacks one. A guard here would be defensive code with
    nothing left to defend against, and the only line the package's coverage
    gate could never reach.
    """
    text = line.strip()[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return tuple(cell.strip().replace("\\|", "|") for cell in _UNESCAPED_PIPE_RE.split(text))


def _is_delimiter(cells: Sequence[str]) -> bool:
    return bool(cells) and all(_DELIMITER_CELL_RE.match(cell) for cell in cells)


def _pad(cells: tuple[str, ...], width: int) -> tuple[str, ...]:
    """Short rows padded with `""`; long rows keep their extra cells.

    Padding rather than refusing, because a plan table missing its last column
    on one row is still a plan table -- and truncating would throw away a cell
    the author wrote.
    """
    return cells if len(cells) >= width else cells + ("",) * (width - len(cells))


def _delimiter_line(body_lines: Sequence[str], start: int, stop: int) -> int:
    """The line number of the `|---|---|` row directly under the header, or 0.

    Only `start + 1` is ever a candidate: a table has at most one delimiter,
    and it is either immediately below the header or not present at all. A
    delimiter-shaped line anywhere else in the table is a data row (see
    `_data_row_lines`), not a second delimiter.
    """
    candidate = start + 1
    if candidate <= stop and _is_delimiter(_split_cells(body_lines[candidate - 1])):
        return candidate
    return 0


def _data_row_lines(body_lines: Sequence[str], start: int, stop: int) -> tuple[int, ...]:
    """The physical line numbers of `start`..`stop` that are this table's data
    rows -- aligned index-for-index with `Table.rows`, which is built from
    exactly these lines and nothing else. **The single place this alignment
    is decided**; `_build` and `okf_ext.tables.splice` both call this rather
    than each deriving the rule, so they cannot disagree about which lines
    are data again.

    Only the line directly under the header can be the delimiter
    (`_delimiter_line`). Every line after that is a data row, even one that
    is itself delimiter-shaped -- a `| - | - | - |` "not filled in yet"
    placeholder, or a copy-pasted second `| --- | --- | --- |`. That is the
    more conservative reading: it never discards a line the author wrote,
    the same instinct `_pad` states for short and long rows.
    """
    delimiter = _delimiter_line(body_lines, start, stop)
    first_row = delimiter + 1 if delimiter else start + 1
    return tuple(range(first_row, stop + 1))


def _build(body_lines: Sequence[str], start: int, stop: int) -> Table:
    headers = _split_cells(body_lines[start - 1])
    delimiter = _delimiter_line(body_lines, start, stop)
    row_lines = _data_row_lines(body_lines, start, stop)
    rows = tuple(_pad(_split_cells(body_lines[number - 1]), len(headers)) for number in row_lines)
    return Table(headers=headers, rows=rows, start=start, stop=stop, delimiter=delimiter)


@lru_cache(maxsize=512)  # mirrors okf_ext.body._skeleton: a body's tables are scanned once
def _scan(body: str) -> tuple[Table, ...]:
    """Every table in *body*, in document order. The unfiltered, cached scan
    `read_all` filters for `within=` rather than re-running.

    A table is a run of contiguous lines that begin with `|` **and that the
    parser calls prose** -- a pipe table inside a fenced block is somebody's
    example, not a table. The first line of the run is the header; a
    `| --- | --- |` row directly under it is recorded as the delimiter rather
    than required.
    """
    body_lines = split_lines(body)
    prose = prose_lines(body)
    total = len(body_lines)

    tables: list[Table] = []
    number = 1
    while number <= total:
        if number not in prose or not _is_table_line(body_lines[number - 1]):
            number += 1
            continue
        start = number
        while number <= total and number in prose and _is_table_line(body_lines[number - 1]):
            number += 1
        tables.append(_build(body_lines, start, number - 1))
    return tuple(tables)


def read_all(body: str, *, within: Section | None = None) -> tuple[Table, ...]:
    """Every table in *body*, or in *within*'s span, in document order.

    `within=` is how the file-map walk gets "the first table in this H3 block"
    without slicing strings first, which would lose every line number. It
    filters the whole-body scan rather than re-scanning a slice: a table is a
    *contiguous* run of pipe lines, so it can never cross a heading, and a
    table starting inside a section's span is therefore always wholly inside
    it -- filtering by containment and re-scanning that span agree on every
    body. Filtering also means `read_all(body)` and `read(body)` -- which
    calls it with `within=None` -- return the very same cached tuple for the
    same *body*, one parse serving every caller the way `_skeleton` does.
    """
    tables = _scan(body)
    if within is None:
        return tables
    return tuple(table for table in tables if within.body_start <= table.start and table.stop <= within.stop)


def read(body: str, *, within: Section | None = None) -> Table | None:
    """The first table in *body*, or in *within*'s span. `None` when there is none."""
    found = read_all(body, within=within)
    return found[0] if found else None


class _SpecRow(Mapping[str, str]):
    """Cells by canonical name, defaulting inside the spec.

    Iterates only the columns the header actually carried, so `list(row)`
    and `matched` tell the same story -- but a lookup of any *other* column
    in the spec reads `""` rather than raising, so a consumer walking a
    partially-conformant corpus does not have to guard every access. A name
    in neither still raises `KeyError`, because that is a caller error, not
    a corpus one.

    Private: `SectionRead.rows` is typed `Mapping[str, str]`, which is the
    whole contract.
    """

    __slots__ = ("_cells", "_columns")

    def __init__(self, cells: Mapping[str, str], columns: frozenset[str]) -> None:
        self._cells = cells
        self._columns = columns

    def __getitem__(self, key: str) -> str:
        if key in self._cells:
            return self._cells[key]
        if key in self._columns:
            return ""
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        return key in self._cells

    def __iter__(self) -> Iterator[str]:
        return iter(self._cells)

    def __len__(self) -> int:
        return len(self._cells)

    def __repr__(self) -> str:
        return f"_SpecRow({dict(self._cells)!r})"


def _match_headers(headers: Sequence[str], spec: TableSpec) -> dict[str, int]:
    """Canonical name -> header index, in spec order.

    Case-insensitive and whitespace-stripped, against each column's canonical
    name first and then its synonyms, so a header carrying both spellings
    binds to the canonical one.

    When two spec columns resolve to the same header (through shared synonyms
    or a synonym equalling another's canonical name), first-wins: only the
    earlier column claims the header, and the later one is omitted from the
    result. This keeps `matched` honest — reporting what was truly found — and
    reuses the already-tested path for "spec column the header lacked" rather
    than adding a new kind of absence.
    """
    header_index: dict[str, int] = {cell.strip().casefold(): i for i, cell in enumerate(headers)}
    positions: dict[str, int] = {}
    claimed: set[int] = set()
    for column in spec.columns:
        for candidate in (column.name, *column.synonyms):
            wanted = candidate.strip().casefold()
            if wanted in header_index:
                index = header_index[wanted]
                if index not in claimed:
                    positions[column.name] = index
                    claimed.add(index)
                break
    return positions


def read_section(body: str, heading: str, spec: TableSpec) -> SectionRead:
    """Read the first table under *heading* against *spec*.

    | `state` | condition |
    |---|---|
    | `missing` | no heading with that text |
    | `malformed` | heading present, but no table found, or too few spec columns |
    | `empty` | header matched the spec, no data rows |
    | `ok` | at least one data row |

    The state distinction is the thing all three surveys singled out: it is
    what lets a lint rule say something useful instead of failing. It exists
    only at this level, because `malformed` means *these headers were not
    found*, which a reader with no expectations cannot know.

    `table` is populated whenever a table was found at all -- including for
    `malformed` -- so a rule can report what *was* there rather than only
    that it was wrong.
    """
    section = find_section(body, heading)
    if section is None:
        return SectionRead(state="missing", rows=(), table=None, matched=())

    table = read(body, within=section)
    if table is None:
        return SectionRead(state="malformed", rows=(), table=None, matched=())

    positions = _match_headers(table.headers, spec)
    matched = tuple(positions)
    # Clamped, so a single-column spec is not permanently `malformed` by a
    # default of 2 it could never meet.
    if len(positions) < min(spec.min_matched, len(spec.columns)):
        return SectionRead(state="malformed", rows=(), table=table, matched=matched)

    if not table.rows:
        return SectionRead(state="empty", rows=(), table=table, matched=matched)

    columns = frozenset(column.name for column in spec.columns)
    rows = tuple(
        _SpecRow(
            # _build pads every row to at least len(headers), and every matched index
            # comes from table.headers, so index < len(cells) is always true.
            MappingProxyType({name: cells[index] for name, index in positions.items()}),
            columns,
        )
        for cells in table.rows
    )
    return SectionRead(state="ok", rows=rows, table=table, matched=matched)
