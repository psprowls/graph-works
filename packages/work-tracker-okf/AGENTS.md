# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Ownership

This package owns the path-native work domain: the item filesystem grammar,
the closed vocabulary, the `WorkItem` projection, lifecycle/hierarchy rules,
and the writers (filing, reparenting, archiving, decision ledgers, indexes).
Keep workspace discovery, configuration, process execution, and transactional
journaling in `graph-works-core`; keep CLI parsing and rendering in the CLI
packages — `cli.py` here is a thin `typer` wrapper, not where domain logic
belongs.

The canonical identity is the complete extensionless bundle-relative path.
Never add basename lookup, date-prefixed aliases, hierarchy frontmatter, or a
fallback from a path to another item — `tests/test_legacy_boundary.py`
mechanically scans every package's source (AST, not text) for the retired
dialect's markers (`DATE_PREFIX`, `workflow_status`, date-prefixed basenames,
any `work_tracker_okf.migration` import) and fails the build if found. That
module was deleted with the one-time migrator (D-013/D-007); the live
migration path now lives outside this package, at
`scripts/migrate_vault_work.py`.

## What the three description terms mean

The pyproject description — "declarations, vocabulary, and the item view" —
names three concrete things, laid out in `src/work_tracker_okf/__init__.py`'s
module docstring (read it; it is the best architecture reference in the
package):

- **Declarations**: the JSON Schemas and section specs this package ships and
  installs into a bundle — `src/work_tracker_okf/assets/schema/*.json` and
  `assets/sections/*.yaml`, enumerated in `resources.SEED_RELATIVE_PATHS` (16
  files: a shared `_base.schema.json` and `_fragments.work_tracker.yaml` plus
  one schema and one section file per of the 7 types). `resources.seed_files()`
  reads them from package data; `init.py` plans their installation into a
  shared bundle via `okf_ext.bundle`. This package ships no config file to
  relocate them persistently — relocation is `declarations_dir=`, applied at
  plan time only (see `resources.seed_files`'s docstring).
- **Vocabulary**: the closed enums and constants in `vocabulary.py` —
  `TYPES`, `PARENT_TYPES`, `ROOT_ONLY_TYPES`, `WORK_STATUSES`, `PHASES`,
  `EFFORTS`, `TERMINAL_STATUSES`, `SLUG_PREFIXES`, etc. — that both the
  declarations and the rules encode. It stays a submodule deliberately (like
  `workflow`, `filing`, `rules`) so `vocabulary.TYPES` says which vocabulary
  is meant rather than putting bare names at the package's front door.
- **The item view**: `items.WorkItem`, the tolerant path-keyed projection
  built by `load_items(bundle)`, plus the read-only rollups and resume
  selection over it in `projection.py` (`rollup()`, `ResumeSelection`).

## Module layering (import direction matters)

Two strictly-downward stacks, both documented in `__init__.py`:

```
items -> hierarchy -> workflow -> {advance, children, projection}
paths -> {filing, sources, results, mutation, reparent, archive, decisions}
```

`filing.apply` and `advance.apply` are deliberately different functions with
the same name in different modules — that's why these stay submodules instead
of being hoisted into the package namespace. Don't "simplify" by re-exporting
them at the top level.

`paths.artifact_ref` returns one frozen `ArtifactRef` carrying the
bundle-relative form, the root-absolute `sources[].resource` form, the
filesystem form, and the matching `sources[].id` together — a caller cannot
obtain a resource string without also getting its id, which is what keeps
managed-artifact and dependency-graph code from inventing ad hoc path joins.

## Invariants

- Item pages occupy root, `children`, or local `_archive` lanes beneath
  `work/`.
- `Release` is root-only; `Release`, `Epic`, and `Feature` may own children.
- Hierarchy and archived state come from the physical path — never duplicated
  in frontmatter.
- Lifecycle state is `work_status` (not `status`, which is a separate,
  document-level field — see `vocabulary.DOCUMENT_STATUSES` vs
  `vocabulary.WORK_STATUSES`).
- Dependency entries are always complete path-keyed mappings — `path`,
  `blocks`, `needs` — never string shorthand; malformed entries are retained
  as `DependencyIssue` values rather than raising, so validation can report
  them.
- Managed artifacts live under the owner's `references/` directory using the
  filenames in `paths.MANAGED_ARTIFACTS` (`00-decisions.md` through
  `04-finish-results.md`).
- `gw work next`'s stamped `sources[]` entry (`missing_design_source` /
  `_apply_normalizations` in `graph_works_core.work.commands`) is frontmatter
  only — this package never writes a body citation for it. If a body citation
  is wanted to satisfy `provenance.source-uncited` (`okf_io/_rules/provenance.py`;
  §5.1 — a bare footnote *definition* with no inline `[^id]` reference already
  satisfies it), write it as `[^id]: [Title](resource)`, the bracketed-link
  style `okf_io.migrate` itself emits, not a bare-path `[^id]: resource`. Only
  the bare form currently blocks `gw work reparent`/`gw work archive` when the
  cited artifact is inside the moved set — see
  `okf-ext/README.md`'s "reference-style link refuses the whole plan" and
  `work/bug-moves-refuses-footnote-definitions`.
