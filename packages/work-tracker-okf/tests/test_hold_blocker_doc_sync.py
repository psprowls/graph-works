"""reconciling-spec quotes the hold blocker; this pins the quote to the code.

Rendered with the skill's own placeholders (`<work-path>`, `D-nnn`) for a
design-stage question hold -- exactly what a reconciling-spec hold produces.
Whitespace is normalized because the skill wraps prose; nothing else is.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.workflow import hold_blocker, hold_reason

SKILL = Path(__file__).resolve().parents[3] / "plugins" / "gw" / "skills" / "reconciling-spec" / "SKILL.md"


def _normalized(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="module")
def skill_text() -> str:
    if not SKILL.is_file():
        pytest.skip("reconciling-spec skill not present in this checkout")
    return _normalized(SKILL.read_text(encoding="utf-8"))


def test_the_skill_quotes_the_rendered_blocker_and_reason(skill_text: str) -> None:
    hold = HoldFact(path="<work-path>", decision_id="D-nnn", shape="question", phase=None)
    assert _normalized(hold_blocker(hold)) in skill_text
    assert _normalized(hold_reason(hold, "design")) in skill_text


def test_the_retired_claims_are_gone(skill_text: str) -> None:
    assert "design-stage gate" not in skill_text
    assert "open decision(s) block re-dispatch" not in skill_text


def test_hold_blocker_and_reason_render_correctly_for_a_park_shape():
    hold = HoldFact(path="<work-path>", decision_id="D-nnn", shape="park", phase="finish")
    assert "(park)" in hold_blocker(hold)
    assert "answer via" in hold_blocker(hold)
    assert "(park at finish)" in hold_reason(hold, "finish")


def test_hold_blocker_and_reason_render_correctly_for_a_skip_shape():
    hold = HoldFact(path="<work-path>", decision_id="D-nnn", shape="skip", phase="entry")
    assert "(skip)" in hold_blocker(hold)
    assert "(skip at entry)" in hold_reason(hold, "entry")
