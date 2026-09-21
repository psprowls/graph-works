"""Markdown structure derived from markdown-it's token stream.

Internal module and the bottom of the package's dependency stack: it imports
markdown-it and stdlib only, which is what lets ``models`` use it for the
``# Citations`` fallback without a cycle.

Three behaviours come out of the token stream for free, and each is a bug in
the regex scanners this replaces. Links inside fenced and indented code blocks
produce no inline children, so they are never offered as links. Reference links
arrive with their destination already resolved. Block tokens carry ``map``, a
``[start, end)`` line range, which is what lets a finding point at a line.

Two limitations, documented rather than fixed. Raw HTML anchors yield no link
token and are not edges. Footnotes are not in markdown-it's core -- they live in
``mdit_py_plugins``, which would be a third runtime dependency -- so labels are
found by a line scan restricted to the ranges the parser says are *not* code.
Two more, found while reviewing the list-item walk. A heading inside a
blockquote (``> # Quoted``) still updates the running ``heading`` used to
attribute subsequent top-level code blocks and list items: ``*_under()`` means
"nearest preceding heading in document order", with no structural scoping.
And ``_INLINE_CODE_RE`` strips single-backtick spans only, so a
``[^label]``-shaped fragment inside a double-backtick span (`` ``[^x]`` ``)
could still read as a footnote reference.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from markdown_it import MarkdownIt
from markdown_it.token import Token

#: One shared parser. `commonmark` deliberately: no linkify, so a bare URL in
#: prose does not become an edge.
_MD = MarkdownIt("commonmark")

#: markdown-it normalizes `\r\n` and `\r` to `\n` before it assigns line
#: numbers, so the footnote scan must split the body exactly the same way.
#: `str.splitlines` also breaks on VT, FF, NEL and U+2028, which would slide
#: every subsequent line number by one on a body containing any of them.
_NEWLINE_RE = re.compile(r"\r\n?|\n")

_FOOTNOTE_DEF_RE = re.compile(r"^ {0,3}\[\^([^\]\s]+)\]:")
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\]")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")

#: A bracketed-number citation prefix -- `[1] `, `[12]` -- per the
#: `crypto_bitcoin` dialect, with CommonMark's 0-3 space indent allowance.
#: Deliberately not matching `[^1]`: a footnote definition is not a citation.
_NUMBERED_RE = re.compile(r"^ {0,3}\[(\d+)\][ \t]*")


@dataclass(frozen=True, slots=True)
class MdLink:
    """A link or image destination, exactly as markdown-it reports it.

    ``raw`` is **not** decoded here: markdown-it percent-encodes destinations on
    the way out (``./café.md`` becomes ``./caf%C3%A9.md``), and undoing that is
    the resolver's first step, in ``links``. Keeping the two apart means this
    layer never has to know what a bundle is.
    """

    raw: str
    line: int  # 1-based, body-relative: the first line of the containing block
    image: bool


@dataclass(frozen=True, slots=True)
class CodeBlock:
    start: int  # 1-based, body-relative, inclusive
    end: int  # 1-based, body-relative, exclusive
    fenced: bool  # False for a four-space indented block
    heading: str | None  # casefolded text of the nearest preceding heading


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str
    line: int  # 1-based, body-relative
    quoted: bool  # True when the heading itself sits inside a blockquote


@dataclass(frozen=True, slots=True)
class ListItem:
    text: str  # the item's inline source
    link_label: str | None  # label of item's first link; normalized to None (never empty)
    link_target: str | None  # destination of the item's first link, if any
    line: int  # 1-based, body-relative: the item's first line
    end: int  # 1-based, body-relative, inclusive: the item's last non-blank line
    heading: str | None  # casefolded text of the nearest preceding heading


@dataclass(frozen=True, slots=True)
class Paragraph:
    """A top-level paragraph's line range.

    Recorded so the citations locator can see the bracketed-number dialect,
    whose entries are lines of one paragraph rather than blocks of their own.
    Only the range is kept: the locator reads each entry's text from the body's
    own lines, which keeps a line and its text the same object rather than two
    that can drift apart on a lazily continued paragraph.
    """

    line: int  # 1-based, body-relative: the paragraph's first line
    end: int  # 1-based, body-relative, inclusive: its last non-blank line
    heading: str | None  # casefolded text of the nearest preceding heading


@dataclass(slots=True)
class _OpenItem:
    """One entry on the walk's open-list-item stack.

    ``list_item_open``/``list_item_close`` nest -- a sub-list inside an item is
    itself made of items -- so a single flat "current item" variable loses the
    outer item the moment a nested one opens. A stack keeps each item's own
    line/heading and its own "have I emitted my ListItem yet" flag, so the
    outer item's *own* trailing paragraph (after its nested sub-list closes)
    still lands once control returns to it.
    """

    line: int
    end: int
    heading: str | None
    emitted: bool = False


@dataclass(frozen=True, slots=True)
class BodyIndex:
    """Everything the graph and the rules need from one body, parsed once."""

    links: tuple[MdLink, ...]
    code_blocks: tuple[CodeBlock, ...]
    headings: tuple[Heading, ...]
    list_items: tuple[ListItem, ...]
    paragraphs: tuple[Paragraph, ...]
    footnote_refs: frozenset[str]
    footnote_defs: frozenset[str]

    @property
    def footnote_labels(self) -> frozenset[str]:
        """References **and** definitions.

        Spec §5.1's join is about which sources the body draws on, and a
        definition carrying no reference still declares one. Counting only
        references would fire ``provenance.source-uncited`` on
        ``acme_retail/metrics/gross-margin.md``, which defines
        ``[^revenue-policy]`` without referencing it while legitimately
        carrying that source.
        """
        return self.footnote_refs | self.footnote_defs


@dataclass(frozen=True, slots=True)
class Citation:
    """One entry of a v0.1 ``# Citations`` section, in either dialect."""

    text: str  # the entry's source text; a `[n]` prefix is already stripped
    link_label: str | None  # label of the entry's first link, if any
    link_target: str | None  # destination of the entry's first link, if any
    line: int  # 1-based, body-relative
    end: int  # 1-based, body-relative, inclusive


