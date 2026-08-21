"""Directory-skip floor + config-driven ignore-glob matching for graph scanning.

Single source of truth for which paths the file scanner and the manifest
scanner skip. `DEFAULT_SKIP_DIRS` is an unconditional floor — VCS metadata,
build output, dependency caches, and virtualenvs are skipped by directory-
component name alone, regardless of any config. On top of that floor, a
workspace's `_repositories.yaml` `ignore:` patterns (global and per-repo,
already merged by `code_wiki_okf.config.load_config`) compile to an
`IgnoreSpec` and are matched against each file's path relative to its repo
root.

Pattern syntax mirrors git pathspec glob magic closely enough that one
pattern behaves the same whether it reaches the mirror lane (which
delegates to `git ls-files :(exclude,glob)<pattern>`) or the graph lane
(this module): `*` and `?` do not cross a `/` boundary, `**` does, and a
pattern with no `/` at all matches only a path that equals it exactly —
bare `fixtures` matches a file or directory named `fixtures` at the repo
root, not `pkg/tests/fixtures/x.py`; write `**/fixtures/**` for that.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".worktrees",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".nox",
    }
)


@dataclass(frozen=True, slots=True)
class IgnoreSpec:
    """Compiled `ignore:` patterns, ready to match repo-relative paths."""

    _compiled: tuple[re.Pattern[str], ...]

    def matches(self, rel_path: str) -> bool:
        return any(pattern.fullmatch(rel_path) for pattern in self._compiled)


def compile_ignore(patterns: Iterable[str]) -> IgnoreSpec:
    """Compile `ignore:` glob patterns into an `IgnoreSpec`.

    Translates each pattern to a `re.Pattern` once, so `IgnoreSpec.matches`
    is a per-file regex check rather than a per-file re-parse.
    """
    return IgnoreSpec(tuple(re.compile(_translate(pattern)) for pattern in patterns))


def _translate(pattern: str) -> str:
    """Translate one pathspec-glob pattern to a `re.fullmatch`-ready regex.

    Works path-component at a time (split on `/`) so a `**` component can
    absorb its adjoining slash as optional: `**/fixtures/**` must match
    bare `fixtures` (no path before or after it) as well as `fixtures`
    buried under an arbitrarily deep prefix and suffix. A leading `**/`
    becomes `(?:.*/)?`, a trailing `/**` becomes `(?:/.*)?`, a `**` that is
    the whole pattern becomes `.*`, and a `**` sandwiched between two other
    components becomes `(?:.*/)?` with an explicit separator in front of
    it. Within a component, `*`/`?` become `/`-bounded regex fragments so
    `src/*/tests` cannot match `src/a/b/tests` the way stdlib `fnmatch`
    would (it has no path-boundary concept).
    """
    segments = pattern.split("/")
    last_idx = len(segments) - 1
    out: list[str] = []
    prev_baked_slash = False
    for i, seg in enumerate(segments):
        if seg == "**":
            if i == 0 and i == last_idx:
                out.append(".*")
                prev_baked_slash = False
            elif i == last_idx:
                out.append("(?:/.*)?")
                prev_baked_slash = False
            else:
                if i != 0 and not prev_baked_slash:
                    out.append("/")
                out.append("(?:.*/)?")
                prev_baked_slash = True
        else:
            if i != 0 and not prev_baked_slash:
                out.append("/")
            out.append(_translate_segment(seg))
            prev_baked_slash = False
    return "".join(out)


def _translate_segment(segment: str) -> str:
    """Translate one `/`-free path component of a pattern to regex.

    `*` becomes `[^/]*`, `?` becomes `[^/]`, and a `[seq]` bracket
    expression rides through with only its leading `!` remapped to `^` —
    the rest of glob and regex character-class syntax already agree. A
    literal `**` glued inside a component (rather than standing alone
    between slashes) falls back to `.*`, matching the char-by-char
    semantics `_translate` uses at the whole-pattern level.
    """
    out: list[str] = []
    i, n = 0, len(segment)
    while i < n:
        char = segment[i]
        if char == "*":
            if i + 1 < n and segment[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        elif char == "[":
            end = _bracket_end(segment, i)
            if end is None:
                out.append(re.escape(char))
                i += 1
            else:
                frag = segment[i : end + 1]
                if frag[1] == "!":
                    frag = "[^" + frag[2:]
                out.append(frag)
                i = end + 1
        else:
            out.append(re.escape(char))
            i += 1
    return "".join(out)


def _bracket_end(pattern: str, start: int) -> int | None:
    """Index of the `]` closing the bracket expression opened at `start`,
    or `None` if it is never closed (an unterminated `[` is then treated as
    a literal character rather than an open class)."""
    n = len(pattern)
    j = start + 1
    if j < n and pattern[j] == "!":
        j += 1
    if j < n and pattern[j] == "]":
        j += 1
    while j < n and pattern[j] != "]":
        j += 1
    return j if j < n else None


def should_skip(rel_path: str, skip_dirs: frozenset[str], ignore: IgnoreSpec | None = None) -> bool:
    if any(part in skip_dirs for part in Path(rel_path).parts):
        return True
    return ignore is not None and ignore.matches(rel_path)
