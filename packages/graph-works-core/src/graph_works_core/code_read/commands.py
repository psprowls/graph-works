"""Read a window of a declared repository's working-tree file, for a cited line range.

Refusals are results, not exceptions: an unknown repository, an escaping
path, an untracked/ignored/absent file, and a start past EOF each come back
as a complete `CodeExcerpt` with `refusal` set. Only invalid bounds raise
(`ValueError`), because they are a caller's usage error.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from code_graph_io import extension_languages

from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repo_files import confine, declared_repos, repo_file_set

ExcerptRefusal = Literal["unknown-repository", "outside-repository", "unknown-file", "out-of-range"]

#: Lines of context served on each side of the cited range.
CONTEXT = 5
#: `end` is capped at `start + MAX_SPAN`; the window is truncated beyond it.
MAX_SPAN = 400

#: Formats a wiki cites that the code-graph parser registry does not parse.
_EXTRA_LANGUAGES = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".sh": "shell",
    ".bash": "shell",
}


@dataclass(frozen=True, slots=True)
class CodeExcerpt:
    """Lines `first`..`last` (1-based, inclusive) of `repo`/`path`; `end` is the effective end."""

    repo: str
    path: str
    start: int
    end: int
    first: int | None
    last: int | None
    total_lines: int | None
    language: str | None
    lines: tuple[str, ...]
    refusal: ExcerptRefusal | None


def language_for(path: str) -> str | None:
    """The language a file extension names, or `None`."""
    suffix = PurePosixPath(path).suffix.lower()
    return extension_languages().get(suffix) or _EXTRA_LANGUAGES.get(suffix)


def _lines(file: Path) -> list[str]:
    lines = file.read_text(encoding="utf-8", errors="replace").split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def run_code_excerpt(layout: WorkspaceLayout, repo: str, path: str, start: int, end: int | None = None) -> CodeExcerpt:
    """Serve `start - 5` .. `min(end, start + 400) + 5`, clamped to the file. Never writes."""
    if start < 1:
        raise ValueError(f"start: must be >= 1, got {start}")
    if end is not None and end < start:
        raise ValueError(f"end: must be >= start ({start}), got {end}")
    eff_end = min(end if end is not None else start, start + MAX_SPAN)

    def refused(refusal: ExcerptRefusal) -> CodeExcerpt:
        return CodeExcerpt(repo, path, start, eff_end, None, None, None, None, (), refusal)

    declared = next((entry for entry in declared_repos(layout) if entry.name == repo), None)
    if declared is None:
        return refused("unknown-repository")
    confined = confine(repo_file_set(declared), path)
    if not isinstance(confined, Path):
        return refused(confined)
    lines = _lines(confined)
    language = language_for(path)
    if start > len(lines):
        return CodeExcerpt(repo, path, start, eff_end, None, None, len(lines), language, (), "out-of-range")
    first = max(1, start - CONTEXT)
    last = min(len(lines), eff_end + CONTEXT)
    return CodeExcerpt(
        repo, path, start, eff_end, first, last, len(lines), language, tuple(lines[first - 1 : last]), None
    )


__all__ = ["CONTEXT", "MAX_SPAN", "CodeExcerpt", "ExcerptRefusal", "language_for", "run_code_excerpt"]
