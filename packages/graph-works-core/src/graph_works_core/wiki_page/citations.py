"""A page's code citations: every inline code span matching `<path>:<N>[-<M>]`.

The grammar is a graph-works convention, not an OKF one, so it lives here
rather than in okf-ext. The body is parsed with markdown-it's `commonmark`
preset (okf-io's own helper is private), so fenced and indented code blocks
yield no `inline` token and are excluded by construction.

`line` is body-relative: 1-based within the body `/v1/wiki/page` serves.
That page's outlink `line` counts file lines (okf-io adds the frontmatter
offset); the two must never be compared.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline
from markdown_it.rules_inline.backticks import backtick
from okf_io import load_bundle

from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repo_files import RepoFiles, repo_file_sets

CitationStatus = Literal["resolved", "ambiguous", "missing"]

_GRAMMAR = re.compile(r"(?P<path>[^\s:]+\.[A-Za-z0-9]+):(?P<start>[1-9]\d*)(?:-(?P<end>[1-9]\d*))?")


@dataclass(frozen=True, slots=True)
class CitationCandidate:
    """One of an ambiguous citation's matches."""

    repo: str
    path: str


@dataclass(frozen=True, slots=True)
class Citation:
    """One citation occurrence."""

    raw: str
    line: int
    repo: str | None
    path: str | None
    start: int
    end: int
    status: CitationStatus
    candidates: tuple[CitationCandidate, ...]


@dataclass(frozen=True, slots=True)
class WikiCitations:
    """A page's citations in body order, duplicates kept."""

    id: str
    citations: tuple[Citation, ...]
    refusal: Literal["unknown-page"] | None


def _backtick_with_source_lines(state: StateInline, silent: bool) -> bool:
    """Keep code-span line maps relative to the enclosing inline source.

    The standard rule normalizes code newlines to spaces. Capture its source
    positions instead of counting rendered content or soft/hardbreak tokens;
    other inline constructs (such as link destinations) can consume newlines too.
    """
    start = state.pos
    count = len(state.tokens)
    matched = backtick(state, silent)
    if matched and not silent and len(state.tokens) > count and state.tokens[-1].type == "code_inline":
        state.tokens[-1].map = [state.src.count("\n", 0, start), state.src.count("\n", 0, state.pos) + 1]
    return matched


def extract_citations(body: str) -> tuple[tuple[str, int, str, int, int], ...]:
    """Return `(raw, line, path, start, end)` for matching inline code spans."""
    found: list[tuple[str, int, str, int, int]] = []
    parser = MarkdownIt("commonmark")
    parser.inline.ruler.at("backticks", _backtick_with_source_lines)
    for token in parser.parse(body):
        if token.type != "inline" or token.map is None:
            continue
        for child in token.children or ():
            if child.type == "code_inline" and child.map is not None and (match := _GRAMMAR.fullmatch(child.content)):
                line = token.map[0] + child.map[0] + 1
                start = int(match["start"])
                end = int(match["end"]) if match["end"] else start
                found.append((child.content, line, match["path"], start, end))
    return tuple(found)


def _by_basename(sets: tuple[RepoFiles, ...]) -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for repo in sets:
        for file in repo.files:
            index[file.rsplit("/", 1)[-1]].append((repo.name, file))
    return index


def _resolve(
    path: str, sets: tuple[RepoFiles, ...], basenames: dict[str, list[tuple[str, str]]]
) -> tuple[str | None, str | None, CitationStatus, tuple[CitationCandidate, ...]]:
    posix = PurePosixPath(path)
    if posix.is_absolute() or ".." in posix.parts:
        return None, None, "missing", ()
    for repo in sets:
        if path in repo.files:
            return repo.name, path, "resolved", ()
    suffix = "/" + path
    hits = sorted((name, file) for name, file in basenames.get(posix.name, ()) if file.endswith(suffix))
    if len(hits) == 1:
        return hits[0][0], hits[0][1], "resolved", ()
    if hits:
        return None, None, "ambiguous", tuple(CitationCandidate(name, file) for name, file in hits)
    return None, None, "missing", ()


def run_wiki_citations(layout: WorkspaceLayout, page_id: str) -> WikiCitations:
    """Extract and resolve a page's citations. Never writes."""
    document = load_bundle(layout.bundle_dir).concept(page_id)
    if document is None:
        return WikiCitations(page_id, (), "unknown-page")
    spans = extract_citations(document.body)
    if not spans:
        return WikiCitations(page_id, (), None)
    sets = repo_file_sets(layout)
    basenames = _by_basename(sets)
    citations = []
    for raw, line, path, start, end in spans:
        repo, hit, status, candidates = _resolve(path, sets, basenames)
        citations.append(Citation(raw, line, repo, hit, start, end, status, candidates))
    return WikiCitations(page_id, tuple(citations), None)


__all__ = [
    "Citation",
    "CitationCandidate",
    "CitationStatus",
    "WikiCitations",
    "extract_citations",
    "run_wiki_citations",
]
