# Lifecycle rules — work_layer

> **Substrate ownership.** This document describes behavior that the graph-works rebuild is
> re-implementing. Identifiers and paths here are retargeted for the `graph-works` namespace, but
> the behavioral truth is owned by [`2026-08-13-epic-feature-work-pipeline-autodrive-wiring`](/work/2026-08-13-epic-feature-work-pipeline-autodrive-wiring.md) and is re-authored there, not here.
> Treat a disagreement between this page and that item as this page being stale.

The 32 rules `/graph-works:lint` runs against `<vault>/work/*.md`
plus the sidecar. Each entry: rule ID, severity, trigger, rationale, remedy.

> **Note:** These rules are live — run by `/graph-works:lint` (mechanical pass) and `gw work lint`, against every `wiki/work/*.md` item plus the sidecar.

## Schema-shape (3)

### `status-not-in-enum` — error
**Trigger:** `status:` outside `{open, accepted, in-progress, mitigated, resolved, wontfix, superseded}`.
**Rationale:** the 7-state lifecycle is the contract every consumer relies on.
**Remedy:** map the value to its closest valid state. `proposed`/`planned` → `open` (downgrade — don't claim acceptance without a `## Plan`). `done` → `resolved`. `cancelled` → `wontfix`.

### `kind-not-in-enum` — error
**Trigger:** `kind:` outside `{bug, tech-debt, test-gap, security, perf, feature, epic, spike}`.
**Rationale:** kind drives per-kind rules (severity allowed, target required) and consumer queries.
**Remedy:** pick the closest match. `data-model-defect` folds to `bug` + a `data-model` tag. `doc-drift` folds to `tech-debt` + `docs`.

### `severity-on-non-bug` — info
**Trigger:** `severity:` set on `kind: feature | epic | spike`.
**Rationale:** severity is a triage knob for things that broke; intent doesn't have severity.
**Remedy:** remove `severity:` from the frontmatter.

## State-conditional (6)

### `accepted-without-plan` — error
**Trigger:** `status: accepted | in-progress | mitigated | resolved` and `## Plan` is missing or empty.
**Rationale:** "accepted" means scope is committed — the plan is what makes the commitment legible.
**Remedy:** offer to draft the `## Plan` table from the body's existing prose. Do not transition the status back to `open` — that hides the work.

### `in-progress-without-ref` — error
**Trigger:** `status: in-progress` and no `pr` or `branch` frontmatter field.
**Rationale:** "in progress" without a place to read the in-progress code is an unverifiable claim.
**Remedy:** populate `branch:` (preferred for early work) or `pr:` (once a PR is open).

### `resolved-without-ref` — warn
**Trigger:** `status: resolved`, `kind` is not `epic`, and `resolved_in:` empty.
**Rationale:** future readers need to find the change that resolved the item. Epics are exempt: they resolve via the children-terminal gate (every child reaches a terminal status), not by landing a single change, so they carry no `resolved_in`.
**Remedy:** populate `resolved_in:` with the PR number, commit SHA, or merged branch name.

### `superseded-without-link` — error
**Trigger:** `status: superseded` and `superseded_by:` empty.
**Rationale:** "superseded" loses meaning without a pointer to what replaced this.
**Remedy:** populate `superseded_by:` with a `work/<slug>` reference.

### `mitigated-without-mitigation` — error
**Trigger:** `status: mitigated` and `mitigation:` empty.
**Rationale:** "mitigated" promises the symptom is hidden — readers need to know how, so they can re-evaluate later.
**Remedy:** populate `mitigation:` with a one-paragraph description.

### `wontfix-without-rationale` — warn
**Trigger:** `status: wontfix` and `rationale:` empty.
**Rationale:** closed without action needs a reason or it'll get re-opened by the next person who hits it.
**Remedy:** populate `rationale:`.

## Reference resolution (2)

### `affects-target-missing` — error
**Trigger:** `affects[]` entry (after stripping `:line` suffix) doesn't resolve under `<repo>/`.
**Rationale:** the link lets consumers (and you) navigate to the affected code.
**Remedy:** check for renames first — the target may have moved. If it's gone for real, update or remove the entry.

### `plan-action-target-missing` — error
**Trigger:** a `## Plan` row mentions a path-shaped token in any cell (Action, Done when, or Rationale) that doesn't resolve under `<repo>/`.
**Rationale:** plan rows that name files should name files that exist.
**Remedy:** correct the path or remove the reference. False positives on regex-shaped tokens get filed as `tech-debt` work items.

## Lifecycle / staleness (3)

### `stuck-open` — warn
**Trigger:** `status: open` and `updated:` older than `--stuck-days` (default 30).
**Rationale:** items that haven't moved in a month either need acceptance or rejection.
**Remedy:** triage during a planning conversation. Don't auto-action.

### `stuck-accepted` — warn
**Trigger:** `status: accepted` and `updated:` older than `--stuck-days × 2` (default 60).
**Rationale:** accepted-but-not-started for two months means the plan got stale.
**Remedy:** review the plan; either start work on it, downgrade to `open` if the plan has gone stale and needs rework, or close with `wontfix`.

### `archive-eligible` — info
**Trigger:** `status: resolved | wontfix | superseded`.
**Rationale:** terminal-status items aren't drift, but they clutter the active queue. Surfacing them as `info` keeps the queue clean without inflating the warning channel.
**Remedy:** run `/graph-works:archive` to move eligible items into `<vault>/work/_archive/`. Pass `--dry-run` first to see what would move; pass slugs to target specific items.

## Body shape (3)

### `done-when-missing` — warn
**Trigger:** a `## Plan` row on `kind: feature | epic` has empty `Done when` cell.
**Rationale:** features need observable completion criteria; bug fixes' completion is implicit (the bug stops happening).
**Remedy:** populate the cell.

### `feature-without-target` — warn
**Trigger:** `kind: feature | epic` and `target:` empty.
**Rationale:** features without a target window slide indefinitely.
**Remedy:** populate `target:` with a quarter (`2026-Q3`) or month.

### `plan-table-malformed` — warn
**Trigger:** `## Plan` heading present but no recognizable markdown table follows.
**Rationale:** the table is the contract format; prose plans aren't queryable.
**Remedy:** convert the prose to a table. Canonical columns: `Action | Done when | Rationale`. Header detection accepts any 2 of those 3, case-insensitive.

## Sidecar (2)

### `sidecar-missing` — warn
**Trigger:** `<vault>/work-index.json` does not exist.
**Rationale:** consumers can't read the queue without it.
**Remedy:** run `/graph-works:regen-index`.

### `sidecar-stale` — warn
**Trigger:** sidecar's `generated_at` is older than the newest item's `updated:`.
**Rationale:** consumers will read stale data.
**Remedy:** run `/graph-works:regen-index`. Never hand-edit `work-index.json`.

## Workflow (4)

These rules fire only when the workflow-owned keys are present — items filed
outside the workflow lint clean.

### `effort-not-in-enum` — warn
**Trigger:** `effort:` set but not one of `xtra-small | small | medium | large | xtra-large`.
**Rationale:** the workflow's effort fork (small bug-like work skips planning) needs a comparable scale; legacy free-text efforts degrade to warnings, not errors.
**Remedy:** re-size the item (`gw work advance <slug> --effort <value>` or edit frontmatter).

### `phase-not-in-enum` — error
**Trigger:** `phase:` set but not one of `design | plan | execute | finish | done`.
**Rationale:** `phase` is machine-owned pipeline position; an unknown value breaks `gw work next` routing.
**Remedy:** fix the value or remove the key (the item re-enters the workflow at first dispatch).

### `phase-status-incoherent` — warn
**Trigger:** `accepted` with phase outside `execute | finish | done`; `in-progress` with phase outside `execute | finish`; `resolved` with phase other than `done`.
**Rationale:** status (commitment) and phase (pipeline position) advance together via `gw work advance`; divergence means hand-editing.
**Remedy:** re-run `gw work advance`, or hand-fix whichever field is wrong. Warn-level because disposition stays human-owned.

### `artifact-doc-missing` — warn
**Trigger:** `spec_doc:` or `plan_doc:` set but the workspace-relative file does not exist.
**Rationale:** fresh-context workflow sessions locate prior output through these pointers; a dangling pointer strands the next stage.
**Remedy:** restore the file under `<workspace>/raw/`, or clear the key.

## Hierarchy (9)

These cross-item rules resolve `parent:` and `depends_on:` references against the
full set of work items in `<vault>/work/`. They fire only when those keys are
present — flat items filed without a parent or dependencies lint clean.

### `parent-missing` — error
**Trigger:** `parent:` set but no work item has that slug.
**Rationale:** a child pointing at a non-existent epic is orphaned — rollups and the children-terminal resolve gate can't find it.
**Remedy:** fix the slug (check for renames first), file the missing epic, or clear `parent:` if the item is standalone.

### `parent-kind-invalid` — error
**Trigger:** `parent:` resolves to a work item whose `kind` is not `epic` or `feature`.
**Rationale:** only epics and features own children (`PARENT_KINDS`). A bug parenting a bug breaks the decomposition contract.
**Remedy:** repoint `parent:` at the owning epic/feature, or promote the referenced item if it is in fact the umbrella.

### `depends-on-missing` — error
**Trigger:** a `depends_on:` slug has no matching work item.
**Rationale:** a dependency on something that doesn't exist can never be satisfied; ordering logic silently skips it.
**Remedy:** fix the slug (check for renames first), file the missing item, or remove the dangling entry.

### `depends-on-cycle` — error
**Trigger:** the dependency graph contains a cycle. The graph is walked over
`(slug, phase)` nodes, not slugs: each item contributes the implicit chain
`design -> plan -> execute -> finish`, and an edge with `blocks: P` / `needs: N`
contributes `(dep, next(N)) -> (this, P)` (`needs: resolved` hangs off
`(dep, finish)`). A phase-granular A -> B -> A pair is therefore legal when the
real ordering is acyclic — A building against B's plan while B waits on A's
code is `B.design -> B.plan -> A.execute -> B.finish`, and is not flagged. Every
slug in a genuine cycle is flagged, each finding carrying the full path
(`a.execute -> b.execute -> a.execute`).
**Rationale:** a dependency cycle has no valid execution order — nothing in the cycle can start.
**Remedy:** break the cycle by dropping the edge that shouldn't be there; reconsider which item truly blocks which.

### `depends-on-not-sibling` — warn
**Trigger:** a `depends_on:` target resolves but its `parent:` differs from this item's `parent:`.
**Rationale:** dependencies are expected within a single epic's children; a cross-epic dependency usually signals a mis-scoped boundary, though it can be legitimate.
**Remedy:** confirm the dependency is intentional. If the work belongs together, move the items under the same epic; otherwise leave it and treat the warning as acknowledged.

### `epic-without-children` — warn
**Trigger:** `kind: epic` at `phase: execute | finish | done` with no work item naming it as `parent:`.
**Rationale:** an epic that has advanced past planning but owns no children is either undecomposed or had its children deleted — its rollup is empty.
**Remedy:** decompose the epic into child items, or close it if the work is no longer planned.

### `parent-cycle` — error
**Trigger:** the `parent:` chain contains a cycle (e.g. A's parent is B and B's parent is A). Every node in the cycle is flagged.
**Rationale:** a cyclic hierarchy has no root; rollups and `--descend` cannot resolve it (descend independently reports it as a blocker).
**Remedy:** break the cycle by removing the `parent:` edge that shouldn't exist.

### `children-stale` — warn
**Trigger:** an item's on-disk `children:` list differs from the list derived from the children's `parent:` keys (archived children included).
**Rationale:** `children` is a derived projection; divergence means a hand-edit or a mutation made outside the gw commands.
**Remedy:** run `gw work regen-index` — never hand-edit `children:`; to detach, delete the child's `parent:` key.

### `dep-edge-invalid` — error
**Trigger:** a mapping-form `depends_on` entry is missing `slug`, carries a key other than `slug`/`blocks`/`needs`, or gives a `blocks`/`needs` value outside its enum. A bare slug string is valid legacy shorthand and is never flagged.
**Rationale:** the gate fails safe — an unrecognized value reads as permanently unsatisfied — so without this rule a typo'd `needs: planning` would block the dependent forever with no explanation.
**Remedy:** correct the key or value on the work page, or drop the entry.
