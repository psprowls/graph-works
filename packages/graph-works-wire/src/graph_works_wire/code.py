"""Plain-data projections for code reads."""

from __future__ import annotations

from graph_works_core.code_read import CodeExcerpt


def excerpt_payload(result: CodeExcerpt) -> dict[str, object]:
    """`/v1/code/excerpt`: the served window, context included, or a refusal."""
    return {
        "repo": result.repo,
        "path": result.path,
        "start": result.start,
        "end": result.end,
        "first": result.first,
        "last": result.last,
        "total_lines": result.total_lines,
        "language": result.language,
        "lines": list(result.lines),
        "refusal": result.refusal,
    }
