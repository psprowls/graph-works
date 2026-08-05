"""Directory-skip set + .graphignore loading for graph scanning.

Single source of truth for which directories the file scanner and the
manifest scanner skip. The default set covers VCS metadata, build
output, dependency caches, and virtualenvs. Repos can extend the set
with a `.graphignore` file at the repo root — one directory name per
line, `#` comments and blank lines ignored.

Match semantics mirror the original `packages._should_skip`: any file
whose relative path contains a component equal to a skip-set entry
is dropped. Glob / anchored patterns are intentionally out of scope.
"""

from __future__ import annotations

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

GRAPHIGNORE_FILENAME = ".graphignore"


def load_skip_dirs(repo_root: Path) -> frozenset[str]:
    extras = _read_graphignore(Path(repo_root) / GRAPHIGNORE_FILENAME)
    return DEFAULT_SKIP_DIRS | extras


def should_skip(rel_path: str, skip_dirs: frozenset[str]) -> bool:
    return any(part in skip_dirs for part in Path(rel_path).parts)


def _read_graphignore(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    out: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line.rstrip("/"))
    return frozenset(out)
