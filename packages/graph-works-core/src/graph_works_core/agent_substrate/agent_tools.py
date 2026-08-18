"""Bounded tool and context helpers, re-expressed over a loaded OKF bundle.

Roughly 150 of the source module's 209 lines walked a wiki directory and
parsed frontmatter. Here that walk is `okf_io.load_bundle`, which already
returns every member typed, from one pass, with tolerant coercion — so the
helpers take a `Bundle` rather than a root path.

**The containment guards are deleted, not reimplemented.** The old
`_resolved_path_under_wiki`, the `.relative_to(wiki_root)` checks and the
`"ERROR: path is outside wiki"` branch existed because a model-supplied string
was joined onto a filesystem path. A concept id is a `Bundle.concepts` key, so
traversal is unrepresentable rather than defended against. A missing key still
returns the `ERROR: …` string shape the tool contract expects: that string is
how the loop tells the model it asked for something that does not exist.

`truncate_text` is defined here rather than imported. The C0 port list retires
`text_utils.py` as superseded, and a grep across the landed packages finds no
successor for this function specifically — so C2 records the gap by carrying
the five lines, per the epic's "a gap in a replacement is a note for whoever
later needs the missing piece".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool
from okf_io import Bundle, Document

DEFAULT_EXCERPT_CHARS = 500

#: Appended inside the caller's `max_chars`, never on top of it. The marker
#: names no number: an honest count would have to include the marker's own
#: digits, which change the count.
_TRUNCATION_MARKER = "\n\n[TRUNCATED]"

#: Lane name to the singular noun used as an entry's `kind`. Only the
#: irregular plural needs an entry; everything else drops a trailing `s`. Lane
#: names are bundle-declared (constraint 5), so this is a display fallback,
#: not a structure.
_IRREGULAR_KINDS: dict[str, str] = {"entities": "entity"}


@dataclass(frozen=True)
class SourceChunks:
    full_text: str | None
    chunks: list[str]
    over_budget: bool


def truncate_text(text: str, max_chars: int) -> str:
    """Return *text* capped at *max_chars*, marker included.

    `len(truncate_text(t, n)) <= n` always. When *max_chars* cannot fit the
    marker, the bound wins and no marker is emitted.
    """
    if len(text) <= max_chars:
        return text
    budget = max_chars - len(_TRUNCATION_MARKER)
    if budget <= 0:
        return text[:max_chars]
    return text[:budget] + _TRUNCATION_MARKER


def strip_code_fence(text: str) -> str:
    """*text* with a leading ```lang fence line and its closing ``` removed.

    A model told to return bare JSON returns it fenced anyway. The one
    implementation: `commands.ingest` and `commands.suggest_pages` kept private
    copies that predated this one and disagreed with it on degenerate input,
    and both now call this. A bare opening fence carries no payload and yields
    `""`; unfenced text comes back stripped.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    newline = stripped.find("\n")
    if newline == -1:
        return ""
    stripped = stripped[newline + 1 :]
    closing = stripped.rfind("```")
    return stripped[:closing].strip() if closing != -1 else stripped.strip()


def _lane(concept_id: str) -> str:
    """The lane a concept id belongs to: its first path segment, `""` at root."""
    head, separator, _ = concept_id.partition("/")
    return head if separator else ""


def _slug(concept_id: str) -> str:
    return concept_id.rpartition("/")[2]


def _humanized(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def _entry(concept_id: str, doc: Document, lane: str, *, excerpt_chars: int) -> dict[str, Any]:
    slug = _slug(concept_id)
    return {
        "kind": _IRREGULAR_KINDS.get(lane, lane.removesuffix("s")),
        "slug": slug,
        "path": f"/{concept_id}.md",
        "title": str(doc.fm.title or _humanized(slug)),
        "summary": str(doc.fm.description or ""),
        "uri": str(doc.fm.resource or ""),
        "entity_kind": str(doc.fm.type or ""),
        "excerpt": " ".join(doc.body.split())[:excerpt_chars],
    }


def build_catalog(
    bundle: Bundle,
    *,
    lanes: Sequence[str],
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
) -> dict[str, list[dict[str, Any]]]:
    """One catalog entry per concept in *lanes*, keyed by lane.

    Every requested lane is a key even when it has no members, so a prompt
    builder can render an empty section rather than branching on absence.
    `index.md` and `log.md` are structurally absent: the loader keeps them in
    `Bundle.indexes` / `Bundle.logs`, not in `concepts`.
    """
    catalog: dict[str, list[dict[str, Any]]] = {lane: [] for lane in lanes}
    for concept_id in sorted(bundle.concepts):
        lane = _lane(concept_id)
        if lane in catalog:
            catalog[lane].append(_entry(concept_id, bundle.concepts[concept_id], lane, excerpt_chars=excerpt_chars))
    return catalog


def read_bounded_page(bundle: Bundle, concept_id: str, *, max_chars: int = DEFAULT_EXCERPT_CHARS) -> str:
    """One concept rendered as `# Title` plus its body, capped at *max_chars*."""
    key = concept_id.strip().removesuffix(".md")
    doc = bundle.concept(key)
    if doc is None:
        return f"ERROR: no concept {key!r} in this bundle"
    title = str(doc.fm.title or _humanized(_slug(key)))
    body = doc.body.strip()
    content = f"# {title}\n\n{body}" if body else f"# {title}"
    return truncate_text(content, max_chars)


def _flatten_catalog(catalog: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket, entries in catalog.items():
        for entry in entries:
            row = dict(entry)
            row.setdefault("bucket", bucket)
            rows.append(row)
    return rows


def _catalog_bucket_matches(bucket: str, entry: dict[str, Any], kind: str | None) -> bool:
    if not kind:
        return True
    normalized = kind.lower().strip()
    if normalized == bucket or normalized == bucket.removesuffix("s"):
        return True
    entry_kind = str(entry.get("kind", "")).lower()
    return normalized == entry_kind or normalized == f"{entry_kind}s"


def search_catalog(
    catalog: dict[str, list[dict[str, Any]]],
    query: str,
    *,
    kind: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Substring search over a built catalog. Ported unchanged: it operates on
    the catalog dict either way, so nothing about the bundle rebuild reaches it."""
    query_lc = query.lower().strip()
    matches: list[dict[str, Any]] = []
    for row in _flatten_catalog(catalog):
        bucket = str(row.get("bucket", ""))
        if not _catalog_bucket_matches(bucket, row, kind):
            continue
        fields = [
            str(row.get("title", "")),
            str(row.get("summary", "")),
            str(row.get("slug", "")),
            str(row.get("target_slug", "")),
        ]
        haystack = " ".join(fields).lower()
        if not query_lc or query_lc in haystack:
            matches.append(row)
        if len(matches) >= limit:
            break
    return matches


def chunk_text(text: str, *, max_chars: int, chunk_chars: int) -> SourceChunks:
    if len(text) <= max_chars:
        return SourceChunks(full_text=text, chunks=[], over_budget=False)
    chunks = [text[start : start + chunk_chars] for start in range(0, len(text), chunk_chars)]
    return SourceChunks(full_text=None, chunks=chunks, over_budget=True)


def filter_graph_tools(graph_tools: list[BaseTool], allowed_names: set[str]) -> list[BaseTool]:
    return [graph_tool for graph_tool in graph_tools if graph_tool.name in allowed_names]


__all__ = [
    "DEFAULT_EXCERPT_CHARS",
    "SourceChunks",
    "build_catalog",
    "chunk_text",
    "filter_graph_tools",
    "read_bounded_page",
    "search_catalog",
    "strip_code_fence",
    "truncate_text",
]
