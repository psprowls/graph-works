"""Packaged dispatch defaults and the shared skill-name validator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from graph_works_core.workspace.errors import WorkspaceError

#: The vendor-neutral tail an `attend` worker carries. It is a packaged default
#: rather than config because it names no vendor: it tells a human-in-the-room
#: worker to ask its questions the ordinary way. The `relay` tail is the
#: opposite case -- it is a value the workspace owns -- so it stays `None` in
#: the packaged table and is seeded into a new workspace's dispatch file instead
#: (`RELAY_TAIL_SEED`, below).
_ATTEND_BASE = "The user may join this session to answer this stage's questions; ask normally."

#: Shared by every dispatch mode whose worker may block on an
#: `orca orchestration ask` a human might not answer before its grace period
#: (design item bug-relayed-answer-arrives-after-ask-timeout, D-004). One
#: constant so `ATTEND_TAIL` and `RELAY_TAIL_SEED` can never drift apart on
#: what "giving up" means -- both fold it in below rather than duplicating it.
#:
#: The protocol doc is named **plugin-relatively, never by filesystem path**.
#: It ships inside the `gw` Claude Code plugin, not inside the workspace and
#: not inside the repository the workspace catalogs, so neither a bare
#: repo-relative literal nor a `{workspace}`-substituted one (the shape
#: `EXECUTE_TAIL` uses for an artifact that *does* live under the workspace)
#: can locate it: a workspace cataloguing any other codebase would hand its
#: workers a path that does not exist there. Every dispatched worker already
#: runs a `gw` plugin skill, so the skill-qualified name resolves wherever the
#: plugin is installed.
GRACE_PERIOD_TAIL = (
    "If an `orca orchestration ask` question of yours times out, follow the "
    "grace-period protocol in the `gw` plugin's `auto-drive` skill -- its "
    "`references/grace-period-protocol.md`, which you read through that "
    "installed skill rather than by repository path -- before giving up on "
    "it. Never end this Dispatch (no `worker_done`, and never call "
    "`worker-stop` or `worker-abandon` yourself -- only the coordinator may) "
    "while a question you asked is still unanswered."
)

ATTEND_TAIL = f"{_ATTEND_BASE}\n{GRACE_PERIOD_TAIL}"

#: The obligation both execute variants carry. A packaged default rather than
#: config for `ATTEND_TAIL`'s reason -- it names no vendor. It is *reported,
#: not enforced*: the file is surfaced to a human by `auto-drive` §4.1 and
#: registered into `sources[]` by the advance, and no transition is gated on
#: its contents. A gate that read a worker's self-report would be trusting the
#: exact judgement the defect this tail exists for shows a model getting wrong.
EXECUTE_TAIL = (
    "Before you advance, write {workspace}/okf/{path}/references/03-execute-coverage.md: "
    "one markdown task-list line per item in this stage's design spec `## Acceptance` section, "
    "each line `- [x]` when delivered or `- [ ]` when not, each with a one-line justification. "
    "Where the spec has no `## Acceptance` section, enumerate its `## Scope` / "
    "`## What this design changes` headings instead and say in the file that you did. "
    "Mark honestly -- an unchecked box is a normal, expected outcome; an inaccurate checked box "
    "is not. Pass that file's path as --report-path on your worker_done."
)

#: The `relay` tail a **new** workspace is seeded with, written into its
#: dispatch file by `init.plan_init` rather than shipped in `PACKAGED_PIPELINE`.
#: The tail is a value the workspace owns; core seeds it and a workspace
#: replaces it with its own vendor wording in its dispatch file.
#:
#: Both halves are load-bearing and neither may be dropped: the relay skill
#: triggers on the literal `Auto-drive context:` prefix, and reads the merge
#: target verbatim out of the same line. A tail carrying one without the other
#: arms half the seam. The grace-period line is a third, independent
#: obligation appended after both -- finishing-relay's R2 only ever reads the
#: first line, so appending here cannot disturb it.
#:
#: **Seeding is `init`-only, and there is no migration.** `init.plan_init`
#: writes this value into a *missing* shared dispatch document and preserves an
#: authored one, so a workspace that already exists keeps whatever relay tail
#: its `dispatch.yaml` was written with -- the grace-period obligation above is
#: inert for it. An operator upgrading an existing workspace must append
#: `GRACE_PERIOD_TAIL`'s text to that file's `branch`-variant `prompt_tail`
#: by hand and run `gw config sync`; nothing here does it for them. Building a
#: rewriter is deliberately out of scope: the tail is a workspace-owned value,
#: and core does not edit values a workspace owns.
RELAY_TAIL_SEED = (
    "Auto-drive context: relay the merge/PR/hold/discard decision to your coordinator "
    "rather than asking interactively; merge target is {merge_target}.\n"
    f"{GRACE_PERIOD_TAIL}"
)


@dataclass(frozen=True, slots=True)
class PipelineEntry:
    """What one variant dispatches to."""

    skill: str
    mode: str
    prompt_tail: str | None = None


#: The packaged defaults read by the dispatch resolver; never mutated.
PACKAGED_PIPELINE: Mapping[str, PipelineEntry] = MappingProxyType(
    {
        "exploration": PipelineEntry("superpowers:brainstorming", "attend", ATTEND_TAIL),
        "diagnosis": PipelineEntry("superpowers:systematic-debugging", "attend", ATTEND_TAIL),
        "reconcile": PipelineEntry("gw:reconciling-spec", "autonomous"),
        "epic-design": PipelineEntry("gw:epic-design", "attend", ATTEND_TAIL),
        "decompose": PipelineEntry("gw:planning-epics", "autonomous"),
        "single": PipelineEntry("superpowers:writing-plans", "autonomous"),
        "planned": PipelineEntry("superpowers:subagent-driven-development", "autonomous", EXECUTE_TAIL),
        "unplanned": PipelineEntry("superpowers:test-driven-development", "autonomous", EXECUTE_TAIL),
        "branch": PipelineEntry("superpowers:finishing-a-development-branch", "relay"),
    }
)


def is_valid_skill_name(value: str) -> bool:
    """Whether *value* is a well-formed stage skill name.

    A **bare** name -- no colon -- is valid and is used verbatim: user-level and
    repo-local skills carry no plugin prefix, so requiring qualification would
    make them unroutable. A colon nonetheless signals *qualification intent*, so
    a malformed qualification (`a:`, `:b`, `a:b:c`) is still a refusable shape.

    There is deliberately **no charset rule**. What three different harnesses
    accept in a skill name is not something this repo knows, and inventing a
    pattern would refuse valid names to guard against nothing observed.
    """
    if not value.strip():
        return False
    if ":" not in value:
        return True
    plugin, _, skill = value.partition(":")
    return ":" not in skill and bool(plugin.strip()) and bool(skill.strip())


def check_skill_name(value: object, *, key: str, source: Path) -> None:
    """A well-formed stage skill name, or a refusal naming the key and the file.

    A hand-edited dispatch document bypasses write-time validation; the strict
    rule parser checks every supplied skill before matching.

    Raises:
        WorkspaceError: for an empty, whitespace-only or malformed-qualification
            name, or a non-string one.
    """
    if not isinstance(value, str) or not is_valid_skill_name(value):
        raise WorkspaceError(
            f"{source}: {key}: expects a skill name — non-empty, optionally "
            f"qualified as <plugin>:<skill> — got {value!r}"
        )


__all__ = [
    "ATTEND_TAIL",
    "EXECUTE_TAIL",
    "PACKAGED_PIPELINE",
    "RELAY_TAIL_SEED",
    "PipelineEntry",
    "check_skill_name",
    "is_valid_skill_name",
]
