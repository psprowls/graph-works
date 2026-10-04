"""`carried_context` — the named-slot frame `gw work next` carries into a stage brief.

Stage-start context that is not guidance: facts a stage needs because of what
happened since the item's last stage (what landed since the design, what the
finish must still honour). Each fact has a named producer slot in a closed
registry; `run_next` assembles the frame and the wire next-payload projection
projects it. Decisions: the item's ledger, D-001 (here, not work-tracker-okf),
D-002 (inline JSON), D-003 (pre-rendered `lines`; the skill never names a
slot), D-004 (usable dispatch only, phase-filtered, fail-soft, no routing
effect, no budget).

**Read-only.** Nothing here writes. A producer that raises `OSError` or
`ValueError` degrades to one slot warning, mirroring `_with_guidance`; any
other exception is a programming error and propagates.

It is a sibling of `commands.py` for the reason `reconcile.py` gives: a new
vertical would edit both import-linter contracts, and appending would grow a
module that already claims another concern.

The producer children (`feature-spec-baseline-and-sibling-context`,
`feature-finish-obligations-and-caveats`, `feature-epic-brief`) edit only their producer body here
(or a sibling module it calls) and its tests; a new input on `SlotInput` is a
frame change recorded in this item's ledger.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from okf_io import Bundle
from work_tracker_okf.affects import code_affects
from work_tracker_okf.items import WorkItem
from work_tracker_okf.pipeline import DESIGN, EXECUTE, PLAN

from graph_works_core.work.epic_brief import epic_brief
from graph_works_core.workspace import provenance
from graph_works_core.workspace.landed import landed_since
from graph_works_core.workspace.layout import WorkspaceLayout

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class SlotInput:
    """What every producer is handed: the workspace, the bundle `run_next`
    loaded, every item, the selected leaf and the dispatched stage."""

    layout: WorkspaceLayout
    bundle: Bundle
    items: tuple[WorkItem, ...]
    item: WorkItem
    stage: str


def _no_data() -> Mapping[str, JsonValue]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class SlotFill:
    """One producer's output. `lines` are markdown, rendered verbatim by the
    skill; `data` is the typed payload for machine readers, kept
    `json.dumps`-able by its producer."""

    lines: tuple[str, ...] = ()
    data: Mapping[str, JsonValue] = field(default_factory=_no_data)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Slot:
    """A registry entry: stable snake_case JSON key, `###` heading, the stages it applies to."""

    name: str
    title: str
    phases: frozenset[str]
    produce: Callable[[SlotInput], SlotFill]


@dataclass(frozen=True, slots=True)
class FilledSlot:
    name: str
    title: str
    fill: SlotFill


@dataclass(frozen=True, slots=True)
class CarriedContext:
    """The frame: filled slots in registry order, plus frame-level warnings (reserved; empty today)."""

    slots: tuple[FilledSlot, ...] = ()
    warnings: tuple[str, ...] = ()


_COMMIT_CAP = 20


def _short(sha: str | None) -> str:
    return sha[:12] if sha is not None else "none"


def _landed_since(inp: SlotInput) -> SlotFill:
    """Render baseline evidence for `feature-spec-baseline-and-sibling-context`."""
    result = landed_since(inp.layout, inp.items, inp.item)
    if result.code_baseline is None:
        return SlotFill(
            lines=("No spec baseline recorded; landed-since unavailable.",),
            data=MappingProxyType({"code_baseline": None, "workspace_baseline": result.workspace_baseline}),
            warnings=result.warnings,
        )
    lines = [f"Spec baseline: code `{_short(result.code_baseline)}`, workspace `{_short(result.workspace_baseline)}`."]
    if result.entries:
        lines.extend(
            f"- `{e.sibling.path}` resolved in `{_short(e.sibling.resolved_in)}` — "
            f"affects overlap: {'yes' if e.overlaps else 'no'}"
            for e in result.entries
        )
    else:
        lines.append("No sibling has landed since the baseline.")
    touched = tuple(
        sorted({*code_affects(inp.item.affects), *(p for e in result.entries for p in code_affects(e.sibling.affects))})
    )
    commits: tuple[tuple[str, str], ...] = ()
    diff_command: str | None = None
    if result.repo is not None and touched:
        commit_range = f"{result.code_baseline}..HEAD"
        commits = provenance.commits_touching(result.repo, commit_range, touched)
        diff_command = f"git diff {commit_range} -- {' '.join(touched)}"
    lines.append(f"Commits since baseline touching affects: {len(commits)}")
    lines.extend(f"- `{_short(sha)}` {subject}" for sha, subject in commits[:_COMMIT_CAP])
    if len(commits) > _COMMIT_CAP:
        lines.append(f"- … and {len(commits) - _COMMIT_CAP} more")
    if diff_command is not None:
        lines.append(f"Diff: `{diff_command}`")
    workspace_commits: int | None = None
    if result.workspace_baseline is not None:
        out = provenance.run_git(inp.layout.root, "rev-list", "--count", f"{result.workspace_baseline}..HEAD")
        if out is not None and out.strip().isdigit():
            workspace_commits = int(out.strip())
            lines.append(
                f"Workspace: {workspace_commits} commits since `{_short(result.workspace_baseline)}` "
                f"(`git -C {inp.layout.root} log --oneline {result.workspace_baseline}..HEAD`)."
            )
    data: dict[str, JsonValue] = {
        "code_baseline": result.code_baseline,
        "workspace_baseline": result.workspace_baseline,
        "siblings": [
            {"path": e.sibling.path, "resolved_in": e.sibling.resolved_in, "overlaps": e.overlaps}
            for e in result.entries
        ],
        "commits": [{"sha": sha, "subject": subject} for sha, subject in commits],
        "diff_command": diff_command,
        "workspace_commits": workspace_commits,
    }
    return SlotFill(lines=tuple(lines), data=MappingProxyType(data), warnings=result.warnings)


def _finish_obligations(inp: SlotInput) -> SlotFill:
    """Owned by `feature-finish-obligations-and-caveats` (D-003): list, never discharge."""
    item = inp.item
    warnings = (
        ("finish_obligations: malformed entries ignored",)
        if "finish_obligations" in item.invalid_optional_fields
        else ()
    )
    if not item.finish_obligations:
        return SlotFill(warnings=warnings)
    obligations: list[JsonValue] = []
    for entry in item.finish_obligations:
        obligations.append({"text": entry.text, "origin": entry.origin, "recorded": entry.recorded})
    return SlotFill(
        lines=tuple(
            f"- [{entry.origin}] {entry.text} (recorded {entry.recorded})" for entry in item.finish_obligations
        ),
        data=MappingProxyType({"obligations": obligations}),
        warnings=warnings,
    )


def _epic_brief(inp: SlotInput) -> SlotFill:
    """Owned by `feature-epic-brief` (its ledger D-001): see `work/epic_brief.py`."""
    brief = epic_brief(inp.layout, inp.items, inp.item)
    return SlotFill(lines=brief.lines, data=MappingProxyType(dict(brief.data)), warnings=brief.warnings)


SLOTS: Final[tuple[Slot, ...]] = (
    Slot("epic_brief", "Epic brief", frozenset({DESIGN, PLAN, EXECUTE}), _epic_brief),
    Slot("landed_since", "Landed since your design", frozenset({"plan"}), _landed_since),
    Slot("finish_obligations", "Finish obligations", frozenset({"finish"}), _finish_obligations),
)


def assemble_carried(inp: SlotInput, *, slots: Sequence[Slot] = SLOTS) -> CarriedContext:
    """Run, in registry order, every slot whose `phases` contains `inp.stage`.

    `slots=` is the test seam; there is no runtime registration. A caller that
    wants a monkeypatched registry must pass `slots=carried.SLOTS` itself --
    the default is bound when this function is defined.
    """
    filled: list[FilledSlot] = []
    for slot in slots:
        if inp.stage not in slot.phases:
            continue
        try:
            fill = slot.produce(inp)
        except (OSError, ValueError) as exc:
            fill = SlotFill(warnings=(f"{slot.name}: unavailable: {exc}",))
        filled.append(FilledSlot(slot.name, slot.title, fill))
    return CarriedContext(tuple(filled))
