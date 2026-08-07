"""Finding the exact text of a reference in a body.

**Spans cannot be read off the link graph.** `okf_io._md.MdLink.line` is the
*containing block's* first line, not the link's own, and carries no column: a
paragraph with four links reports the same line four times. So exact spans are
re-derived here. A destination never spans lines -- an angle-bracket
destination may not contain a line ending -- so a `(line, column, text)` span
is always well-defined.

**The scan is bounded by a real parse, never a bare regex.** The line mask
comes from `okf_ext.body.prose_lines()`, which excludes fenced blocks,
indented blocks and raw HTML blocks, so a fenced `[x](concepts/foo.md)` example
is never rewritten -- the first of the three bugs `okf_io._md`'s docstring
attributes to regex scanners. `prose_lines` is a *line* mask, so inline code
spans are masked here as well: an inline span sits on a prose line, and a
rewrite inside one would be an edit the graph never saw. For a concept, the
count reconciliation in `plan` catches a mismatch in *either* direction -- a
shortfall or an excess -- so a false positive here does not silently corrupt
anything; it turns a legitimate move into a confusing `unlocatable-reference`
refusal instead, which is reason enough to close the false positive at the
source rather than lean on the reconciliation to catch it. `index.md` and
`log.md` are not link sources the graph counts at all, so an excess there has
no count to be caught by -- a known limitation, tracked in Task 7.

`markdown_it` is imported directly for `reference_definitions`, matching
`okf_ext.body`: reaching into `okf_io._md` would couple this package to a
private module of the one it sits above. That question -- what reference
definitions does this body declare -- is moves-specific, so it lives here
rather than in the shared layer; hoist it the day a second capability wants it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from markdown_it import MarkdownIt

from okf_ext.body import prose_lines, split_lines

#: One shared parser. `commonmark` deliberately, matching `okf_io._md` and
#: `okf_ext.body`: no linkify, so a bare URL in prose does not become an edge.
_MD = MarkdownIt("commonmark")

#: Backtick spans, longest-run-first so ``` ``[^x]`` ``` is masked whole. A
#: genuine inline span can cross a line break (```` `code\nmore` ```` is one
#: token, not two), which is why `re.DOTALL` is here -- but the regex has no
#: concept of a block boundary, so it is applied per contiguous *prose run*
#: (see `_prose_runs`), never over the raw body: a stray backtick inside one
#: fenced block would otherwise read as opening a span that only closes at
#: the *next* fenced block's delimiter, blanking every genuine prose line
#: (and any real link in it) in between.
_INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)*?\1", re.DOTALL)

#: The label exactly as written on a reference definition's own line --
#: `^\s{0,3}\[label]:` -- used to recover the source casing that markdown-it's
#: normalized `env["references"]` key has already thrown away.
_REF_LABEL_RE = re.compile(r"^ {0,3}\[([^\]]+)\]:")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One inline destination, and exactly where its text sits.

    `column` indexes the line as `okf_ext.body.split_lines()` returns it -- with its
    terminator -- so `line_text[column : column + len(text)] == text` always.
    `text` is the destination **as written**, never markdown-it's normalized
    form: the bytes in the file are what an edit replaces.

    `bracketed` records an `<...>` destination, whose brackets are excluded
    from `text` and whose replacement is emitted unencoded (an angle-bracket
    destination may hold spaces and parens as-is).
    """

    line: int  # 1-based, body-relative
    column: int  # 0-based, an index into that line
    text: str
    image: bool
    bracketed: bool


@dataclass(frozen=True, slots=True)
class RefDef:
    """A `[label]: dest` definition. Refused, never rewritten.

    markdown-it's block parser consumes these, so the reference-style links
    that use them produce a resolved edge with no locatable text. Rewriting
    the definition alone would repair the link while leaving the count
    reconciliation unable to prove it -- so a definition resolving into the
    moved set refuses the plan instead (spec §6).
    """

    label: str
    href: str
    line: int  # 1-based, body-relative