@dataclass(frozen=True, slots=True)
class CitationsSection:
    """A located ``# Citations`` section: what it holds and where it sits."""

    start: int  # 1-based, body-relative: the heading's own line
    stop: int  # 1-based, inclusive: the last line the section owns
    entries: tuple[Citation, ...]
    pure: bool  # every non-blank line the section owns belongs to an entry


def _code_lines(blocks: tuple[CodeBlock, ...]) -> frozenset[int]:
    return frozenset(line for block in blocks for line in range(block.start, block.end))


def _footnotes(body: str, blocks: tuple[CodeBlock, ...]) -> tuple[frozenset[str], frozenset[str]]:
    """Scan for footnote labels, only where the parser says there is prose.

    This is the one place a parser is not doing the work, and it is bounded by
    the parser: the fenced-block false positive that makes regex link scanning
    unacceptable cannot occur here. Inline code spans are stripped as well,
    which kills the remaining common false positive at no structural cost.
    """
    code = _code_lines(blocks)
    refs: set[str] = set()
    defs: set[str] = set()
    for number, source in enumerate(_NEWLINE_RE.split(body), start=1):
        if number in code:
            continue
        line = _INLINE_CODE_RE.sub("", source)
        rest = line
        definition = _FOOTNOTE_DEF_RE.match(line)
        if definition is not None:
            defs.add(definition.group(1))
            rest = line[definition.end() :]
        refs.update(match.group(1) for match in _FOOTNOTE_REF_RE.finditer(rest))
    return frozenset(refs), frozenset(defs)


def _line_of(token_map: list[int] | None) -> int:
    return token_map[0] + 1 if token_map else 1


def _item_end(lines: list[str], start: int, token_map: list[int] | None) -> int:
    """A block's last non-blank line, 1-based and inclusive.

    markdown-it's ``[start, end)`` map for a list item runs to the start of
    whatever follows, so the final item of a list owns the blank line that
    separates it from the next block. An entry removed by that range would
    take the separator with it and glue two blocks together, so the range is
    trimmed back to real content.
    """
    end = token_map[1] if token_map else start
    while end > start and not lines[end - 1].strip():
        end -= 1
    return end


