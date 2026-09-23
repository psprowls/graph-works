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

from okf_io import Bundle, load_bundle
from work_tracker_okf.advance import RefusalReason
from work_tracker_okf.advance import apply as apply_advance
from work_tracker_okf.compose import AdvanceOutcome, advance_and_stamp, ensure_plan_row
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref, item_page
from work_tracker_okf.results import render as render_results
from work_tracker_okf.sources import upsert

from graph_works_core.workspace import anchor, provenance
from graph_works_core.workspace.decision_owner import hold_for, hold_in, locked_decision_owner
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import (
    ItemRepo,
    declared_repositories,
    resolve_item_repo,
    resolve_repo,
    resolve_repos,
)
from graph_works_core.workspace.transactions import MutationApplication, apply_mutation

#: The phases whose *completion* produces a results stub. A design or plan
#: stage leaves an artifact of its own; only the two that touch code leave a
#: commit range worth summarizing.
RESULTS_PHASES: frozenset[str] = frozenset({"execute", "finish"})

#: The prefix every unevaluable-gate warning carries, so a reader grepping the
#: coordinator's output finds all of them with one string.
_GATE = "execute -> finish gate not evaluated"


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

    `warnings` carries what the `execute -> finish` commit gate could not
    evaluate. The gate fails **open**: an advance that cannot see a repo, or
    an item that declares no `affects`, proceeds -- but says so, because an
    unevaluable gate is otherwise indistinguishable from a gate that passed.
    It also carries a skipped worktree inference when the item's repository
    came from `repo:` (frontmatter) or `repo_name` (flag) and cwd is not in
    that repository -- inference is skipped, never a silent guess, and a
    foreign checkout is never stamped.
    """

    outcome: AdvanceOutcome
    results_path: Path | None = None
    pointer_path: Path | None = None
    repo_note: str | None = None
    application: MutationApplication | None = None
    warnings: tuple[str, ...] = ()

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

    `start_sha` is a **caller argument**, not a frontmatter field. The reference
    implementation stamped a `phase_started_commit` key; adding one here is a
    schema change this item's spec does not take, so the caller that knows where
    the phase started supplies it. Omitted at the `execute` stage, it is derived
    by `workspace.anchor.phase_start_sha` — merge-base first, then the spec's own
    arms — which is a fallback derivation, not a schema change. Omitted at
    `finish` it derives nothing: a derived range there sweeps in the whole
    execute range, and a finish report claiming work it did not do is exactly
    what this pipeline exists to prevent.

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
    pointer. The commit gate below is bound by the same rule: a dry run
    inspects no worktree and refuses nothing.

    `before_apply`, when supplied on a live call, inspects the actual candidate
    with application fields empty before any domain write. Raising aborts the
    call; exceptions propagate. The callback must not mutate the candidate or
    workspace. Dry runs never invoke it. Omitting it preserves CLI behavior.

    The callback sees the routed advance under the decision-owner lock, before
    the live-only commit gate. That gate may still refuse or add diagnostics;
    it cannot select a different transition after validation.

    Live advances hold the decision owner's lock across the read, hold
    resolution, routing, commit gate and mutation. Dry runs resolve holds
    without locking. On win32, `okf_ext.locking` gives up after ten one-second
    retries and raises `OSError` naming the lock; the CLI reports it as `io`.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    if dry_run or not any(item.path == path for item in items):
        return _advance(
            layout,
            bundle,
            items,
            path,
            hold=hold_for(items, bundle.root, path) if dry_run else None,
            dry_run=dry_run,
            today=today,
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
            before_apply=before_apply,
        )
    # The whole read -> route -> gate -> write sequence shares hold filing's
    # owner lock. A waiting writer sees the hold or phase the first committed.
    # Release only after apply_mutation (and the pointer write) returns.
    with locked_decision_owner(layout, path) as context:
        return _advance(
            layout,
            context.bundle,
            context.items,
            path,
            hold=hold_in(context, path),
            dry_run=False,
            today=today,
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
            before_apply=before_apply,
        )


def _advance(
    layout: WorkspaceLayout,
    bundle: Bundle,
    items: Sequence[WorkItem],
    path: str,
    *,
    hold: HoldFact | None,
    today: date,
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
                detected = provenance.worktree_state(here, resolved_repo)
                if detected is not None:
                    stamped_worktree, stamped_branch = detected

    outcome = advance_and_stamp(
        bundle,
        path,
        today=today,
        effort=effort,
        owner=owner,
        resolved_in=resolved_in,
        released_at=released_at,
        worktree=stamped_worktree,
        branch=stamped_branch,
        return_=return_,
        hold=hold,
        dry_run=True,
    )
    candidate = StageAdvance(outcome=outcome, repo_note=repo_note, warnings=inference_warnings)
    if not dry_run and before_apply is not None:
        before_apply(candidate)
    if dry_run or outcome.plan.refusal is not None or outcome.plan.transition is None:
        return candidate

    new_phase = outcome.plan.transition.phase or old_phase

    assert item is not None
    warnings: tuple[str, ...] = ()
    if old_phase == "execute" and new_phase == "finish":
        refusal, detail, warnings = _commit_gate(
            item, _facts_root(item, stamped_worktree, resolved_repo), start_sha=start_sha, repo_note=repo_note
        )
        if refusal is not None:
            refused = replace(outcome.plan, refusal=refusal, changes=(), stamp_source=None, detail=detail, trigger=None)
            return StageAdvance(
                outcome=replace(outcome, plan=refused, stamped=None, stamp_title=None, plan_row=False),
                repo_note=repo_note,
                warnings=inference_warnings + warnings,
            )

    document = load_bundle(layout.bundle_dir, ignore=IGNORE).concepts[path]
    apply_advance(document, outcome.plan)
    if outcome.stamped is not None and outcome.stamp_title is not None:
        upsert(document, outcome.stamped, title=outcome.stamp_title)
        if outcome.plan.sync_plan_table:
            ensure_plan_row(document, outcome.stamped)

    results_path: Path | None = None
    result_member: str | None = None
    result_bytes: bytes | None = None
    facts_root = _facts_root(item, stamped_worktree, resolved_repo)
    if not return_ and facts_root is not None and old_phase in RESULTS_PHASES and new_phase != old_phase:
        effective_start_sha = _effective_start_sha(
            start_sha, phase=old_phase, facts_root=facts_root, bundle_root=bundle.root, item=item
        )
        if effective_start_sha is not None:
            facts = provenance.results_facts(
                facts_root,
                phase=old_phase,
                start_sha=effective_start_sha,
                paths=item.affects,
                opened=item.opened,
            )
            if facts is not None:
                key = f"{facts.phase}-results"
                ref = artifact_ref(path, MANAGED_ARTIFACTS[key])
                result_member = ref.rel
                result_bytes = render_results(facts).encode("utf-8")
                upsert(document, ref, title=f"{facts.phase.capitalize()} results")

    if old_phase == "execute" and new_phase == "finish":
        coverage_ref = artifact_ref(path, MANAGED_ARTIFACTS["execute-coverage"])
        if coverage_ref.path(bundle.root).exists():
            upsert(document, coverage_ref, title="Execute coverage")

    page_member = item_page(path).rel
    page_before = (bundle.root / page_member).read_bytes()
    writes = [PlannedWrite(page_member, hashlib.sha256(page_before).hexdigest(), document.serialize().encode("utf-8"))]
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
        validate_paths=(path,),
        directory_preconditions=conditions,
    )
    # `bundle` is the lock-held projection (or the dry-run read), loaded with
    # `IGNORE`, so it satisfies `apply_mutation`'s baseline precondition.
    application = apply_mutation(layout, mutation, repo_root=resolved_repo, repo_roots=declared, baseline_bundle=bundle)
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
        warnings=inference_warnings + warnings,
    )


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
    skipped and the commit gate fails open. The cwd matcher never refuses.
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


def _commit_gate(
    item: WorkItem, facts_root: Path | None, *, start_sha: str | None, repo_note: str | None
) -> tuple[RefusalReason | None, str, tuple[str, ...]]:
    """Whether the execute stage left its work somewhere git can see it.

    Three independent signals, any of which refuses. **Uncommitted work**
    reads `git status` scoped to `item.affects` and needs nothing but a
    worktree -- which is the point: `start_sha` is a caller argument with no
    frontmatter home, so a gate that could only speak when the coordinator
    remembered to supply one would be silent in exactly the unattended run
    this gate exists for. **Zero commits** reads the range and needs an
    *explicit* `start_sha`; a derived one is not enough, because the
    merge-base derivation answers `HEAD` on the main checkout and would refuse
    every advance made from there. **Nothing touched** reads the same range's
    file list: commits that net out to no change under `affects` are work the
    stage cannot have landed where it said it would.

    Fails **open, loudly**. No repo, no `affects`, or a `git status` that
    itself failed all return `(None, "", (warning,))`: `gw work advance` runs
    against workspaces with no code repo at all, and a fail-closed unevaluable
    gate would break the pipeline everywhere for a condition it cannot even
    observe.

    False positives are accepted and recoverable -- unrelated dirt under an
    `affects` directory refuses an advance that would otherwise pass. The
    refusal names every offending path, and committing or stashing them is
    cheaper, and far more visible, than the silent data loss it replaces.
    """
    if facts_root is None:
        note = f" ({repo_note})" if repo_note else ""
        return None, "", (f"{_GATE}: no code repository resolved{note}",)
    if not item.affects:
        return None, "", (f"{_GATE}: the item declares no `affects` paths to scope the read to",)
    dirty = provenance.dirty_paths(facts_root, item.affects)
    if dirty is None:
        return None, "", (f"{_GATE}: `git status` could not be read in {facts_root}",)
    if dirty:
        return (
            "uncommitted-work",
            "the execute stage left uncommitted changes under `affects`: "
            + ", ".join(dirty)
            + " -- commit them (or stash what does not belong to this item), then advance again",
            (),
        )
    if not start_sha:
        return None, "", (f"{_GATE}: no explicit `start_sha` to read the commit range from",)
    facts = provenance.results_facts(
        facts_root, phase="execute", start_sha=start_sha, paths=item.affects, opened=item.opened
    )
    if facts is None:
        return None, "", (f"{_GATE}: no commit range readable from {start_sha}",)
    if not facts.commits:
        return (
            "no-commits",
            f"the execute stage recorded no commits touching `affects` in {start_sha}..{facts.end_sha}",
            (),
        )
    # Evaluation order is uncommitted-work -> no-commits -> no-affects-touched,
    # most-specific diagnosis last: an item with zero commits has an empty file
    # list too, and should be told it committed nothing rather than that it
    # touched nothing.
    if not facts.files:
        return (
            "no-affects-touched",
            "the execute stage committed work but touched nothing under this item's declared "
            f"surface ({', '.join(facts.scope)}); either the work landed outside `affects` or "
            "`affects` is wrong",
            (),
        )
    return None, "", ()


def _effective_start_sha(
    explicit: str | None, *, phase: str | None, facts_root: Path, bundle_root: Path, item: WorkItem
) -> str | None:
    """The start of the range this stage covers, or `None` for no stub.

    An explicit caller argument wins outright, at either results phase. Omitted,
    only `execute` derives one: a merge-base or spec-anchor range at `finish`
    sweeps in the whole execute range, so the finish report would claim work it
    did not do — precisely the failure this epic exists to remove. At `finish`
    the item declines to guess and requires the flag.
    """
    if explicit:
        return explicit
    if phase != "execute":
        return None
    spec_path = bundle_root / anchor.spec_ref(item)
    spec_text = spec_path.read_text(encoding="utf-8") if spec_path.is_file() else ""
    return anchor.phase_start_sha(facts_root, spec_path, spec_text)


def _facts_root(item: WorkItem | None, worktree: str | None, repo: Path | None) -> Path | None:
    """Where the stage's commits actually landed: the worktree this call
    detected, then the item's recorded one, then the repo. A stub gathered from
    the main checkout when the work happened in a worktree is a stub of the
    wrong range."""
    for candidate in (worktree, item.worktree if item is not None else None):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return repo


__all__ = ["RESULTS_PHASES", "StageAdvance", "run_stage_advance"]
