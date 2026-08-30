"""`BLOCKED_KINDS` <-> the auto-drive coordinator skill's own vocabulary.

`BLOCKED_KINDS` is a closed vocabulary the auto-drive coordinator skill
(`plugins/graph-works/skills/auto-drive/SKILL.md`) branches on. The prose
contract has already drifted once (`relay-untailed` and
`worktree-unsupported` went missing until they were re-synced), so this test
makes the next drift a gate failure instead of something a human has to
notice by hand.

This only reads the vendored plugin tree -- never writes to it. Editing
anything under `plugins/` outside a recorded `PATCHES.md` entry is forbidden
(see the workspace `AGENTS.md`), and a test asserting a mirror is not an
exemption from that.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from graph_works_core.orchestrate.commands import BLOCKED_KINDS

_SKILL_RELATIVE_PATH = Path("plugins/graph-works/skills/auto-drive/SKILL.md")


def _find_skill_md() -> Path | None:
    """Walk up from this file looking for the vendored plugin's SKILL.md.

    Not every consumer of this package vendors the plugin tree, so absence is
    a skip, not a failure -- but only genuine absence, found by walking all
    the way to the filesystem root.
    """
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / _SKILL_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    return None


def test_blocked_kinds_all_appear_in_the_skill_s_section_2_2_list() -> None:
    skill_path = _find_skill_md()
    if skill_path is None:
        pytest.skip("plugins/graph-works/skills/auto-drive/SKILL.md is not vendored in this checkout")

    text = skill_path.read_text(encoding="utf-8")

    section_match = re.search(r"### 2\.2 Plan\n(.*?)\n### 2\.3", text, re.DOTALL)
    assert section_match is not None, "§2.2 Plan section not found in SKILL.md"
    section = section_match.group(1)

    blocked_match = re.search(r"`blocked\[\]` — each: `path`, `kind` \(one of exactly ([^)]*)\)", section)
    assert blocked_match is not None, "§2.2 does not describe blocked[].kind's vocabulary"
    listed_kinds = {token.strip(" \n`") for token in blocked_match.group(1).split(",")}

    missing = BLOCKED_KINDS - listed_kinds
    assert not missing, f"BLOCKED_KINDS member(s) missing from SKILL.md §2.2: {sorted(missing)}"
