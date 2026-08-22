# work-tracker-okf

Work-item tracking as an OKF v0.2 lane: typed item projection, dependency and
hierarchy rules, immutable mutation plans, and a standalone CLI. This package
receives an explicit bundle root. Workspace-aware callers use the qualified
`graph_works_core.work.commands` surface described by `graph-works-core`.

## The CLI

    work-tracker-okf <command> <root> [options]

Eight flat commands, no subgroups, `<root>` first everywhere — `code-wiki-okf`'s
shape.

| Command | Writes | `--today` | `--json` | What it does |
|---|---|---|---|---|
| `init` | yes | ✓ | — | Install this package's fourteen files into a bundle |
| `file` | yes | ✓ | — | File one item, reconcile `work/index.md`, log the arrival |
| `next` | never | — | ✓ | Report what to dispatch for a slug |
| `advance` | yes | ✓ | — | Apply the next transition, stamp, ensure the plan row, then lint |
| `archive` | yes | ✓ | — | Move terminal items to `work/_archive/` |
| `lint` | never | ✓ | ✓ | Validate against the lane rule set |
| `status` | never | — | ✓ | Count the active items, name the one worth resuming |
| `sync-children` | yes | — | — | Refresh every parent's derived `children:` |

`next` and `status` take no `--today` because neither reads a clock: routing
and counting are pure functions of the item graph.

### Declarations directory

Four commands accept `--declarations-dir`: `init`, `file`, `lint`, `advance`. It
relocates `schema/` and `sections/` for that invocation. It is **not**
persisted: this package writes no configuration file, so a later command needs
the flag again.

