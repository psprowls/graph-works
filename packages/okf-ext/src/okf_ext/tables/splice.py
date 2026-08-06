"""Writing a row into a table: a pure text->text splice, and the bundle pair.

**The splice preserves the body's bytes.** The reference implementation
returns `"\\n".join(lines) + "\\n"`, which normalizes every line ending in
the document and forces a trailing newline. Against this repo's fixture
discipline -- `edge/encoding/crlf.md` is uniformly CRLF *on purpose* -- that
is a regression, not a detail. The splice here preserves the body's dominant
line ending and its trailing-newline state, and changes only the lines it
claims.

**Idempotence is the contract.** A generator that re-runs is the reason this
exists, so splicing the same row twice must equal splicing it once, byte for
byte. It surfaces as an unchanged result here and as an empty plan at the
bundle level -- never as a boolean, which could not express "these 38 of
these 40 would change".

This module imports the shared layer and its own capability's modules, and
nothing else from its own package.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from okf_io import Bundle, Document
from okf_io.document import rendered_with_body
from ruamel.yaml.error import YAMLError

from okf_ext.body import Section, find_section, split_lines
from okf_ext.tables.model import RowSplice, SpliceAction, SplicePlan, Table, TableSpec, TextSplice
from okf_ext.tables.read import _data_row_lines, _match_headers, _split_cells, read_section
from okf_ext.writing import ApplyResult, PendingWrite, Skipped, WriteFailure, write_all

_CRLF, _LF, _CR = "\r\n", "\n", "\r"

__all__ = ["apply", "plan_row", "splice_text"]


def _require_key(spec: TableSpec, key: str) -> None:
    """Reject a key that is not one of the spec's columns.

    A caller error, caught here rather than producing a splice (or a plan)
    that can never match anything and silently appends a duplicate row on
    every run.
    """
    names = tuple(column.name for column in spec.columns)
    if key not in names:
        raise ValueError(f"key {key!r} is not a column of this spec; expected one of {names!r}")


def _dominant_newline(body: str) -> str:
    """The body's own line ending. Ties prefer CRLF, then LF, then CR."""
    crlf = body.count(_CRLF)
    lf = body.count(_LF) - crlf
    cr = body.count(_CR) - crlf
    count, _, newline = max(
        [(crlf, 0, _CRLF), (lf, 1, _LF), (cr, 2, _CR)],
        key=lambda item: (item[0], -item[1]),
    )
    return newline if count else _LF


def _normalize(value: str) -> str:
    """A cell's comparable form: newlines flattened to spaces, then stripped.

    No escaping -- `okf_ext.tables.read` returns cells already decoded, so
    this is the form both sides of a key comparison are in.
    """
    return " ".join(value.replace(_CRLF, _LF).replace(_CR, _LF).split(_LF)).strip()


def _escape(value: str) -> str:
    """A cell's written form. A raw `|` would end the cell.

    Must stay the exact inverse of `read._split_cells`'s decode (`\\|` ->
    `|`) -- idempotence depends on a round trip through both, and nothing
    type-checks that relationship. `\\\\|` -- an escaped backslash followed by
    a real separator -- is not invertible by either function; `_split_cells`
    documents that as shared, undefined territory rather than a promise this
    makes.
    """
    return value.replace("|", "\\|")


def _render(cells: Mapping[str, str], names: Sequence[str]) -> str:
    """One table row. A name not in *cells* -- including the empty string, for
    a disk column the spec does not map -- renders as an empty cell."""
    return "| " + " | ".join(_escape(cells.get(name, "")) for name in names) + " |"


def _disk_names(headers: Sequence[str], spec: TableSpec) -> tuple[str, ...]:
    """One canonical name per header column, `""` where the spec maps none.

    This is what makes an appended row carry the column count of the table
    **on disk** rather than the spec's: a four-column table gets four cells,
    with the unmapped one left empty.
    """
    by_index = {index: name for name, index in _match_headers(headers, spec).items()}
    return tuple(by_index.get(index, "") for index in range(len(headers)))


