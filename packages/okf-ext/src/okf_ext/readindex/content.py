"""Serve a member's verified content from its stored row, or from one parse.

The body slice lands where okf-io's splitter does without importing its private
`_yaml` module (item D-003): lines break only on CR, LF and CRLF, a leading BOM
belongs to line 1, an unterminated block has an empty body, and a file ending
at its closing delimiter or in trailing gap whitespace has an empty body.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from okf_io import Document, ParseError, Unreadable

from okf_ext.readindex.model import PageContent
from okf_ext.readindex.project import fm_json

#: okf-io's line rule (`okf_io/_yaml.py` `_LINE_RE`): only CR, LF and CRLF end a line.
_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")
_BOM = "﻿"
_CLOSE_DELIMS = ("---", "...")


@dataclass(frozen=True, slots=True)
class StoredContent:
    """The stored columns one verified read needs, decoded from a member row."""

    sha256: str | None
    fm: Mapping[str, object] | None
    fm_exact: bool
    body_line_offset: int | None
    parse_error: ParseError | None
    coercion_failures: frozenset[str]


def body_at(text: str, offset: int, parse_error: ParseError | None) -> str:
    """Return the body of *text*, which starts after its first *offset* complete lines."""
    if parse_error is not None and parse_error.kind == "unterminated":
        return ""
    rest = text[len(_BOM) :] if text.startswith(_BOM) else text
    if offset == 0:
        return rest
    lines = _LINE_RE.findall(rest)
    remainder = "".join(lines[offset:])
    if len(lines) > offset + 1 or remainder.endswith(("\n", "\r")):
        return remainder
    # An empty or single unterminated remainder is still prefix when it is trailing
    # gap whitespace, or when it is the closing delimiter itself (no earlier close).
    if not remainder.strip():
        return ""
    closed = any(line.rstrip() in _CLOSE_DELIMS for line in lines[1:offset])
    return remainder if closed else ""


def content_of(member_id: str, doc: Document) -> PageContent:
    """Project a parsed Document the way a verified stored row is served."""
    text, exact = fm_json(doc)
    frontmatter: dict[str, object]
    if exact:
        frontmatter = json.loads(text)
    else:
        try:
            frontmatter = doc.fm_data(dates="iso")
        except RecursionError:
            frontmatter = json.loads(text)
    return PageContent(member_id, MappingProxyType(frontmatter), doc.body, doc.parse_error, doc.fm.coercion_failures)


def serve(member_id: str, data: bytes, stored: StoredContent | None) -> PageContent | Unreadable:
    """Serve *data* from *stored* when its hash matches an exact row; otherwise parse it once."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return Unreadable(f"not valid UTF-8: {exc}")
    if (
        stored is not None
        and stored.fm_exact
        and stored.fm is not None
        and stored.body_line_offset is not None
        and stored.sha256 == hashlib.sha256(data).hexdigest()
    ):
        return PageContent(
            member_id,
            MappingProxyType(deepcopy(dict(stored.fm))),
            body_at(text, stored.body_line_offset, stored.parse_error),
            stored.parse_error,
            stored.coercion_failures,
        )
    return content_of(member_id, Document.parse(text))


def read_content(root: Path, member_id: str, stored: StoredContent | None) -> PageContent | Unreadable:
    """Read one member's bytes once, then serve them; the bytes hashed are the bytes sliced."""
    try:
        data = root.joinpath(*member_id.split("/")).read_bytes()
    except OSError as exc:
        return Unreadable(f"could not be read: {exc}")
    return serve(member_id, data, stored)
