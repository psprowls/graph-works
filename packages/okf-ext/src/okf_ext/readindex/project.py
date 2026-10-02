"""The public body-free member projection shared by full reads and the index."""

from __future__ import annotations

import json
import math
from types import MappingProxyType

from okf_io import Document, ParseError, effective_status
from okf_io.bundle import MemberKind

from okf_ext.readindex.model import MemberRow


def fm_json(document: Document) -> tuple[str, bool]:
    """Return frontmatter JSON and whether every value was represented exactly."""
    exact = True

    def inexact(value: object) -> str:
        nonlocal exact
        exact = False
        try:
            return repr(value)
        except RecursionError:
            return "<recursive frontmatter value>"

    def finite(value: object) -> object:
        if isinstance(value, float) and not math.isfinite(value):
            return inexact(value)
        if isinstance(value, dict):
            return {key: finite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [finite(item) for item in value]
        return value

    try:
        data = document.fm_data(dates="iso")
    except RecursionError:
        # Preserve JSON-supported fields; only unrepresentable frontmatter
        # values use repr. Never include the document body in this fallback.
        data = {}
        for key, value in document.fm_raw.items():
            try:
                data[str(key)] = json.loads(json.dumps(value, allow_nan=False, default=inexact))
            except (TypeError, ValueError, RecursionError):
                data[str(key)] = inexact(value)
    text = json.dumps(finite(data), ensure_ascii=False, allow_nan=False, default=inexact)
    return text, exact


def _columns(document: Document) -> tuple[str | None, str | None, str | None, ParseError | None, frozenset[str]]:
    return (
        document.fm.type,
        document.fm.title,
        effective_status(document.fm),
        document.parse_error,
        document.fm.coercion_failures,
    )


def _tags(document: Document) -> tuple[str, ...]:
    return tuple(sorted(set(document.fm.tags)))


def member_row(
    member_id: str,
    kind: MemberKind,
    document: Document | None = None,
    *,
    sha256: str | None = None,
) -> MemberRow:
    """Project a readable member exactly as an IndexView decodes its stored row."""
    if document is None:
        return MemberRow(member_id, kind, sha256, None, None, None, (), None, True, None, frozenset())
    text, exact = fm_json(document)
    type_, title, status, error, failures = _columns(document)
    return MemberRow(
        member_id,
        kind,
        sha256,
        type_,
        title,
        status,
        _tags(document),
        MappingProxyType(json.loads(text)),
        exact,
        error,
        failures,
    )