def _scan_link(children: Sequence[Token]) -> tuple[str | None, str | None]:
    """The label and destination of the first link among *children*.

    Depth of the *first* link only: a second, sibling link in the same
    paragraph must not touch label/target (``target is None`` already guards
    that), and if a link somehow nests inside the first -- invalid CommonMark,
    but defensive -- depth keeps the close that matters lined up with the open
    that matters.

    An ``image`` child contributes nothing to the label. Its ``content`` is the
    alt text, and folding that into ``[![alt](i.png)](t.md)``'s label would
    make the label read "alt" rather than empty.
    """
    target: str | None = None
    label_parts: list[str] = []
    depth = 0
    closed = False
    for child in children:
        if child.type == "link_open":
            if target is None and not closed:
                if depth == 0:
                    href = child.attrGet("href")
                    target = href if isinstance(href, str) else ""
                depth += 1
        elif child.type == "link_close":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    closed = True
        elif child.type == "image":
            continue
        elif depth > 0 and child.content:
            # Flatten whatever the label renders as: `text` and `code_inline`
            # both carry `content`; `strong_open` and friends carry none and
            # contribute nothing, which is exactly right -- `[**bold**](t.md)`
            # should read "bold".
            label_parts.append(child.content)
    return ("".join(label_parts) or None, target)


def _first_link(text: str) -> tuple[str | None, str | None]:
    """The label and destination of *text*'s first markdown link.

    Runs the fragment through the shared parser rather than a regex over it:
    regex link extraction is a named lesson from the ecosystem survey, and
    ``[label](target)`` is exactly the shape ``parse_body`` already reads off
    the token stream. Used for the bracketed-number dialect, whose entries are
    lines *inside* one paragraph token rather than blocks of their own.
    """
    for token in _MD.parseInline(text):
        label, target = _scan_link(token.children or ())
        if target is not None:
            return label, target
    return None, None


@lru_cache(maxsize=512)  # generous headroom for one process's largest bundle walk; not tuned
def parse_body(body: str) -> BodyIndex:
    """Index *body*. Memoized, so no consumer can parse the same body twice.

    Spec §9.3 requires one parse per body. Two call sites want the same one --
    ``links.build`` for the graph and ``models._scan_citations`` for the
    citations fallback -- so the guarantee is made structural here rather than
    left as a convention both modules have to remember. ``BodyIndex`` is
    frozen, so sharing one is safe.
    """
    links: list[MdLink] = []
    code_blocks: list[CodeBlock] = []
    headings: list[Heading] = []
    items: list[ListItem] = []
    paragraphs: list[Paragraph] = []

    body_lines = _NEWLINE_RE.split(body)
    heading: str | None = None
    heading_line = 0
    heading_level = 0
    heading_quoted = False
    in_heading = False
    quote_depth = 0
    item_stack: list[_OpenItem] = []

    for token in _MD.parse(body):
        if token.type == "heading_open":
            in_heading = True
            heading_line = _line_of(token.map)
            heading_level = int(token.tag[1:])
            heading_quoted = quote_depth > 0
        elif token.type == "heading_close":
            in_heading = False
        elif token.type in {"fence", "code_block"}:
            start = _line_of(token.map)
            end = token.map[1] + 1 if token.map else start + 1
            code_blocks.append(CodeBlock(start, end, token.type == "fence", heading))
        elif token.type == "list_item_open":
            start = _line_of(token.map)
            item_stack.append(_OpenItem(start, _item_end(body_lines, start, token.map), heading))
        elif token.type == "list_item_close":
            if item_stack:
                item_stack.pop()
        elif token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth = max(0, quote_depth - 1)
        elif token.type == "inline":
            line = _line_of(token.map)
            children = token.children or ()
            for child in children:
                if child.type == "link_open":
                    href = child.attrGet("href")
                    links.append(MdLink(href if isinstance(href, str) else "", line, False))
                elif child.type == "image":
                    src = child.attrGet("src")
                    links.append(MdLink(src if isinstance(src, str) else "", line, True))
            label, target = _scan_link(children)
            if in_heading:
                text = token.content.strip()
                headings.append(Heading(heading_level, text, heading_line, heading_quoted))
                heading = text.casefold()
            elif item_stack and not item_stack[-1].emitted:
                top = item_stack[-1]
                items.append(ListItem(token.content, label, target, top.line, top.end, top.heading))
                top.emitted = True  # only the item's own first paragraph
            elif not item_stack and quote_depth == 0:
                # A top-level paragraph. Inside a list item or a blockquote it
                # is somebody else's content: the citations locator must not
                # read a quoted `[1] ...` line as an entry, and a section
                # holding one is not pure precisely because nothing covers it.
                paragraphs.append(Paragraph(line, _item_end(body_lines, line, token.map), heading))

    blocks = tuple(code_blocks)
    refs, defs = _footnotes(body, blocks)
    return BodyIndex(
        links=tuple(links),
        code_blocks=blocks,
        headings=tuple(headings),
        list_items=tuple(items),
        paragraphs=tuple(paragraphs),
        footnote_refs=refs,
        footnote_defs=defs,
    )


