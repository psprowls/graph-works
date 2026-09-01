# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Python ≥3.12 (the workspace floor). Tests are pytest.

## Commands

code-graph-io resolves its own dependency closure (tree-sitter, tiktoken, etc.)
that the workspace root does not install, so every root `just` recipe runs it
under `--package` rather than a bare `uv run`:

```bash
uv run --package code-graph-io mypy --strict --platform linux packages/code-graph-io/src
uv run --package code-graph-io mypy --strict --platform win32 packages/code-graph-io/src
uv run --package code-graph-io pytest packages/code-graph-io/tests
uv run --package code-graph-io pytest packages/code-graph-io/tests \
  --cov=code_graph_io --cov-branch --cov-report=term-missing --cov-fail-under=90
```

The coverage floor is **90%**, not the 95% okf-io/okf-ext carry — see the root
`justfile`'s `cov` recipe. A failure reports only a global percentage; read
`term-missing` and start with the lowest-covered module.

Subset examples (same `--package` prefix, then ordinary pytest args):

```bash
uv run --package code-graph-io pytest packages/code-graph-io/tests/test_update.py
uv run --package code-graph-io pytest packages/code-graph-io/tests/parser -k typescript
uv run --package code-graph-io pytest packages/code-graph-io/tests -k "ignore or resolve"
uv run --package code-graph-io pytest packages/code-graph-io/tests -m integration
```

