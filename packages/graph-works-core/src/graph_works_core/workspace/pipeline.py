"""Packaged dispatch defaults and the shared skill-name validator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from graph_works_core.workspace.errors import WorkspaceError

#: A fixed prompt line so tail-less variants and workspace-owned tail overrides
#: receive the same findings reporting obligation.
FINDINGS_LINE = (
    "If you filed, or found already filed, work items for findings outside this item's scope, "
    "name each canonical path in your worker_done --body."
)

#: The vendor-neutral tail an `attend` worker carries (design
#: feature-structured-worker-ask §4.4, the attend-ask decision). An attended
#: stage has one channel: the human answers in the worker's own terminal. The
#: tail says so *after* Orca's preamble, which tells every worker to use
#: `orca orchestration ask`, and names that contradiction outright so the
#: later instruction wins. The single `escalation` ping is what tells the
#: coordinator a human is wanted there -- `escalation` because auto-drive's
#: wait (§2.7) already delivers that type, and adding `status` would start
#: delivering park messages too. `{path}` / `{phase}` are substituted by
#: `orchestrate._prompt`. A packaged default rather than config: it names no
#: vendor. The `relay` tail is the opposite case -- a value the workspace owns
#: -- so it stays `None` in the packaged table and is seeded into a new
#: workspace's dispatch file instead (`RELAY_TAIL_SEED`, below). The
#: workspace commit line is appended below, once `WORKSPACE_COMMIT_TAIL` exists.
_ATTEND_BASE = (
    "This is an attended stage: the human answers its questions in this terminal. Ask them here, "
    "the ordinary way, even where your Orca preamble says to use `orca orchestration ask` -- do "
    "not route this stage's questions through the coordinator, and do not use `gw work ask`. "
    "Before your first question, send exactly one notice: `orca orchestration send --from "
    '<your --from> --dispatch-capability <yours> --type escalation --subject "Needs you at '
    '<your --from handle>" --body "{path} {phase}: waiting for the human in this terminal." '
    "--task-id <yours> --dispatch-id <yours>`."
)

#: The obligation of every dispatch mode whose worker may block on an
#: `orca orchestration ask` that a human might not answer before its grace
#: period (design item bug-relayed-answer-arrives-after-ask-timeout, D-004).
#: Only `RELAY_TAIL_SEED` folds it in now: an attend worker asks in its own
#: terminal (`ATTEND_TAIL`), so no Orca ask of its can time out.
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

#: Every dispatched worker's reminder that it never commits the workspace's
#: `main` checkout: `apply_mutation` commits each gw verb's own writes under the
#: bundle lock (feature-single-workspace-commit-authority). Names the main
#: checkout only -- an execute worker's commits on a workspace *branch* are the
#: placement feature's concern. Seeded into `RELAY_TAIL_SEED` too, with the
#: same `init`-only, no-migration caveat as the grace-period line.
WORKSPACE_COMMIT_TAIL = (
    "Never `git commit` in the graph-works workspace at {workspace}; gw verbs commit their own workspace writes."
)

ATTEND_TAIL = f"{_ATTEND_BASE}\n{WORKSPACE_COMMIT_TAIL}"

#: Every non-attend dispatch's fixed line for reaching a human (design
#: feature-structured-worker-ask §4.3). `orchestrate._prompt` appends it after
#: `FINDINGS_LINE` unless the mode is `attend`, whose worker asks in its own
#: terminal instead. Fixed, like `FINDINGS_LINE`: it needs no substitution.
ASK_LINE = (
    "If you need a human decision, prepare it with `gw work ask <work-path> --kind "
    "spec-review|choice|free` and pass the `orca.question` and `orca.options` it prints to "
    "`orca orchestration ask` unchanged (omit `--options` when `orca.options` is null); never "
    "hand-write `--options`. The reply body is JSON; read `choice`, `effort` and `notes` from it."
)

#: How a dispatched agent waits on a gate without spending responses (D-003): start
#: it with `--notify`, end the turn, and resume from the line the runner types into
#: the terminal when the gate finishes. Vendor-neutral; `WAIT_LINE` is the fallback
#: when registration fails. Mirrored verbatim, immediately before `WAIT_LINE`, in
#: every place `WAIT_LINE` is mirrored; `test_wait_line_mirrors.py` pins each copy.
NOTIFY_LINE = (
    "Add `--notify` to the `gw work gate run {path}` command you start the gate with, "
    "keeping any `--worktree` or `--scope` flags it already has. If it reports `satisfied`, continue. "
    "If it reports `notify.registered: true`, end your turn now, with no `gate wait` and no polling: "
    "when the gate finishes, a line starting `Gate run` is typed into this terminal as your next prompt. "
    "Then run the `gw work gate wait {path} --run <id>` it names, once, and act on the result. "
    "If `notify.registered` is false, or the run is `current` and `notify` is null, wait as below."
)

#: How a dispatched agent waits (epic D-011, D-014): one long blocking call, spaced
#: polls, no sleeps. Phrased tool-relatively so it names no vendor -- "600000 ms" is
#: the shell-tool timeout where a tool takes milliseconds, "at least 60 s" the yield
#: where a tool hands control back early. Mirrored verbatim, with `{path}` rendered
#: as `<work-path>`, in the workflow skill's subagent-driven-development,
#: test-driven-development and finishing-a-development-branch riders
#: (`references/brief-riders.md`) and in finishing-relay's R1 and R4 gate
#: instructions; `test_wait_line_mirrors.py` pins every copy. `NOTIFY_LINE` precedes it in every copy.
WAIT_LINE = (
    "Wait on the gate with `gw work gate wait {path}` as one blocking call at its default timeout; "
    "never pass a smaller `--timeout`. Give your shell tool its maximum call timeout "
    "(600000 ms where it takes milliseconds). If the tool hands control back before the command ends, "
    "keep waiting on that same process with the longest yield your tool allows, at least 60 s; "
    "never use short yields and never start a second wait beside it. When it reports `running`, "
    "call it again at once, without a sleep. Wait on subagents the same way: one blocking wait for "
    "their completion, not a loop of short checks."
)

#: The obligation both execute variants carry. A packaged default rather than
#: config for `ATTEND_TAIL`'s reason -- it names no vendor. It is *reported,
#: not enforced*: the file is surfaced to a human by `auto-drive` §4.1 and
#: registered into `sources[]` by the advance, and no transition is gated on
#: its contents. A gate that read a worker's self-report would be trusting the
#: exact judgement the defect this tail exists for shows a model getting wrong.
#: Its wait sentence is `WAIT_LINE`; see that constant for its mirrors. The rest of
#: the gate paragraph is not mirrored: attended execute sessions receive this tail
#: through the workflow skill's verbatim `prompt_tail` append.
#: The deferral sentence is mirrored verbatim in the workflow skill's
#: execute-stage deferral bullet (`<work-path>` there, `{path}` here);
#: unchecked coverage lines need no instruction -- the `execute -> finish`
#: advance records them as `finish_obligations` itself (D-001).
#: `{execute_artifact}` is the configured execute artifact filename, substituted
#: by orchestrate's `_prompt`.
#: The context-hygiene paragraph quotes `sanitize_tail`'s 40-line default
#: (`orchestrate/gate_receipts.py`); a change there changes this text.
EXECUTE_TAIL = (
    "Before you advance, write {workspace}/okf/{path}/references/{execute_artifact}: "
    "one markdown task-list line per item in this stage's design spec `## Acceptance` section, "
    "each line `- [x]` when delivered or `- [ ]` when not, each with a one-line justification. "
    "Where the spec has no `## Acceptance` section, enumerate its `## Scope` / "
    "`## What this design changes` headings instead and say in the file that you did. "
    "Mark honestly -- an unchecked box is a normal, expected outcome; an inaccurate checked box "
    "is not. Pass that file's path as --report-path on your worker_done.\n"
    "When the plan marks a step `Deferred to finish`, record it with "
    '`gw work obligation add {path} --text "<the step>" --apply` instead of doing it or leaving it in prose.\n'
    "Context hygiene: when the gate fails, `gw work gate wait` already prints the last 40 lines of its log -- "
    "never `tail`, `cat` or `grep` a gate or test log by hand. Hand artifacts to subagents as file paths "
    "(task briefs, review packages, reports), never pasted inline. Read each skill file once per session; "
    "re-open only a named section when you need it again.\n"
    "The gate is `gw work gate run {path}`, then `gw work gate wait {path}`. " + NOTIFY_LINE + " " + WAIT_LINE + " "
    "Run it on the committed, clean tree before you advance. Use `--scope scoped` for fix-wave rechecks. "
    "Never run the repository's check command directly as the gate, and never write gate logs to a shared tmp path."
    "\n" + WORKSPACE_COMMIT_TAIL
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
#: first line, so appending here cannot disturb it. The workspace commit line
#: follows the same init-only seed rule.
#:
#: **Seeding is `init`-only, and there is no migration.** `init.plan_init`
#: writes this value into a *missing* shared dispatch document and preserves an
#: authored one, so a workspace that already exists keeps whatever relay tail
#: its `dispatch.yaml` was written with -- these appended obligations are
#: inert for it. An operator upgrading an existing workspace must append
#: `GRACE_PERIOD_TAIL` and `WORKSPACE_COMMIT_TAIL` to that file's `{stage: finish}` rule's `prompt_tail`
#: by hand and run `gw config sync`; nothing here does it for them. Building a
#: rewriter is deliberately out of scope: the tail is a workspace-owned value,
#: and core does not edit values a workspace owns.
RELAY_TAIL_SEED = (
    "Auto-drive context: relay the merge/PR/hold/discard decision to your coordinator "
    "rather than asking interactively; merge target is {merge_target}.\n"
    f"{GRACE_PERIOD_TAIL}\n{WORKSPACE_COMMIT_TAIL}"
)


@dataclass(frozen=True, slots=True)
class PackagedRule:
    """One packaged, attribute-matched dispatch default. Later match wins (D-008)."""

    name: str
    match: Mapping[str, str | bool | tuple[str | bool, ...]]
    skill: str
    mode: str
    prompt_tail: str | None = None


def _rule(
    name: str, match: dict[str, str | bool | tuple[str | bool, ...]], skill: str, mode: str, tail: str | None
) -> PackagedRule:
    return PackagedRule(name, MappingProxyType(match), skill, mode, tail)


_PARENTS = ("Epic", "Release")

#: The packaged defaults read by the dispatch resolver, in precedence order; never mutated.
PACKAGED_PIPELINE: tuple[PackagedRule, ...] = (
    _rule("design", {"stage": "design"}, "superpowers:brainstorming", "attend", ATTEND_TAIL),
    _rule("design-bug", {"stage": "design", "type": "Bug"}, "superpowers:systematic-debugging", "attend", ATTEND_TAIL),
    _rule("design-parent", {"stage": "design", "type": _PARENTS}, "gw:epic-design", "attend", ATTEND_TAIL),
    _rule(
        "design-reconcile",
        {"stage": "design", "has_spec": True},
        "gw:reconciling-spec",
        "autonomous",
        WORKSPACE_COMMIT_TAIL,
    ),
    _rule("plan", {"stage": "plan"}, "superpowers:writing-plans", "autonomous", WORKSPACE_COMMIT_TAIL),
    _rule("plan-parent", {"stage": "plan", "type": _PARENTS}, "gw:planning-epics", "autonomous", WORKSPACE_COMMIT_TAIL),
    _rule(
        "plan-reconcile",
        {"stage": "plan", "spec_stale": True},
        "gw:reconciling-spec",
        "autonomous",
        WORKSPACE_COMMIT_TAIL,
    ),
    _rule("execute", {"stage": "execute"}, "superpowers:test-driven-development", "autonomous", EXECUTE_TAIL),
    _rule(
        "execute-planned",
        {"stage": "execute", "has_plan": True},
        "superpowers:subagent-driven-development",
        "autonomous",
        EXECUTE_TAIL,
    ),
    _rule("finish", {"stage": "finish"}, "superpowers:finishing-a-development-branch", "relay", None),
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
    "ASK_LINE",
    "ATTEND_TAIL",
    "EXECUTE_TAIL",
    "FINDINGS_LINE",
    "NOTIFY_LINE",
    "PACKAGED_PIPELINE",
    "RELAY_TAIL_SEED",
    "WAIT_LINE",
    "WORKSPACE_COMMIT_TAIL",
    "PackagedRule",
    "check_skill_name",
    "is_valid_skill_name",
]
