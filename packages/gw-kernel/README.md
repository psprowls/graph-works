# gw-kernel

Band 3. The one package that knows what a workspace is. Everything below it
receives resolved paths as arguments — there is no `graph_dir(workspace)`
anywhere beneath this line, and an import-linter contract in the workspace root
makes a violation a `just check` failure.

## Platform

`workspace/anchors.py` imports `fcntl` at four sites, each behind a
`sys.platform` guard, choosing between a `_PosixAnchor` and a `_WindowsAnchor`
implementation of the five POSIX-only primitives (`flock`, the NOREPLACE
rename, descriptor-to-path resolution, `st_dev`/`st_ino` identity, directory
`fsync`) that the rest of `workspace` depends on. That abstraction is what
lets the durability tier and the work vertical both depend on locking without
either carrying its own platform seam. See `gw util platform` for the live,
per-capability answer on the running host.

`orchestrate/gate.py` starts the gate runner detached: `start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows. `orchestrate/gate_runner.py` runs the configured gate command through `sh -c` on POSIX and `%COMSPEC% /c` on Windows, so a gate command must be written for the shell of the host that runs it. Runner liveness is a held `okf_ext.locking` lock, never a signal to a pid.

## The layout

```
.works/
  workspace.yaml    the manifest — version, topic, four layout overrides,
                    plus the bundle declarations (`repositories`, `ignore`,
                    `state_gate`) merged in per ADR 2026-08-22-single-workspace-config
  .gw/              every graph-works-owned machinery member, flattened together
    schema/         committed declarations
    sections/       committed declarations
    tags.yaml       committed declaration
    cache/          gitignored machine state: the graph database, config.json
    worktrees/      gitignored feature worktrees
    .gitignore      /cache/ and /worktrees/ — the workspace's own, never the repo's
  okf/              the OKF v0.2 bundle: index.md, log.md, pages
```

`workspace.yaml` stays at the **workspace root**, not under `.gw/` or the
bundle: `workspace.config.load_workspace_config` reads it from there, via
`workspace_store` and `code_wiki_okf.config.config_from_mapping` (or from
wherever a layout override points it).

## Using it

```python
from datetime import date
from gw_kernel import apply_init, plan_init, resolve

plan = plan_init(root, today=date.today(), topic="My Works")
if not plan.is_empty:
    result = apply_init(plan)
    print(result.diff())

layout = resolve()  # explicit argument -> GRAPH_WORKS_DIR -> .git walk-up
```

`plan_init` writes nothing — **not calling `apply_init` is the dry run**. There
is no `dry_run` flag, following the six shipped writers in `okf-ext` and
`work-tracker-okf` that return plans.

## One config surface

`workspace.yaml` is the workspace's one configuration file:
`version`, `initialized_at`, `topic`, the four layout overrides, and the
bundle declarations `load_workspace_config` reads — `repositories`,
`ignore`, `state_gate` — via `workspace_store` and `config_from_mapping`, the
one adapter over `code_wiki_okf.config`. `graph_dir` and `declarations_dir` are not stored in
it; they are resolved from the layout and supplied by the caller at read time
(e.g. `graph_dir=layout.cache_dir`).

## Discovery

Precedence: explicit argument → `GRAPH_WORKS_DIR` → a `.git` walk-up from cwd,
defaulting to `<repo>/.works`. The marker is `<root>/workspace.yaml`; without
it, `resolve` raises `WorkspaceNotFound`. `GRAPH_WIKI_WORKSPACE` is
deliberately not consulted — it names the old layout.

Config raises; content never does. `WorkspaceError` (a `ValueError`) with
`WorkspaceNotFound` and `InitError` subclasses.

## Work commands (qualified only)

Use `work_tracker_okf` directly when the caller already owns a bundle root,
loaded `Bundle`, and `WorkItem` projection. Use
`gw_work.commands` when the caller owns a `WorkspaceLayout` and
wants workspace path resolution composed around those domain plans.

```python
from gw_work import commands as work
```

The work vertical remains qualified-only. None of these names is an attribute
of `gw_kernel` or a member of `gw_kernel.__all__`; generic names
such as `run_lint` need their module owner to remain legible.

These are the exact workspace-qualified mutation and routing signatures:

```python
work.run_file(
    layout: WorkspaceLayout,
    config: Config,
    *,
    type: str,
    title: str,
    description: str,
    on: date,
    name: str | None = None,
    effort: str | None = None,
    blast_radius: str | None = None,
    target: str | None = None,
    owner: str | None = None,
    parent_path: str | None = None,
    depends_on: Sequence[DependencyEdge] = (),
    affects: Sequence[str] = (),
    tags: Sequence[str] = (),
    dry_run: bool = True,
) -> FilingOutcome

