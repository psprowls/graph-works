"""One stage completion: advance the item, then capture what the stage left.

Split out of `commands.py`, which held this shell and the IO-free planner in
one 1240-line file. The two halves share no module-level symbol — every name
here is an external import — so the seam was already there and this module
only states it. There is deliberately **no re-export from `commands.py`**: a
re-export would be a surface both the planner lane and the stage-gate lane
still have a reason to edit, which is exactly the collision the split removes.

Provenance never fails an advance. A `None` `results_path` or `pointer_path`
is a normal outcome, not an error.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType

from okf_io import load_bundle
from work_tracker_okf.advance import RefusalReason
from work_tracker_okf.advance import apply as apply_advance
from work_tracker_okf.compose import AdvanceOutcome, advance_and_stamp, ensure_plan_row
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref, item_page
from work_tracker_okf.results import render as render_results
from work_tracker_okf.sources import upsert
from work_tracker_okf.workflow import route, state_for

from graph_works_core.workspace import anchor, provenance
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repo
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
    `results_path`, a `done` landing for `pointer_path`), and whenever the
    corresponding capture degraded -- provenance never fails an advance, so
    "nothing was written" is a normal outcome, not an error.

    `repo_note` carries `resolve_repo`'s note: the reason no code repo was
    resolved. Named for exactly that and not for a general provenance log --
    when it is set, every git-derived field above it is `None` for one known
    reason rather than for an unknown one.

    `warnings` carries what the `execute -> finish` commit gate could not
    evaluate. The gate fails **open**: an advance that cannot see a repo, or
    an item that declares no `affects`, proceeds -- but says so, because an
    unevaluable gate is otherwise indistinguishable from a gate that passed.
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


def _stage_phase(items: Sequence[WorkItem], item: WorkItem, *, effort: str | None) -> str | None:
    """The phase whose stage just ran, for an advance that is completing it.

    `item.phase` when it is set. A never-advanced item has no recorded phase,
    and its stage is the one its entry transition is about to open -- `design`
    for a Bug or Feature, `execute` for a TestGap routed straight at code.
    Reading that destination is what keeps the suppression below from also
    silencing a first-ever `execute`, which `item.phase is None` alone cannot
    distinguish from a first-ever `design`.
    """
    if item.phase is not None:
        return item.phase
    state = state_for(items, item.path, effort=effort)
    if state is None:  # pragma: no cover -- `item` was drawn from `items`, so `state_for`
        # cannot fail to find it there
        return None
    entry = route(state).on_dispatch
    return entry.phase if entry is not None else None


def _infers_from_cwd(items: Sequence[WorkItem], item: WorkItem, *, effort: str | None) -> bool:
    """Whether a cwd-inferred stamp may be written for this advance.

    **A stage that cannot produce a commit must not acquire a placement
    stamp.** `design` and `plan` write only into the vault, yet a stamp taken
    there pins `execute` and `finish` -- the phases that do commit -- to
    whatever directory a vault-only stage happened to sit in. Suppressing the
    inference here is the enforcement site that matters: a worker running a
    bare `gw work advance` from inside a shared epic worktree re-stamps the
    item regardless of what the planner emitted, so no change confined to
    `commands.py` would prevent it.

    The subtree root is exempt at every phase. `_epic_stamp` prefers the
    root's own stamp over its acknowledged-unstable descendant-scan fallback,
    and an epic's `execute` phase dispatches *children* rather than a worker
    for the epic itself -- so a root that did not stamp at `design` or `plan`
    would never stamp at all, and no descendant would have an anchor to
    resolve against.

    `parent_path is None` stands in for "is the subtree root of this run".
    This module is `gw work advance`'s shell and has no run root to compare
    against, and the two coincide for every epic-and-children shape in this
    vault. Where they would not -- a run rooted at a nested epic -- the
    planner already tells every root dispatch to pass `--worktree`/`--branch`
    explicitly (`commands._prompt`), and an explicit pair wins over inference
    unconditionally. This test is the backstop, not the mechanism.
    """
    if item.parent_path is None:
        return True
    return _stage_phase(items, item, effort=effort) in RESULTS_PHASES


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
    cwd: Path | None = None,
    repo: Path | None = None,
    repo_name: str | None = None,
    start_sha: str | None = None,
    return_: bool = False,
    dry_run: bool = True,
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
    when `--git-dir` and `--git-common-dir` agree), which is why a main-mode
    worker is told to pass the pair explicitly.

    Inference is suppressed outright for a **read-only stage of a non-root
    item** -- see `_infers_from_cwd`. A `design` or `plan` stage leaves no
    commit, so the directory it ran in is not a placement, and letting it
    become one pins every later code phase to a directory nothing chose.

    `start_sha` is a **caller argument**, not a frontmatter field. The reference
    implementation stamped a `phase_started_commit` key; adding one here is a
    schema change this item's spec does not take, so the caller that knows where
    the phase started supplies it. Omitted at the `execute` stage, it is derived
    by `workspace.anchor.phase_start_sha` — merge-base first, then the spec's own
    arms — which is a fallback derivation, not a schema change. Omitted at
    `finish` it derives nothing: a derived range there sweeps in the whole
    execute range, and a finish report claiming work it did not do is exactly
    what this pipeline exists to prevent.

    `repo` defaults to `resolve_repo(layout, repo_name=repo_name)` rather than
    to the layout's `repo_root`: in a split topology -- the workspace and the
    code in different git repositories -- the walk-up resolves to the
    workspace's own repo, and both `worktree_state` and `results_facts` then
    degrade to `None` without a word. An explicit `repo` wins and skips the
    config read. `repo_name` selects among several declared repositories and
    is ignored when `repo` is given.

    `return_=True` walks the routing table's one backwards transition,
    `finish -> execute`. It writes no results stub: a return is not a stage
    completion, and a `finish` stub for a stage that is being reopened would
    claim work nobody did.

    `dry_run=True` is okf-io's writer default throughout this workspace: the
    call plans and writes nothing -- not the page, not the stub, not the
    pointer. The commit gate below is bound by the same rule: a dry run
    inspects no worktree and refuses nothing.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    item = next((candidate for candidate in items if candidate.path == path), None)
    old_phase = item.phase if item is not None else None
    repo_note: str | None = None
    resolved_repo = repo
    if resolved_repo is None:
        resolved_repo, repo_note = resolve_repo(layout, repo_name=repo_name)

    stamped_worktree: str | None = None
    stamped_branch: str | None = None
    if worktree and branch:
        stamped_worktree, stamped_branch = worktree, branch
    elif item is not None and resolved_repo is not None and _infers_from_cwd(items, item, effort=effort):
        recorded = item.worktree
        if not recorded or not Path(recorded).is_dir():
            detected = provenance.worktree_state(cwd or Path.cwd(), resolved_repo)
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
        dry_run=True,
    )
    if dry_run or outcome.plan.refusal is not None or outcome.plan.transition is None:
        return StageAdvance(outcome=outcome, repo_note=repo_note)

    new_phase = outcome.plan.transition.phase or old_phase

    assert item is not None
    warnings: tuple[str, ...] = ()
    if old_phase == "execute" and new_phase == "finish":
        refusal, detail, warnings = _commit_gate(
            item, _facts_root(item, stamped_worktree, resolved_repo), start_sha=start_sha, repo_note=repo_note
        )
        if refusal is not None:
            refused = replace(outcome.plan, refusal=refusal, changes=(), stamp_source=None, detail=detail)
            return StageAdvance(
                outcome=replace(outcome, plan=refused, stamped=None, stamp_title=None, plan_row=False),
                repo_note=repo_note,
                warnings=warnings,
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
    application = apply_mutation(layout, mutation, repo_root=resolved_repo)
    if application.ok:
        outcome = replace(outcome, written=True)
        if result_member is not None:
            results_path = bundle.root / result_member

    pointer_path: Path | None = None
    if application.ok and new_phase is not None and new_phase != "done":
        pointer_path = provenance.write_active_work(layout, path, new_phase, updated=today.isoformat())
    return StageAdvance(
        outcome=outcome,
        results_path=results_path,
        pointer_path=pointer_path,
        repo_note=repo_note,
        application=application,
        warnings=warnings,
    )


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
