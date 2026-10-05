"""One stage completion: advance the item, then capture what the stage left.

Split out of `commands.py`, which held this shell and the IO-free planner in
one 1240-line file. The two halves share no module-level symbol — every name
here is an external import — so the seam was already there and this module
only states it. There is deliberately **no re-export from `commands.py`**: a
re-export would be a surface both the planner lane and the stage-gate lane
still have a reason to edit, which is exactly the collision the split removes.

Provenance never fails an advance. A `None` `results_path` or `pointer_path`
is a normal outcome, not an error. `pointer_path` is also `None` by design,
not degradation, on an exit or return transition -- only a `dispatch`
transition stamps the active-work pointer, since only that transition runs
at the start of the session it prepares.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType

from okf_io import Bundle
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.advance import COMMIT_GATE_REFUSALS as COMMIT_GATE_REFUSALS
from work_tracker_okf.advance import ExpectedPhase as ExpectedPhase
from work_tracker_okf.advance import FieldChange, RefusalReason
from work_tracker_okf.advance import apply as apply_advance
from work_tracker_okf.affects import affects_drift, code_affects, plan_files, touches_workspace
from work_tracker_okf.compose import AdvanceOutcome, advance_and_stamp, ensure_plan_row, stage_artifact_ref
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.items import IGNORE, WorkItem, is_commit_oid, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.obligations import KEY, apply_obligations, plan_derive
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref, item_page
from work_tracker_okf.pipeline import PipelineDefinition, results_phases
from work_tracker_okf.results import render as render_results
from work_tracker_okf.sources import upsert
from work_tracker_okf.workflow import Blocker

from graph_works_core.orchestrate import gate_git, gate_units
from graph_works_core.orchestrate.anchors import Anchor, AnchorRefusal, enclosing_owner, reader_anchor
from graph_works_core.orchestrate.gate_receipts import GateEvidence, evaluate
from graph_works_core.workspace import anchor, provenance
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.commits import WorkspaceCommit, commit_mode, item_stem
from graph_works_core.workspace.decision_owner import (
    DecisionOwner,
    decision_context,
    hold_for,
    hold_in,
    locked_decision_owner,
)
from graph_works_core.workspace.dispatch_artifacts import routing_items
from graph_works_core.workspace.dispatch_config import load_dispatch_config
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.finish import finish_read_guard, inspect_finish
from graph_works_core.workspace.gate_config import repo_gate
from graph_works_core.workspace.landed import stale_spec_for
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repo_context import observe_repository
from graph_works_core.workspace.repos import (
    ItemRepo,
    declared_repositories,
    resolve_item_repo,
    resolve_repo,
    resolve_repos,
)
from graph_works_core.workspace.transactions import MutationApplication, apply_mutation

#: The phases whose *completion* produces a results stub. A design or plan
#: stage leaves an artifact of its own; the stage table's `results` column
#: identifies stages with a commit range worth summarizing.
RESULTS_PHASES: frozenset[str] = results_phases()

#: The prefix of the one gate note (a workspace-only pass), so a reader grepping
#: the coordinator's output finds it with one string.
_GATE_NOTE = "execute -> finish gate"


@dataclass(frozen=True, slots=True)
class GateBypass:
    """An attributed, per-code bypass of the execute -> finish gate. `decision_id`
    is the ledger entry it wrote; `None` on a dry run."""

    code: str
    reason: str
    actor: str
    detail: str
    decision_id: str | None = None


@dataclass(frozen=True, slots=True)
class StageAdvance:
    """What one stage completion did: the advance, plus its provenance.

    `results_path` and `pointer_path` are `None` for a dry run, for a refusal,
    when the stage produced nothing to capture (a non-code phase for
    `results_path`, a `done` landing or an exit or return transition for
    `pointer_path`), and whenever the corresponding capture degraded --
    provenance never fails an advance, so "nothing was written" is a normal
    outcome, not an error.

    `repo_note` carries the reason no code repo was resolved: `resolve_repo`'s
    note, or that several are declared and the cwd is in none of them. Named
    for exactly that and not for a general provenance log -- when it is set,
    every git-derived field above it is `None` for one known reason rather
    than for an unknown one.

    `warnings` carries the gate's note for a workspace-only item, a skipped
    worktree inference when the item's repository came from `repo:`
    (frontmatter) or `repo_name` (flag) and cwd is not in that repository --
    inference is skipped, never a silent guess, and a foreign checkout is
    never stamped -- and the plan-exit affects-drift check. Gate *failures*
    are refusals, never warnings.
    At `plan -> execute` it also carries the advisory affects-drift check
    (plan-named files outside `affects`), on a dry run too.
    """

    outcome: AdvanceOutcome
    results_path: Path | None = None
    pointer_path: Path | None = None
    repo_note: str | None = None
    application: MutationApplication | None = None
    warnings: tuple[str, ...] = ()
    gate_bypass: GateBypass | None = None
    gate_receipt: GateEvidence | None = None

    @property
    def changed(self) -> bool:
        return self.outcome.changed


def _infers_from_cwd(item: WorkItem) -> bool:
    """Whether an attended advance may fall back to a cwd-inferred stamp.

    Only a physically top-level item may. A descendant's placement is recorded
    by its coordinator from Orca's observed readback (`gw work record-placement`,
    D-006), never inferred from wherever a worker happens to stand. The former
    code-phase inference left placement ownership with the advancing worker
    instead of the coordinator that observed the dispatch.

    This is a conservative attended fallback. A nested orchestration root is a
    descendant here and is recorded explicitly, and a supervised worker disables
    inference outright with `infer_worktree=False` (`--no-infer-worktree`).
    """
    return item.parent_path is None


def run_stage_advance(
    layout: WorkspaceLayout,
    path: str,
    *,
    today: date,
    expected_phase: ExpectedPhase | None = None,
    effort: str | None = None,
    owner: str | None = None,
    resolved_in: str | None = None,
    released_at: date | None = None,
    worktree: str | None = None,
    branch: str | None = None,
    infer_worktree: bool = True,
    cwd: Path | None = None,
    repo: Path | None = None,
    repo_name: str | None = None,
    start_sha: str | None = None,
    return_: bool = False,
    skip_gate: str | None = None,
    skip_reason: str | None = None,
    actor: str | None = None,
    dry_run: bool = True,
    before_apply: Callable[[StageAdvance], None] | None = None,
) -> StageAdvance:
    """Complete one stage: advance the item and capture what the stage left.

    Worktree provenance has two paths. An explicit `worktree`/`branch` pair is
    the caller's own resolved `plan()` decision -- main-mode eviction,
    fork-child, cold start -- never a guess, so it is applied unconditionally
    and overwrites an already-valid recorded stamp. That is what lets an item
    be evicted out of a shared main checkout: a stamp that is still a live
    directory could otherwise never be repointed. Without the pair, provenance
    falls back to cwd inference under the original conservative guard -- stamp
    only when the recorded path is unset or gone -- so an advance run from an
    unrelated cwd cannot silently repoint a live item. Inference also cannot
    see the main checkout at all (`provenance.worktree_state` answers `None`
    when `--git-dir` and `--git-common-dir` agree).

    Inference runs only for a top-level item (`_infers_from_cwd`) and only when
    `infer_worktree` is true. Every supervised worker passes
    `infer_worktree=False`: its coordinator records the observed placement
    separately, and a worker's cwd is not evidence of where its stage runs.

    The execute baseline resolves in order: the `start_sha` argument, then the
    placement's recorded `start_sha` (its `repo_stamps` entry for the gated
    repository when that entry carries one, else the scalar one). The commit gate uses only those two and
    refuses `no-start-sha` when neither exists. Only the results *stub* falls
    back further: at `execute` it derives one by
    `workspace.anchor.phase_start_sha` -- merge-base first, then the spec's own
    arms. At `finish` it uses only an explicit argument and derives nothing: a derived range there sweeps in the
    whole execute range, and a finish report claiming work it did not do is
    exactly what this pipeline exists to prevent.

    `repo` defaults to `resolve_item_repo` rather than to the layout's
    `repo_root`: in a split topology -- the workspace and the code in
    different git repositories -- the walk-up resolves to the workspace's own
    repo, and both `worktree_state` and `results_facts` then degrade to
    `None` without a word. An explicit `repo` wins and skips resolution
    entirely. Precedence otherwise (`_resolve_repo`): the item's own `repo:`
    (or an ancestor's) wins first, then `repo_name`, then the cwd matcher --
    one declared repo, or none, is `resolve_repo`'s answer; several are
    narrowed to the one *cwd*'s repository belongs to, and none at all when
    it belongs to none. The cwd matcher never refuses -- a resolved-`None`
    repo comes back as a `repo_note`, not an error. But the chain above it
    does refuse: a `WorkspaceError` naming the item is raised when the
    chain's `repo:` names an undeclared repository, or when `repo_name`
    conflicts with a `repo:` the chain already set -- deliberately, even for
    a vault-only transition that touches no code at all. And when the repo
    came from `repo:`/`repo_name` but cwd is outside it, worktree inference
    is skipped rather than guessed, with an entry in `warnings` explaining
    why. Postcondition validation checks `affects` against every declared
    repo either way.

    `return_=True` walks the routing table's one backwards transition,
    `finish -> execute`. It writes no results stub: a return is not a stage
    completion, and a `finish` stub for a stage that is being reopened would
    claim work nobody did.

    `dry_run=True` is okf-io's writer default throughout this workspace: the
    call plans and writes nothing -- not the page, not the stub, not the
    pointer. The commit gate evaluates on a dry run too and reports its
    refusal, but a dry run writes nothing.

    `skip_gate` bypasses one execute -> finish gate refusal, attributed. It is
    accepted only when this invocation's gate refuses with exactly that code;
    the gate then stops there, so signals after the bypassed one are not
    evaluated -- the ledger entry says which code was bypassed and why.
    Validation refusals, in order: a missing or blank `skip_reason` or `actor`,
    or a code outside `COMMIT_GATE_REFUSALS` -> `gate-bypass-invalid`; no
    execute -> finish gate on this advance, or the gate passed ->
    `gate-bypass-unused`; a different refusal -> `gate-bypass-mismatch`; a
    ledger plan refusal -> `gate-bypass-unrecorded`. The ledger entry (in the
    decision owner's ledger) and the phase transition are one
    `WorkMutationPlan`, so a failed apply writes neither.

    `before_apply`, when supplied on a live call, inspects the actual candidate
    with application fields empty before any domain write. Raising aborts the
    call; exceptions propagate. The callback must not mutate the candidate or
    workspace. Dry runs never invoke it. Omitting it preserves CLI behavior.

    The callback sees the routed advance under the decision-owner lock, before
    the commit gate. That gate may still refuse or add diagnostics;
    it cannot select a different transition after validation.

    Live advances hold the decision owner's lock across the read, hold
    resolution, routing, commit gate and mutation. Dry runs resolve holds
    without locking except for receipt-guarded finish verification. On win32,
    `okf_ext.locking` gives up after ten one-second
    retries and raises `OSError` naming the lock; the CLI reports it as `io`.

    Raises:
        WorkspaceError: dispatch rules are missing or malformed, or repository
        selection is invalid. Dispatch failures occur before mutation.
    """
    definition = load_dispatch_config(layout).definition
    bundle = load_workspace_bundle(layout, ignore=IGNORE)
    items = load_items(bundle)
    receipt_finish = any(
        item.path == path
        and item.phase == "finish"
        and (item.repo_stamps or "repo_stamps" in item.invalid_optional_fields)
        for item in items
    )
    if (dry_run and not receipt_finish) or not any(item.path == path for item in items):
        return _advance(
            layout,
            bundle,
            items,
            path,
            definition=definition,
            hold=hold_for(items, bundle.root, path) if dry_run else None,
            dry_run=dry_run,
            today=today,
            expected_phase=expected_phase,
            effort=effort,
            owner=owner,
            resolved_in=resolved_in,
            released_at=released_at,
            worktree=worktree,
            branch=branch,
            infer_worktree=infer_worktree,
            cwd=cwd,
            repo=repo,
            repo_name=repo_name,
            start_sha=start_sha,
            return_=return_,
            skip_gate=skip_gate,
            skip_reason=skip_reason,
            actor=actor,
            decision_owner_=None,
            before_apply=before_apply,
        )
    # The whole read -> route -> gate -> write sequence shares hold filing's
    # owner lock. A waiting writer sees the hold or phase the first committed.
    # Release only after apply_mutation (and the pointer write) returns.
    if not dry_run:
        commit_mode(layout)
    with locked_decision_owner(layout, path) as context:
        return _advance(
            layout,
            context.bundle,
            context.items,
            path,
            definition=definition,
            hold=hold_in(context, path),
            dry_run=dry_run,
            today=today,
            expected_phase=expected_phase,
            effort=effort,
            owner=owner,
            resolved_in=resolved_in,
            released_at=released_at,
            worktree=worktree,
            branch=branch,
            infer_worktree=infer_worktree,
            cwd=cwd,
            repo=repo,
            repo_name=repo_name,
            start_sha=start_sha,
            return_=return_,
            skip_gate=skip_gate,
            skip_reason=skip_reason,
            actor=actor,
            decision_owner_=context.owner,
            before_apply=before_apply,
        )


_NO_CODE_BASELINE = "spec baseline: no code sha resolvable; landed-since will be unavailable"


def _baseline_stamp(
    layout: WorkspaceLayout,
    item: WorkItem,
    repo: Path | None,
    cwd: Path | None,
    bundle_root: Path,
    *,
    definition: PipelineDefinition,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Resolve spec anchors, then the matching cwd HEAD, without refusing an advance.

    Omit the existing stamp when resolving its replacement. Workspace HEAD is
    observed before this advance commits.
    """
    stamp: dict[str, str] = {}
    code: str | None = None
    if repo is not None:
        spec_path = bundle_root / anchor.spec_ref(item, definition=definition)
        try:
            spec_text = spec_path.read_text(encoding="utf-8") if spec_path.is_file() else ""
        except (OSError, UnicodeError):
            spec_text = ""
        code, _source = anchor.resolve_anchor(repo, spec_path, spec_text)
        if code is not None:
            resolved = provenance.run_git(repo, "rev-parse", "--verify", "--end-of-options", f"{code}^{{commit}}")
            normalized = (resolved or "").strip().lower()
            code = normalized if is_commit_oid(normalized) else None
        here = cwd or Path.cwd()
        if code is None and provenance.repository_of(here, (repo,)) is not None:
            code = provenance.head_sha(here)
    if code is not None:
        stamp["code"] = code
    workspace = provenance.head_sha(layout.root)
    if workspace is not None:
        stamp["workspace"] = workspace
    return stamp, (() if code is not None else (_NO_CODE_BASELINE,))


def _advance(
    layout: WorkspaceLayout,
    bundle: Bundle,
    items: Sequence[WorkItem],
    path: str,
    *,
    definition: PipelineDefinition,
    hold: HoldFact | None,
    today: date,
    expected_phase: ExpectedPhase | None,
    effort: str | None,
    owner: str | None,
    resolved_in: str | None,
    released_at: date | None,
    worktree: str | None,
    branch: str | None,
    infer_worktree: bool,
    cwd: Path | None,
    repo: Path | None,
    repo_name: str | None,
    start_sha: str | None,
    return_: bool,
    skip_gate: str | None,
    skip_reason: str | None,
    actor: str | None,
    decision_owner_: DecisionOwner | None,
    dry_run: bool,
    before_apply: Callable[[StageAdvance], None] | None,
) -> StageAdvance:
    item = next((candidate for candidate in items if candidate.path == path), None)
    old_phase = item.phase if item is not None else None
    repo_note: str | None = None
    resolved_repo = repo
    declared: tuple[Path, ...] = ()
    item_repo: ItemRepo | None = None
    if resolved_repo is None:
        item_repo, declared = _resolve_repo(layout, items, item, repo_name=repo_name, cwd=cwd)
        resolved_repo, repo_note = item_repo.path, item_repo.note

    inference_warnings: tuple[str, ...] = ()
    stamped_worktree: str | None = None
    stamped_branch: str | None = None
    inferred = False
    if worktree and branch:
        stamped_worktree, stamped_branch = worktree, branch
    elif infer_worktree and item is not None and resolved_repo is not None and _infers_from_cwd(item):
        here = cwd or Path.cwd()
        if (
            item_repo is not None
            and item_repo.source in ("frontmatter", "flag")
            and provenance.repository_of(here, (resolved_repo,)) is None
        ):
            inference_warnings = (
                f"worktree inference skipped: {here} is not in {item.path}'s repository "
                f"{item_repo.name!r} ({resolved_repo}); record placement explicitly if it runs elsewhere",
            )
        else:
            recorded = item.worktree
            if not recorded or not Path(recorded).is_dir():
                detected = provenance.worktree_state(here, resolved_repo, git=provenance.gate_git(layout))
                if detected is not None:
                    stamped_worktree, stamped_branch = detected
                    inferred = True

    finish_guard: str | None = None
    finish_blockers: tuple[str, ...] = ()
    if (
        item is not None
        and old_phase == "finish"
        and not return_
        and (item.repo_stamps or "repo_stamps" in item.invalid_optional_fields)
    ):
        finish_guard = finish_read_guard(layout, path, bundle=bundle)
        verification = inspect_finish(layout, path)
        finish_blockers = verification.blockers
        if verification.complete:
            if resolved_in is not None and resolved_in != verification.resolved_in:
                finish_blockers = ("resolved_in does not agree with verified finish receipt",)
            else:
                resolved_in = verification.resolved_in

    stale = stale_spec_for(layout, items, item) if item is not None and not return_ else ()
    outcome = advance_and_stamp(
        bundle,
        path,
        today=today,
        expected_phase=expected_phase,
        effort=effort,
        owner=owner,
        resolved_in=resolved_in,
        released_at=released_at,
        worktree=stamped_worktree,
        branch=stamped_branch,
        return_=return_,
        hold=hold,
        stale_spec=stale,
        definition=definition,
        routing_items=routing_items(bundle.root, items, definition=definition),
        dry_run=True,
    )
    if outcome.plan.trigger == "repair" and inferred:
        # Moving off a removed stage does not claim the caller's checkout or
        # discard the recorded baseline. Explicit placement remains authored.
        outcome = replace(
            outcome,
            plan=replace(
                outcome.plan,
                changes=tuple(c for c in outcome.plan.changes if c.key not in {"worktree", "branch", "start_sha"}),
            ),
        )
        stamped_worktree, stamped_branch = None, None
        inferred = False
    if finish_blockers:
        refused = replace(
            outcome.plan,
            refusal="finish-incomplete",
            route=replace(
                outcome.plan.route,
                blockers=outcome.plan.route.blockers
                + tuple(Blocker("finish-incomplete", message) for message in finish_blockers),
            ),
            changes=(),
            stamp_source=None,
            stamp_baseline=False,
            detail="; ".join(finish_blockers),
            trigger=None,
        )
        outcome = replace(outcome, plan=refused, stamped=None, stamp_title=None, plan_row=False)
    drift_warnings: tuple[str, ...] = ()
    if (
        item is not None
        and outcome.plan.trigger == "complete"
        and old_phase == "plan"
        and outcome.plan.refusal is None
        and outcome.plan.transition is not None
        and outcome.plan.transition.phase == "execute"
    ):
        drift_warnings = _affects_drift_warnings(bundle.root, item, definition=definition)
    stamp: dict[str, str] = {}
    baseline_warnings: tuple[str, ...] = ()
    if outcome.plan.stamp_baseline and outcome.plan.refusal is None and item is not None:
        stamp, baseline_warnings = _baseline_stamp(layout, item, resolved_repo, cwd, bundle.root, definition=definition)
    candidate = StageAdvance(
        outcome=outcome, repo_note=repo_note, warnings=inference_warnings + drift_warnings + baseline_warnings
    )
    if not dry_run and before_apply is not None:
        before_apply(candidate)
    if skip_gate is not None:
        if not (skip_reason or "").strip() or not (actor or "").strip():
            return replace(
                candidate,
                outcome=_refuse(outcome, "gate-bypass-invalid", "--skip-gate needs a nonempty --reason and --actor"),
            )
        if skip_gate not in COMMIT_GATE_REFUSALS:
            return replace(
                candidate,
                outcome=_refuse(
                    outcome,
                    "gate-bypass-invalid",
                    f"{skip_gate!r} is not a bypassable gate code; one of {sorted(COMMIT_GATE_REFUSALS)}",
                ),
            )
    if outcome.plan.refusal is not None or outcome.plan.transition is None:
        return candidate

    new_phase = outcome.plan.transition.phase or old_phase

    assert item is not None
    warnings: tuple[str, ...] = ()
    bypass: GateBypass | None = None
    gate_receipt: GateEvidence | None = None
    # D-014: the gate belongs to completing the stage that writes code; a path
    # without execute reaches finish ungated.
    completes_execute = old_phase == "execute" and outcome.plan.trigger == "complete" and new_phase != old_phase
    gated = completes_execute
    if skip_gate is not None and not gated:
        return replace(
            candidate, outcome=_refuse(outcome, "gate-bypass-unused", "no execute -> finish gate runs on this advance")
        )
    anchor_root = _anchor_resolver(layout, items, item, resolved_repo, item_repo)
    if gated:
        verdict = _commit_gate(
            item,
            anchor_root=anchor_root,
            repo=resolved_repo,
            repo_note=repo_note,
            repo_name=item_repo.name if item_repo is not None else None,
            stamped_worktree=stamped_worktree,
            worktree_inferred=inferred,
            explicit_start=start_sha,
            git=provenance.gate_git(layout),
        )
        commit_refusal = verdict.refusal
        if verdict.root is not None and (
            commit_refusal is None or (skip_gate is not None and commit_refusal == skip_gate)
        ):
            # A bypassed commit-gate code never exempts the receipt: each gate is bypassable only by its own code.
            receipt_verdict = _receipt_gate(
                layout,
                item,
                verdict.root,
                item_repo.name if item_repo is not None else _repo_name_of(layout, resolved_repo),
            )
            if commit_refusal is None:
                verdict = receipt_verdict
            elif receipt_verdict.refusal is not None:
                # the bypass covers the commit code only; the receipt refusal stands under its own code
                return StageAdvance(
                    outcome=_refuse(outcome, receipt_verdict.refusal, receipt_verdict.detail),
                    repo_note=repo_note,
                    warnings=inference_warnings + drift_warnings,
                )
        warnings = (verdict.note,) if verdict.note else ()
        gate_receipt = verdict.receipt
        if skip_gate is not None:
            if verdict.refusal is None:
                return replace(
                    candidate,
                    outcome=_refuse(outcome, "gate-bypass-unused", "the gate passed; there is nothing to bypass"),
                )
            if verdict.refusal != skip_gate:
                return replace(
                    candidate,
                    outcome=_refuse(
                        outcome,
                        "gate-bypass-mismatch",
                        f"the gate refused {verdict.refusal} ({verdict.detail}); "
                        f"--skip-gate {skip_gate} bypasses only that code",
                    ),
                )
            assert skip_reason is not None and actor is not None
            bypass = GateBypass(skip_gate, skip_reason.strip(), actor.strip(), verdict.detail)
        elif verdict.refusal is not None:
            return StageAdvance(
                outcome=_refuse(outcome, verdict.refusal, verdict.detail),
                repo_note=repo_note,
                warnings=inference_warnings + drift_warnings + baseline_warnings + warnings,
            )
    owner_ctx = decision_owner_
    ledger_plan: _decisions.DecisionPlan | None = None
    if bypass is not None:
        owner_ctx = owner_ctx or decision_context(layout, path).owner
        ledger_plan = _decisions.plan_append(
            owner_ctx.ledger,
            question=f"Bypass the execute -> finish `{bypass.code}` gate for {path}?",
            status="answered",
            answer=f"Bypassed by {bypass.actor}: {bypass.reason}",
            rationale=f"Gate refusal: {bypass.detail}",
            if_wrong=None,
            affects=(path,),
            on=today,
            decided_by=bypass.actor,
        )
        if ledger_plan.refusal is not None:
            return replace(
                candidate,
                outcome=_refuse(
                    outcome, "gate-bypass-unrecorded", f"the bypass could not be recorded: {ledger_plan.detail}"
                ),
            )
    if dry_run:
        return replace(candidate, warnings=candidate.warnings + warnings, gate_bypass=bypass, gate_receipt=gate_receipt)

    document = load_workspace_bundle(layout, ignore=IGNORE).concepts[path]
    apply_advance(document, outcome.plan)
    if stamp:
        document.set("spec_baseline", stamp)
    if outcome.stamped is not None and outcome.stamp_title is not None:
        upsert(document, outcome.stamped, title=outcome.stamp_title)
        if outcome.plan.sync_plan_table:
            ensure_plan_row(document, outcome.stamped)

    results_path: Path | None = None
    result_member: str | None = None
    result_bytes: bytes | None = None
    facts_root = _facts_root(item, stamped_worktree, resolved_repo, anchor_root=anchor_root)
    if (
        outcome.plan.trigger == "complete"
        and facts_root is not None
        and old_phase in RESULTS_PHASES
        and new_phase != old_phase
    ):
        effective_start_sha = _effective_start_sha(
            start_sha,
            definition=definition,
            phase=old_phase,
            facts_root=facts_root,
            bundle_root=bundle.root,
            item=item,
            repo_name=item_repo.name if item_repo is not None else None,
        )
        if effective_start_sha is not None:
            facts = provenance.results_facts(
                facts_root,
                phase=old_phase,
                start_sha=effective_start_sha,
                paths=code_affects(item.affects),
                opened=item.opened,
            )
            if facts is not None:
                key = f"{facts.phase}-results"
                ref = artifact_ref(path, MANAGED_ARTIFACTS[key])
                result_member = ref.rel
                result_bytes = render_results(facts).encode("utf-8")
                upsert(document, ref, title=f"{facts.phase.capitalize()} results")

    if completes_execute:
        coverage_ref = stage_artifact_ref(path, definition.artifacts["execute"])
        coverage_path = coverage_ref.path(bundle.root)
        coverage_text: str | None = None
        coverage_readable = True
        if coverage_path.exists():
            upsert(document, coverage_ref, title="Execute coverage")
            try:
                coverage_text = coverage_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                coverage_readable = False
                warnings = (*warnings, f"finish_obligations: coverage unreadable: {exc}")
        # Advancing accepts caveats; a return leaves the current list intact,
        # then re-advancing replaces only coverage-derived entries.
        if coverage_readable:
            obligation_plan = plan_derive(item, coverage_text, on=today)
            if obligation_plan.changed or KEY in item.invalid_optional_fields:
                before_obligations = document.fm_data(dates="iso").get(KEY, [])
                apply_obligations(document, obligation_plan)
                change = FieldChange(
                    KEY,
                    before_obligations,
                    [entry.to_data() for entry in obligation_plan.after],
                )
                outcome = replace(outcome, plan=replace(outcome.plan, changes=(*outcome.plan.changes, change)))

    page_member = item_page(path).rel
    page_before = (bundle.root / page_member).read_bytes()
    writes: list[PlannedWrite] = []
    mkdirs: tuple[str, ...] = ()
    conditions: tuple[DirectoryPrecondition, ...] = ()
    if result_member is not None and result_bytes is not None:
        result_path = bundle.root / result_member
        try:
            result_before = result_path.read_bytes()
        except FileNotFoundError:
            result_before = None
        writes.append(
            PlannedWrite(
                result_member,
                hashlib.sha256(result_before).hexdigest() if result_before is not None else None,
                result_bytes,
            )
        )
        parent = Path(result_member).parent.as_posix()
        mkdirs = (parent,)
        if not (bundle.root / parent).exists():
            conditions = (DirectoryPrecondition(parent, None),)

    # The bypass entry rides in the same mutation as the phase change: one
    # apply writes both or neither.
    validate_paths: tuple[str, ...] = (path,)
    if bypass is not None and ledger_plan is not None and owner_ctx is not None:
        ledger_member = owner_ctx.ledger.relative_to(bundle.root).as_posix()
        ledger_before = owner_ctx.ledger.read_bytes() if owner_ctx.ledger.exists() else None
        ledger_text = _decisions.render(_decisions.parse(ledger_plan.snapshot.text).preamble, ledger_plan.after)
        writes.append(
            PlannedWrite(
                ledger_member,
                hashlib.sha256(ledger_before).hexdigest() if ledger_before is not None else None,
                ledger_text.encode("utf-8"),
            )
        )
        ledger_parent = Path(ledger_member).parent.as_posix()
        if ledger_parent not in mkdirs:
            mkdirs = (*mkdirs, ledger_parent)
            if not (bundle.root / ledger_parent).exists():
                conditions = (*conditions, DirectoryPrecondition(ledger_parent, None))
        if owner_ctx.owner_path == path:
            upsert(document, _decisions.ledger_ref(path), title="Decisions")
        else:
            owner_member = item_page(owner_ctx.owner_path).rel
            owner_before = (bundle.root / owner_member).read_bytes()
            owner_document = load_workspace_bundle(layout, ignore=IGNORE).concepts[owner_ctx.owner_path]
            upsert(owner_document, _decisions.ledger_ref(owner_ctx.owner_path), title="Decisions")
            writes.append(
                PlannedWrite(
                    owner_member, hashlib.sha256(owner_before).hexdigest(), owner_document.serialize().encode("utf-8")
                )
            )
            validate_paths = (path, owner_ctx.owner_path)
        assert ledger_plan.primary is not None
        bypass = replace(bypass, decision_id=ledger_plan.primary.id)
    writes.insert(
        0, PlannedWrite(page_member, hashlib.sha256(page_before).hexdigest(), document.serialize().encode("utf-8"))
    )
    mutation = WorkMutationPlan(
        root=bundle.root,
        operation="file",
        path_mapping=MappingProxyType({}),
        move_plan=None,
        moves=(),
        writes=tuple(writes),
        deletes=(),
        mkdirs=mkdirs,
        warnings=(),
        refusals=(),
        validate_paths=validate_paths,
        directory_preconditions=conditions,
    )

    # `bundle` is the lock-held projection (or the dry-run read), loaded with
    # `IGNORE`, so it satisfies `apply_mutation`'s baseline precondition.
    def validate_finish() -> None:
        verified = inspect_finish(layout, path)
        if (
            finish_read_guard(layout, path) != finish_guard
            or not verified.complete
            or verified.resolved_in != resolved_in
        ):
            raise WorkspaceError("finish evidence changed; inspect and retry before advancing")

    transition = outcome.plan.transition
    resolved = transition is not None and transition.work_status == "resolved"
    subject = f"workspace: advance {item_stem(path)} {old_phase or 'none'} -> {new_phase or 'none'}"
    commit_items: tuple[str, ...] = (path,)
    if bypass is not None:
        subject += f" (gate bypass {bypass.code})"
        if owner_ctx is not None and owner_ctx.owner_path != path:
            commit_items = (path, owner_ctx.owner_path)
    workspace_commit = WorkspaceCommit(subject + (" (resolved)" if resolved else ""), items=commit_items)
    application = apply_mutation(
        layout,
        mutation,
        repo_root=resolved_repo,
        repo_roots=declared,
        baseline_bundle=bundle,
        validate_read_set=validate_finish if finish_guard is not None else None,
        commit=workspace_commit,
    )
    if application.ok:
        outcome = replace(outcome, written=True)
        if result_member is not None:
            results_path = bundle.root / result_member

    # Only an entry transition stamps the pointer: it runs at the start of the
    # session it prepares. An exit (`complete`) or `return` runs inside the
    # session it ends, and that session's `SessionEnd` capture must still see
    # the phase it ran -- `gw work touch-active-work` stamps the next session.
    pointer_path: Path | None = None
    if application.ok and outcome.plan.trigger == "dispatch" and new_phase is not None and new_phase != "done":
        pointer_path = provenance.write_active_work(layout, path, new_phase, updated=today.isoformat())
    return StageAdvance(
        outcome=outcome,
        results_path=results_path,
        pointer_path=pointer_path,
        repo_note=repo_note,
        application=application,
        warnings=inference_warnings + drift_warnings + baseline_warnings + warnings,
        gate_bypass=bypass if application.ok else None,
        gate_receipt=gate_receipt,
    )


def _refuse(outcome: AdvanceOutcome, reason: RefusalReason, detail: str) -> AdvanceOutcome:
    refused = replace(
        outcome.plan, refusal=reason, changes=(), stamp_source=None, stamp_baseline=False, detail=detail, trigger=None
    )
    return replace(outcome, plan=refused, stamped=None, stamp_title=None, plan_row=False)


def _resolve_repo(
    layout: WorkspaceLayout,
    items: Sequence[WorkItem],
    item: WorkItem | None,
    *,
    repo_name: str | None,
    cwd: Path | None,
) -> tuple[ItemRepo, tuple[Path, ...]]:
    """`(repo, declared)`: the code repo this advance reads, and every declared
    one for postcondition validation.

    The item's own `repo:` (or an ancestor's) wins, then *repo_name*; both are
    `resolve_item_repo`'s. Otherwise the cwd matcher answers: one declared
    repo, or none, is `resolve_repo`'s answer; several are narrowed to the one
    *cwd*'s repository belongs to (`provenance.repository_of` -- a linked
    worktree of it counts). When none does, the repo is `None` with a note:
    the same degrade as a workspace that declares no repo, so inference is
    skipped and the commit gate refuses `no-repo`. The cwd matcher never refuses.
    """
    declared = resolve_repos(layout)

    def by_cwd() -> ItemRepo:
        names = declared_repositories(layout)
        if len(names) > 1:
            here = cwd or Path.cwd()
            match = provenance.repository_of(here, tuple(names.values()))
            if match is not None:
                return ItemRepo(next(name for name, path in names.items() if path == match), match, "cwd")
            return ItemRepo(
                None,
                None,
                "cwd",
                f"{layout.manifest_path}: {len(names)} repositories declared and {here} is in none of them, "
                "so no code repo was resolved",
            )
        path, note = resolve_repo(layout)
        return ItemRepo(next(iter(names), None), path, "sole", note)

    by_path = {candidate.path: candidate for candidate in items}
    return resolve_item_repo(layout, item, by_path, repo_name=repo_name, fallback=by_cwd), declared


def _affects_drift_warnings(bundle_root: Path, item: WorkItem, *, definition: PipelineDefinition) -> tuple[str, ...]:
    """Advisory plan-file drift, reading a registered source or managed plan artifact.

    The plan-to-execute advance usually registers the source, so its managed
    artifact is the normal fallback. The later commit gate checks actual changes.
    """
    registered = next(
        (
            source.resource
            for source in item.sources
            if source.id == definition.artifacts["plan"].source and source.resource
        ),
        None,
    )
    plan_path = (
        bundle_root / registered.removeprefix("/")
        if registered
        else stage_artifact_ref(item.path, definition.artifacts["plan"]).path(bundle_root)
    )
    try:
        text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ("affects drift: no plan artifact to read",)
    files = plan_files(text)
    if not files:
        return ("affects drift: no file bullets found in the plan",)
    drift = affects_drift(files, code_affects(item.affects))
    if not drift.uncovered:
        return ()
    return (
        f"affects drift: the plan names {len(drift.uncovered)} path(s) outside `affects`: "
        f"{', '.join(drift.uncovered)}. Suggested widening: {', '.join(drift.widening)}",
    )


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """The execute -> finish gate's answer. `refusal is None` is a pass; `note`
    explains a pass that evaluated nothing because the gate does not apply."""

    refusal: RefusalReason | None
    detail: str = ""
    note: str | None = None
    root: Path | None = None
    receipt: GateEvidence | None = None


AnchorRoot = Callable[[], "Path | GateVerdict"]


def _anchor_resolver(
    layout: WorkspaceLayout,
    items: Sequence[WorkItem],
    item: WorkItem | None,
    repo: Path | None,
    item_repo: ItemRepo | None,
) -> AnchorRoot | None:
    """A memoized, lazy selector for a descendant's enclosing integration anchor.

    `None` for a top-level item (the declared checkout stays its fallback) or when
    no repository resolved. Observation of the repository happens at most once.
    """
    if item is None or repo is None or _infers_from_cwd(item):
        return None
    by_path = {candidate.path: candidate for candidate in items}
    memo: list[Path | GateVerdict] = []

    def refuse(detail: str) -> GateVerdict:
        # the closed vocabulary keeps `worktree-missing`; a bypass never authorizes reading trunk
        return GateVerdict("worktree-missing", detail)

    def select() -> Path | GateVerdict:
        owner = enclosing_owner(item, by_path)
        if owner is None:
            return refuse(
                f"{item.path} has no enclosing integration owner (Epic, Release or Feature with children) and "
                "records no worktree; the gate never substitutes the declared checkout -- record its placement"
            )
        repo_name = item_repo.name if item_repo is not None else _repo_name_of(layout, repo)
        gated = ItemRepo(repo_name, repo, "flag")
        try:
            owner_repo = resolve_item_repo(layout, owner, by_path, fallback=lambda: gated)
            anchor_ = reader_anchor(
                owner,
                repos={owner.path: owner_repo},
                repo=gated,
                context=observe_repository(repo),
            )
        except WorkspaceError as exc:
            return refuse(f"{item.path}: integration owner {owner.path}: {exc}")
        if isinstance(anchor_, AnchorRefusal):
            return refuse(f"{item.path}: nearest integration owner {owner.path}: {anchor_.reason}")
        if not isinstance(anchor_, Anchor):
            return refuse(
                f"{item.path} records no worktree and its nearest integration owner {owner.path} records no "
                f"anchor for repository {repo_name or repo}; record the placement (`gw work record-placement`)"
            )
        return Path(anchor_.worktree)

    def resolve() -> Path | GateVerdict:
        if not memo:
            memo.append(select())
        return memo[0]

    return resolve


def _gate_placement(item: WorkItem, repo_name: str | None) -> tuple[str | None, str | None]:
    """`(worktree, start_sha)` recorded for the gated repository: its `repo_stamps`
    entry when it has one *with* a `start_sha`, else the scalar placement. A
    stamp without a baseline still supplies its worktree."""
    stamp = item.repo_stamps.get(repo_name) if repo_name is not None else None
    if stamp is not None:
        return stamp.worktree, stamp.start_sha or item.start_sha
    return item.worktree, item.start_sha


def _commit_gate(
    item: WorkItem,
    *,
    anchor_root: AnchorRoot | None = None,
    repo: Path | None,
    repo_note: str | None,
    repo_name: str | None,
    stamped_worktree: str | None,
    explicit_start: str | None,
    git: provenance.GitExecutable | provenance.GitFailure,
    worktree_inferred: bool = False,
) -> GateVerdict:
    """Whether the execute stage left its work, and only its work, where git can see it.

    Fails **closed**. Every input the gate needs -- a code scope, a repository,
    the recorded worktree, a runnable git, a baseline, a readable range -- is
    either present or the advance refuses with its own code (D-004). The only
    way past a refusal is `gw work advance --skip-gate <code> --reason ... --actor ...`,
    which the ledger records. A `gw:workspace`-only item is the one pass that
    evaluates nothing: it has no code surface, and its workspace checks run elsewhere.

    Order: no-affects, no-repo, worktree-missing, git-unavailable, then the three
    evaluated signals -- uncommitted-work before the baseline is even needed,
    then no-start-sha, range-unreadable, no-commits, no-affects-touched (most
    specific last: an empty range has an empty file list too).

    False positives are accepted and recoverable -- unrelated dirt under an
    `affects` directory refuses an advance that would otherwise pass. The
    refusal names every offending path.
    """
    paths = code_affects(item.affects)
    if not paths:
        if touches_workspace(item.affects):
            return GateVerdict(
                None, note=f"{_GATE_NOTE}: workspace-only item (`gw:workspace`), no code surface to gate"
            )
        return GateVerdict(
            "no-affects",
            "the item declares no code `affects` to scope the gate to; declare them, or mark a workspace-only "
            "item with `gw:workspace`",
        )
    if repo is None:
        note = f" ({repo_note})" if repo_note else ""
        return GateVerdict("no-repo", f"no code repository resolved for {item.path}{note}")
    recorded_worktree, recorded_start = _gate_placement(item, repo_name)
    if stamped_worktree and not (worktree_inferred and recorded_worktree and not Path(recorded_worktree).is_dir()):
        root = Path(stamped_worktree)
    elif recorded_worktree:
        root = Path(recorded_worktree)
        if not root.is_dir():
            return GateVerdict(
                "worktree-missing",
                f"{item.path} records worktree {recorded_worktree}, which no longer exists; the gate never "
                "substitutes another checkout -- restore it or record the placement again",
            )
    elif anchor_root is not None and not _infers_from_cwd(item):
        # D-004: an unstamped descendant reads its enclosing anchor, never the declared checkout.
        selected = anchor_root()
        if isinstance(selected, GateVerdict):
            return selected
        root = selected
    else:
        root = repo

    def refuse(code: RefusalReason, detail: str) -> GateVerdict:
        # the resolved root rides along so a bypassed commit refusal still reaches the receipt gate
        return GateVerdict(code, detail, root=root)

    if isinstance(git, provenance.GitFailure):
        return refuse("git-unavailable", f"no usable git ({git.cause}): {git.detail}")
    dirty = provenance.strict_dirty_paths(root, paths, git=git)
    if isinstance(dirty, provenance.GitFailure):
        return refuse("git-unavailable", f"`git status` could not be read in {root} ({dirty.cause}): {dirty.detail}")
    if dirty:
        return refuse(
            "uncommitted-work",
            "the execute stage left uncommitted changes under `affects`: "
            + ", ".join(dirty)
            + " -- commit them (or stash what does not belong to this item), then advance again",
        )
    start = explicit_start or recorded_start
    if not start:
        return refuse(
            "no-start-sha",
            f"{item.path} has no recorded execute baseline (`start_sha`) and none was passed; record one with "
            f"`gw work record-baseline {item.path}` before execute work starts, pass --start-sha, or, for an item "
            f"that predates baselines, `gw work advance {item.path} --from execute --skip-gate no-start-sha "
            '--reason "..." --actor <you>`',
        )
    resolved = provenance.strict_commit(root, start, git=git)
    if isinstance(resolved, provenance.GitFailure):
        return refuse(
            "range-unreadable", f"start_sha {start} does not resolve to a commit in {root}: {resolved.detail}"
        )
    facts = provenance.strict_range(root, start_sha=resolved, paths=paths, git=git)
    if isinstance(facts, provenance.GitFailure):
        return refuse("range-unreadable", f"the range {start}..HEAD is unreadable in {root}: {facts.detail}")
    if not facts.commits:
        return refuse(
            "no-commits", f"the execute stage recorded no commits touching `affects` in {start}..{facts.end_sha}"
        )
    if not facts.files:
        return refuse(
            "no-affects-touched",
            "the execute stage committed work but touched nothing under this item's declared "
            f"surface ({', '.join(facts.scope)}); either the work landed outside `affects` or "
            "`affects` is wrong",
        )
    return GateVerdict(None, root=root)


def _repo_name_of(layout: WorkspaceLayout, repo: Path | None) -> str | None:
    if repo is None:  # pragma: no cover -- the commit gate refuses `no-repo` before a receipt is consulted
        return None
    return next(
        (name for name, path in declared_repositories(layout).items() if path.resolve() == repo.resolve()), None
    )


def _receipt_gate(layout: WorkspaceLayout, item: WorkItem, root: Path, repo_name: str | None) -> GateVerdict:
    """Execute -> finish needs a green full-gate receipt for the tree being handed over.

    Runs only after the commit gate passed on a code item, so the tree is clean and committed.
    """
    if repo_name is None:
        return GateVerdict("no-gate-configured", f"{item.path}: its repository is not a declared repository")
    gate = repo_gate(layout, repo_name)
    if gate.full is None:
        return GateVerdict(
            "no-gate-configured",
            f"set repositories.{repo_name}.gate.full in {layout.manifest_path}, then run "
            f"`gw work gate run {item.path}`",
        )
    git = provenance.gate_git(layout)
    if isinstance(git, provenance.GitFailure):
        return GateVerdict("git-unavailable", f"no usable git ({git.cause}): {git.detail}")
    try:
        snap = gate_git.snapshot(root, git=git)
    except gate_git.GitUnavailable as exc:
        return GateVerdict("git-unavailable", str(exc))
    try:
        state = gate_units.resolve_unit_state(gate, root, snap.tree, git=git)
        if gate.units is not None:
            after = gate_git.snapshot(root, git=git)
            if after.dirty:
                return GateVerdict(
                    "uncommitted-work", "manifest command left uncommitted changes: " + ", ".join(after.dirty)
                )
            if after.tree != snap.tree:
                return GateVerdict(
                    "git-unavailable", f"manifest command changed gated tree: expected {snap.tree}, found {after.tree}"
                )
    except gate_units.ManifestError as exc:
        return GateVerdict(exc.reason, f"{repo_name}: {exc}")
    except gate_git.GitUnavailable as exc:
        return GateVerdict("git-unavailable", str(exc))
    result = evaluate(
        layout, repo=repo_name, tree=snap.tree, full_command=gate.full,
        repo_wide_command=state.manifest.repo_wide, hashes=state.hashes,
    )  # fmt: skip
    if not result.satisfied or result.evidence is None:
        missing: list[str] = []
        if result.stale and not state.manifest.implicit:
            missing.append(f"stale units: {', '.join(result.stale)}")
        if not result.repo_wide_green:
            missing.append("repo-wide checks not green on this tree")
        what = "; ".join(missing) or f"no green `{gate.full}` receipt"
        return GateVerdict(
            "no-gate-receipt",
            f"{what} for tree {snap.tree} in {repo_name}; run "
            f"`gw work gate run {item.path}` then `gw work gate wait {item.path}`",
        )
    note = "; ".join((f"gate receipt: {result.evidence.owner} run {result.evidence.run_id}", *result.warnings))
    return GateVerdict(None, note=note, receipt=result.evidence)


def _effective_start_sha(
    explicit: str | None,
    *,
    definition: PipelineDefinition,
    phase: str | None,
    facts_root: Path,
    bundle_root: Path,
    item: WorkItem,
    repo_name: str | None,
) -> str | None:
    """The start of the range this stage covers, or `None` for no stub.

    An explicit caller argument wins. At `execute`, the placement's recorded
    baseline comes next, then a derived one. At `finish` nothing else applies:
    the recorded baseline is the *execute* range's start, and a finish stub
    built on it would claim the execute commits.
    """
    if explicit:
        return explicit
    if phase != "execute":
        return None
    _worktree, recorded = _gate_placement(item, repo_name)
    if recorded:
        return recorded
    spec_path = bundle_root / anchor.spec_ref(item, definition=definition)
    spec_text = spec_path.read_text(encoding="utf-8") if spec_path.is_file() else ""
    return anchor.phase_start_sha(facts_root, spec_path, spec_text)


def _facts_root(
    item: WorkItem | None, worktree: str | None, repo: Path | None, *, anchor_root: AnchorRoot | None = None
) -> Path | None:
    """Where the stage's commits actually landed: the worktree this call
    detected, then the item's recorded one, then (for a descendant with no
    recorded placement) its enclosing anchor, then the repo. A stub gathered
    from the main checkout when the work happened in a worktree is a stub of
    the wrong range. A descendant whose anchor cannot be proven gets no stub
    rather than a trunk one."""
    for candidate in (worktree, item.worktree if item is not None else None):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    if item is not None and anchor_root is not None and not item.worktree and not _infers_from_cwd(item):
        selected = anchor_root()
        return selected if isinstance(selected, Path) else None
    return repo


__all__ = ["RESULTS_PHASES", "StageAdvance", "run_stage_advance"]
