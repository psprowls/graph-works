"""Confinement of bundle-relative member paths."""

from __future__ import annotations

from pathlib import Path


def confined_member(root: Path, member: str) -> Path:
    """Reject absolute/traversing paths and symlink escapes before any read."""
    rel = Path(member)
    target = root / rel
    if not member or rel.is_absolute() or ".." in rel.parts or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"member outside bundle: {member!r}")
    return target