def _spec_names(spec: TableSpec) -> tuple[str, ...]:
    return tuple(column.name for column in spec.columns)


def _header_row(names: Sequence[str]) -> str:
    return "| " + " | ".join(names) + " |"


def _delimiter_row(count: int) -> str:
    return "| " + " | ".join("---" for _ in range(count)) + " |"


def _assemble(lines: Sequence[str], newline: str, trailing: bool) -> str:
    """Join lines-with-terminators, restoring the body's trailing state."""
    text = "".join(lines)
    if not trailing and text.endswith(newline):
        text = text[: -len(newline)]
    return text


def _insert(lines: Sequence[str], at: int, new: Sequence[str], newline: str) -> list[str]:
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
    if at > len(head) and head and not head[-1].endswith((_LF, _CR)):
        head[-1] = head[-1] + newline
    return [*head[: at - 1], *(item + newline for item in new), *head[at - 1 :]]


def splice_text(
    body: str,
    heading: str,
    spec: TableSpec,
    row: Mapping[str, str],
    *,
    key: str,
    on_conflict: Literal["skip", "update"] = "skip",
    create: bool = True,
) -> TextSplice:
    """Ensure *body*'s table under *heading* carries *row*.

    | Prior state | Result |
    |---|---|
    | key already present, `on_conflict="skip"` | unchanged, `action=None` |
    | key already present, `on_conflict="update"` | non-key cells rewritten, `action="update"` |
    | `ok` or `empty` | row appended after the last table line, `action="append"` |
    | `malformed` | a fresh table inserted below the heading; the prose survives. `action="create-table"` |
    | `missing`, `create=True` | heading, table and row appended at the end. `action="create-section"` |
    | `missing`, `create=False` | unchanged, `action=None` |

    A `create-table` splice writes a fresh header, delimiter and row directly
    below the heading, followed by a blank line so nothing already there can
    merge into the new table as a lazy continuation -- the prose underneath
    is otherwise untouched.

    **`create` defaults to `True`, unlike `okf_io.update_index`'s
    `create_missing=False`.** Creating a *file* is a bigger act than editing
    one, so that one makes a caller opt in. Creating a heading and a table
    inside a document that already exists is what "idempotent splice" means
    in all three surveys -- the alternative is that the common call always
    passes a flag.

    **`on_conflict` defaults to `"skip"`**, matching `ensure_plan_row`: when
    the key column matches but other cells differ, leave it alone. The same
    instinct as `descriptions="preserve"` in `update_index()` -- the machine
    owns which rows exist, the human owns what they say, until told
    otherwise.

    A created heading is written at level 2. `find_section` stays
    level-agnostic on read; the level only has to be chosen when there is
    nothing there yet, and all three consumers write `##`.

    **Idempotence needs the key column to exist in the table on disk.** When
    it does not, no row can match and every call appends -- which is the
    honest outcome, since nothing identifies the row that would have to be
    updated instead. And because nothing matches, *row*'s own key value is
    silently dropped from every cell rendered this way too -- `_disk_names`
    has no disk column to put it in. A caller that must know whether the key
    column is there should check with `read_section(...).matched` first
    (`key in matched`) rather than discover it from a row missing its key.

    Raises `ValueError` when *key* is not one of the spec's column names.
    Nothing else here raises, and nothing here touches a filesystem -- which
    is why there is no `dry_run` flag.
    """
    _require_key(spec, key)

    newline = _dominant_newline(body)
    lines = split_lines(body)
    trailing = not lines or lines[-1].endswith((_LF, _CR))
    values = {name: _normalize(value) for name, value in row.items()}
    unchanged = TextSplice(before=body, after=body, action=None, line=0)

    result = read_section(body, heading, spec)

    if result.state == "missing":
        if not create:
            return unchanged
        return _create_section(body, lines, heading, spec, values, newline=newline, trailing=trailing)

    if result.state == "malformed":
        section = find_section(body, heading)
        assert section is not None  # `missing` is the only state without one
        return _create_table(body, lines, section, spec, values, newline=newline, trailing=trailing)

    table = result.table
    assert table is not None  # `ok` and `empty` both carry one

    names = _disk_names(table.headers, spec)
    wanted = values.get(key, "")
    existing = next(
        (index for index, cells in enumerate(result.rows) if cells[key] == wanted),
        None,
    )

    if existing is not None:
        if on_conflict == "skip":
            return unchanged
        return _update_row(body, lines, table, existing, names, values, key, newline=newline, trailing=trailing)

    at = table.stop + 1
    after = _assemble(_insert(lines, at, [_render(values, names)], newline), newline, trailing)
    return _splice_or_unchanged(body, after, "append", at)


