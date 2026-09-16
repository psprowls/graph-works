from __future__ import annotations

import pytest
from test_checkpoints import VALID
from work_helpers import make_item
from work_tracker_okf.holds import HoldRefusal, check_hold, current_phase, prepare_checkpoint

PATH = "work/feature-a"


def _check(item=None, **overrides):
    kwargs = {"status": "open", "hold": "skip", "phase": "execute", "affects": (PATH,), "has_checkpoint": False}
    kwargs.update(overrides)
    return check_hold(item or make_item(PATH, phase="execute", work_status="in-progress"), **kwargs)


def test_a_well_formed_skip_and_park_pass() -> None:
    assert _check() is None
    assert _check(hold="park", has_checkpoint=True) is None


def test_current_phase_defaults_to_entry() -> None:
    assert current_phase(make_item(PATH, phase=None)) == "entry"


@pytest.mark.parametrize(
    ("overrides", "refusal"),
    [
        ({"status": "answered"}, "hold-status"),
        ({"affects": ("work/other",)}, "hold-affects"),
        ({"affects": (PATH, "work/other")}, "hold-affects"),
        ({"phase": None}, "hold-phase-mismatch"),
        ({"phase": "plan"}, "hold-phase-mismatch"),
        ({"hold": "park", "has_checkpoint": False}, "hold-checkpoint"),
        ({"hold": "skip", "has_checkpoint": True}, "hold-checkpoint"),
    ],
)
def test_each_refusal(overrides, refusal) -> None:
    result = _check(**overrides)
    assert isinstance(result, HoldRefusal) and result.refusal == refusal


def test_phase_mismatch_names_the_current_phase() -> None:
    result = _check(phase="plan")
    assert result is not None and "execute" in result.detail


@pytest.mark.parametrize("state", [{"work_status": "resolved", "phase": "done"}, {"work_status": "wontfix"}])
def test_terminal_items_refuse(state) -> None:
    item = make_item(PATH, **{"phase": "execute", **state})
    assert _check(item, phase=item.phase).refusal == "hold-terminal"


def test_skip_at_entry_passes_and_park_at_entry_refuses() -> None:
    item = make_item(PATH, phase=None, work_status="open")
    assert _check(item, phase="entry") is None
    refused = _check(item, phase="entry", hold="park", has_checkpoint=True)
    assert refused is not None and refused.refusal == "hold-phase-mismatch"


def test_prepare_checkpoint_stamps_and_validates() -> None:
    item_path = "work/epic-a/children/feature-b"
    stamped, refusal = prepare_checkpoint(VALID, item_path=item_path, phase="execute", decision_id="D-003")
    assert refusal is None and "decision: D-003" in stamped


def test_prepare_checkpoint_refuses_a_disagreeing_draft() -> None:
    _, refusal = prepare_checkpoint(
        VALID, item_path="work/epic-a/children/feature-b", phase="plan", decision_id="D-003"
    )
    assert refusal is not None and refusal.refusal == "checkpoint-invalid" and "phase" in refusal.detail
