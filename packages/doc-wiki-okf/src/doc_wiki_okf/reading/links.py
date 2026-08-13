"""Markdown link targets, and which of them are companion files."""

from __future__ import annotations

import re
from pathlib import Path

# Inline markdown link target: captures the URL token after `](` up to the
# first whitespace or `)`. Optional `<...>` angle-bracket form is captured too.
_MD_INLINE_LINK_RE = re.compile(r"\]\(\s*(<[^>]+>|[^)\s]+)")
# Reference-style definition `[id]: target` at line start (≤3 leading spaces).
_MD_REF_DEF_RE = re.compile(r"^[ ]{0,3}\[[^\]]+\]:\s*(\S+)", re.MULTILINE)


def iter_link_targets(content: str) -> list[str]:
    """Return markdown link targets in appearance order (inline + reference defs)."""
    matches: list[tuple[int, str]] = []
    for m in _MD_INLINE_LINK_RE.finditer(content):
        matches.append((m.start(), m.group(1)))
    for m in _MD_REF_DEF_RE.finditer(content):
        matches.append((m.start(), m.group(1)))
    matches.sort(key=lambda pair: pair[0])
    return [raw.strip().strip("<>") for _, raw in matches]


def resolve_companion(target: str, linking_dir: Path, skill_dir: Path) -> Path | None:
    """Resolve a link target to a companion .md inside skill_dir, or None.

    Keeps only targets that: end in `.md`; are not http(s)/mailto URLs or pure
    `#anchor`; resolve (relative to the linking file's dir) to an existing file;
    and stay inside `skill_dir` (no `../` escape). `skill_dir` must be resolved.
    """
    cleaned = target.split("#", 1)[0].strip()  # strip any #fragment
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if lowered.startswith(("http://", "https://", "mailto:")):
        return None
    if not cleaned.endswith(".md"):
        return None
    candidate = (linking_dir / cleaned).resolve()
    if not candidate.is_file():
        return None
    if not candidate.is_relative_to(skill_dir):
        return None
    return candidate