def code_blocks_under(index: BodyIndex, heading: str) -> tuple[CodeBlock, ...]:
    """Code blocks whose nearest preceding heading is *heading* (case-folded)."""
    wanted = heading.casefold()
    return tuple(block for block in index.code_blocks if block.heading == wanted)


def list_items_under(index: BodyIndex, heading: str) -> tuple[ListItem, ...]:
    """List items whose nearest preceding heading is *heading* (case-folded)."""
    wanted = heading.casefold()
    return tuple(item for item in index.list_items if item.heading == wanted)


def paragraphs_under(index: BodyIndex, heading: str) -> tuple[Paragraph, ...]:
    """Top-level paragraphs whose nearest preceding heading is *heading*."""
    wanted = heading.casefold()
    return tuple(item for item in index.paragraphs if item.heading == wanted)


def citations_section(body: str) -> CitationsSection | None:
    """Locate a v0.1 ``# Citations`` section: what it holds, and where it sits.

    One locator, two callers. ``models._scan_citations`` reads the entries for
    the ADR 2026-08-02-v01-compat-read read fallback and ``migrate`` deletes the range they sit in,
    so a shared function is what stops reader and writer from ever disagreeing
    about what a citations section is.

    Both dialects the corpus actually uses are recognised: markdown list items
    (64 files at the reference commit) and bracketed-number paragraph lines
    (``[1] [Title](url)``, `crypto_bitcoin`), which are not list items and so
    were invisible to the list-item-only scan this replaces. The heading's
    *level* is never consulted -- one corpus file writes ``### Citations``.

    ``pure`` is what the rewriter gates its all-or-nothing rewrite on: every
    non-blank line the section owns belongs to an entry. A stray paragraph, a
    fenced block or a blockquote leaves a line uncovered, and the section is
    not pure.

    Takes the body rather than a ``BodyIndex`` because ``parse_body`` is
    memoized: passing the text cannot desync the entries from the lines they
    were located in, and costs nothing.
    """
    index = parse_body(body)
    # A blockquoted heading is somebody else's content, exactly like a
    # blockquoted `[1] ...` line -- so it is never the anchor, and (below) it
    # is never what ends the section either. A real section that happens to
    # contain a quoted heading therefore keeps running past it: the quoted
    # lines stay inside the range, stay uncovered by any entry, and the
    # section comes back `pure=False` -- refused rather than partially
    # rewritten, not truncated as if the quoted heading were a real boundary.
    heading = next(
        (item for item in index.headings if not item.quoted and item.text.casefold() == "citations"),
        None,
    )
    if heading is None:
        return None

    lines = _NEWLINE_RE.split(body)
    if lines and lines[-1] == "":
        lines.pop()  # a trailing terminator ends a line, it does not start one
    following = [item.line for item in index.headings if not item.quoted and item.line > heading.line]
    stop = following[0] - 1 if following else len(lines)

    entries: list[Citation] = []
    for item in list_items_under(index, "citations"):
        # A second `# Citations` heading ends this section, but attribution by
        # nearest-preceding-heading would still hand its items to us.
        if heading.line < item.line <= stop:
            entries.append(Citation(item.text, item.link_label, item.link_target, item.line, item.end))
    for paragraph in paragraphs_under(index, "citations"):
        for number in range(paragraph.line, paragraph.end + 1):
            if not heading.line < number <= stop:
                continue
            match = _NUMBERED_RE.match(lines[number - 1])
            if match is None:
                continue  # not an entry: the line stays uncovered, so impure
            text = lines[number - 1][match.end() :].strip()
            label, target = _first_link(text)
            entries.append(Citation(text, label, target, number, number))
    entries.sort(key=lambda entry: (entry.line, entry.end))

    covered = {n for entry in entries for n in range(entry.line, entry.end + 1)}
    pure = all(number in covered for number in range(heading.line + 1, stop + 1) if lines[number - 1].strip())
    return CitationsSection(start=heading.line, stop=stop, entries=tuple(entries), pure=pure)
