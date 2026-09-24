# graph-works-core

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
from graph_works_core import apply_init, plan_init, resolve

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

## The agent substrate

Everything the verticals share, so they compose one substrate instead of each
re-deriving it.

```python
from graph_works_core import make_llm, role_binding, run_tool_loop, build_catalog
from graph_works_core import prompts

llm = make_llm("librarian", layout=layout)  # layout is optional: None is packaged-only
binding = role_binding("librarian", layout=layout)  # what SubagentPool wants — a factory per item
catalog = build_catalog(bundle, lanes=("concepts", "sources", "entities"))
system = "\n\n".join([prompts.IRON_RULES, prompts.render_architecture_overview(layout)])
```

| Piece | What it is |
|---|---|
| `agent_substrate.roles` | packaged `models.toml` → `workspace.yaml` `roles.<name>.<field>` → explicit argument, merged by `subagents_io.resolve_role_spec` |
| `role_spec` / `role_binding` / `make_llm` | the three constructors over that merge: a provider-free `RoleSpec`, a `RoleBinding` (spec plus a factory `SubagentPool` calls once per item), or a constructed `BaseChatModel` |
| `agent_substrate.agent_loop` | the capped tool-call loop: two distinct cap outcomes, and a tool-name coercion that is written back into the replayed history |
| `agent_substrate.agent_tools` | catalog / bounded page read / chunking, all over an `okf_io.Bundle` |
| `prompts` | seven shared fragments plus two renderers |

**`layout` is an argument, never a discovery call.** `None` means
packaged-only; passing a layout is how a caller opts into workspace overrides.

The role override lives in `workspace.yaml` as five wildcard catalog keys, so
`gw config set roles.librarian.model_id …` works through config-io's validated,
rollback-safe path. `Manifest` itself is untouched: roles are not layout.

Bedrock is the only backend the packaged catalog names, so `models-io[bedrock]`
is a hard dependency and the gateway arrives through the `vercel` extra.
`AI_GATEWAY_API_KEY` is read here — models-io never reads the environment —
and its name is handed back as `credential_hint` so the refusal names it.

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
`graph_works_core.work.commands` when the caller owns a `WorkspaceLayout` and
wants workspace path resolution composed around those domain plans.

```python
from graph_works_core.work import commands as work
```

The work vertical remains qualified-only. None of these names is an attribute
of `graph_works_core` or a member of `graph_works_core.__all__`; generic names
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
Add, answer, and supersede use immutable `work_tracker_okf.decisions` plans.
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
from graph_works_core import graph_target, resolve
from graph_works_core.graph import commands as graph

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

There is no Typer surface and no tracing here — `graph-works-cli` builds the
command line over these functions, and timing an invocation is the concern of
whoever invokes.

`graph_tools` holds the same capability one level down, as five plain callables
over an open `GraphReader`: `find`, `describe`, `callers`, `callees`,
`imports`. Each returns a string on every path, failures included, because the
consumer is an LLM. **Reader lifetime is the caller's** — open at command
entry, close in `finally`. There is no `@tool` decorator and no
`langchain_core` import: the query vertical owns that binding, being the only
consumer and the one already carrying the dependency.

## Ingesting a document

One document in, one Source page plus its reference copy out, and a set of
lane-targeted proposals arguing for what the source justifies.

```python
import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

from graph_works_core import resolve, run_ingest_source

layout = resolve(workspace=Path(".works"))
result = asyncio.run(
    run_ingest_source(
        Path("~/Downloads/some-spec.md").expanduser(),
        layout=layout,
        repo=Path("."),
        today=date.today(),
        at=datetime.now(timezone.utc),
        source_kind="spec",  # a hint; the model classifies, the bundle's enum validates
        origin="https://example.com/some-spec",
        backend_override="bedrock",
    )
)
print(result.page, result.copy, result.proposal_status)
```

`backend_override` is required here because `ingestor` now defaults to the
brief-only `claude_code` backend — `run_ingest_source` itself always runs the
full write pipeline regardless of backend, so a caller must explicitly opt
into a real model backend (`"bedrock"` or `"vercel"`) via `backend_override`
or an equivalent workspace/CLI override.

`today` and `at` are required and have no default — nothing in this package
reads the clock.

Three seams, all defaulted so the call above is the whole API:

| Seam | Default | Supplied by |
|---|---|---|
| `match_entity` | this package's own matcher over the configured code graph | `entity_matcher(reader, schema_set)` |
| `state_gate` | `None` — no drift stamp | `state_gate_adapter(config)` |
| `graph_tools` | `()` — a narrower reasoner, not a failure | the CLI, from the graph-tool builder |

**An uninitialized graph is not an error.** The ingest completes without an
`entity_uri` and without the forward link. Whether that should be fatal is a
CLI policy question, not a library one.

**The page and its reference copy are one plan.** They land together or not at
all, and re-recording material whose page already exists is refused rather than
merged — provided the model's title still resolves to that same page. Since the
ingestor's `title` decides the path, a re-ingest whose model retitles the
material lands a second page instead. Keying the refusal on `origin` instead is
an open question on graph-works-cli.

**The suggest phase is best-effort.** A reasoner or extractor failure yields
zero proposals, records the reason in `IngestResult.proposal_status` (not on the page),
and never fails the ingest.

**Skill ingest is not here.** The two-pass planner→synthesizer flow that writes
guidance pages is the guidance layer, one above this one, and is deferred with
it.

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
dispatch profile, placement, backend provisioning. Only a candidate that
passes all of them consumes a slot, claims a worktree, or reserves its
`affects`. A candidate refused at any gate reserves nothing, so an overlapping
sibling behind it can still dispatch in the same cycle. Live dispatches always
reserve. A dispatch the coordinator chooses not to launch after planning is
outside `plan()`'s view: it still reserved in that plan.

**Placement is recorded from observation, not inferred (D-006).** Orca's actual
worktree and branch are established by native observation, and the recorded pair
selects every later stage's placement. The coordinator therefore records the
observed pair with `gw work record-placement` (`orchestrate/placement.py`),
which writes `worktree`/`branch` or the selected `repo_stamps` entry plus
`updated`, never fires a routing transition, and runs under the same decision-owner lock as `gw work advance`.
It refuses when the item's phase no longer matches the recorded dispatch, so a
worker that finishes first produces a visible refusal instead of a stamp on the
wrong stage. The subtree root is recorded at every phase — its stamp is the
anchor descendants resolve against — and a descendant only at `execute` and
`finish`: a `design` or `plan` stage writes only into the vault and reuses the
epic worktree as a read context. `plan()` attaches the epic pair only to the
root's planned advance. `run_stage_advance` infers a pair from cwd only for a
top-level item, and never with `infer_worktree=False` (`--no-infer-worktree`),
which every supervised worker passes. An explicit `--worktree`/`--branch` pair
on an attended advance is still applied as stated.

**Repository-local anchors are prepared lazily.** A runnable modifying child
without its nearest owner's verified anchor emits a deferred `preparations[]`
entry. Preparation consumes no worker slot and authorizes no child launch.
Create or recover the deterministic worktree outside the workspace lock, verify
its repository, branch, parent/base and cleanliness, then record the observed
pair on `owner_path` at `owner_phase`. Own-repository pairs remain scalar;
foreign pairs live in `repo_stamps`. A refused stamp leaves a discoverable
worktree; replan before dispatch. Nested owners prepare from their nearest
repository-local enclosing anchor. Read-only stages need no new anchor.

**Overlap is repository-scoped; capacity is global.** Each dispatch carries
its own resolved `repo`; top-level `repo` remains root metadata. Equal affects
paths in independent repositories do not overlap. Linked worktrees share a
repository identity; different identities still share one `max_parallel`
budget. Finish reserves every verified source target, including live sources
whose fresh admission evidence later fails.

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

**Nothing Orca-shaped reaches this package's API.** The prompt `plan()`
assembles is four vendor-neutral lines; a vendor command can only enter through
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
- When the root carries no worktree stamp, "the epic worktree" is the first
  stamped descendant in pick order. Reproducible from vault state, but it means
  the plan's worktree decisions depend on which child happened to run first.
- The decision-hold scan in `graph_works_core.orchestrate.commands` walks every
  item in the vault on every plan call, regardless of root, and loads one
  decisions ledger per distinct epic. A lone item with no epic ancestor pays
  the walk too. `work_tracker_okf.hierarchy.nearest_epic` rebuilds its index
  per call, so the pass is quadratic in vault size — negligible at present
  scale, and not fixed here because both available fixes either duplicate the
  ancestor walk or change the domain signature.
