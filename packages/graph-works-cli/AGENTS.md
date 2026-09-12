# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`gw` — a thin Typer CLI over `graph-works-core` (ADR-0013: all logic stays in core, this package
routes, parses, formats, and traces). Python >=3.12. Tests are pytest.

## Commands

From the repo root (this package resolves its own dependency closure, so it runs under `--package`,
not a bare `uv run`):

```bash
uv run --package graph-works-cli pytest packages/graph-works-cli/tests
uv run --package graph-works-cli mypy --strict --platform linux packages/graph-works-cli/src
uv run --package graph-works-cli mypy --strict --platform win32 packages/graph-works-cli/src
uv run --package graph-works-cli pytest packages/graph-works-cli/tests --cov=graph_works_cli \
    --cov-branch --cov-report=term-missing --cov-fail-under=95
```

These are exactly `just test` / `just types` / `just cov`'s per-package lines for this package (see
root `justfile`); `just check` runs the whole workspace, not just this one. `just types` runs the
`mypy` line twice, once per `--platform` arm.

Subset examples:

```bash
uv run --package graph-works-cli pytest packages/graph-works-cli/tests/test_graph_cli.py
uv run --package graph-works-cli pytest -k "surface or describe_surface"
```

`test_surface_freeze.py` and `test_describe_surface.py` check the CLI tree against
`tests/fixtures/surface.golden.json` — regenerate that fixture deliberately, alongside the command
change that moved it, never as an incidental side effect.

## Architecture

### Command surface

`cli.py` builds the root Typer app (`gw`), wires the `-v/-vv` verbose callback, `version`, `help
[--json]`, and mounts five sub-apps via `add_typer()` / root-command registration:

- `graph_cli` — `gw graph build|describe|find|export`, code-graph queries.
- `wiki_cli` — `gw wiki lint|drift|stats|index|archive|proposals|proposal ...`, plus root-level
  aliases `gw bootstrap|scan|ingest|query` registered by `wiki_cli.main.register_root_commands()`.
- `work_cli` — `gw work ...` (the work-item pipeline verbs, `decision` sub-app,
  `reconcile-context`), plus root-level aliases `gw next` and `gw archive` registered by
  `util_cli.main.register_util_root_commands()`. `gw next` is a genuine alias — it reuses `gw work
  next`'s own callback object rather than a copy (see `util_cli/main.py`'s `work_next_callback()`),
  so the two can never drift.

  `gw work next` has one blocker source the routing table cannot see: a malformed
  configured stage skill. `entry_for()` is called under a `WorkspaceError` guard
  and its message is appended to `blockers[]` with `action` nulled, rather than
  failing the command — the command owns a blockers channel and the workflow skill
  already stops on one. `gw work advance` and `gw work orchestrate` own no such
  channel and keep the `WorkspaceError` → `SCHEMA_MISMATCH` mapping.
- `config_cli` — `gw config get|list|set|unset|sync|hooks enable|disable`, the sole programmatic
  writer for `workspace.yaml` catalog keys. `set`/`unset --local` write the gitignored, per-machine
  `workspace.local.yaml` overlay instead. `set`/`unset` refresh `.gw/cache/config.json`
  automatically; `sync` is the manual refresh after a hand edit.
- `util_cli` — diagnostics: `gw util describe-surface [--json]`, `log`, `tokens`, `trace`.

Every sub-app's commands take `--workspace PATH` and call `workspace_resolution.resolve_workspace()`
first (D-002): explicit path -> `GRAPH_WORKS_DIR` env -> cwd git walk-up, all via
`graph_works_core.workspace.discovery.resolve()`. A refusal there prints `Error: ...` to stderr and
exits `exit_codes.NOT_INITIALIZED` — precedence and discovery logic live in core, never here.

### `exit_codes.py` — the one numbering, CLI-wide (ADR-0013 rule 1)

