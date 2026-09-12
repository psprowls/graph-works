# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

`code-wiki-okf` reads the shared code graph (via `code-graph-io`'s
`GraphReader`) and generates/updates a standalone OKF v0.2 bundle describing
it — one page per `Repository`, `Package`, `App`, `AgentPlugin`, `TestSuite`,
`File`, and `Dependency`. It is a tier-3 consumer of `okf-io` (ADR-0005):
depends on `okf-io`, `okf-ext[schemas]`, and `code-graph-io`; nothing depends
on it. Python ≥3.12.

## Commands

From the repo root (this package resolves its own dependency closure, so it
needs `--package`, unlike `okf-io`/`okf-ext` which share the root `uv run`):

```bash
uv run --package code-wiki-okf mypy --strict --platform linux packages/code-wiki-okf/src
uv run --package code-wiki-okf mypy --strict --platform win32 packages/code-wiki-okf/src
uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests
uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests --cov=code_wiki_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
```

These are exactly the `types` / `test` / `cov` lines the root `justfile`
runs for this package — `just check` runs them for every workspace member.
`types` runs twice, once per `--platform` arm (`linux`, `win32`). Coverage
is gated at 95%, same floor as okf-io/okf-ext.

Subset examples:

```bash
uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests/sync -v
uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests/test_placement.py -k duplicate
```

Note: a bare `uv sync` only provisions the root's dependencies. `typer`
(this package's CLI dependency) is only pulled in by `uv sync --all-packages`
or a `--package code-wiki-okf` invocation — without one of those, `mypy
--strict` reports every `@app.command()` in `cli.py` as untyped even though
the code is fine.

## Architecture

### CLI surface (`cli.py`) — the only clock reader

Three Typer commands: `init`, `sync`, `validate`. `cli.py` is the one module
in this package allowed to call `datetime.now()`; every function below it
takes `today=`/`at=` as an argument. `sync` and `validate` both resolve
`graph_dir` to `bundle_root` unconditionally — this standalone CLI has no
separate workspace layout to derive a cache directory from.

A known, deliberate gap: `init` no longer seeds a `workspace.yaml` of its own
(2026-08-22 tech-debt item, ADR-0033). A bundle that was `init`'d but never
given a `workspace.yaml` (by hand, or via `--config-path`) makes `sync`/
`validate` fail with a raw uncaught `OSError`, not a clean `ConfigError` exit.

### Config vs. content — two different "never fails" rules

`config.py`'s `load_config()` raises `ConfigError` (a `ValueError` subclass,
matching `okf_ext.tags.VocabularyError`/`SchemaError`) for a malformed
`workspace.yaml`. This is deliberately the opposite of okf-io's own
never-raise rule for concept *content* — the workspace manifest's
`repositories`/`ignore`/`state_gate` blocks are configuration, and a caller
getting them wrong should hear about it immediately, not get a silently
degraded `Bundle`. `config.py` reads the same `workspace.yaml` that
`graph_works_core.workspace.manifest` reads, but independently and only for
those three blocks — this package cannot depend on `graph_works_core`
(ADR-0005 puts it above tier 3), so every other top-level key is ignored
rather than validated.

**Two entry points, one validator.** `load_config(bundle_root, config_path=…)`
reads the file and validates it. `config_from_mapping(content, anchor=…,
bundle_root=…, …)` validates an already-parsed mapping, for a caller that has
its own reader — `graph_works_core.workspace.config`, which reads through
`config-io`'s store so the workspace has one YAML parser rather than two. The
band rule is untouched: this package is handed a mapping and two directories,
and still imports nothing from `graph_works_core`.

`anchor` is the directory a relative `repositories.*.path` resolves against
(ADR-0041) and `bundle_root` is what a relative `graph_dir` /
`declarations_dir` resolves against — different directories in a graph-works
layout, so do not pass one for the other. `source` is the name every
`ConfigError` quotes.

`Config.state_gate` (`StateGateConfig`) is parsed from `workspace.yaml` and
`git_state.compute_state_gate()`/`StateGate` are fully implemented, but as of
this writing **nothing in `cli.py` or `sync/run.py` calls
`compute_state_gate`** — the gate is wired plumbing with no caller yet. Don't
assume dirty-tree or wrong-branch protection is actually enforced anywhere
just because the config and the function both exist.

### Placement — the single source of truth for "where does this page live"

`placement.py` is deliberately pure: given a `PlacementContext` (type name +
graph `resource:` string, e.g. `pkg:org/repo/name`), `canonical_concept_id()`
computes the *one* legal bundle path, with no bundle, schema set, or
filesystem consulted. Every other module — the entity planner, the mirror
planner, the pruner, the catalog writer, and `placement_rule` (the
`placement.*` validation rule) — calls through this same function, so
writers and validators can never disagree about where something belongs.

Two `placement.*` codes, both error-severity by construction in this
package's own `validate` wiring (`placement_rule(severity="error")`), unlike
most okf-ext rules which default to warn:

- `placement.directory-mismatch` — a page's concept ID doesn't match the one
  its `resource:` implies.
- `placement.duplicate-resource` — two pages claim the same `resource:`.

Both are hard errors because routine `sync` *refuses* rather than silently
repairs a page at the wrong canonical path.

