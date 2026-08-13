"""What is in a directory, and what language each file is written in."""

from __future__ import annotations

from pathlib import Path

LANGUAGE_BY_EXT = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".py": "python",
    ".go": "go",
    ".rs": "rust",
    ".md": "markdown",
    ".json": "json",
}

REPRESENTATIVE_INDEX_NAMES = [
    "index.ts",
    "index.tsx",
    "index.js",
    "index.py",
    "index.go",
    "index.rs",
]


def language_for(path: Path) -> str:
    return LANGUAGE_BY_EXT.get(path.suffix.lower(), "unknown")


def list_folder_files(root: Path) -> list[tuple[str, int]]:
    """Return sorted (rel_path, size) for every regular file under root."""
    entries: list[tuple[str, int]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root)
            entries.append((str(rel).replace("\\", "/"), p.stat().st_size))
    return entries


def pick_representative(root: Path, entries: list[tuple[str, int]]) -> str | None:
    """Return rel-path of representative file.

    Priority: README.md (case-insensitive) -> index.{ts,tsx,js,py,go,rs} -> largest.
    """
    by_name_lower = {rel.lower(): rel for rel, _ in entries}
    if "readme.md" in by_name_lower:
        return by_name_lower["readme.md"]
    for cand in REPRESENTATIVE_INDEX_NAMES:
        if cand in by_name_lower:
            return by_name_lower[cand]
    if not entries:
        return None
    sorted_entries = sorted(entries, key=lambda e: (-e[1], e[0]))
    return sorted_entries[0][0]