work.run_next(
    layout: WorkspaceLayout,
    path: str,
    *,
    descend: bool = False,
    dry_run: bool = True,
) -> NextResult

work.run_decision_add(
    layout: WorkspaceLayout,
    path: str,
    *,
    question: str,
    status: str = "open",
    answer: str | None = None,
    rationale: str | None = None,
    if_wrong: str | None = None,
    affects: Sequence[str] = (),
    on: date,
    decided_by: str,
    dry_run: bool = True,
) -> DecisionCommandResult

work.run_decision_answer(
    layout: WorkspaceLayout,
    path: str,
    decision_id: str,
    *,
    answer: str,
    rationale: str | None = None,
    on: date,
    decided_by: str,
    dry_run: bool = True,
) -> DecisionCommandResult

work.run_decision_amend(
    layout: WorkspaceLayout,
    path: str,
    decision_id: str,
    *,
    answer: str,
    note: str,
    rationale: str | None = None,
    affects: Sequence[str] | None = None,
    on: date,
    decided_by: str,
    dry_run: bool = True,
) -> DecisionCommandResult

work.run_decision_list(
    layout: WorkspaceLayout,
    path: str,
    *,
    status: str | None = None,
    affects: str | None = None,
    cites: str | None = None,
) -> DecisionCommandResult

work.run_decision_supersede(
    layout: WorkspaceLayout,
    path: str,
    decision_id: str,
    *,
    question: str,
    answer: str,
    rationale: str | None = None,
    affects: Sequence[str] | None = None,
    on: date,
    decided_by: str,
    dry_run: bool = True,
) -> DecisionCommandResult

work.run_decision_overturn(
    layout: WorkspaceLayout,
    config: Config,
    path: str,
    decision_id: str,
    *,
    answer: str,
    rationale: str | None,
    follow_up_title: str,
    follow_up_type: str = "TechDebt",
    follow_up_affects: Sequence[str] = (),
    on: date,
    decided_by: str,
    dry_run: bool = True,
) -> OverturnResult
```

All mutating commands default to `dry_run=True`. A dry run returns the complete
plan and an empty application; pass `dry_run=False` only after inspecting the
plan and its refusal. `run_decision_list` is read-only and therefore has no
`dry_run` parameter. Dates and actors are required inputs; this layer does not
read the clock.

### Filing and dependency edges

`run_file` plans the new page, `work/index.md` reconciliation, and root log
append before any write. `result.plan.refusal` prevents every effect.
`result.application.written` is true only after the full apply completes; an
unexpected I/O failure preserves partial effects in the raised
`work_tracker_okf.compose.FilingApplyError.application`.

Pass typed edges, including distinct gates for the same path when needed:

```python
from datetime import date

from work_tracker_okf.dependencies import DependencyEdge

