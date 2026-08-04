"""Round-trip YAML storage and frontmatter splitting.

Internal module. Nothing here is part of okf-io's public API; import from
``okf_io`` instead.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.emitter import Emitter

BOM = "\ufeff"
_OPEN_DELIM = "---"
_CLOSE_DELIMS = ("---", "...")

#: A line is any run of non-break characters up to and including its own break.
#: Only CR, LF and CRLF end a line - see :func:`_lines`.
_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")

# Frontmatter is never improved by line wrapping, and a re-wrap is a diff bomb.
_UNBOUNDED_WIDTH = 1 << 30

_SEQ_DASH_RE = re.compile(r"^(?P<indent>[ ]*)-[ ]", re.MULTILINE)
_MAP_INDENT_RE = re.compile(r"^(?P<indent>[ ]+)\S", re.MULTILINE)
_PADDED_FLOW_RE = re.compile(r"\{[ ]")

#: An LF that is not already the tail of a CRLF - see :func:`dump_fm`.
_UNPAIRED_LF_RE = re.compile(r"(?<!\r)\n")


def _lines(text: str) -> list[str]:
    r"""Split *text* into lines, keeping ends, breaking only on CR, LF and CRLF.

    Deliberately not ``str.splitlines``, which also breaks on VT, FF, FS, GS,
    RS, NEL (``\x85``), LINE SEPARATOR (``\u2028``) and PARAGRAPH SEPARATOR
    (``\u2029``). YAML treats all of those as ordinary scalar characters, so
    ``splitlines`` lets a scalar containing one manufacture a phantom ``---``
    line: the frontmatter closes early, and real frontmatter keys are handed
    back as body. That is the same class of bug as matching delimiters with
    ``strip()``, and it is invisible to a round-trip test because
    :func:`join` is pure concatenation either way.
    """
    return _LINE_RE.findall(text)


@dataclass(frozen=True, slots=True)
class Split:
    """A concept file cut into verbatim pieces.

    ``bom + open_delim + fm_text + close_delim + gap + body`` reconstructs the
    source exactly, for every shape — including "no frontmatter at all" and
    "opening delimiter with no close". No line ending is ever reconstructed
    from an assumption; both delimiter lines are captured with their own.

    ``gap`` takes *every* blank line between the closing delimiter and the
    first non-blank one, however many, so ``body`` always begins at real
    content. A consumer inspecting ``body`` alone therefore cannot tell how
    much vertical space preceded it; ``gap + body`` is the whole remainder.
    """

    bom: str
    open_delim: str
    fm_text: str
    close_delim: str
    gap: str
    body: str
    has_frontmatter: bool
    unterminated: bool


def split(text: str) -> Split:
    r"""Cut *text* into frontmatter and body along line boundaries.

    Line-anchored on purpose: a ``find("\n---")`` splitter truncates the moment
    a YAML scalar contains ``---``. Delimiters are matched with ``rstrip()``,
    never ``strip()`` — an indented ``---`` inside a block scalar is content.
    """
    bom = BOM if text.startswith(BOM) else ""
    rest = text[len(bom) :]
    lines = _lines(rest)

    if not lines or lines[0].rstrip() != _OPEN_DELIM:
        return Split(
            bom=bom,
            open_delim="",
            fm_text="",
            close_delim="",
            gap="",
            body=rest,
            has_frontmatter=False,
            unterminated=False,
        )

    close_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() in _CLOSE_DELIMS:
            close_idx = i
            break

    if close_idx is None:
        return Split(
            bom=bom,
            open_delim=lines[0],
            fm_text="".join(lines[1:]),
            close_delim="",
            gap="",
            body="",
            has_frontmatter=True,
            unterminated=True,
        )

    after = lines[close_idx + 1 :]
    gap_end = 0
    while gap_end < len(after) and not after[gap_end].strip():
        gap_end += 1

    return Split(
        bom=bom,
        open_delim=lines[0],
        fm_text="".join(lines[1:close_idx]),
        close_delim=lines[close_idx],
        gap="".join(after[:gap_end]),
        body="".join(after[gap_end:]),
        has_frontmatter=True,
        unterminated=False,
    )


def join(split_result: Split, fm_text: str | None = None) -> str:
    """Reassemble a :class:`Split`, optionally replacing the frontmatter text.

    Pure concatenation. When *fm_text* is ``None`` the original text is reused,
    which is how the clean-document short-circuit stays byte-exact.

    **Precondition when overriding.** The delimiters are reused as-is, so
    passing *fm_text* is only meaningful for a Split with ``has_frontmatter``
    and not ``unterminated``. Overriding on any other shape splices the new
    text into a document whose delimiters are empty, yielding frontmatter with
    no ``---`` around it or a block that never closes. A caller synthesizing
    frontmatter onto a document that has none must build a Split carrying real
    delimiters first (``dataclasses.replace``) rather than relying on this.
    """
    text = split_result.fm_text if fm_text is None else fm_text
    return (
        split_result.bom
        + split_result.open_delim
        + text
        + split_result.close_delim
        + split_result.gap
        + split_result.body
    )


class NotAMappingError(ValueError):
    """Frontmatter parsed as valid YAML but is not a mapping."""


class _PaddedFlowEmitter(Emitter):
    """Emit ``{ a: b }`` instead of ruamel's default ``{a: b}``.

    ruamel preserves flow *style* but not the padding inside flow mappings, so
    a source written ``generated: { by: …, at: … }`` comes back as
    ``{by: …, at: …}`` and every save rewrites the line.

    ``flow_map_start`` cannot simply be set to ``"{ "``: ruamel pushes that same
    string onto ``flow_context`` and later asserts it is exactly ``"{"``.
    Padding at the ``write_indicator`` boundary leaves parser state untouched.
    ``flow_map_end`` is only ever written, never compared, so it is safe to set.
    """

    flow_map_end = " }"

    def write_indicator(
        self,
        indicator: str,
        need_whitespace: bool,
        whitespace: bool = False,
        indention: bool = False,
    ) -> None:
        if indicator.endswith("{"):
            indicator = indicator + " "
        super().write_indicator(indicator, need_whitespace, whitespace, indention)


@dataclass(frozen=True, slots=True)
class Style:
    """Per-document emitter configuration, sniffed from the source text."""

    mapping_indent: int = 2
    sequence_indent: int = 4
    sequence_dash_offset: int = 2
    padded_flow: bool = False
    newline: str = "\n"


def sniff_style(fm_text: str) -> Style:
    """Infer emitter settings from the source frontmatter text.

    Best-effort. Sniffing that finds nothing falls back to defaults; since the
    line-splice write-back (``document.serialize``) never re-emits an unchanged
    line, a wrong guess costs formatting on the edited line, not the file.
    """
    newline = "\r\n" if "\r\n" in fm_text else "\n"

    indents = sorted({len(m.group("indent")) for m in _MAP_INDENT_RE.finditer(fm_text)})
    mapping_indent = indents[0] if indents and 1 <= indents[0] <= 10 else 2

    dashes = {len(m.group("indent")) for m in _SEQ_DASH_RE.finditer(fm_text)}
    if dashes:
        dash = min(dashes)
        sequence_indent, dash_offset = dash + 2, dash
    else:
        sequence_indent, dash_offset = mapping_indent + 2, mapping_indent

    return Style(
        mapping_indent=mapping_indent,
        sequence_indent=sequence_indent,
        sequence_dash_offset=dash_offset,
        padded_flow=bool(_PADDED_FLOW_RE.search(fm_text)),
        newline=newline,
    )


def _yaml_for(style: Style) -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.allow_unicode = True
    yaml.width = _UNBOUNDED_WIDTH
    yaml.explicit_start = False
    yaml.explicit_end = False
    yaml.indent(
        mapping=style.mapping_indent,
        sequence=style.sequence_indent,
        offset=style.sequence_dash_offset,
    )
    if style.padded_flow:
        yaml.Emitter = _PaddedFlowEmitter
    return yaml


def load_fm(fm_text: str) -> CommentedMap:
    """Parse frontmatter text into a round-trippable mapping.

    Raises ``ruamel.yaml.YAMLError`` for invalid YAML and
    :class:`NotAMappingError` for valid YAML that is not a mapping. An empty
    block is a legitimate shape and yields an empty mapping.
    """
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    data = yaml.load(fm_text)
    if data is None:
        return CommentedMap()
    if not isinstance(data, CommentedMap):
        raise NotAMappingError(f"Frontmatter must be a YAML mapping, got {type(data).__name__}")
    return data


def dump_fm(data: CommentedMap, *, style: Style) -> str:
    r"""Emit *data* as frontmatter text using *style*.

    The newline rewrite only promotes an LF that is not already preceded by a
    CR. A blanket ``replace("\n", "\r\n")`` also hits the LF of a ``\r\n`` that
    a scalar's own value carried in, turning it into ``\r\r\n``; re-parsing
    that yields a spurious blank line inside the scalar. ``load_fm`` never
    produces such a value -- ruamel normalizes line breaks on parse -- but a
    caller assigning one directly can.
    """
    stream = io.StringIO()
    _yaml_for(style).dump(data, stream)
    text = stream.getvalue()
    if style.newline != "\n":
        text = _UNPAIRED_LF_RE.sub(style.newline, text)
    return text
