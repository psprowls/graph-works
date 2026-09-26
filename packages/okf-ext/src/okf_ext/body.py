"""Block structure over a concept body: sections, and the prose/code mask.

**Shared layer, not a capability.** `find_section` is not table-specific --
the filed `generators` capability needs the same heading walk for its
two-class section model, and the `diataxis.*` gates will need it too -- and
the independence contract forbids a capability importing a sibling, so a
`find_section` living in `tables` would force `generators` to either
duplicate the walk or break the contract. That widens what the shared layer
means, from "cross-cutting configuration" to "configuration and
block-structure primitives"; the README says so.

**The skeleton comes from a parser, never a bare regex.** Both reference
implementations this replaces find tables by scanning for lines beginning
with `|`, so a pipe table inside a fenced code block parses as a real table
-- exactly the false positive `okf_io._md` refuses to accept for links, where
"regex link extraction is a named lesson from the ecosystem survey". Headings
and code-block ranges (fences, indented blocks, and raw HTML blocks alike)
come from markdown-it here, and the tolerant cell scan in `okf_ext.tables`
runs only over the lines `prose_lines` reports. This is the shape of
`okf_io._md._footnotes`: the one place a parser is not doing the work,
bounded by the parser.

`markdown_it` is imported directly rather than through `okf_io._md`, which is
private -- reaching into it would couple this package to a private module of
the one it sits above, the precise thing the README's `ruamel.yaml` paragraph
exists to prevent.

**Not markdown-it's own table tokens.** Strictly correct, and it would remove
the scan entirely -- but markdown-it emits a table token only for a
well-formed GFM table (and the `commonmark` preset used here emits none at
all). The `malformed` state, which is the distinction all three consumer
surveys singled out, cannot be derived from a token stream that refuses to
produce a token for the malformed case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from markdown_it import MarkdownIt
from okf_io import Bundle

#: One shared parser. `commonmark` deliberately, matching `okf_io._md`: no
#: linkify, no GFM tables, so nothing here depends on a plugin set.
_MD = MarkdownIt("commonmark")

#: markdown-it normalizes `\r\n` and `\r` to `\n` before it assigns line
#: numbers, so every split here must break in exactly the same places.
#: `str.splitlines` also breaks on VT, FF, NEL and U+2028, which would slide
#: every subsequent line number by one on a body containing any of them.
_NEWLINE_RE = re.compile(r"\r\n?|\n")


def split_lines(body: str) -> tuple[str, ...]:
    """*body* split into lines that keep their own terminators.

    ``"".join(split_lines(body)) == body`` always, which is what lets a
    splice rebuild a body from an edited line list without normalizing a
    single byte -- the regression `edge/encoding/crlf.md` exists to catch,
    one layer up. A second line-splitter inside a capability would silently
    disagree with the one that produced every span in this module, so this
    is public rather than private.

    A trailing terminator ends a line; it does not start a new one, so
    ``split_lines("a\\n")`` is one line, not two, and line *n* here is line
    *n* in every markdown-it token map.
    """
    parts: list[str] = []
    position = 0
    for match in _NEWLINE_RE.finditer(body):
        parts.append(body[position : match.end()])
        position = match.end()
    if position < len(body):
        parts.append(body[position:])
    return tuple(parts)


@dataclass(frozen=True, slots=True)
class Section:
    """One heading and the lines it owns. **Spans, not text.**

    A *copy* of the section body would be enough to read and useless to write
    back into: everything downstream of a splice needs to know where.

    `stop` is inclusive and is **not** trimmed back past trailing blank lines
    -- the splice needs to know which blank line belongs to whom. A section
    with nothing under its heading has `stop == start`, so `body_start > stop`
    and `slice()` is empty.
    """

    heading: str  # the heading's inline source, as written, stripped
    level: int
    start: int  # 1-based, body-relative: the heading's own first line
    body_start: int  # the first line after the heading
    stop: int  # inclusive: the last line the section owns

    def slice(self, body: str) -> str:
        """The section's own text, heading excluded, byte for byte."""
        return "".join(split_lines(body)[self.body_start - 1 : self.stop])


@dataclass(frozen=True, slots=True)
class _Skeleton:
    """Everything this module needs from one body, parsed once."""

    total: int
    sections: tuple[Section, ...]
    code: frozenset[int]


#: Block token types `prose_lines` treats as code, never prose. `fence` and
#: `code_block` are backtick/tilde fences and four-space indented blocks;
#: `html_block` is raw HTML (a `<pre>`/`<table>` blob, say) -- without it, a
#: pipe-shaped line inside one would leak into the mask, exactly the false
#: positive this module exists to keep out of the tolerant table scan.
_CODE_TOKENS = frozenset({"fence", "code_block", "html_block"})


