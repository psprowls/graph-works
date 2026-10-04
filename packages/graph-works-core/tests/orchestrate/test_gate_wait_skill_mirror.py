"""`GATE_WAIT_STATES` <-> auto-drive §2.7's gate-wait rule.

The coordinator branches on `liveness[].gate_wait.state`; a state the skill does not
name would fall through to the manual probe and nudge a parked worker.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from graph_works_core.orchestrate.gate import GATE_WAIT_STATES

_SKILL = Path("plugins/gw/skills/auto-drive/SKILL.md")


def _section() -> str:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / _SKILL
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
            match = re.search(r"^### 2\.7 Wait\n(.*?)^## ", text, flags=re.MULTILINE | re.DOTALL)
            assert match is not None, "§2.7 not found"
            return match.group(1)
    pytest.skip(f"{_SKILL} is not present in this checkout")


@pytest.mark.parametrize("state", GATE_WAIT_STATES)
def test_every_gate_wait_state_is_named_in_2_7(state):
    assert f"`{state}`" in _section()


def test_2_7_types_the_resume_line_and_never_probes_a_parked_worker():
    section = _section()
    assert "`gate_wait`" in section and "waiting on gate <run_id>" in section
    assert '--text "<gate_wait.resume_line>" --enter --json' in section
    assert section.index("`gate_wait`") < section.index("Manual ordered probe")
