"""The editing guide lists the closed work vocabulary; this pins the lists to the code.

The guide (`plugins/gw/skills/workflow/references/editing-work-items.md`) is
what a model reads before hand-editing a work item, so a value it lists must
be one `gw work lint` accepts, and a value the code accepts must be listed.
Equality, not subset. Read only: the plugin tree is never written. A checkout
without the plugin tree skips rather than fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from work_tracker_okf.vocabulary import (
    BLAST_RADII,
    DOCUMENT_STATUSES,
    EFFORTS,
    PHASES,
    TERMINAL_STATUSES,
    TYPES,
    WORK_STATUSES,
)

GUIDE = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "gw"
    / "skills"
    / "workflow"
    / "references"
    / "editing-work-items.md"
)
ANCHOR = "## Accepted values"
EXPECTED: dict[str, frozenset[str]] = {
    "type": TYPES,
    "status": DOCUMENT_STATUSES,
    "work_status": WORK_STATUSES,
    "terminal work_status": TERMINAL_STATUSES,
    "phase": PHASES,
    "effort": EFFORTS,
    "blast_radius": BLAST_RADII,
}

_ENTRY = re.compile(r"^- `(?P<key>[^`]+)`: (?P<values>.+)$")
_VALUE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\]\(([^)\s]+)\)")


@pytest.fixture(scope="module")
def guide_text() -> str:
    if not GUIDE.is_file():
        pytest.skip("editing-work-items guide not present in this checkout")
    return GUIDE.read_text(encoding="utf-8")


def _accepted_values(text: str) -> list[tuple[str, frozenset[str]]]:
    lines = text.splitlines()
    assert ANCHOR in lines, f"guide has no {ANCHOR!r} section"
    entries: list[tuple[str, frozenset[str]]] = []
    for line in lines[lines.index(ANCHOR) + 1 :]:
        if line.startswith("## "):
            break
        if match := _ENTRY.match(line):
            entries.append((match["key"], frozenset(_VALUE.findall(match["values"]))))
    return entries


def test_the_guide_lists_each_vocabulary_exactly_once(guide_text: str) -> None:
    keys = [key for key, _ in _accepted_values(guide_text)]
    assert len(keys) == len(set(keys)), f"duplicate vocabulary lines: {keys}"
    assert set(keys) == set(EXPECTED)


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_each_listed_vocabulary_equals_the_code(guide_text: str, key: str) -> None:
    listed = dict(_accepted_values(guide_text))
    assert listed.get(key) == EXPECTED[key]


def test_every_relative_link_in_the_guide_resolves(guide_text: str) -> None:
    missing = []
    for target in _LINK.findall(guide_text):
        if target.startswith(("http://", "https://", "#")):
            continue
        if not (GUIDE.parent / target.split("#", 1)[0]).exists():
            missing.append(target)
    assert missing == []
