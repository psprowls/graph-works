"""The park checkpoint artifact: parse, validate, stamp. Pure.

A park preserves where a stopped stage was, in a file resume may rely on.
This module states that contract and nothing more: revalidating branch, base,
and head against live git belongs above this git-free package.

``parse`` never raises for content reasons. Problems land in
``Checkpoint.problems``; ``validate`` adds identity checks that need caller
context.
"""

from __future__ import annotations

import importlib.resources
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from okf_io import parse as parse_document

REQUIRED_KEYS: tuple[str, ...] = (
    "title",
    "item",
    "decision",
    "phase",
    "dispatch_key",
    "branch",
    "worktree",
    "base",
    "head",
    "created",
)
REQUIRED_SECTIONS: tuple[str, ...] = (
    "Completed work",
    "Remaining actions",
    "Question",
    "Placement",
    "Validation evidence",
)
PENDING = "pending"

_CREATED_PLACEHOLDER = "<ISO-8601 instant supplied by the writer>"
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_INSTANT_RE = re.compile(r".+T.+(?:Z|[+-]\d{2}(?::?\d{2})?)")


@dataclass(frozen=True, slots=True)
class Checkpoint:
    frontmatter: Mapping[str, str]
    sections: tuple[tuple[str, str], ...]
    problems: tuple[str, ...]


def _sections(body: str) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, list[str]]] = []
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match is not None:
            found.append((match.group(1), []))
        elif found:
            found[-1][1].append(line)
    return tuple((heading, "\n".join(lines).strip()) for heading, lines in found)


def _render(value: Any) -> str:  # noqa: ANN401 -- raw YAML value of unknown shape
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _is_instant(value: Any) -> bool:  # noqa: ANN401 -- raw YAML value of unknown shape
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        if value == _CREATED_PLACEHOLDER:
            return True
        if _INSTANT_RE.fullmatch(value) is None:
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def parse(text: str) -> Checkpoint:
    """Parse a checkpoint and retain every content failure as a problem."""
    document = parse_document(text)
    problems: list[str] = []
    frontmatter: dict[str, str] = {}
    native_data: dict[str, Any] = {}
    if document.parse_error is not None:
        problems.append(f"frontmatter unparseable: {document.parse_error.message}")
    else:
        native_data = document.fm_data(dates="native")
        frontmatter = {key: _render(value) for key, value in native_data.items() if value is not None}
    for key in REQUIRED_KEYS:
        if not frontmatter.get(key, "").strip():
            problems.append(f"frontmatter key {key!r} missing or empty")
    created = native_data.get("created")
    if "created" in native_data and not _is_instant(created):
        problems.append("frontmatter key 'created' must be an ISO-8601 instant")

    sections = _sections(document.body)
    by_heading = dict(sections)
    for heading in REQUIRED_SECTIONS:
        if heading not in by_heading:
            problems.append(f"section '## {heading}' missing")
        elif not by_heading[heading]:
            problems.append(f"section '## {heading}' is empty")
    present = [heading for heading, _ in sections if heading in REQUIRED_SECTIONS]
    if present != sorted(present, key=REQUIRED_SECTIONS.index) or len(set(present)) != len(present):
        problems.append(f"required sections out of order: expected {list(REQUIRED_SECTIONS)}")
    return Checkpoint(MappingProxyType(frontmatter), sections, tuple(problems))


def validate(
    checkpoint: Checkpoint,
    *,
    item_path: str,
    phase: str,
    decision_id: str,
) -> tuple[str, ...]:
    """Return parse and caller-supplied identity problems."""
    problems = list(checkpoint.problems)
    fm = checkpoint.frontmatter
    if fm.get("item") != item_path:
        problems.append(f"item {fm.get('item')!r} is not {item_path!r}")
    if fm.get("phase") != phase:
        problems.append(f"phase {fm.get('phase')!r} is not {phase!r}")
    if fm.get("decision") != decision_id:
        problems.append(f"decision {fm.get('decision')!r} is not {decision_id!r}")
    if fm.get("created") == _CREATED_PLACEHOLDER:
        problems.append("frontmatter key 'created' must be an ISO-8601 instant")
    return tuple(problems)


def stamp(text: str, decision_id: str) -> str:
    """Replace ``decision: pending`` while preserving every other byte."""
    document = parse_document(text)
    if document.parse_error is not None or document.fm_data(dates="iso").get("decision") != PENDING:
        return text
    document.set("decision", decision_id)
    return document.serialize()


def template() -> str:
    """Return the packaged checkpoint draft template."""
    return (importlib.resources.files("work_tracker_okf") / "assets" / "checkpoint.md").read_text(encoding="utf-8")


__all__ = [
    "PENDING",
    "REQUIRED_KEYS",
    "REQUIRED_SECTIONS",
    "Checkpoint",
    "parse",
    "stamp",
    "template",
    "validate",
]