def _splice_or_unchanged(before: str, after: str, action: SpliceAction, line: int) -> TextSplice:
    """`action is None` exactly when nothing changed, and `line` is 0 then.

    A rewrite that happens to produce identical bytes -- the second
    `on_conflict="update"` against an already-updated row -- is not a change,
    and reporting one would break the idempotence contract at the only place
    it could.
    """
    if after == before:
        return TextSplice(before=before, after=before, action=None, line=0)
    return TextSplice(before=before, after=after, action=action, line=line)


def _update_row(
    body: str,
    lines: Sequence[str],
    table: Table,
    index: int,
    names: Sequence[str],
    values: Mapping[str, str],
    key: str,
    *,
    newline: str,
    trailing: bool,
) -> TextSplice:
    """Rewrite one row's non-key cells in place.

    The key cell keeps whatever is on disk -- it already matches, and
    rewriting it would let a normalization difference move the row's
    identity out from under the caller. A disk column the spec does not map
    (`names[position] == ""`) keeps its cell too: this rewrites the cells the
    caller named, not the row.
    """
    number = _data_row_lines(lines, table.start, table.stop)[index]
    current = _split_cells(lines[number - 1])

    def cell(position: int, name: str) -> str:
        if name and name != key and name in values:
            return values[name]
        return current[position] if position < len(current) else ""

    rendered = "| " + " | ".join(_escape(cell(position, name)) for position, name in enumerate(names)) + " |"
    rewritten = list(lines)
    rewritten[number - 1] = rendered + (newline if lines[number - 1].endswith((_LF, _CR)) else "")
    after = _assemble(rewritten, newline, trailing)
    return _splice_or_unchanged(body, after, "update", number)


def _create_table(
    body: str,
    lines: Sequence[str],
    section: Section,
    spec: TableSpec,
    values: Mapping[str, str],
    *,
    newline: str,
    trailing: bool,
) -> TextSplice:
    """A heading with no table: insert one directly below the heading.

    The trailing blank line is what keeps existing content from merging into
    the fresh table -- a paragraph on the line after a table row is a lazy
    continuation of it. No *leading* blank is needed: the line above is the
    heading, which is its own block.

    Added only when the line the insertion pushes down is not already blank
    -- the common shape, a heading followed by a blank line before its prose
    -- so the splice does not stack a second blank on top of one that is
    already there. The same instinct as `_create_section`'s `gap`. Also
    skipped when the heading is the body's last line (`at` one past the end
    of `lines`): there is nothing below the new table for a blank to guard
    against merging into it, so appending one would only add a stray blank
    at end of file -- or, on a body with no trailing newline, silently grant
    it one, breaking the trailing-newline guarantee this module's own
    docstring states.
    """
    names = _spec_names(spec)
    at = section.body_start
    already_blank = at - 1 < len(lines) and not lines[at - 1].strip()
    tail = [] if already_blank or at > len(lines) else [""]
    new = [_header_row(names), _delimiter_row(len(names)), _render(values, names), *tail]
    after = _assemble(_insert(lines, at, new, newline), newline, trailing)
    return _splice_or_unchanged(body, after, "create-table", at)