@lru_cache(maxsize=512)  # generous headroom for one process's largest bundle walk; not tuned
def _skeleton(body: str) -> _Skeleton:
    """Parse *body* once. Memoized, so no consumer parses the same body twice.

    `sections()` and `prose_lines()` are both called for a single
    `read_section`, and `okf_io._md.parse_body` makes the same guarantee
    structural for the same reason. The heading walk and the code-range walk
    both run over the one `tokens` list below rather than each calling
    `_MD.parse(body)` for itself, for that same "parsed once" guarantee.
    """
    body_lines = split_lines(body)
    tokens = _MD.parse(body)
    heads: list[tuple[int, int, str, int]] = []  # (start, level, text, body_start)
    quote_depth = 0
    in_heading = False
    quoted = False
    start = body_start = level = 0
    code: set[int] = set()

    for token in tokens:
        if token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth = max(0, quote_depth - 1)
        elif token.type == "heading_open":
            span = token.map
            assert span is not None  # markdown-it always maps a block token
            in_heading = True
            quoted = quote_depth > 0
            # `span` is `[start, end)` 0-based. A setext heading spans two
            # lines, so the body starts after `end`, not after `start + 1`.
            start = span[0] + 1
            body_start = span[1] + 1
            level = int(token.tag[1:])
        elif token.type == "heading_close":
            in_heading = False
        elif token.type == "inline" and in_heading and not quoted:
            heads.append((start, level, token.content.strip(), body_start))
        if token.type in _CODE_TOKENS:
            span = token.map
            assert span is not None
            code.update(range(span[0] + 1, span[1] + 1))

    total = len(body_lines)
    found: list[Section] = []
    for index, (head_start, head_level, text, head_body_start) in enumerate(heads):
        stop = total
        for later_start, later_level, _text, _body in heads[index + 1 :]:
            if later_level <= head_level:
                stop = later_start - 1
                break
        found.append(Section(heading=text, level=head_level, start=head_start, body_start=head_body_start, stop=stop))

    return _Skeleton(total=total, sections=tuple(found), code=frozenset(code))


def sections(body: str) -> tuple[Section, ...]:
    """Every heading in *body*, in document order, with the span it owns.

    A section runs to the next heading of the same or shallower level, or to
    the end of the body.

    **A heading inside a blockquote is never a section and never ends one** --
    it is somebody else's content, following `okf_io._md`'s `quoted`
    precedent. A real section containing one therefore keeps running past it.
    """
    return _skeleton(body).sections


def find_section(body: str, heading: str, *, level: int | None = None) -> Section | None:
    """The first section whose heading text matches *heading*.

    Case-insensitive and whitespace-stripped, and **level-agnostic by
    default**: the reference implementation matched `^##\\s+plan\\s*$`, but one
    corpus file writes `### Citations`, which is why okf-io's own citations
    locator never consults level either. `level=` narrows when a caller does
    care.
    """
    wanted = heading.strip().casefold()
    for section in sections(body):
        if section.heading.casefold() == wanted and (level is None or section.level == level):
            return section
    return None


def top_level_items(section_text: str) -> tuple[str, ...]:
    """The text of each top-level list item in *section_text*, in order.

    Ordered and bullet lists both count, and separate top-level lists keep one
    running sequence, which is what a Source's `drain:` ordinals number.
    Nested items belong to their parent. Items inside a code block are not
    items, and neither are items in a blockquote: markdown-it nests those one
    level deeper, so `level == 1` excludes them.
    """
    lines = split_lines(section_text)
    found: list[str] = []
    for token in _MD.parse(section_text):
        if token.type != "list_item_open" or token.level != 1:
            continue
        span = token.map
        assert span is not None  # markdown-it always maps a block token
        found.append("".join(lines[span[0] : span[1]]).rstrip())
    return tuple(found)


def normalized_text(text: str) -> str:
    """*text*'s comparable form: what "this section is empty" or "this section
    still equals its placeholder" is decided against.

    Line endings normalised, each line stripped, leading and trailing blank
    lines dropped. Deliberately **not** a marker scheme: nothing leaks into the
    rendered document, and nothing collides with `render.angle-bracket`.

    Hoisted from `sections/rule.py` so the `sections` rule and
    `okf_ext.shape.audience_view` share one definition -- a second copy is how
    a view and a lint drift on what "unfilled" means.
    """
    flat = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in flat.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def prose_lines(body: str) -> frozenset[int]:
    """Every 1-based body line the parser does **not** call code.

    This is the mask the tolerant table scan runs inside. Backtick and tilde
    fences, fences nested inside list items, four-space indented blocks, and
    raw HTML blocks are all excluded, because a pipe table inside any of them
    is somebody's example, not a table.
    """
    skeleton = _skeleton(body)
    return frozenset(number for number in range(1, skeleton.total + 1) if number not in skeleton.code)