def _mask_inline_code(text: str) -> str:
    """Blank out inline code spans in *text*, preserving length and newlines.

    Every replacement character keeps a `\\r` or `\\n` exactly where it was
    and blanks everything else to a single space, so the masked text is the
    same length as the original and every subsequent line number and column
    stays exact.

    *text* must already be scoped to one contiguous prose run (see
    `_prose_runs`), never the raw body: the regex has no notion of a fence or
    an indented block, so handed the whole body it can match a stray backtick
    inside one code block as the opener of a span that only closes at the
    *next* code block's delimiter -- masking every genuine prose line in
    between. A per-run scope means the regex never sees across that boundary,
    while a genuine inline span crossing a line break *within* one run is
    still masked whole.
    """

    def _blank(match: re.Match[str]) -> str:
        return "".join(char if char in "\r\n" else " " for char in match.group())

    return _INLINE_CODE_RE.sub(_blank, text)


def _prose_runs(prose: frozenset[int], total: int) -> Iterator[tuple[int, ...]]:
    """*prose*'s 1-based line numbers, grouped into maximal contiguous runs.

    A blank line is itself prose -- `prose_lines` includes it, since it is not
    inside any code block -- so it joins the run on either side of it rather
    than splitting one; only an actual fenced, indented, or HTML block line
    breaks a run. Each run is masked independently in `destinations`, which is
    what stops an unclosed backtick in one run from leaking into the next.
    """
    run: list[int] = []
    for number in range(1, total + 1):
        if number in prose:
            run.append(number)
        elif run:
            yield tuple(run)
            run = []
    if run:
        yield tuple(run)


def _is_escaped(text: str, index: int) -> bool:
    """Whether the character at *index* is escaped by an odd run of backslashes.

    An even run is that many literal backslashes escaping each other in pairs,
    leaving *index* itself untouched -- two backslashes before a `[` is one
    literal backslash followed by a real, unescaped `[`, not an escaped one.
    """
    count = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        count += 1
        cursor -= 1
    return count % 2 == 1


def _match_opener(text: str, close_index: int) -> int | None:
    """The index of the `[` that opens the bracket pair closing at *close_index*.

    `None` when the `]` at *close_index* is not really opened by anything --
    prose text that merely contains the two-character sequence `](`
    ("...something](wrong)..."), or a `]` sitting inside a link title
    (`[a](./x.md "titled ](weird)")`) -- so `_scan` never manufactures a
    candidate for either.

    Walks back over nested brackets rather than searching for the nearest
    `[`: `[![alt](i.png)](t.md)`'s outer link would otherwise be matched by
    the inner `[`.
    """
    depth = 0
    index = close_index - 1
    while index >= 0:
        char = text[index]
        if char in "[]" and not _is_escaped(text, index):
            if char == "]":
                depth += 1
            else:
                if depth == 0:
                    return index
                depth -= 1
        index -= 1
    return None


def _opens_image(text: str, opener_index: int) -> bool:
    """Whether the `[` at *opener_index* is a real, unescaped `![`."""
    if opener_index == 0:
        return False
    return text[opener_index - 1] == "!" and not _is_escaped(text, opener_index - 1)


def _has_closing_paren(text: str, start: int) -> bool:
    """Whether an unescaped `)` appears anywhere in *text* at or after *start*.

    Called once a destination has broken on whitespace, to tell "a title
    follows, and the link really does close later on this line" from "this is
    prose that happens to contain `](` followed by bare words, and never
    closes" -- `[a](unterminated and more text` is the latter.
    """
    index = start
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == ")":
            return True
        index += 1
    return False


