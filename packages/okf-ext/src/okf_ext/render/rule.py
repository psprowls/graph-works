"""The rule that turns one markdown-it parse per document into `Finding`s.

    validate(bundle, today=..., extra_rules=[render_rule()])

The only `Finding` source in this capability, and the only thing here that
touches a bundle. Nothing raises for content: a malformed callout is a
`Finding`, never an exception.

**One parse per document, not four.** The four checks share a token stream
because parsing is the expensive part, and one rule walking the bundle once is
what makes that structural -- with four separate `Rule` objects, sharing a
parse would need a closure-level cache whose correctness nothing enforces.

**This is a second parse of each concept body, knowingly.** okf-io already
parses every concept in `build_link_graph` and memoizes a `BodyIndex` on
`LinkGraph.bodies`, but that index carries links, headings, list items, code
blocks and footnotes -- not the `html_block`, `html_inline`, `blockquote_open`
and `table_open` tokens these four rules read. Widening it is a change to the
core for a tier-2 convenience, and reaching into `okf_io._md` would
couple this package to a private module of the one it sits above.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token
from okf_io import Bundle, Document, Finding, Rule, RuleContext, Severity

#: The topic prefix this rule set claims. `validate()` raises the moment an
#: external rule emits a built-in prefix, and `render` collides with none of the
#: eight (computation, frontmatter, legacy, lifecycle, links, provenance,
#: reserved, trust). The module name is the prefix, as in okf-io.
TOPIC = "render"

CODES = (
    "render.angle-bracket",  # a bare `<placeholder>` parsed as raw HTML and swallowed
    "render.callout",  # a callout header that is malformed, or names an unknown type
    "render.wikilink",  # an unbalanced `[[...` or an empty `[[ ]]` target
    "render.table-pipe",  # an unescaped `|` splitting a body row past the header width
)

#: Unpacked from `CODES` rather than re-typed, so a code-string edit to one
#: cannot silently drift from the other -- the habit `tags/vocabulary.py` set.
_CODE_ANGLE_BRACKET, _CODE_CALLOUT, _CODE_WIKILINK, _CODE_TABLE_PIPE = CODES

#: `Finding.spec` is "the thing that says so". These codes cite no OKF section --
#: a bundle with a typo'd callout is a conformant bundle -- so they cite the
#: module that defines them, the same move `vocabulary_rule` makes when it cites
#: the vocabulary file rather than the spec.
_SPEC = "okf_ext.render"

#: Reserved member names, restated here rather than imported from
#: `okf_io.bundle`: those constants are public-but-unexported, and two string
#: literals are cheaper than a dependency on a name the core never promised.
_INDEX_NAME = "index.md"
_LOG_NAME = "log.md"

#: Hand-maintained allowlist of HTML tag names a markdown renderer actually
#: handles inline or as a block. Any other syntactically-valid tag name (e.g.
#: `<slug>`) is almost always an un-backticked placeholder and vanishes when
#: rendered. Kept deliberately generous -- a false negative on a rare real tag
#: is cheaper than a false positive on prose.
_HTML_ALLOWLIST = frozenset(
    {
        "a",
        "abbr",
        "audio",
        "b",
        "blockquote",
        "br",
        "center",
        "cite",
        "code",
        "del",
        "details",
        "div",
        "em",
        "font",
        "hr",
        "i",
        "iframe",
        "img",
        "ins",
        "kbd",
        "li",
        "mark",
        "ol",
        "p",
        "pre",
        "s",
        "small",
        "source",
        "span",
        "strong",
        "sub",
        "summary",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
        "video",
    }
)

#: `<` + optional `/` + tag name (letter then alnum/hyphen). Matches the
#: CommonMark raw-HTML tag-name grammar that produces the `html_inline` and
#: `html_block` tokens in the first place.
_TAG_RE = re.compile(r"^</?([a-zA-Z][a-zA-Z0-9-]*)")

#: Known callout types, lowercased. An unknown type renders with a default icon
#: rather than breaking, but is usually a typo -- hence a separate message, not
#: a separate code.
_CALLOUT_TYPES = frozenset(
    {
        "note",
        "abstract",
        "summary",
        "tldr",
        "info",
        "todo",
        "tip",
        "hint",
        "important",
        "success",
        "check",
        "done",
        "question",
        "help",
        "faq",
        "warning",
        "caution",
        "attention",
        "failure",
        "fail",
        "missing",
        "danger",
        "error",
        "bug",
        "example",
        "quote",
        "cite",
    }
)

#: A well-formed callout header: `[!type]`, an optional fold marker, then an
#: optional space-separated title. Anchored to the start of the already
#: `> `-stripped first blockquote line.
_CALLOUT_OK_RE = re.compile(r"^\[!([a-zA-Z][a-zA-Z0-9-]*)\][+-]?(?:\s.*)?$")
#: The "attempted callout" trigger -- the first line opens with `[!` or `[![`.
#: An ordinary blockquote never matches, and is therefore never flagged.
_CALLOUT_ATTEMPT_RE = re.compile(r"^\[!?\[?!")

#: An opening `[[` or `![[`, used to find candidate spans to validate.
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

#: Inline children that end a source line. markdown-it emits these between the
#: `text` children they separate, and they carry no content of their own.
_BREAKS = frozenset({"softbreak", "hardbreak"})


def _members(bundle: Bundle) -> Iterator[tuple[str, Document]]:
    """Every markdown member a person reads, in sorted path order.

    Concepts, indexes **and** logs: render correctness is a property of
    markdown, not of a document's role. `bundle.assets` is deliberately absent
    -- it holds non-markdown members, and parsing an HTML asset as markdown
    would flag every real tag in it.

    Sorted because findings must not depend on mapping order; `validate()`
    sorts after collection anyway, so this is about the rule being reproducible
    when called directly.
    """
    members: dict[str, Document] = {f"{concept_id}.md": doc for concept_id, doc in bundle.concepts.items()}
    for directory, document in bundle.indexes.items():
        members[f"{directory}/{_INDEX_NAME}" if directory else _INDEX_NAME] = document
    for directory, document in bundle.logs.items():
        members[f"{directory}/{_LOG_NAME}" if directory else _LOG_NAME] = document
    for path in sorted(members):
        yield path, members[path]


def _line(mapping: Sequence[int], offset: int) -> int:
    """A token's file line. markdown-it maps are 0-based and body-relative;
    `body_line_offset` is what turns one into the other.

    Takes the map rather than the token so it carries no `map is None` branch of
    its own -- every caller has already guarded, and a second unreachable guard
    would cost branch coverage for nothing.
    """
    return mapping[0] + 1 + offset


def _advance(child: Token) -> int:
    """How many source lines *child* moves everything after it down.

    An inline token carries the *block's* map, not per-child positions, so a
    finding inside a multi-line paragraph can only be placed by walking the
    children and counting. A break child ends a line; any other child advances
    by the newlines its own content carries (a raw HTML chunk may span lines).

    **One residual imprecision, and it is not fixable without re-lexing.**
    CommonMark collapses the line ending inside a multi-line code span to a
    space, so ``content`` reports no newline and everything after `` `a\\nb` ``
    is placed one line early. Reporting the block's first line -- what this
    replaced -- was wrong for *every* child past the first line, not just these.
    """
    return 1 if child.type in _BREAKS else child.content.count("\n")


def _tag_name(html: str) -> str | None:
    """The tag name in an `html_inline`/`html_block` chunk, or `None` for a
    comment or a non-tag fragment. `<!--` returns `None` and is never flagged."""
    stripped = html.lstrip()
    if stripped.startswith("<!--"):
        return None
    match = _TAG_RE.match(stripped)
    return match.group(1).lower() if match else None


def _split_pipes(line: str) -> list[str]:
    """Split a markdown table row on `|`, honouring `\\|` escapes.

    Strips exactly one leading and one trailing `|` structurally, before
    splitting -- not by dropping empty cells afterwards, which would also
    swallow a legitimately empty first or last cell.
    """
    text = line.strip()
    # Unreachable from `_table_pipes`' two call sites -- both pre-filter rows
    # on `lstrip().startswith("|")`, so `text` always starts with `|` here.
    # Kept for a caller that isn't pre-filtered.
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells: list[str] = []
    buffer: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] == "|":
            buffer.append("|")
            index += 2
            continue
        if char == "|":
            cells.append("".join(buffer).strip())
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    cells.append("".join(buffer).strip())
    return cells


def _flag_html(path: str, html: str, line: int, severity: Severity) -> Iterator[Finding]:
    name = _tag_name(html)
    if name is None or name in _HTML_ALLOWLIST:
        return
    fragment = html.strip().splitlines()[0][:40]
    yield Finding(
        code=_CODE_ANGLE_BRACKET,
        severity=severity,
        message=f"Bare `<{name}>` renders as raw HTML and is swallowed; wrap it in backticks: `{fragment}`.",
        spec=_SPEC,
        path=path,
        line=line,
    )


def _angle_brackets(path: str, tokens: Sequence[Token], offset: int, severity: Severity) -> Iterator[Finding]:
    # The `not token.map` guard comes first in every one of these four loops --
    # closing tokens carry no map, so the guard is genuinely reachable and the
    # narrowing it gives the type checker costs no dead branch. Testing the
    # token type first would short-circuit it into a branch that never fires.
    for token in tokens:
        if not token.map:
            continue
        if token.type == "html_block":
            yield from _flag_html(path, token.content, _line(token.map, offset), severity)
        elif token.type == "inline":
            # Tracked per child rather than taken from the token: the map is the
            # whole block's, so a placeholder on a paragraph's third line would
            # otherwise be reported against its first.
            line = _line(token.map, offset)
            for child in token.children or []:
                if child.type == "html_inline":
                    yield from _flag_html(path, child.content, line, severity)
                line += _advance(child)


def _callouts(
    path: str, tokens: Sequence[Token], lines: Sequence[str], offset: int, severity: Severity
) -> Iterator[Finding]:
    for token in tokens:
        if not token.map or token.type != "blockquote_open":
            continue
        index = token.map[0]
        raw = lines[index] if index < len(lines) else ""
        first = raw.lstrip()
        # A blockquote nested in a list item (`- > [!note ...`) is not
        # recognised as a callout attempt: this reads the raw source line, not
        # the blockquote token's own content, so the leading `- ` survives the
        # lstrip and `first` never starts with `>`. Ported behaviour, matching
        # `wiki_io/lint/obsidian_render.py`.
        if first.startswith(">"):
            first = first[1:].lstrip()
        if not _CALLOUT_ATTEMPT_RE.match(first):
            continue  # an ordinary blockquote is not a callout attempt
        match = _CALLOUT_OK_RE.match(first)
        line = index + 1 + offset
        if match is None:
            yield Finding(
                code=_CODE_CALLOUT,
                severity=severity,
                message=f"Malformed callout header `{first[:40]}`; expected `> [!type] title`.",
                spec=_SPEC,
                path=path,
                line=line,
            )
        elif match.group(1).lower() not in _CALLOUT_TYPES:
            yield Finding(
                code=_CODE_CALLOUT,
                severity=severity,
                message=f"Unknown callout type `{match.group(1)}`.",
                spec=_SPEC,
                path=path,
                line=line,
            )


def _wikilinks(path: str, tokens: Sequence[Token], offset: int, severity: Severity) -> Iterator[Finding]:
    for token in tokens:
        if not token.map or token.type != "inline":
            continue
        start = _line(token.map, offset)
        # Only `text` children contribute scannable text -- `code_inline` and
        # `autolink` children do not, so `[[foo` inside backticks never reaches
        # the scan. Every other child still contributes **its newlines**: they
        # keep `text` line-aligned with the source, which is what both places
        # below depend on.
        #
        # Line breaks are preserved rather than dropped. Joining across them
        # glues an unclosed `[[` at the end of one line to a stray `]]` on the
        # next into one apparently-valid link -- a silent miss, and a garbled
        # excerpt in every message that spans the seam.
        text = "".join(
            child.content if child.type == "text" else "\n" * _advance(child) for child in (token.children or [])
        )
        for opener in _WIKILINK_OPEN_RE.finditer(text):
            # Anchor the candidate span at the `[[` (the match's last two
            # characters), so an `![[...]]` embed is validated from its brackets
            # rather than from the leading `!` -- otherwise a valid embed would
            # be false-flagged.
            rest = text[opener.end() - 2 :]
            line = start + text.count("\n", 0, opener.start())
            valid = _WIKILINK_RE.match(rest)
            if valid is None:
                # The excerpt stops at the line end for the same reason the scan
                # does: what follows is a different line and did not break this.
                fragment = rest.split("\n", 1)[0]
                yield Finding(
                    code=_CODE_WIKILINK,
                    severity=severity,
                    message=f"Unbalanced wikilink `{fragment[:30]}`; missing closing `]]`.",
                    spec=_SPEC,
                    path=path,
                    line=line,
                )
            elif not valid.group(1).strip():
                yield Finding(
                    code=_CODE_WIKILINK,
                    severity=severity,
                    message=f"Empty wikilink target `{valid.group(0)}`.",
                    spec=_SPEC,
                    path=path,
                    line=line,
                )


def _table_pipes(
    path: str, tokens: Sequence[Token], lines: Sequence[str], offset: int, severity: Severity
) -> Iterator[Finding]:
    for token in tokens:
        if not token.map or token.type != "table_open":
            continue
        start, end = token.map
        rows = [i for i in range(start, min(end, len(lines))) if lines[i].lstrip().startswith("|")]
        if len(rows) < 2:
            continue
        header_cells = len(_split_pipes(lines[rows[0]].strip()))
        # `rows[1]` is the `|---|---|` delimiter; body rows start at `rows[2]`.
        # Fewer cells than the header is legal padding and is not flagged.
        for index in rows[2:]:
            if len(_split_pipes(lines[index].strip())) > header_cells:
                yield Finding(
                    code=_CODE_TABLE_PIPE,
                    severity=severity,
                    message=(
                        "Unescaped `|` in a table cell splits the row into more columns "
                        "than the header; escape it as `\\|`."
                    ),
                    spec=_SPEC,
                    path=path,
                    line=index + 1 + offset,
                )


def render_rule(*, severity: Severity = "warn") -> Rule:
    """Build an `okf_io.Rule` that checks every markdown member for render breakage.

    **Every code is `warn` by default.** `Report.ok` is a claim about OKF v0.2
    conformance, and none of these four describes a conformance failure -- a
    bundle with a typo'd callout is a conformant bundle. The knob exists because
    okf-io's `strict=True` promotes *every* warning, including the deliberately
    `warn` `links.broken`, so a team wanting CI red on a malformed wikilink
    should not also get CI red on a dead link.

    Note this is stricter than wiki-io, where the two wikilink checks were
    `"error"`. That severity was set in a tool whose report was its own; inside
    okf-io's report it would make a conformant bundle fail `Report.ok`.

    Concepts, indexes and logs are walked; assets are not, and documents okf-io
    could not parse are skipped rather than re-reported.

    **`Finding.line` is the offending source line for all four codes.**
    `callout` and `table-pipe` read raw source lines and are exact. The two
    inline codes -- `angle-bracket` and `wikilink` -- are placed by walking a
    block's inline children and counting line breaks, because markdown-it gives
    an inline token the whole block's map. That is exact except after a
    multi-line code span, whose line ending CommonMark collapses to a space:
    findings past one are reported a line early. See `_advance`.
    """
    # `commonmark` plus the core `table` block rule. NOT `"gfm-like"`, which
    # enables `linkify` and raises `ModuleNotFoundError`, and no
    # `mdit-py-plugins`. Built once here rather than per document.
    parser = MarkdownIt("commonmark").enable("table")

    def rule(context: RuleContext) -> Iterable[Finding]:
        for path, document in _members(context.bundle):
            if document.parse_error is not None:
                continue
            body = document.body
            if not body:
                continue
            offset = document.body_line_offset
            lines = body.splitlines()
            tokens = parser.parse(body)
            yield from _angle_brackets(path, tokens, offset, severity)
            yield from _callouts(path, tokens, lines, offset, severity)
            yield from _wikilinks(path, tokens, offset, severity)
            yield from _table_pipes(path, tokens, lines, offset, severity)

    return rule