- Decision ledgers belong to the nearest parent-capable item (`Release`,
  `Epic`, `Feature`), addressed via `decisions.ledger_ref(owner_path)`.
- Path mutations cover a complete owned subtree, opaque attachments included:
  `Bundle.ignored` files beneath an owned subtree count as members during a
  mutation, so unregistered `references/` content moves with the item while
  staying opaque, and registered/canonical Markdown is promoted for repair.
  `mutation.WorkMutationPlan` is the single immutable effect vocabulary for
  reparenting, Release adoption, and local archival.
- Planners are write-free and return refusals as data. Filing is the one
  small direct writer among the writer surface; workspace-level composition
  converts its result into a transactional mutation. Decision mutations are
  the exception: they use an immutable plan/apply pair where `apply` rechecks
  the ledger snapshot under the existing exclusive lock (plan capture never
  writes).

`IGNORE` is the ordinary read/validation lens (excludes `references/*`,
`.DS_Store`, and the schema/section declaration trees). `ARCHIVE_IGNORE`
drops only the schema/section exclusions — it exists so a move planner can
see reference-tree members that must move with an item; never use it for
validation.

## Rules

`rules.lane_rules(repo_root=…, vault_root=…)` composes six rule-topic
factories (state, plan, graph, structure, targets, decisions — see
`_rules/`) into one `extra_rules=` tuple for `okf_io.validate()`. It's a
**factory per capability**, not a module-level tuple, because `RuleContext`
carries no filesystem and two codes need to know about a repository.
`repo_root` and `vault_root` are two different roots in a split topology
(workspace vs. code repo are different git repos) — either being `None`
*skips* its rule rather than failing it, since not knowing where a root is
says nothing about whether paths under it are good.

## Testing

Fixtures under `tests/fixtures/` are contracts, not incidental data:

- `minimal` — a small nested path-native tree with one intentionally
  malformed page.
- `conformant` — root Release, nested Epic and Feature, leaf, local archive,
  every lane index, cross-tree dependency, nested registered and opaque
  attachments.
- `nonconformant` — triggers every lane catalog code without legacy
  hierarchy fields.
- `nonconformant_repo` — a tiny fake Python package tree used to exercise
  `targets`/`affects` rules against a real repo root.

Run this package's suite in isolation from the repo root (these are the exact
invocations the root `justfile`'s `test` and `cov` recipes use):

```bash
uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests

uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests \
  --cov=work_tracker_okf --cov-branch --cov-report=term-missing --cov-fail-under=95

uv run --package work-tracker-okf mypy --strict --platform linux packages/work-tracker-okf/src
uv run --package work-tracker-okf mypy --strict --platform win32 packages/work-tracker-okf/src
```

(The two `mypy` lines are the root `justfile`'s `types` recipe for this
package — one invocation per `--platform` arm.)

A subset, same pattern as the rest of the workspace:

```bash
uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests/test_rules_plan.py
uv run --package work-tracker-okf pytest -k "reparent or archive"
```

`just sync` (`uv sync --all-packages`) must have run at least once — a bare
`uv run` only installs the root's dependencies, not `typer`, which this
package's `cli.py` needs; without it `mypy --strict` reports every
`@app.command()` as untyped rather than the actual code being wrong.