#: An opening `[[` or `![[`, used to find candidate spans to validate. Moved
#: here from `okf_ext.render.rule` (not copied) -- `render`'s lint rule and
#: `work_tracker_okf.archive`'s stranded-reference count both need the same
#: wikilink scan, and the independence contract forbids one capability
#: importing another, so the scan has to live in this shared layer.
_WIKILINK_OPEN_RE = re.compile(r"!?\[\[")
#: `[[target]]`, `[[target#anchor]]`, `[[target|alias]]`. Inside a table cell the
#: alias separator is escaped as `\|`, so the lookahead stops the target there
#: and lets the alias group consume `\|alias`. Note the target group requires at
#: least one character, so `[[]]` does not match and reads as unbalanced.
#:
#: **No group crosses a newline.** A wikilink is a single-line construct -- an
#: unclosed `[[` at the end of one line and a stray `]]` on the next render as
#: neither -- so every character class excludes `\n`. Without that exclusion the
#: two halves join into one apparently-valid link and the finding is lost.
_WIKILINK_RE = re.compile(r"\[\[((?:(?!\\\|)[^\]|#\n])+)(?:#[^\]|\n]*)?(?:\\?\|[^\]\n]*)?\]\]")

#: A run of one or more backticks -- CommonMark closes a code span with the
#: next run of *exactly* the same width.
_BACKTICK_RUN_RE = re.compile(r"`+")
#: Filler for a masked-out inline-code span. Same length as what it replaces,
#: so every match offset still points at the same place in the unmasked line.
_CODE_MASK = "\x00"


@dataclass(frozen=True, slots=True)
class Wikilink:
    """One `[[...]]` occurrence in a body, well-formed or not."""

    raw: str  # the occurrence exactly as written, e.g. "[[work/x|Alias]]"
    target: str | None  # the target with alias and anchor stripped; None when malformed
    line: int  # 1-based, body-relative
    column: int  # 0-based index into that line
    embed: bool  # True for the `![[...]]` form


def _mask_inline_code(line: str) -> str:
    """Blank out inline-code spans in *line*, preserving every other offset.

    CommonMark's rule: a run of N backticks is closed by the next run of
    *exactly* N -- a naive `` `[^`]*` `` mis-pairs a double-backtick span, and
    these are exactly the spans a wikilink hides inside (`` `[[foo` ``).
    """
    runs = [(match.start(), match.end()) for match in _BACKTICK_RUN_RE.finditer(line)]
    if not runs:
        return line
    out = list(line)
    index = 0
    while index < len(runs):
        start, end = runs[index]
        width = end - start
        for later in range(index + 1, len(runs)):
            if runs[later][1] - runs[later][0] == width:
                for position in range(start, runs[later][1]):
                    out[position] = _CODE_MASK
                index = later + 1
                break
        else:
            index += 1
    return "".join(out)


def _line_text(raw_line: str) -> str:
    """*raw_line* with its own terminator (if any) removed."""
    if raw_line.endswith("\r\n"):
        return raw_line[:-2]
    if raw_line.endswith(("\n", "\r")):
        return raw_line[:-1]
    return raw_line


def wikilinks(body: str) -> tuple[Wikilink, ...]:
    """Every `[[...]]` occurrence in *body*'s prose, well-formed or not.

    **Two classes of code are excluded**, because a wikilink can hide inside
    either: fenced blocks, indented blocks and raw HTML blocks (`prose_lines`'s
    mask), and inline-code spans within an otherwise-prose line (backtick
    masking, in place, the same hazard `scripts/convert_wikilinks.py` names and
    solves the same way).

    The only scanner for this syntax in the package: `okf_ext.render`'s lint
    rule and `work_tracker_okf.archive`'s stranded-reference count both consume
    this rather than each parsing wikilinks for themselves.
    """
    prose = prose_lines(body)
    found: list[Wikilink] = []
    for number, raw_line in enumerate(split_lines(body), start=1):
        if number not in prose:
            continue
        text = _line_text(raw_line)
        masked = _mask_inline_code(text)
        for opener in _WIKILINK_OPEN_RE.finditer(masked):
            start = opener.start()
            embed = masked[start] == "!"
            bracket_start = opener.end() - 2  # the "[[" itself, past any leading "!"
            rest = masked[bracket_start:]
            valid = _WIKILINK_RE.match(rest)
            if valid is None:
                found.append(Wikilink(raw=text[start:], target=None, line=number, column=start, embed=embed))
                continue
            target_start, target_end = valid.span(1)
            target = text[bracket_start + target_start : bracket_start + target_end].strip() or None
            found.append(
                Wikilink(
                    raw=text[start : bracket_start + valid.end()],
                    target=target,
                    line=number,
                    column=start,
                    embed=embed,
                )
            )
    return tuple(found)


def resolve_wikilink(target: str, *, bundle: Bundle) -> str | None:
    """The bundle member `target` names, or None. Tries `<target>.md`, then `<target>`.

    **Obsidian's shortest-path (bare-name) resolution is deliberately not
    implemented.** Measured against a live vault, it rescues none of its
    dangling wikilinks -- the ones that are genuinely broken name no file
    anywhere under any basename, so bare-name matching would buy nothing and
    introduce an ambiguity rule with no test to anchor it.
    """
    candidate = f"{target}.md"
    if bundle.has_member(candidate):
        return candidate
    if bundle.has_member(target):
        return target
    return None


#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = [
    "Section",
    "Wikilink",
    "find_section",
    "normalized_text",
    "prose_lines",
    "resolve_wikilink",
    "sections",
    "split_lines",
    "top_level_items",
    "wikilinks",
]
