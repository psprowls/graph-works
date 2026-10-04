"""`NOTIFY_LINE` + `WAIT_LINE` are mirrored verbatim in the plugin's riders and finishing-relay.

The tail reaches supervised workers through `_prompt`; attended finish sessions and the
relay read these skill docs instead, so a copy that drifts from the constant silently
teaches a different wait. `{path}` renders as `<work-path>` in the docs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from graph_works_core.workspace.pipeline import NOTIFY_LINE, WAIT_LINE

_RIDERS = Path("plugins/gw/skills/workflow/references/brief-riders.md")
_RELAY = Path("plugins/gw/skills/finishing-relay/SKILL.md")
RENDERED = WAIT_LINE.replace("{path}", "<work-path>")
NOTIFIED = NOTIFY_LINE.replace("{path}", "<work-path>")
BOTH = f"{NOTIFIED} {RENDERED}"


def _find(relative: Path) -> Path | None:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / relative
        if candidate.is_file():
            return candidate
    return None


def _sections(text: str, heading: str) -> dict[str, str]:
    parts = re.split(rf"^{re.escape(heading)}", text, flags=re.MULTILINE)
    return {part.split("\n", 1)[0].strip(): part for part in parts[1:]}


@pytest.mark.parametrize(
    "rider", ["subagent-driven-development", "test-driven-development", "finishing-a-development-branch"]
)
def test_each_rider_carries_the_wait_line(rider: str) -> None:
    path = _find(_RIDERS)
    if path is None:
        pytest.skip(f"{_RIDERS} is not present in this checkout")
    section = _sections(path.read_text(encoding="utf-8"), "## Rider: ")[rider]
    assert BOTH in section


def test_the_sdd_and_tdd_waiting_paragraphs_are_identical() -> None:
    path = _find(_RIDERS)
    if path is None:
        pytest.skip(f"{_RIDERS} is not present in this checkout")
    sections = _sections(path.read_text(encoding="utf-8"), "## Rider: ")
    line = f"> **Waiting.** {BOTH}"
    assert line in sections["subagent-driven-development"].splitlines()
    assert line in sections["test-driven-development"].splitlines()


@pytest.mark.parametrize("prefix", ["R1 ", "R4 "])
def test_finishing_relay_gate_steps_carry_the_wait_line(prefix: str) -> None:
    path = _find(_RELAY)
    if path is None:
        pytest.skip(f"{_RELAY} is not present in this checkout")
    sections = _sections(path.read_text(encoding="utf-8"), "## ")
    (section,) = [body for title, body in sections.items() if title.startswith(prefix)]
    assert BOTH in section
