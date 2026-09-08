"""`BLOCKED_KINDS` <-> the auto-drive coordinator skill's own vocabulary.

`BLOCKED_KINDS` is a closed vocabulary the auto-drive coordinator skill
(`plugins/gw/skills/auto-drive/SKILL.md`) branches on. The prose
contract has already drifted once (`relay-untailed` and
`worktree-unsupported` went missing until they were re-synced), so this test
makes the next drift a gate failure instead of something a human has to
notice by hand.

This only reads the plugin tree -- never writes to it. A test asserting a
mirror reads the skill; keeping the two in sync is an edit to the skill,
made deliberately, not a side effect of running the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from graph_works_core.orchestrate.commands import BLOCKED_KINDS

_SKILL_RELATIVE_PATH = Path("plugins/gw/skills/auto-drive/SKILL.md")


def _find_skill_md() -> Path | None:
    """Walk up from this file looking for the `gw` plugin's SKILL.md.

    Not every consumer of this package ships the plugin tree, so absence is
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
        pytest.skip("plugins/gw/skills/auto-drive/SKILL.md is not present in this checkout")

    text = skill_path.read_text(encoding="utf-8")

    section_match = re.search(r"### 2\.2 Plan\n(.*?)\n### 2\.3", text, re.DOTALL)
    assert section_match is not None, "§2.2 Plan section not found in SKILL.md"
    section = section_match.group(1)

    blocked_match = re.search(r"`blocked\[\]` — each: `path`, `kind` \(one of exactly ([^)]*)\)", section)
    assert blocked_match is not None, "§2.2 does not describe blocked[].kind's vocabulary"
    listed_kinds = {token.strip(" \n`") for token in blocked_match.group(1).split(",")}

    missing = BLOCKED_KINDS - listed_kinds
    assert not missing, f"BLOCKED_KINDS member(s) missing from SKILL.md §2.2: {sorted(missing)}"


def test_section_2_5_s_two_bullets_partition_blocked_kinds() -> None:
    """§2.5 enumerates the same vocabulary a second time, as a partition.

    §2.2 describes the `blocked[].kind` field; §2.5 says what the coordinator
    *does* about each kind, and splits them into `effort-required` (ask the
    user to size the item) and "Every other kind" (print and take no action).
    The second list is therefore a complement, not a copy -- it is correct for
    it to be one member short.

    §2.2's list drifted once already and nothing caught the §2.5 pair, whose
    failure is quieter: a kind missing from *both* bullets is a kind the skill
    names nowhere in its handling section, so a coordinator reading §2.5 to
    decide what to do finds no instruction. Asserting the partition catches
    that, and catches a kind landing in both bullets with contradictory
    instructions.
    """
    skill_path = _find_skill_md()
    if skill_path is None:
        pytest.skip("plugins/gw/skills/auto-drive/SKILL.md is not present in this checkout")

    text = skill_path.read_text(encoding="utf-8")

    section_match = re.search(r"### 2\.5 Blockers\n(.*?)\n### 2\.5\.1", text, re.DOTALL)
    assert section_match is not None, "§2.5 Blockers section not found in SKILL.md"
    section = section_match.group(1)

    sized_match = re.search(r"- \*\*`([a-z-]+)`\*\*:", section)
    assert sized_match is not None, "§2.5 does not open with a single-kind bullet"
    sized = {sized_match.group(1)}

    rest_match = re.search(r"- \*\*Every other kind\*\* \(([^)]*)\)", section)
    assert rest_match is not None, "§2.5 has no 'Every other kind' bullet"
    rest = {token.strip(" \n`") for token in rest_match.group(1).split(",")}

    overlap = sized & rest
    assert not overlap, f"§2.5 kind(s) in both bullets, with conflicting instructions: {sorted(overlap)}"

    covered = sized | rest
    missing = BLOCKED_KINDS - covered
    assert not missing, f"BLOCKED_KINDS member(s) handled by neither §2.5 bullet: {sorted(missing)}"

    unknown = covered - BLOCKED_KINDS
    assert not unknown, f"§2.5 names kind(s) that are not in BLOCKED_KINDS: {sorted(unknown)}"