- A read-only dispatch is a **non-exclusive** occupant: it claims no worktree
  slot, so two `design` stages may share the epic worktree and neither blocks a
  code stage from it. A `design` worker can therefore read a tree a concurrent
  `execute` worker is mutating. It degrades a read; it cannot corrupt one.
  Recorded rather than mitigated.

## Custom-type provenance

An LLM-extracted page has no ground truth to reconcile against: it goes stale
silently, and no shipped check catches it. This is the model that fixes that.
The capability ships in `okf-ext`, later and not here — what this package owes
is the contract, because it owns the drift machinery and the contract has to
exist before the extractor does.

**The contract.** An extracted page carries one `sources[]` entry per code
resource it was read from, each with the commit it was read at:

```yaml
type: Endpoint
sources:
  - id: src-handler
    resource: /src/api/checkout.py
    at_commit: a1b2c3d
  - id: src-router
    resource: /src/api/router.py
    at_commit: a1b2c3d
```

**Why this and nothing else.** Declaring the resource set *is* the ground
truth, and it is the same ground truth the graph-derived lanes already have.
`code_wiki_okf.sync.snapshot_bundle` can answer "did `/src/api/checkout.py`
change since `a1b2c3d`?", so `sync.stale-page` fires with no new checker, no
new frontmatter key and no new lane. The untrusted region becomes
drift-checkable using machinery that already ships.

Two alternatives were rejected: making the provenance block a JSONSchema
requirement in `schema/`, which enforces mechanically but pushes a graph-works
opinion about extraction into okf-ext's deliberately generic schema capability;
and a quarantine lane with a TTL, which is cheap to specify but accepts a
permanently untrusted region — the outcome this model exists to avoid.

**The re-extraction trigger** is that same finding, routed to the extractor
rather than to a proposal. A `sync.stale-page` on a graph-derived page means "a
sync run would update it"; on an extracted page it means "a re-extraction run
would update it". The routing key is the page's `type` being a declared custom
type rather than a graph-derived one.

**The failure mode, named.** A page that under-declares its sources is silently
under-checked: it will not go stale when code it actually describes changes,
only when code it *admitted to reading* changes. This is strictly better than
the status quo, where nothing is checked at all, but it is not airtight and
should not be sold as if it were. The mitigation, if it proves necessary, is
extraction-side rather than lint-side — have the extractor record every file it
opened, not every file it cited.

**One surface already writes it.**
`graph_works_core.lint_drift.propagate_drift` writes this
staleness-checkable shape (`id`, `resource`, `at_commit`, plus `title` and
`rationale` for its own presentation needs) into every proposal it files. It
is the only one: `graph_works_core.ingest.suggest_pages` and
`graph_works_core.ingest.commands`, through `doc_wiki_okf.sources`, each build
`sources[]` independently, with different key sets and no `at_commit`. Whether
they should carry one is an open question, not a settled shape.

Out of scope here: the extractor, the schema for custom types, the lane the
extracted pages live in, and any enforcement that a page declares provenance at
all.

### Read-only agent configuration reports

`agent_config.read_project()` and `show()` model the selected on-disk Claude,
Codex and Pi layers, keeping raw parsed values and per-key origins. They do not
reproduce running-session flags, headless/SDK acceptance, remote policy sources,
model alias resolution or nested Codex configuration traversal.

Claude local settings use repository/main-checkout identity, retaining an existing
legacy starting-directory file below the root file. The caller supplies `user_id`
for POSIX ownership checks; when placement cannot be established, the report
includes an `agent-config.local-location` finding and excludes that uncertain
local layer from effective values. Windows and home-root exceptions keep the
starting-directory file. Repository lookup failures yield unknown trust with the
existing explicit assumed-trust finding. State-only custom git probes still work,
but need a `RepositoryContext` (or a `context()` method) to establish identity.

Object and array nesting is limited to 64 levels before conversion and merging.
Deeper or unreadable content becomes a layer error, leaving other layers readable
and report values safe for the normal wire/JSON encoding path. `modelSettings`
effort resolves against each file's top-level `effortLevel`; derived effort keys
retain empty `defined_in` when no file authored that exact path.