Re-exports `code_graph_io.exit_codes` by explicit name (not `from x import *`), so every sub-app
imports from inside this package and `test_exit_codes.py` can pin the identity. Every sub-app maps
its own typed result onto the closest-fitting code here rather than inventing a second numbering:
missing workspace -> `NOT_INITIALIZED`, unresolved slug/entity match -> `AMBIGUOUS`, stale routing
checkout -> `STALE`, uncaught error -> `GENERIC`.

### Error-message convention — capitalized `Error:`, and one live inconsistency (D-026)

The CLI-wide convention is `Error: <message>` on stderr (`errors.py`'s `exit_error()`). Core spells
its own failures `error: <exc>` (lowercase); `graph_cli/main.py`'s `_emit()`/`_normalize_error()`
restyles that prefix on the way out because `gw graph`'s error text is part of the frozen command
surface. `wiki_cli/errors.py` and `workspace_resolution.py` each still hand-spell `Error: {exc}`
independently rather than calling `errors.exit_error()` — widening the normalization to those two
sites is deliberately deferred (tracked as D-026), not an oversight to "fix" incidentally while
touching either file.

### The `--json` refusal envelope — `gw work` only (D-004/D-007)

A `--json` `gw work` command that refuses still exits non-zero, but now also prints a structured
document on stdout before it does, instead of leaving stdout empty:

```json
{
  "error": {
    "command": "work archive",
    "reason": "refused",
    "message": "archive refused; nothing was applied",
    "exit_code": 1,
    "payload": { "...the verb payload the CLI already computed, or null..." }
  }
}
```

`work_cli/rendering.py`'s `fail()` is the single choke point every `gw work` exit site routes
through (65 call sites as of D-004); it now takes a required `reason=` from a closed vocabulary
(`refused`, `incomplete-apply`, `conflict`, `incomplete`, `usage`, `workspace`, `unresolved`,
`not-a-repo`, `io`) and an optional `payload=` — the verb payload already computed for a
post-payload refusal, `None` for a pre-payload failure (an argument-parse or workspace-resolution
error, where no payload was ever built). `"error" in doc` is the discriminator: no success
projection in `rendering.py` carries a top-level `error` key or is a single-key object, so a refusal
document can never be misread as a result.

The mechanism is a `ContextVar` (`rendering._JSON_MODE`, plus `_COMMAND_NAME` for the envelope's
`command` field), set by `rendering.json_option()` — the one `--json` declaration every `gw work`
command must use instead of hand-writing `typer.Option(False, "--json", ...)` — and reset by
`cli.py`'s root callback before each subcommand's own option parsing runs. `fail()` asserts rather
than defaults when the var was never set, so a command that skips `json_option()` fails loudly in
its own test suite instead of silently never emitting an envelope.

This is scoped to `gw work` only (D-004): `gw help --json`'s own `{"status": "error", ...}` failure
shape (`cli.py`) predates this and is deliberately not converged onto it — `status` collides with
`work_status`/`document_status` elsewhere in the work lane, and reconciling the two shapes is out of
scope for this item.

### Formatting — delegate for `graph`, bespoke everywhere else (D-003)

`code_graph_io.render` ships a generic `render()` dispatcher, a `describe_block()` sectioned-spine
builder, and one `format_<kind>` per graph entity kind, built for exactly the `GraphTarget`/
`GraphResult` shape `graph_works_core.graph.commands` returns. `gw graph`'s four verbs
(`build`/`describe`/`find`/`export`) delegate directly to that: each command resolves a `GraphTarget`,
calls one core function, and routes the returned `output`/`error` strings to stdout/stderr before
exiting `result.exit_code` — no behavior beyond routing (ADR-0013 rule 5).

Every other sub-app (`wiki`/`work`/`config`/`util`) keeps its own `rendering.py` (see
`wiki_cli/rendering.py`, `work_cli/rendering.py`, `config_cli/rendering.py`): their result types
(`LintReport`, `IngestResult`, `ArchiveRun`, decision payloads, etc.) don't fit `describe_block`'s
graph-entity spine. This is a per-command-family judgment call, not a rule to apply mechanically to
new sub-apps — if a future graph-shaped result genuinely fits the spine, delegate; if not, write
bespoke rendering the way `work_cli`/`wiki_cli` already do.

### Typer-tree introspection — shared by `help --json` and `describe-surface`

`introspection.py` holds one walker (`command_to_help_entry()`) used by both `gw help [PATH]
--json` (single node, `cli.py`) and `gw util describe-surface --json` (every node, groups and
leaves, `util_cli/describe.py`) — one payload shape, so the two views cannot drift apart.
`describe_surface()` sorts entries by `path` for byte-stable output, which is what makes
`tests/fixtures/surface.golden.json` a meaningful fixture rather than a snapshot of incidental
ordering.

**The `typer.core` vs `click` gotcha this workspace's resolved `typer` creates:** `typer.main.
get_command()` here returns `typer._click.core.Command` instances — typer's own internal fork,
*not* `click.core.Command`/`Option`/`Argument`/`Group`. `isinstance(param, click.Option)` silently
returns `False` for every param under this version instead of erroring, which is the dangerous
failure mode — it reports empty option/subcommand lists rather than crashing. `introspection.py`
checks `typer.core.TyperOption`/`TyperArgument`/`TyperGroup` instead (typer's own public re-exports
of the classes `get_command()` actually returns), plus one `typing.cast` where a real
`click.Context` is constructed from typer's command object. Reuse this pattern for any new
command-tree introspection; do not copy `isinstance(x, click.*)` checks from older reference code —
they silently no-op here.

### `--trace` is out of scope for this package (D-024)

`graph_works_core.scan.commands` and `graph_works_core.query.commands` already write JSONL traces
unconditionally to `layout.cache_dir / "traces"`. There is no write-side CLI flag anywhere — `gw
util trace <file>` (`util_cli/trace.py`) is a read-side renderer for whatever JSONL file the caller
points it at; it was never wired to a write-side flag and there is nothing to build here.

### The provenance guard (`provenance.py`)

`warn_if_stale_routing()` warns, on stderr only, when the running `gw`'s routing code
(`graph_works_core` / `work-tracker-okf`) was loaded from a different checkout than the one the
current working directory belongs to *and* that other checkout's routing sources genuinely differ
by git content (not just "is a different worktree" — that alone would fire on nearly every
invocation and be filtered out as noise). It only fires at call sites that are actually
routing-sensitive — `gw work next`/`advance`/`orchestrate` (and their root aliases) — not globally
from `cli.py`. Every resolution failure (no source checkout found, git not runnable, etc.) degrades
to silence: a guard that guesses is worse than no guard. Kill switch:
`GRAPH_WORKS_PROVENANCE_GUARD=0`.

### `graph-works-core` is a real dependency — no deferred-import pattern

`graph-works-core` is a real, `py.typed`, `mypy --strict`-clean workspace member and a pinned
dependency (`graph-works-core>=0.4.0,<0.5` in `pyproject.toml`, workspace-sourced via
`[tool.uv.sources]`). `workspace_resolution.py` imports it at module level; `provenance.py` imports
it inside `_source_checkout_root()` because only the provenance guard needs it there — that is an
ownership choice, not a fallback for a package that might not exist. Do not reintroduce a
missing-package fallback or `sys.modules` test-injection pattern for a `graph_works_core` import in
this package; tests patch the real `graph_works_core.workspace.discovery.resolve` via
`monkeypatch.setattr` on the module reference `workspace_resolution.py` holds.

### Verbose logging (`logging_config.py`)

`-v`/`-vv` installs stderr-only logging handlers (INFO / DEBUG on the root logger), idempotently —
stdout stays clean so `gw -v scan ... --json | jq` still works. The fan-out trace logger
(`subagents_io.pool.trace`) gets its own handler with a bare `%(message)s` formatter and
`propagate=False`, so live per-item completion lines during `gw scan`/orchestration stay
byte-identical to what `gw util trace` renders from the JSONL file afterward — don't let a future
formatter change on the root handler leak a level/name prefix into that stream.