See [the corresponding library sections](#the-writers) and
[Routing and advancement](#routing-and-advancement) for what each command does.

### `--dry-run` means "stop after plan"

Write-by-default, `--dry-run` to opt out — one rule across all four CLIs in this
repo. It is **not a second code path**: three of the four writers have no
`dry_run` parameter at all, so `--dry-run` skips the `apply()` call and prints
the plan object the library already built. A dry run cannot drift from the real
run, because it is the real run minus its last line.

The one exception is `advance`, which cannot honestly show post-write findings
and says so instead — see [The CLI's one asymmetry](#advance-lints-after-writing).

### Exit codes

Results to stdout, refusals to stderr. A caller-configuration error — a root
that is not a directory, a malformed `schema/`, an unparseable `--today` —
exits 1 with a message rather than a traceback. A refused `FilingPlan`,
`AdvancePlan` or `ArchivePlan` exits 1 with its refusal rendered: refusals are
data all the way to the shell.

`lint` exits 1 when `report.ok` is false. **`--strict` promotes every warning to
an error first**, which is why it fails even a conformant vault — the seven
standing warnings are correct, not defects — and why it must not be wired into
an acceptance gate.

### advance lints after writing

`advance` re-validates the item it just wrote and prints only that item's
findings. The stamp is unconditional: a pointer at a not-yet-written artifact
is `targets.artifact-missing`, a warning surfaced immediately rather than
waiting for the next `lint` run. This is why `--dry-run` cannot show post-write
findings honestly and says the check was skipped instead.

## The lane

Six types — `Epic`, `Feature`, `Bug`, `TechDebt`, `TestGap`, `Spike`. They
differ in how they route, not in frontmatter shape, which is why the six schema
wrappers carry nothing but a `type` const over one shared base.

## The layout

An item page lives at `work/<slug>.md`, its artifacts at
`work/<slug>/references/`, and an archived item at `work/_archive/<slug>.md` —
the same shape, so nothing downstream branches on whether an item is archived.

The slug is `<opened>-[epic-]<type-kebab>-<w1..w4>`: date-prefixed, so a bare
directory listing is chronological, with `epic-` marking a child filed under an
epic. `filing.compose_slug` composes the whole thing.

`paths` returns one frozen carrier rather than a bare string, because only a
leading slash separates the bundle-relative form from the root-absolute one and
`okf_io.links.resolve_reference` makes the mistake silent:

```python
from work_tracker_okf.paths import artifact_path

ref = artifact_path("2026-03-02-epic-feature-filing-writer", "design", "spec")
ref.rel  # work/2026-03-02-epic-feature-filing-writer/references/01-design-spec.md
ref.resource  # /work/2026-03-02-epic-feature-filing-writer/references/01-design-spec.md
ref.source_id  # design-spec
ref.path(root)  # <root>/work/.../references/01-design-spec.md
```

`artifact_path` derives the id from the same `(phase, kind)` it derives the
filename from, so a caller cannot obtain a resource without its matching id.
Note the flip, which predates the port: the filename is `<phase>-<kind>`
(`03-execute-results`), the id is `<kind>-<phase>` (`results-execute`).

## Dependency edges

`depends_on` accepts legacy slug strings and structured edges in the same
ordered list:

```yaml
depends_on:
  - 2026-08-18-feature-legacy-terminal-gate
  - slug: 2026-08-18-feature-design-input
    blocks: plan
    needs: design
```

The first entry is shorthand for `blocks: execute` and `needs: resolved`: it
does not gate design or plan, and a terminal dependency satisfies it. The
second gates plan and every later phase until the dependency moves *past*
design. Entering design is not enough; `needs` names a phase that must be
complete.

One slug may carry distinct gates for different consumers or phases. An exact
duplicate `(slug, blocks, needs)` triple is invalid. Unknown dependencies,
invalid phase names, and dependencies with no usable phase fail closed once
their edge gates the current phase. Readers retain malformed entries as
`DependencyIssue` values so lint can report them without crashing; writers
accept typed `dependencies.DependencyEdge` values and refuse invalid graphs.

## The writers

Mutation names remain qualified by their owning submodule. In particular,
`filing.apply`, `decisions.apply_plan`, and `adoption.apply_adoption` are not
flattened onto the `work_tracker_okf` package root.

The table highlights the APIs most useful when composing writers; it is not an
exhaustive module reference. The owning module remains authoritative; where it
declares `__all__`, that collection is its complete public surface.

| Module | Selected composition APIs | Shape |
|---|---|---|
| `work_tracker_okf.filing` | `FilingSeed`, `FilingPlan`, `FilingRefusal`, `plan_filing`, `apply` | one page; planning is the default no-write operation |
| `work_tracker_okf.compose` | `FilingCompositionPlan`, `FilingApplication`, `FilingOutcome`, `plan_file_and_reconcile`, `apply_file_and_reconcile` | preflights the page, lane index, and root log before any write |
| `work_tracker_okf.decisions` | `DecisionPlan`, `DecisionApplication`, `plan_append`, `plan_update`, `plan_supersede`, `apply_plan` | immutable ledger snapshot; apply rechecks it under the ledger lock |
| `work_tracker_okf.adoption` | `AdoptionPlan`, `AdoptionApplication`, `plan_adoption`, `apply_adoption` | classifies migrated child specs without workspace discovery or Git |
| `work_tracker_okf.sources` | `upsert` | mutates a `Document` in memory, returns whether it changed; the caller saves |
| `work_tracker_okf.results` | `ResultsFacts`, `render`, `write_results` | the one direct write — a stub derived entirely from its facts |
| `work_tracker_okf.archive` | `SkipReason`, `Skipped`, `ArchivePlan`, `ArchiveResult`, `plan_archive`, `apply_archive` | plan + apply; no `dry_run` flag — the plan *is* the preview, and a dry run could not honestly report its `IndexUpdate`s |

```python
from datetime import date

from okf_ext.shape import load_sections
from work_tracker_okf.filing import FilingSeed, apply, plan_filing

plan = plan_filing(
    root,
    items,
    FilingSeed(
        type="Feature",
        title="The filing writer",
        description="Owns where a work item lands.",
        on=date(2026, 3, 2),
        affects=("packages/work-tracker-okf",),
    ),
    load_sections(root / "sections"),
)
if plan.refusal is None:
    apply(plan)  # not calling this is the dry run
```

Filing writes **one page**. `work/index.md` reconciliation and the `log.md` line
belong to `compose.plan_file_and_reconcile`, whose `FilingCompositionPlan`
preflights all three resources. `compose.apply_file_and_reconcile` applies that
same plan and reports completed effects in `FilingApplication`; an unexpected
I/O failure raises `FilingApplyError` with the partial application attached.
Neither planner writes, and neither apply operation has a `force` escape hatch.

## Archiving

Archiving a terminal item is a **symmetric prefix move**: `work/<slug>.md` and
everything under `work/<slug>/` relocate to their `work/_archive/` twins in one
`okf_ext.moves` batch, every inbound **OKF markdown** reference is repaired,
the emptied working directory is pruned, and both lane indexes are reconciled.
**No frontmatter is written** — an item reaching this path is already
terminal.

```python
from okf_io import load_bundle
from work_tracker_okf import ARCHIVE_IGNORE
from work_tracker_okf.archive import apply_archive, plan_archive

bundle = load_bundle(root, ignore=ARCHIVE_IGNORE)  # note the recipe
plan = plan_archive(bundle)  # sweep; pass slugs=[...] to target
if plan.ok:
    result = apply_archive(bundle, plan)  # not calling this is the dry run
```

Note the **second recipe**. `okf_ext.moves` builds its mapping from
`bundle.concepts`, `bundle.assets`, `bundle.indexes` and `bundle.logs` — never
from `bundle.ignored` — so a plan built through `IGNORE` reports `ok` while
covering only the item page, leaving `references/` behind and every
`sources[]` entry dangling. `ARCHIVE_IGNORE` is `IGNORE` minus
`*/references/*`, and it is for planning moves and nothing else: it must never
reach `validate()`, which would then schema-check every artifact in every
`references/` tree.

One walk serves both eligibility and the move, because `load_items` projects
identically under either recipe. The reconcile that follows the move uses the
*other* lens — a reload through `IGNORE` — for two independent reasons, either
of which decides it: under the wider lens `update_index` would want a
`# Subdirectories` entry for every item with a working directory, and
`moves.apply` never updates the in-memory `Bundle` anyway.

Sweep mode reports **no** skips: a sweep's non-candidates were never
candidates. Targeted mode reports one `Skipped` per named slug that does not
move — `unknown-slug`, `not-terminal` or `already-archived` — because there the
caller named the slug and is owed an answer.

**`[[wikilinks]]` are not repaired, and the archive command says so.** A
wikilink is not a link form OKF v0.2 defines, so nothing in `moves` can see
one. A vault authored in wikilink form needs converting before its first
archive; until then, `ArchivePlan.stranded` counts the inbound wikilinks the
move could not reach and `archive` prints the count to stderr without
touching the exit code — `render.wikilink-target` covers the same gap
vault-wide, at lint time.

## Reading a vault

```python
from okf_io import load_bundle
from work_tracker_okf import IGNORE, load_items

bundle = load_bundle(root, ignore=IGNORE)
items = load_items(bundle)
```

`IGNORE` is the **composed** recipe — this lane's `*/references/*` plus
`okf_ext.schemas.DEFAULT_IGNORE` plus `okf_ext.shape.DEFAULT_IGNORE`. Every
consumer of a vault carrying this lane needs all three, and three copies of the
composition is how two consumers end up loading the same bundle differently.

`load_items` never raises. A page whose fields are the wrong shape still
projects, with the uncoercible fields at their empty value — the *rules*
report malformed content, the *reader* does not refuse it.

`IGNORE` is the right lens for everything above — routing, hierarchy, decision
ledgers, validation — but not for planning a move. Reach for
`ARCHIVE_IGNORE` only inside the [Archiving](#archiving) path, where
`okf_ext.moves` needs `work/<slug>/references/` visible to find what to move.
**`ARCHIVE_IGNORE` must never reach `validate()` or `update_index`** — both
would then walk every artifact under every `references/` tree as if it were a
concept in its own right.

## Routing and advancement

Six modules form the qualified routing surface used by a CLI tier. The table
highlights common composition points rather than enumerating every public name;
the owning module remains authoritative, with `__all__` naming its complete
declared surface where present.

| Module | Selected routing APIs |
|---|---|
| `work_tracker_okf.dependencies` | `DependencyEdge`, `DependencyFact`, `DependencyIssue`, `parse_dependencies`, `serialize_dependencies`, `entry_phase`, `resolve_facts`, `satisfied`, `gates`, `unmet`, `describe`, `validate_dependencies` |
| `work_tracker_okf.hierarchy` | `ChildRollup`, `DescendResult`, `child_rollup`, `unknown_depends_on`, `child_gated_node`, `descend`, `nearest_epic` |
| `work_tracker_okf.workflow` | `RouteState`, `Transition`, `Dispatch`, `RouteResult`, `Stage`, `Variant`, `PLAN_OR_EXECUTE`, `route`, `state_for` |
| `work_tracker_okf.advance` | `AdvancePlan`, `FieldChange`, `RefusalReason`, `advance`, `apply` |
| `work_tracker_okf.children` | `ChildrenSync`, `plan_children_sync`, `apply_children_sync` |
| `work_tracker_okf.projection` | `Rollup`, `ResumeItem`, `ResumeSelection`, `rollup`, `select_resume`, `resolve` |

```python
from datetime import date

from okf_io import load_bundle
from work_tracker_okf import IGNORE, load_items
from work_tracker_okf.advance import advance, apply
from work_tracker_okf.workflow import route, state_for

bundle = load_bundle(root, ignore=IGNORE)
items = load_items(bundle)

state = state_for(items, "my-slug")  # None for an unknown slug
if state is not None:
    result = route(state)  # RouteResult: .dispatch (stage, variant) or .blockers
plan = advance(items, "my-slug", today=date(2026, 8, 10))
if plan.refusal is None:
    apply(bundle.concepts["work/my-slug"], plan)
```

`route` returns a `(stage, variant)` pair, never a skill name: mapping the
seven pairs to seven skills is the harness's job. `Stage` and `Variant` are
`Literal` types, so that mapping is exhaustively checkable at the caller's end.

`advance` **plans**; it does not mutate. Refusals are data — a closed
`RefusalReason` vocabulary — and a refused plan carries no changes, so `apply`
is safe by construction.

Two of a transition's fields are handed onward rather than applied:
`AdvancePlan.stamp_source` (the `sources[]` id to record) and
`sync_plan_table` (the `## Plan` row to add). Both need path and table
functions this package does not yet own; a composing CLI resolves them.

## Decision ledgers

Each epic owns one decision ledger. Callers may resolve a child to its epic in
`hierarchy`, but the ledger operations themselves take an explicit path and do
no discovery.

```python
from datetime import date

from work_tracker_okf.decisions import apply_plan, plan_append
from work_tracker_okf.paths import decisions_ledger

ledger = decisions_ledger(epic_slug).path(root)
plan = plan_append(
    ledger,
    question="Which store?",
    status="assumed",
    answer="SQLite",
    rationale="single writer",
    if_wrong="re-plan storage",
    affects=(child_slug,),
    on=date(2026, 8, 18),
    decided_by="pat",
)
if plan.refusal is None:
    application = apply_plan(plan)
    if application.stale:
        print("ledger changed after planning; nothing landed")
```

`decisions.plan_append`, `decisions.plan_update`, and
`decisions.plan_supersede` are the supported planned mutations. Planning
captures expected refusals and writes nothing. `decisions.apply_plan` rechecks
the immutable ledger snapshot while holding the existing exclusive lock; a
stale plan returns `DecisionApplication(stale=True, written=False)` rather than
claiming its entries landed. `decisions.query` and `decisions.counts` are pure
read helpers.

Status is part of the contract: `open` is unanswered, `answered` requires an
answer, `assumed` requires both an answer and an `if_wrong` recovery, and
`superseded` is produced by `plan_supersede` rather than appended directly.

## Migrated child-spec adoption

Migration donors have exactly one supported root:
`work/<epic>/references/child-specs/`. A matched direct child's canonical
destination is `work/<child>/references/01-design-spec.md`, registered as the
root-absolute resource
`/work/<child>/references/01-design-spec.md`.

```python
from work_tracker_okf.adoption import apply_adoption, plan_adoption

plan = plan_adoption(bundle, items, epic_slug)
if plan.refusal is None:
    application = apply_adoption(plan)  # omit this call for a byte-identical dry run
```

`adoption.plan_adoption` only classifies already-loaded domain data. It never
discovers a workspace, invokes Git, or writes. `adoption.apply_adoption`
creates a missing canonical destination without overwrite, removes its donor
only after the destination write succeeds, and registers the canonical source.
An authored noncanonical `design-spec` source wins, including one introduced
after planning. Re-running after a successful apply is idempotent. There is no
`force` option.

## Linting a lane

The lane's 31 finding codes across 14 rule functions ship as one
`extra_rules=` bundle:

```python
from datetime import date

from okf_io import load_bundle, validate
from work_tracker_okf import IGNORE
from work_tracker_okf.rules import lane_rules

bundle = load_bundle(root, ignore=IGNORE)
report = validate(bundle, today=date.today(), extra_rules=lane_rules(repo_root=repo))
```

`repo_root` is optional. Omitting it **skips** `targets.affects-missing` and
`plan.action-target-missing` rather than reporting them: not knowing where the
repo is says nothing about whether the paths are good.

Compose it with the house rules the same way — they are all just rules:

```python
report = validate(
    bundle,
    today=today,
    extra_rules=(
        schema_rule(load_schemas(root / "schema"), severity="error"),
        section_rule(load_sections(root / "sections"), severity="error"),
        *lane_rules(repo_root=repo),
    ),
)
```

Codes are dotted and topic-prefixed (`state.stuck-open`,
`graph.depends-on-cycle`), so `report.by_code(...)` and a
`code.startswith("state.")` filter both work with no lookup table. Sixteen
codes are `error`; the other fifteen are `warn`, which means `Report.ok` stays
a claim about conformance rather than about tidiness.

Nothing in okf-io or okf-ext validates a `sources[].resource` **target** — the
link graph reads body prose only, and `provenance.source-resource-missing`
checks only that `resource` is present, never that it resolves.
`targets.artifact-missing` is this lane's own check for that gap.

## What this package deliberately does not do

Settled, so nobody has to re-open it:

- **No consumer cutover.** Repointing the graph-wiki plugin at this package is
  a separate integration decision, not part of this lane.
- **No dispatch, no auto-drive, no `orchestrate`.** Mapping a `(stage, variant)`
  pair to a skill name and driving a pipeline belong to the harness.
  `route()` returns `Literal` types precisely so that mapping stays
  exhaustively checkable at the caller's end.
- **No `work-index.json` and no `regen-index`.** The sidecar was a cache whose
  invalidation nobody could see. `projection.resolve` is two stat calls,
  which is what the cache existed to serve.
- **`results` has no CLI command.** `ResultsFacts` needs a `start_sha`, and the
  former tier-4 phase marker no longer exists. Nothing this package can reach
  knows where the phase started, so no honest stub can be written from here. It
  stays a library function, called by whoever dispatched the phase, which is
  the only layer that knows the range. This is the one stated hole in "every
  capability is exercisable from the CLI", and naming it is better than a
  command that guesses a sha.