result = work.run_file(
    layout,
    config,
    type="Feature",
    title="Consume the design input",
    description="Starts planning after the input design is complete.",
    on=date(2026, 8, 18),
    depends_on=(
        DependencyEdge(
            "work/feature-design-input",
            blocks="plan",
            needs="design",
        ),
    ),
)
```

`DependencyEdge(path)` means `blocks="execute", needs="resolved"`. A
dependency satisfies a phase requirement only after moving past that phase;
terminal items satisfy every edge. Unknown dependencies and malformed gates
fail closed. Exact duplicate triples are invalid, while distinct triples for
one path are allowed.

### Next and canonical source normalization

`run_next` returns both `requested_path` and `selected_path`; `descend=True`
walks a gated parent to its next actionable leaf. If a canonical
`references/01-design.md` exists but the item lacks its `design`
source, the dry run includes a `SourceNormalization` and routes against that
planned state without writing.

With `dry_run=False`, normalization is best-effort. Each page is reloaded
before saving, so an authored `design` source introduced after planning
wins and does not prevent later normalizations from running. The command then
reloads the bundle and recomputes the selected route from persisted state;
save failures appear in `NextResult.warnings` rather than producing a route
that assumes an unpersisted stamp.

### Decisions and overturn

Decision commands accept either an epic or any active or archived descendant
and report the resolved epic-owned ledger in `DecisionCommandResult.owner`.
Add, answer, amend, and supersede use immutable `work_tracker_okf.decisions` plans. Amend refines an answered entry in place: it keeps the id and status and appends an `**Amended:**` block recording the previous answer.
Apply rechecks the ledger snapshot under its exclusive lock; a stale
application reports `stale=True`, `written=False`, and no landed entries.
List filters returned entries while its counts cover the whole ledger.
`open` entries are unanswered; `answered` requires an answer; `assumed`
requires an answer plus `if_wrong`; and supersession creates a replacement
entry without deleting history.

`run_decision_overturn` preflights a supersession and its peer follow-up filing
before applying either. A refusal in either plan writes neither resource.
Application is ordered: decision first, filing second. If the decision plan is
stale, no follow-up is filed. If filing fails after partial effects, the raised
`OverturnApplyError` preserves both the completed decision application and the
partial filing application, with the original `FilingApplyError` as its
`__cause__`; this is observable partial-effect reporting, not rollback.

### Intentional holds while the ledger owner is at finish

A named open question, skip, or park can be filed while its ledger owner is at
`finish`. Filing retains the shared decision-owner lock and applies the ledger,
owner source, and any park checkpoint in one rollback-journaled transaction.
The affected item stays held until all its open entries are answered or
superseded. The owner can differ from the affected child; standalone fallback
owners use the same policy.

This is an explicit operation-specific exception to ADR 2026-08-25-the-work-mutation's default
no-surplus postcondition guarantee. Only decision append grants a budget of
one `(owner page member, decisions.open-at-finish)` surplus finding, and only
when the new entry names an existing, non-archived, nonterminal item whose
resolved ledger owner is that owner. Unnamed, missing-target, terminal-target,
and foreign-owner questions receive no exception. Answer, supersede, and stage
advance receive none either. The real baseline is consumed first; all other
findings, structural checks, and absolute index checks keep their existing
behavior. If baseline capture fails, the allowance is disabled and the
absolute fallback applies.

The ordinary lint catalog is unchanged: `decisions.open-at-finish` remains an
error. Consumed allowances appear in command warnings as operation-specific,
separately from pre-existing findings. The `planned` journal record stores the
exact counted allowance before effects begin, and completion-evidence checking
includes it. Any unrelated failure restores the entire snapshot, including a
park checkpoint. An interrupted journal keeps its existing recovery treatment;
an allowance is evidence of requested validation policy, never permission to
replay effects or declare completion. Validation still runs on both sides of
the mutation; the additional cost is a small counter and a journal field.

## The graph surface

`code-graph-io` takes a resolved `graph_dir` and raises typed exceptions. A
workspace has a layout and a bundle-declared graph directory. `graph.commands`
is the seam.

```python
from gw_kernel import graph_target, resolve
from gw_kernel.graph import commands as graph

target = graph_target(resolve())
result = graph.build(target, full=True)
if not result.ok:
    print(result.error)
