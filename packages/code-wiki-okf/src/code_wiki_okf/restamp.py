"""Rewrite identity keys on a page that already moved."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from okf_io import parse


def restamp_identity(bundle_root: Path, member: str, values: Mapping[str, object]) -> None:
    """Set each of *values* on the page at *member*; rewrite it only when something changed.

    A missing page (a move that failed to write) is skipped. Every write goes through
    `Document.set`, never hand-built YAML.
    """
    target = bundle_root / member
    if not target.is_file():
        return
    with target.open(encoding="utf-8", newline="") as handle:
        original = handle.read()
    document = parse(original)
    for key, value in values.items():
        document.set(key, value)
    rendered = document.serialize()
    if rendered != original:
        target.write_text(rendered, encoding="utf-8", newline="")
