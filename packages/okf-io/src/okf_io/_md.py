"""Markdown structure derived from markdown-it's token stream.

Internal module and the bottom of the package's dependency stack: it imports
markdown-it and stdlib only, which is what lets ``models`` use it for the
ADR-0003 ``# Citations`` fallback without a cycle.

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
from dataclasses import dataclass
from functools import lru_cache

from markdown_it import MarkdownIt

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


@dataclass(frozen=True, slots=True)
class ListItem:
    text: str  # the item's inline source
    link_label: str | None  # label of item's first link; normalized to None (never empty)
    link_target: str | None  # destination of the item's first link, if any
    line: int  # 1-based, body-relative: the item's first line
    end: int  # 1-based, body-relative, inclusive: the item's last non-blank line
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
    """A list item's last non-blank line, 1-based and inclusive.

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


@lru_cache(maxsize=512)  # generous headroom for one process's largest bundle walk; not tuned
def parse_body(body: str) -> BodyIndex:
    """Index *body*. Memoized, so no consumer can parse the same body twice.

    Spec §9.3 requires one parse per body. Two call sites want the same one --
    ``links.build`` for the graph and ``models._scan_citations`` for the
    ADR-0003 fallback -- so the guarantee is made structural here rather than
    left as a convention both modules have to remember. ``BodyIndex`` is
    frozen, so sharing one is safe.
    """
    links: list[MdLink] = []
    code_blocks: list[CodeBlock] = []
    headings: list[Heading] = []
    items: list[ListItem] = []

    body_lines = _NEWLINE_RE.split(body)
    heading: str | None = None
    heading_line = 0
    heading_level = 0
    in_heading = False
    item_stack: list[_OpenItem] = []

    for token in _MD.parse(body):
        if token.type == "heading_open":
            in_heading = True
            heading_line = _line_of(token.map)
            heading_level = int(token.tag[1:])
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
        elif token.type == "inline":
            line = _line_of(token.map)
            target: str | None = None
            label_parts: list[str] = []
            # Depth of the item's *first* link only: a second, sibling link in
            # the same paragraph must not touch label/target (`target is None`
            # below already guards that), and if a link somehow nests inside
            # the first -- invalid CommonMark, but defensive -- depth keeps
            # the close that matters lined up with the open that matters.
            depth = 0
            closed = False
            for child in token.children or ():
                if child.type == "link_open":
                    href = child.attrGet("href")
                    destination = href if isinstance(href, str) else ""
                    links.append(MdLink(destination, line, False))
                    if target is None and not closed:
                        if depth == 0:
                            target = destination
                        depth += 1
                elif child.type == "link_close":
                    if depth > 0:
                        depth -= 1
                        if depth == 0:
                            closed = True
                elif child.type == "image":
                    src = child.attrGet("src")
                    links.append(MdLink(src if isinstance(src, str) else "", line, True))
                elif depth > 0 and child.content:
                    # Flatten whatever the label renders as: `text` and
                    # `code_inline` both carry `content`; `strong_open` and
                    # friends carry none and contribute nothing, which is
                    # exactly right -- `[**bold**](t.md)` should read "bold".
                    label_parts.append(child.content)
            label = "".join(label_parts) or None
            if in_heading:
                text = token.content.strip()
                headings.append(Heading(heading_level, text, heading_line))
                heading = text.casefold()
            elif item_stack and not item_stack[-1].emitted:
                top = item_stack[-1]
                items.append(ListItem(token.content, label, target, top.line, top.end, top.heading))
                top.emitted = True  # only the item's own first paragraph

    blocks = tuple(code_blocks)
    refs, defs = _footnotes(body, blocks)
    return BodyIndex(
        links=tuple(links),
        code_blocks=blocks,
        headings=tuple(headings),
        list_items=tuple(items),
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