```

`graph_target` reads `<root>/workspace.yaml`'s `repositories` block for the
graph directory and the members. With no such file it falls back to
`code_graph_io.paths.graph_dir(layout.root)` and the workspace's own repo — the
bootstrap path that exists because the graph DB is created before any manifest
may exist. A malformed `workspace.yaml` raises `ConfigError`; config
raises, content never does.

**No command raises for a graph-state reason.** Each returns a `GraphResult`
carrying a `code_graph_io.exit_codes` value, which that package documents as
stable from v1 forward:

| Condition | `exit_code` |
|---|---|
| success | `SUCCESS` (0) |
| entity not found, unknown `only` member, catch-all | `GENERIC` (1) |
| no graph at `graph_dir` | `NOT_INITIALIZED` (3) |
| graph written at another schema | `SCHEMA_MISMATCH` (4) |
| nothing to build, or a member outside git | `NOT_IN_GIT_REPO` (5) |
| another build holds the lock | `UPDATE_IN_PROGRESS` (6) |
| bare entry-point name matching several packages | `AMBIGUOUS` (7) |

`error` is non-empty only when `exit_code` is not `SUCCESS`, so the check above
is the only one a consumer needs. Everything a successful command has to say —
including the notice `find` appends when the 50-row cap fires — is in `output`.

There is no Typer surface and no tracing here — `gw-cli` builds the
command line over these functions, and timing an invocation is the concern of
whoever invokes.

`graph_tools` holds the same capability one level down, as five plain callables
over an open `GraphReader`: `find`, `describe`, `callers`, `callees`,
`imports`. Each returns a string on every path, failures included, because the
consumer is an LLM. **Reader lifetime is the caller's** — open at command
entry, close in `finally`. There is no `@tool` decorator and no
`langchain_core` import: the query vertical owns that binding, being the only
consumer and the one already carrying the dependency.

## The dispatch seam

`work-tracker-okf`'s `route()` owns `Dispatch(stage, variant)` and names no
skill. `workspace.dispatch.resolve_dispatch` folds complete packaged defaults,
then shared and local ordered rules, into the same profile for attended
reporting and orchestration. It records each field's origin and reset reason.
`workspace.dispatch_config` owns loading, strict validation and explicit-layer
writes; `workspace.dispatch_projection` owns cache freshness and publication.
`workspace.pipeline` holds only packaged defaults and skill-name validation.

See [Dispatch rules](docs/dispatch-rules.md) for YAML examples, matching and
reset semantics, launch accounting, and exact manual cutover instructions.

`orchestrate.commands.plan()` is IO-free: it receives items, repository
observations and finish plans and returns one ordered frontier. The shell
`run_orchestrate()` resolves each item's repository and observes Git through
`workspace.provenance.probe_git`. `stage_advance.py`, `placement.py` and
`finish_receipt.py` own their separate mutations; shared repository and finish
readers live in `workspace/` so the work and orchestration verticals agree.

**A reservation describes an emitted dispatch, not a candidate.** `plan()`
walks the sorted candidates once and checks each against every gate in order —
declared affects, overlap with live or already-emitted dispatches, capacity,
dispatch profile, computed placement. Only a candidate that
passes all of them consumes a slot, claims a worktree, or reserves its
`affects`. A candidate refused at any gate reserves nothing, so an overlapping
sibling behind it can still dispatch in the same cycle. Live dispatches always
reserve. A dispatch the coordinator chooses not to launch after planning is
outside `plan()`'s view: it still reserved in that plan.

**Placement is computed, never recorded.** `gw work record-placement` and `record-baseline` are gone: dispatch writes no stamp (its journal `record` step is a no-op kept for Runs that straddle the upgrade). No writer survives: workspace writes land on the primary checkout, so nothing records a workspace stamp either. A legacy `repo_stamps._workspace` entry is read only by `gw work migrate-placement`, and the manifest still refuses a repository named `_workspace` until that legacy read path is deleted.
`run_stage_advance` never writes placement: it takes no `worktree`, `branch` or
`infer_worktree`, and `gw work advance --no-infer-worktree` is a hidden no-op that warns.
The commit gate reads placement through `gw_pipeline.placement.effective_placement`.

**Placement is computed and realized at dispatch.** `plan()` computes where each item's worker runs from the item, its owner chain and the repository (`gw_pipeline.placement.placement_for` / `placement_chain`) and states it on the dispatch: a `placement` block plus `PlannedDispatch.worktree`, a `WorktreePlacement(path, branch, parent_path)`. Every stage, design and plan included, runs in the item's own computed worktree; there is no worktree action vocabulary, no pinned reader checkout and no `preparations[]`. A child's worktree parents on its epic's. A workspace-only item has a null placement and launches in the workspace primary checkout. Every chain step is checked as a write claim against live and accepted claims, so an owner held by a live finish yields `worktree-pending`.

`gw work dispatch` recomputes the placement and refuses a plan that differs (`plan-invalid`). It then asks the placement engine to realize the checkout (create, adopt or refresh) before any Orca call, and launches into that path. An engine refusal is `placement-refused` and journals nothing. Backends only launch into an existing path and provision nothing. A retry or park-resume goes through `gw work dispatch --retry-of`, which verifies the placement without ever creating, adopting or refreshing it: a missing worktree is refused.

**Overlap is repository-scoped; capacity is global.** Each dispatch carries
its own resolved `repo`; top-level `repo` remains root metadata. Equal affects
paths in independent repositories do not overlap. Linked worktrees share a
repository identity; different identities still share one `max_parallel`
budget. Finish claims every verified source checkout and merge-target checkout
(`FinishTarget.target_worktree`), including the checkout holding the default
base for a root finish and each foreign-repository target. The shared
`claims.conflicts()`/`first_conflicts()` gate serializes same-target finishes
against both live and already-accepted work while allowing independent targets.
Known live occupancy remains reserved even if fresh or partial revalidation
fails; refused candidates reserve nothing.

**Finish proves every integration before one advance.** `finish_targets`
contains the scalar source (if any) and every foreign source, ordered by
repository. Each integrates into the nearest enclosing owner in that same
repository, or its configured/default base at the top level. Foreign-only
owners need no invented scalar stamp. `run_record_finish` derives Git ancestry
evidence and persists `references/04-finish-receipt.md`; `inspect_finish`
revalidates source tips and result ancestry on every read. Partial integration
survives a restart and refuses completion with `finish-incomplete` and route
blockers. Both attended and relay consumers inspect the receipt before the one
final advance. PR, hold and discard never advance. The scalar `resolved_in`
prefers the owner's own repository when present; otherwise it uses the first
verified repository in deterministic order. The receipt retains the complete
multi-repository evidence.
Release date requirements remain enforced. Git merges across repositories are
not atomic; preserve sources until all proof is recorded. Squash/rebase alone
cannot establish the required ancestry.

`workspace.provenance` owns Git subprocesses. Provenance capture degrades
quietly when unavailable; placement and finish safety checks fail closed when
that evidence cannot be established.

**Nothing Orca-shaped reaches the planner's prompt.** Its base instructions
and reader baseline are vendor-neutral; a vendor command can only enter through
a variant's `prompt_tail`, which lives in workspace configuration.

The `branch` variant has no packaged tail. Init seeds `pipeline.RELAY_TAIL_SEED`
in a missing shared dispatch document; authored documents stay untouched.
Final profile validation blocks any relay with a missing tail. Add the intended
`prompt_tail` to a matching shared/local dispatch rule, then run `gw config sync`.

**Repository assignment comes from `workspace.yaml`, never `affects`.**
An item's nearest inherited `repo:` wins, then `repo_name`, then a sole declared
repository. Ambiguity refuses rather than guessing. Each repository's checkout
eligibility, inventory and branch evidence remain independent. Explicit Python
`repo=Path(...)` is the single-repository compatibility override; it does not
manufacture foreign assignments, and foreign stamps are refused. `layout.repo_root`
continues to locate the workspace's own repository, not the code repositories.

Four limits worth knowing before you rely on the result:

- The owning epic's decisions ledger is read **twice** per plan — once for the
  routing gate, once for the plan's decision fields — so the two reads are not
  one atomic snapshot. A decision answered between them leaves the response
  internally inconsistent. Deduplicating means threading parsed ledger state
  out of the frontier walk, and is deliberately not done.
- The legacy single-repository path can infer an unstamped root's epic pair
  from the first stamped descendant in pick order. Reader source selection does
  not use that fallback: it requires its owner's verified anchor or, for an
  unanchored root, its default base.
- The decision-hold scan in `gw_orchestrate.commands` walks every
  item in the vault on every plan call, regardless of root, and loads one
  decisions ledger per distinct epic. A lone item with no epic ancestor pays
  the walk too. `work_tracker_okf.hierarchy.nearest_epic` rebuilds its index
  per call, so the pass is quadratic in vault size — negligible at present
  scale, and not fixed here because both available fixes either duplicate the
  ancestor walk or change the domain signature.
- Readers claim no mutable worktree slot, including any historical stamp, but
  still take code-affects claims and can be blocked by live code claims. The
  sibling stage-aware-claims work owns changing that policy. Reader baseline
  propagation beyond the dispatch prompt and receipt remains separate work;
  checkout and temporary creation-branch cleanup remain the coordinator's and
  Orca's lifecycle responsibility.

### Shared pipeline service ownership

`gw-pipeline` owns journaled transactions, finish evidence/cleanup, execute-return
journals, landed comparisons, gate configuration, decision-owner locks, workspace
branch/placement observations, repository context, guidance assembly/render and
transcript capture. Kernel owns resolved layout/discovery, bundle locks/pending
commits, anchors, commits, provenance, hooks and work schemas. Kernel has no
pipeline dependency. Only pipeline transcript capture delegates to kernel
discovery; its other services accept resolved paths. Import defining pipeline
modules; its root has no facade exports.