The placement matrix (also in the README, repeated here because it's the
thing new code most often gets wrong): `repositories/<repo>/repository.md`,
`repositories/<repo>/{packages,apps,agent-plugins,test-suites}/<slug>.md`,
`repositories/<repo>/files/<source-path>.md`, and
`dependencies/<ecosystem>/<slug>.md`. Top-level `packages/`, `apps/`, etc.
are discovery indexes only — they link to the canonical repository-owned
pages, never duplicate them. Files have no top-level lane at all.

### Two independent write lanes, reconciled through one plan

`sync/run.py`'s `plan_sync()`/`sync_bundle()` is the composite entry point.
It drives two lanes that are planned and preflighted together but applied in
sequence:

1. **Entities** (`entities/sync.py`, `entities/pages.py`, `entities/render.py`)
   — Repository/Package/App/AgentPlugin/TestSuite/Dependency pages, planned
   from the graph reader via `plan_entities()`.
2. **Mirror** (`mirror/plan.py`, `mirror/apply.py`, `mirror/walk.py`) — one
   `File` page per tracked source file in a repo, planned via `plan_mirror()`
   against `git ls-files` (`mirror/walk.py`) plus git's own rename detection
   (`git_state.find_renames`), so a moved-and-unedited file becomes a page
   *move*, not a delete+create.

Both lanes' planned bundle members are cross-checked in
`_preflight_union()` *before* either lane writes anything, catching a member
that's filesystem-equivalent (NFC + casefold — same collision policy as
`resources.py`) across the two lanes. `sync_bundle()` then applies entities,
applies each repo's mirror, prunes entities/pages whose resource vanished
(`entities/delete.py`), reloads the bundle, and only then reconciles the
catalogs (`entities/catalog.py`) against what's actually on disk — catalogs
are computed last because they need to see the post-prune, post-mirror
state, not the pre-write plan.

`--dry-run` walks a parallel path that *projects* the same end state
(`_project_catalog_pages`, `_project_mirror_indexes`) without touching disk,
so the plan it prints matches what a real run would do, including catalog
membership after moves/creates/deletes that haven't happened yet.

### Provenance and idempotence

`provenance.py` builds the `generated`/`last_updated_commit`/`tokens`
frontmatter values from injected `at`/`sha`/`count` — no clock, no I/O. These
three keys are deliberately excluded from every "did this page actually
change" comparison (`mirror/plan.py`'s `_render_matches_disk`,
`entities/sync.py`'s regeneration diffing): `generated.at` is a fresh
wall-clock stamp on every run, so comparing it would make every page look
changed on every sync, defeating the idempotence check entirely.

### Drift validation (`sync/rule.py`, `sync/snapshot.py`)

`validate`'s `sync.*` codes (`sync.stale-page`, `sync.missing-page`,
`sync.orphan-page`) are commit-derived staleness, computed by re-running the
*same* planners `sync` would use (`snapshot_bundle()` calls `plan_entities`
and `plan_mirror` read-only) and diffing against what's on disk — it never
independently reimplements "is this stale". Don't confuse `sync.stale-page`
with okf-io's own `lifecycle.stale` — that's a human-authored `stale_after:`
date field; this is purely commit-derived and unrelated. In `validate`,
every `sync.*` finding fails the run regardless of severity (unlike the
catalog's normal error-only bar), because staleness is this command's whole
reason to exist.

### `resources.py` — find-by-resource, collision-aware

`resource_index(bundle)` walks the bundle once and indexes every document by
its `resource:` frontmatter value. A resource claimed by more than one
document does **not** get a first-in-walk winner in `by_resource` — it's
visible only in `members_by_resource`, forcing every caller to notice the
ambiguity rather than silently picking one. The same index also answers
cross-platform filesystem collisions (NFC + casefold identity) via
`filesystem_members_for`/`filesystem_path_conflicts_for`, used by the
preflight checks in `sync/run.py` before any write.

### `init.py` — additive install, not "own the whole directory"

`install_bundle()` used to refuse any non-empty target; it doesn't anymore.
The contract narrowed because three tier-3 packages can now share one
bundle: `index.md`/`log.md`/`tags.yaml` are the shared scaffold (owned by
`okf_ext.bundle`'s `plan_scaffold`, not this package — `tags.yaml` in
particular is deliberately absent from `SEED_RELATIVE_PATHS`), and this
package's own seven schemas + seven section declarations install alongside
whatever else is already there. The only remaining refusal is per-file: a
file this package owns exists with content it didn't write. All three
writers (`init`, `sync`, and okf-io's own writers underneath) default to
`dry_run=True`.

## Gotchas worth knowing before touching multiple files

- `okf_io._rules` vs this package's own rules: `placement_rule` and
  `sync_rule` are *extra_rules* passed into okf-io's `validate()` — they are
  not part of okf-io's catalog and won't show up in `test_catalog.py` there.
- The mirror lane's `updates`/`creates` carry `Render` objects, not a
  `RegenerationPlan` — `plan_regenerate` can't run until after moves/creates
  land and the bundle is reloaded, which is why `sync_bundle()` interleaves
  apply-then-reload-then-catalog rather than planning catalogs up front.
- `context_from_resource()` parses a `resource:` string; `canonical_concept_id()`
  turns a `PlacementContext` into a path. Mixing up which one you need against
  a loaded `Document` vs. a fresh graph resource is the most common source of
  confusion when extending a rule or planner.
</content>