def _scan(masked: str, text: str) -> Iterator[tuple[int, str, bool, bool]]:
    """Yield `(column, destination, image, bracketed)` for one line of prose.

    *masked* is *text* with every inline code span blanked to spaces (see
    `_mask_inline_code`) -- scanned for structure -- while *text* is the
    original, unmasked line, which is what a yielded destination's text is
    sliced from.

    Every branch below strictly advances `cursor` past `open_at`, so the
    surrounding `while True` always terminates even on adversarial input: a
    `](` that turns out not to be a real link, or a destination that never
    closes, costs one skipped position rather than a stuck scan.
    """
    length = len(masked)
    cursor = 0
    while True:
        open_at = masked.find("](", cursor)
        if open_at < 0:
            return
        opener = _match_opener(masked, open_at)
        if opener is None:
            cursor = open_at + 1
            continue
        image = _opens_image(masked, opener)
        index = open_at + 2
        while index < length and masked[index] in " \t":
            index += 1
        if index < length and masked[index] == "<":
            close = masked.find(">", index + 1)
            if close < 0:
                cursor = open_at + 2
                continue
            if close > index + 1:
                yield (index + 1, text[index + 1 : close], image, True)
            cursor = close + 1
            continue
        start = index
        depth = 0
        terminator: str | None = None
        while index < length:
            char = masked[index]
            if char == "\\":
                index += 2
                continue
            if char in " \t":
                terminator = "space"
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    terminator = "paren"
                    break
                depth -= 1
            index += 1
        closes = terminator == "paren" or (terminator == "space" and _has_closing_paren(masked, index))
        if index > start and closes:
            yield (start, text[start:index], image, False)
        # `index` only ever advances from `start`, which itself only ever
        # advances from `open_at + 2` -- so `index` already exceeds `open_at`
        # here on every path, matched or not, and this is forward progress.
        cursor = index


def destinations(body: str) -> tuple[Candidate, ...]:
    """Every inline link and image destination in *body*'s prose, in order.

    Resolution is not this function's job: a fragment-only destination and an
    external URL both come back, and `plan` drops them once
    `okf_io.links.parse_destination` has spoken. Locating and resolving stay
    apart so neither has to know about the other's edge cases.
    """
    prose = prose_lines(body)
    body_lines = split_lines(body)
    masked_lines = list(body_lines)
    for run in _prose_runs(prose, len(body_lines)):
        run_text = "".join(body_lines[number - 1] for number in run)
        masked_run_lines = split_lines(_mask_inline_code(run_text))
        for offset, number in enumerate(run):
            masked_lines[number - 1] = masked_run_lines[offset]
    found: list[Candidate] = []
    for number, (masked, text) in enumerate(zip(masked_lines, body_lines, strict=True), start=1):
        if number not in prose:
            continue
        for column, destination, image, bracketed in _scan(masked, text):
            found.append(Candidate(line=number, column=column, text=destination, image=image, bracketed=bracketed))
    return tuple(found)


def reference_definitions(body: str) -> tuple[RefDef, ...]:
    """Every `[label]: dest` definition *body* declares, with its line.

    Read from markdown-it's `env` after a parse -- the definitions are gone
    from the token stream by then, and `env["references"]` is the only place
    they survive. Its key is markdown-it's own normalization (case-folded,
    internal whitespace collapsed), which is not the source casing, so the
    label is re-read from the definition's own line via `_REF_LABEL_RE`
    instead; the case-folded key is used only as a fallback should that ever
    fail to match a well-formed definition.
    """
    env: dict[str, object] = {}
    _MD.parse(body, env)
    references = env.get("references")
    if not isinstance(references, dict):
        return ()
    lines = split_lines(body)
    found: list[RefDef] = []
    for label, info in references.items():
        if not isinstance(info, dict):
            continue
        href = info.get("href")
        span = info.get("map")
        line = span[0] + 1 if isinstance(span, list) and span else 1
        source_label = str(label).lower()
        if 1 <= line <= len(lines):
            match = _REF_LABEL_RE.match(lines[line - 1])
            if match:
                source_label = match.group(1)
        if isinstance(href, str):
            found.append(RefDef(label=source_label, href=href, line=line))
    found.sort(key=lambda definition: (definition.line, definition.label))
    return tuple(found)


#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = [
    "Candidate",
    "RefDef",
    "destinations",
    "reference_definitions",
]