def _create_section(
    body: str,
    lines: Sequence[str],
    heading: str,
    spec: TableSpec,
    values: Mapping[str, str],
    *,
    newline: str,
    trailing: bool,
) -> TextSplice:
    """No such heading: append a whole section at the end of the body.

    `line` is `at`, the first line of the whole inserted block -- the
    separating blank when one was needed, otherwise the heading itself --
    never `at + len(gap)`. The gap is still new content the splice wrote, so
    a caller diffing from `line` must see a contiguous inserted span with no
    gap-shaped hole above it.
    """
    names = _spec_names(spec)
    gap = [""] if lines and lines[-1].strip() else []
    new = [
        *gap,
        f"## {heading.strip()}",
        "",
        _header_row(names),
        _delimiter_row(len(names)),
        _render(values, names),
    ]
    at = len(lines) + 1
    after = _assemble(_insert(lines, at, new, newline), newline, trailing)
    return _splice_or_unchanged(body, after, "create-section", at)


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _body_committer(document: Document, body: str) -> Callable[[], None]:
    """What `write_all` runs once this document's bytes have landed.

    `set_body` writes through to the `Split` as well as the `body` field, so
    the in-memory bundle agrees with disk immediately, with no reload, for
    exactly the documents written and no others.
    """

    def commit() -> None:
        document.set_body(body)

    return commit


def plan_row(
    bundle: Bundle,
    concept_ids: Iterable[str],
    heading: str,
    spec: TableSpec,
    row: Mapping[str, str],
    *,
    key: str,
    on_conflict: Literal["skip", "update"] = "skip",
    create: bool = True,
) -> SplicePlan:
    """Plan *row* into the table under *heading* in each of *concept_ids*.

    **Targets are named explicitly.** A tag rename discovers its own targets
    by scanning for the tag; "which concepts should carry this row" has no
    such derivation, and inventing a predicate parameter for a caller who
    already knows the answer is ceremony. `Bundle.by_type` and friends are
    how a caller builds the list.

    **Idempotence surfaces as an empty plan.** A concept already carrying the
    row contributes no splice at all, so `plan.is_empty` is the "nothing to
    do" signal and `plan.splices` names exactly what would change -- a
    preview a boolean return could not express.

    Content problems never raise: an unreadable or unparseable concept
    becomes a `Skipped`, as does one with no such heading under
    `create=False`. A concept named here but absent from *bundle* is
    `Skipped` with reason `"unreadable"` and a detail saying so -- the
    closest existing member of the vocabulary, which this function does not
    widen further.

    Raises `ValueError` when *key* is not one of the spec's column names --
    a caller error, caught at planning time rather than producing a plan that
    can never match. It is checked before any document is read, so an empty
    bundle does not hide it.
    """
    _require_key(spec, key)

    splices: list[RowSplice] = []
    skipped: list[Skipped] = []
    for concept_id in sorted(set(concept_ids)):
        member = f"{concept_id}.md"
        document = bundle.concepts.get(concept_id)
        if document is None or document.path is None:
            skipped.append(
                Skipped(
                    concept_id=concept_id,
                    path=member,
                    reason="unreadable",
                    detail="concept is not a member of this bundle",
                )
            )
            continue
        if document.parse_error is not None:
            skipped.append(
                Skipped(
                    concept_id=concept_id,
                    path=member,
                    reason="parse-error",
                    detail=f"{document.parse_error.kind}: {document.parse_error.message}",
                )
            )
            continue

        splice = splice_text(document.body, heading, spec, row, key=key, on_conflict=on_conflict, create=create)
        if splice.action is None:
            # Two very different no-ops. A section that is simply not there
            # under `create=False` is something the caller asked about and
            # got nothing for, so it is reported; a row already present is
            # the idempotent hit, and reporting it would make every re-run
            # of a generator look like a pile of skips.
            if not create and read_section(document.body, heading, spec).state == "missing":
                skipped.append(
                    Skipped(
                        concept_id=concept_id,
                        path=member,
                        reason="section-missing",
                        detail=f"no `{heading}` heading, and create=False",
                    )
                )
            continue

        splices.append(
            RowSplice(
                concept_id=concept_id,
                path=member,
                heading=heading,
                row=MappingProxyType(dict(row)),
                action=splice.action,
                line=splice.line,
                digest=_digest(document.body),
                after=splice.after,
            )
        )

    return SplicePlan(root=bundle.root, splices=tuple(splices), skipped=tuple(skipped))