The `integration` marker (declared in this package's `pyproject.toml`) is not
filtered out by any `addopts` here or at the root — plain `pytest tests/`
already runs `tests/integration/`. Its own docstring describes an
opt-in-by-default convention (`# integration-gate-allow`, mirroring a
`GRAPH_WIKI_RUN_INTEGRATION` gate used elsewhere in the workspace) but nothing
in this package currently enforces that skip; treat `-m integration` /
`-m "not integration"` as available filters, not as the default split.

## Layout

- `src/code_graph_io/` — library only, no CLI. The command layer that turns
  this into `gw graph build` / `gw graph update` lives in the sibling
  `graph-works-core` package.
- `src/code_graph_io/parser/` — tree-sitter-backed source parsing (formerly
  the standalone `code-parser` package).
- `tests/` — pytest tests (unit + integration); `tests/parser/` mirrors the
  parser subpackage with its own fixtures.
- `conftest.py` (package root) — a bare in-memory `sqlite3` `conn` fixture for
  low-level table tests; `tests/conftest.py` adds `sys.path` wiring plus the
  session-scoped `seeded_workspace`/`seeded_db` fixtures that build a real
  graph from `tests/fixtures/sample_monorepo` via `update.run(..., full=True)`.

## Architecture

### The pipeline: parse → project → upsert → resolve

```
git diff (or full walk)  ->  parse_bytes()  ->  to_graph_records()
                          ->  upsert.upsert_records()  ->  resolve.sweep()
```

`update.run_workspace()` (single-repo `update.run()` delegates to it with one
member) drives this per configured repo, inside **one SQLite transaction**
for the whole workspace. Per member (`_update_one_repo`): diff files against
the last-indexed commit, parse and upsert the changed ones, refresh
manifest/C#-project/builtin/structural/agent-plugin/entry-point/test-suite
node sets, resolve import edges, then (full builds only) delete any node
under a tracked path that the pass didn't touch. Global steps — dependency
reconciliation, `resolve.sweep()`, the strict-tree-invariant check, and
workspace metadata — run once after every member has been processed.

Gotchas that span several files:

- **Two version numbers, two different triggers.** `schema.SCHEMA_VERSION`
  (currently 3) gates whether the on-disk DB shape matches what this build's
  code expects at all — a mismatch raises `SchemaMismatchError` and refuses
  to open unless `full=True`, which drops and recreates `code.db`.
  `schema.DERIVER_VERSION` (currently 10) tracks *derivation logic* changes
  (classification rules, derived-edge rules, new attrs) that don't change the
  table shape but do make existing rows stale; a mismatch there silently
  forces `full=True` on the next `run_workspace()` call instead of raising.
  Bumping the wrong one either forces needless raw refusals or lets stale
  derived data survive.
- **`ignore:` pattern changes have no git footprint.** Excluding a
  previously-tracked path from scanning doesn't show up in `git diff`, so
  `_update_one_repo` fingerprints the resolved ignore patterns
  (`ignore_fingerprint:<repo-uri>` in `metadata`) and forces that member's
  `full=True` when the fingerprint moves — otherwise nodes under the
  newly-ignored path would be stranded forever.
- **Node identity is scoped to the repo mid-pass.** `upsert.set_current_repo()`
  is set before a member's pipeline runs and cleared in a `finally` (twice,
  defensively — once per-member, once in `run_workspace`'s outer `finally`)
  so that two sibling repos sharing a relative path (e.g. both have a root
  `pyproject.toml`) don't collide into one node.
- **Emitter ordering around the full-mode cleanup DELETE is load-bearing.**
  `packages.refresh()` runs *before* the full-mode stale-node purge (it has
  its own `_prune_vanished` pass keyed off the freshly-discovered manifest
  set, since a package/app node's `path` is a directory — often `""` for a
  root manifest — that would never appear in the purge's `tracked_paths`).
  Every other emitter (`structural_nodes`, `agent_plugins`, `entry_points`,
  `test_suites`) runs *after* the purge and gets pruning for free. Reordering
  any of this silently reintroduces either false deletions or orphaned nodes.
- **The strict-tree invariant is always checked, once, at the very end.**
  `physically_contains` must form a tree — no node may have more than one
  such parent edge. `_enforce_strict_tree_invariant` runs inside the same
  transaction as everything else, so a violation rolls back the whole
  workspace update, not just the offending repo.
- Deferred imports inside `update.py` (`agent_plugins`, `entry_points`,
  `structural_nodes`, `test_suites`, `repo_context`) exist to break real
  import cycles (those modules call back into `update._git` /
  `NotInGitRepoError`), not stylistic laziness — don't "clean them up" to
  top-level imports.

### The graph DB boundary (invariant, mechanically enforced)

Only code-graph-io may build the `code.db` path, open a connection to it, or
run SQL against it. Every other package reaches the graph through a
`GraphReader`/`GraphStore` from `code_graph_io.open_reader(graph_dir=…)` /
`open_writer(graph_dir=…)`. The conn-level modules — `queries`, `upsert`,
`resolve`, `store`, `schema` — are code-graph-io-internal; callers use the
handle API, the record dataclasses, and the error classes re-exported from
the `code_graph_io` top level instead. `tests/test_db_boundary.py` walks
every other package's `src/` and fails on a `"code.db"` literal or an import
of those five internal modules — this is a live gate, not just a convention
in prose.

`GraphReader` wraps a `check_same_thread=False` read-only connection (so a
handle can be handed across threads, e.g. a LangChain tool's `_run` inside
`run_in_executor`) but serializes every query behind `self._lock`, since raw
sqlite3 connections still aren't safe for *concurrent* multi-thread use —
only sequential cross-thread use. `dump_sql()` is deliberately **not**
`@_locked`: `sqlite3.Connection.iterdump()` returns a lazy generator whose SQL
runs as the caller iterates, well after the method returns, so locking just
the call would protect nothing.

`GraphStore(GraphReader)` adds the mutating surface (`upsert_records`,
`resolve_file_imports`, `sweep`, `transaction()`); read-only callers never see
it. Tests should reach the DB the same sanctioned way production code would
where possible — `code_graph_io.testing.open_store` / `raw_conn` are the
blessed test-only escape hatches (`raw_conn` for bulk-INSERT fixture seeding
that bypasses the handle API on purpose); avoid calling `sqlite3.connect`
directly in new tests.

### Parsing sits below the rest of the package (import-linter contract)

`code_graph_io.parser` is one layer, everything else in `code_graph_io` is
the other (root `pyproject.toml`, contract `"Parsing sits below the graph"`,
`exhaustive = true`) — parser code may not import back up into the graph
layer, and any new top-level module has to be added to that layer list or the
contract fails closed. Other packages never import `code_graph_io.parser`
directly: language metadata goes through `code_graph_io.source_meta`
(`extension_languages()`), and record types go through `code_graph_io.records`
(`GraphNode`/`GraphEdge`/`GraphRecords`/`NodeKey`, plus the `as_graph_records`
tuple-boundary helper) — both proxy the parser's own types without exposing
the subpackage.

`.tsx` files use the `tsx` tree-sitter grammar, not `typescript` — the plain
TypeScript grammar cannot parse JSX and drops component bodies into
error-laden trees. The emitted logical language stays `"typescript"`; only
`TypeScriptParser`'s grammar selection (`parsers/typescript.py`) branches on
the file extension.

Parser tests are fixture-driven: `tests/parser/fixtures/<lang>/*.py|.ts|...`
paired with `*.expected.json` (raw parser output) and
`*.graph.expected.json` (post-projection graph output), parametrized via
`tests/parser/_fixture_loader.fixtures_for(...)`.

### Schema shape and the read/write split

The SQLite schema is deliberately minimal — `nodes`, `edges`, `metadata`
(`schema.py`); per-language detail lives in each node's `attrs_json` blob
rather than as columns. `store.connect()` (read-write, `create=True`
bootstraps a fresh DB) and `store.read_only_connect()` (opens with
`?mode=ro` + `PRAGMA query_only = ON`) both check `metadata.schema_version`
against `schema.SCHEMA_VERSION` on open and raise `SchemaMismatchError` on a
mismatch, or `GraphNotInitializedError` if the file or its `nodes` table
doesn't exist yet. All updates run inside one transaction
(`store.transaction()` — `BEGIN` / rollback-on-exception / commit).

### Exit codes

`exit_codes.py` only declares the stable v1+ constants (`SUCCESS`,
`GENERIC`, `STALE` — reserved, zero producers today — `NOT_INITIALIZED`,
`SCHEMA_MISMATCH`, `NOT_IN_GIT_REPO`, `UPDATE_IN_PROGRESS`, `AMBIGUOUS`); the
command layer that actually returns them lives in `graph-works-core`
(`graph_works_core/graph/commands.py`, `graph_tools.py` — the user-facing
entry point is `gw graph build`). Don't expect a CLI in this package.

### `ignore:` handling

The scanner skips a built-in floor (`_ignore.DEFAULT_SKIP_DIRS`: `.git`,
`node_modules`, `.worktrees`, `.venv`, `venv`, `dist`, `build`,
`__pycache__`, `.tox`, `.nox`) unconditionally, regardless of config.
Additional `ignore:` glob patterns (from a workspace's `workspace.yaml`, via
`code_wiki_okf.config.load_config`) are compiled by callers into an
`IgnoreSpec` (`_ignore.compile_ignore`) and passed through explicitly —
code-graph-io itself never reads an ignore file from disk.

## Conventions

- Read-only queries always go through `store.read_only_connect()` (or the
  `GraphReader` handle wrapping it) — never a plain read-write `sqlite3.connect`.
- All writes run inside one transaction (`store.transaction()` /
  `GraphStore.transaction()`).
- Errors go to stderr, JSON output goes to stdout. Never mix.
- Exit codes are stable from v1 forward — see `exit_codes.py`.
