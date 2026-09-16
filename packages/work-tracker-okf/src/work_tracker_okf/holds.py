"""Filing a hold: the pure checks `gw work decision add --hold` runs.

Core runs these against a projection re-read under the decision owner's lock,
so no advance can interleave between check and write. Every refusal is data;
an unknown shape is a caller error and is rejected by core before this runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from work_tracker_okf import checkpoints
from work_tracker_okf.decisions import DecisionRefusal
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import TERMINAL_STATUSES


@dataclass(frozen=True, slots=True)
class HoldRefusal:
    refusal: DecisionRefusal
    detail: str


def current_phase(item: WorkItem) -> str:
    return item.phase or "entry"


def check_hold(
    item: WorkItem,
    *,
    status: str,
    hold: str,
    phase: str | None,
    affects: Sequence[str],
    has_checkpoint: bool,
) -> HoldRefusal | None:
    if status != "open":
        return HoldRefusal("hold-status", f"a hold must be filed open, not {status!r}")
    if tuple(affects) != (item.path,):
        return HoldRefusal("hold-affects", f"a hold names exactly its own item {item.path!r}; got {list(affects)}")
    if item.work_status in TERMINAL_STATUSES or item.phase == "done":
        return HoldRefusal(
            "hold-terminal", f"{item.path} is {item.work_status} at phase {item.phase!r}; nothing to hold"
        )
    current = current_phase(item)
    if phase != current:
        return HoldRefusal(
            "hold-phase-mismatch", f"{item.path} is at phase {current!r}, not {phase!r}; re-file deliberately"
        )
    if hold == "park" and current == "entry":
        return HoldRefusal("hold-phase-mismatch", f"{item.path} has no running stage to park (entry); file a skip")
    if (hold == "park") != has_checkpoint:
        wanted = "a park requires --checkpoint" if hold == "park" else "a skip takes no --checkpoint"
        return HoldRefusal("hold-checkpoint", wanted)
    return None


def prepare_checkpoint(draft: str, *, item_path: str, phase: str, decision_id: str) -> tuple[str, HoldRefusal | None]:
    stamped = checkpoints.stamp(draft, decision_id)
    problems = checkpoints.validate(
        checkpoints.parse(stamped), item_path=item_path, phase=phase, decision_id=decision_id
    )
    if problems:
        return stamped, HoldRefusal("checkpoint-invalid", "; ".join(problems))
    return stamped, None


__all__ = ["HoldRefusal", "check_hold", "current_phase", "prepare_checkpoint"]