def apply(bundle: Bundle, plan: SplicePlan) -> ApplyResult:
    """Write *plan* against *bundle*.

    **Content failures are refused per document; siblings still write.** A
    concept missing from the bundle, a parse error, a plan naming one
    concept twice, or a stale splice is caught before that document is
    queued to write. The two I/O regimes -- probe/staging all-or-nothing,
    and per-document commit -- belong to `okf_ext.writing.write_all`, which
    is where they are documented.

    **Staleness is a body digest.** `tags` records a *position* in the raw
    sequence and refuses if that position no longer holds what the plan
    claims; the analogue here is the body the splice was computed against. A
    digest rather than a re-derived state comparison, because the splice is
    a deterministic function of the whole body -- a change anywhere in it
    can move the line the row lands on, so "the section still parses to the
    same state" is a weaker check than it looks.

    A plan naming one concept twice is refused with `duplicate-edit`. No
    plan `plan_row` builds can carry one (it iterates a deduplicated set),
    but a hand-built plan could, and both splices were computed against the
    same body -- applying them in sequence would silently discard the first.

    Does not raise for a write failure or an unwritable target; both are
    reported in `ApplyResult.failed`, sorted by path. `stale` is the one
    worth re-planning over; `unwritable`, `stage-error` and `commit-error`
    are the ones worth retrying as-is.

    Raises `ValueError` for a plan built against a different bundle: the
    digests and line numbers in a plan mean nothing anywhere else.
    """
    if Path(plan.root).resolve() != Path(bundle.root).resolve():
        raise ValueError(
            f"Plan was built against a different bundle ({plan.root}), not {bundle.root}. "
            f"A plan's splices mean nothing outside the bundle it was planned against."
        )

    grouped: dict[str, list[RowSplice]] = {}
    for splice in plan.splices:
        grouped.setdefault(splice.concept_id, []).append(splice)

    failed: list[WriteFailure] = []
    pending: list[PendingWrite] = []

    for concept_id, splices in sorted(grouped.items()):
        member = f"{concept_id}.md"
        document = bundle.concepts.get(concept_id)
        if document is None or document.path is None:
            failed.append(
                WriteFailure(path=member, kind="not-a-member", error="concept is not a member of this bundle")
            )
            continue
        if document.parse_error is not None:
            failed.append(
                WriteFailure(
                    path=member,
                    kind="parse-error",
                    error=(
                        f"cannot mutate a document that failed to parse "
                        f"({document.parse_error.kind}): {document.parse_error.message}"
                    ),
                )
            )
            continue
        if len(splices) > 1:
            failed.append(
                WriteFailure(
                    path=member,
                    kind="duplicate-edit",
                    error=(
                        "plan carries more than one splice for this concept; "
                        "refusing rather than silently applying only the last"
                    ),
                )
            )
            continue

        splice = splices[0]
        if _digest(document.body) != splice.digest:
            failed.append(
                WriteFailure(
                    path=member,
                    kind="stale",
                    error="stale plan: the body changed since it was planned; re-plan against the current bundle",
                )
            )
            continue

        try:
            rendered = rendered_with_body(document, splice.after)
        except (YAMLError, ValueError, RecursionError) as exc:
            failed.append(WriteFailure(path=member, kind="serialize-error", error=str(exc)))
            continue

        assert document.path is not None  # guaranteed by the `document is None or document.path is None` check
        pending.append(
            PendingWrite(
                member=member,
                path=document.path,
                rendered=rendered,
                on_written=_body_committer(document, splice.after),
            )
        )

    return write_all(pending, failed=failed, skipped=plan.skipped)
