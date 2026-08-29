# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

Band 1 of the graph-works layering (see the root `AGENTS.MD`'s workspace
description). `subagents-io` is a bounded async fan-out pool plus the generic
run path over it: role resolution, model routing, a runner that streams one
item or fans out a worklist, and the value types/Protocols a worker-dispatch
coordinator is built from. It is deliberately inert: **no process environment
reads, no workspace discovery, no filesystem or provider names.** The trace
directory is a constructor argument, the price table is an injected callable,
the graph reader and chat model are both caller-supplied. This is held
mechanically, not just by convention — see "Boundaries are tested, not just
documented" below.

## Commands

Run from the repo root (`uv run --package` resolves this package's own
dependency closure — a plain `uv run` from here will not see `langchain-core`
correctly wired against the workspace lock):

```bash
uv run --package subagents-io pytest packages/subagents-io/tests
uv run --package subagents-io mypy --strict packages/subagents-io/src
uv run --package subagents-io pytest packages/subagents-io/tests --cov=subagents_io --cov-branch --cov-report=term-missing --cov-fail-under=95
```

These are exactly the root `justfile`'s `test`, `types`, and `cov` recipe
lines for this package — see `/Users/pat/Personal/graph-works/justfile`.

Subset examples:

```bash
uv run --package subagents-io pytest packages/subagents-io/tests/test_pool.py
uv run --package subagents-io pytest packages/subagents-io/tests/test_boundaries.py
uv run --package subagents-io pytest -k "trace or roles" packages/subagents-io/tests
```

`asyncio_mode = "auto"` is set in this package's own `pyproject.toml`, not
inherited from the root — `uv run --package` resolves pytest's rootdir to this
file, so the whole `test_pool*.py` suite (all `async def`) would silently
collect as sync tests without it.

Coverage is gated at 95% for this package, same as most band-1 packages.

## Architecture

### The bounded async fan-out pool (`pool.py`)

`SubagentPool.run_all(items, task, role, *, model_id, max_concurrency, ...)`
dispatches every item through an `asyncio.Semaphore` sized by
`max_concurrency`. The semaphore is created **inside** `run_all`, not
`__init__` — building it in a sync context binds it to whatever event loop
happens to be current at construction time, which breaks under pytest-asyncio
fixtures that spin their own loop per test.

`task` may take `(item)` or `(item, config)`; arity is detected once per
fan-out call via `inspect.signature`, not per item — a hot-path optimization.
The two-arg form receives a `RunnableConfig`-shaped plain dict
(`{"recursion_limit": ...}`) built without importing `langchain_core` at
runtime for that one dict — `RunnableConfig` is `TYPE_CHECKING`-only there.

### Per-item failure isolation

`asyncio.gather(*, return_exceptions=True)` is load-bearing, not decorative:
without it, the first item's exception cancels every sibling in flight. A
raised exception inside `task` is caught, traced with `status="error"`, and
returned as a `PerItemError(item, exception)` — it never becomes a raised
exception out of `run_all`. `FanOutResult` splits `successes` from `errors`
after the gather. `asyncio.CancelledError` is the one exception that is
**not** swallowed this way: a per-item cancel is traced with
`status="cancelled"` and then re-raised, and an outer cancel during the gather
itself writes one `batch_cancelled` terminal record (discriminated by an
`event` key, not `status`) before re-raising. Getting this backwards — folding
`CancelledError` into `PerItemError` — would silently absorb a real shutdown
signal.

### Role-bound model dispatch

A "role" here is a name (e.g. `"librarian"`) that resolves to a `RoleSpec`
(`model_id`, `backend`, `region`, `max_tokens`, `max_concurrency`) via
`resolve_role_spec(role, packaged, override)` in `roles.py` — a field-by-field
merge of a packaged entry and a workspace override, both handed in by the
caller. This package never opens a role catalog or a workspace manifest; it
only merges mappings it's given. `RoleBinding` pairs a resolved `RoleSpec`
with a caller-supplied `make_llm` **factory** (not a constructed model) —
`run_all`/`run_single` call it once per item, so whether to share one client
across items is the caller's decision made by closing over it, never this
package's.

`routing.py` is a separate, independent resolution: given a rules block and a
dict of dispatch attributes, `resolve_model` picks which model runs a
`PlannedDispatch` (first-match-wins over `overrides`, then a `models` tier
keyed by `default_key`, then `None` meaning "inherit the session model"). The
match dimensions (`phase`, `kind`, `effort`, ...) are entirely the caller's —
this module holds no work-item vocabulary. Do not confuse role resolution
(which model a *role* runs) with model routing (which model a *dispatch*
runs); they are unrelated code paths that happen to share the "resolve from a
caller-supplied mapping, never import a source of truth" idiom.

### The JSONL trace record

Every `run_all` call writes one file, `{epoch}_{8-hex}.jsonl`, into the
`trace_dir` passed at `SubagentPool` construction (epoch prefix for
chronological ordering, hex suffix for same-second uniqueness). Every record
carries `schema_version: 1`. `write_trace_record` (in `trace.py`, not
`pool.py`) is the single writer both the pool and any pool-free caller use, so
records never drift in shape; `render_trace_record` is the single line
formatter shared by live logging and any post-hoc viewer.

Two loggers are involved and they are **not interchangeable**:
- the module logger (`__name__`, i.e. `subagents_io.pool`) gets per-item
  *start* lines at `DEBUG`;
- a dedicated logger exported as `TRACE_LOGGER_NAME`
  (`"subagents_io.pool.trace"`, defined in `trace.py`) gets per-item
  *completion* lines at `INFO`, rendered through `render_trace_record`.

The pool always logs both; whether anything shows depends entirely on what
handler the caller attaches. `TRACE_LOGGER_NAME` and the module-logger name
are derived independently on purpose — `tests/test_pool_logging.py` is what
keeps them from drifting apart, not shared code.

A trace write catching `OSError` and logging a `WARNING` instead of raising
is a deliberate invariant repeated in three places (`write_trace_record`,
`_write_batch_terminal`): a trace failure must never mask a successful task
result.

Reading a trace back is a separate, tolerant path: `read_trace_records`
skips blank lines, drops malformed lines with a warning (never raises on bad
content — only a missing *file* propagates `OSError`), and de-dupes
version warnings once per file. `aggregate_trace`/`collapse_runs` roll records
up per `(role, model_id)`; `is_groupable` is the rule for excluding records
that carry an `event` or `kind` key (batch-terminal records, or anything a
future producer discriminates that way) from those rollups, since such
records have no `role` to bucket under.

### Cost accounting is opt-in, and silently so

`price_lookup: Callable[[str, Mapping[str, int]], float] | None` is
keyword-only on both `SubagentPool.__init__` and `write_trace_record`, and
defaults to `None`. **Omitting it makes `cost_usd` null in every record,
with no error and no warning** — this is the one sharp edge in the package.
When supplied, cost is computed from `(model_id, {"input": tin, "output":
tout})`; a lookup that raises `KeyError` (the shape `models_io`'s
`UnknownModelError` uses, though this package never imports or names that
type) also yields a null `cost_usd` rather than failing the run. `tokens_in`/
`tokens_out` come from `response.usage_metadata`, which is `None`-guarded
throughout — a throttled or content-filtered response has no usage metadata,
and accessing it unguarded is a documented historical failure mode
(`pool.py`'s docstring cites it).

Cost accounting is why `subagents-io` has no dependency on any pricing
package: the price table is always the caller's to inject, never this
package's to know.

### The `DispatchBackend` seam (`backend.py` + `dispatch.py`)

This is the main extension point and the one future work will most often
touch from the outside. `subagents-io` defines the seam; it ships **zero
implementations** — `workflow-local` and `workflow-orca` (sibling packages)
each implement it, because vendor/system coupling (subprocess management,
worktree provisioning, whatever transport a real backend needs) is exactly
what band 1 is not allowed to carry.

Two halves:

- `dispatch.py` is what a **planner** hands a runner: `PlannedDispatch` (a
  frozen, method-free dataclass — `key`, `slug`, `phase`, `kind`, `effort`,
  `skill`, `mode`, `model`, `reasoning_effort`, `worktree`, `merge_target`,
  `prompt`) and its nested `WorktreeAction`. The field names spell work-item
  concepts on purpose, but this package only stores those strings — it never
  decides what a `phase` or `kind` *means*. Two closed vocabularies live here:
  `WORKTREE_ACTIONS` (`"reuse" | "fork-child" | "create-top-level" | "main"`)
  and `DISPATCH_MODES` (`"autonomous" | "attend" | "relay"`). Note
  `WorktreeAction.action == "main"` still carries a concrete `path`/`branch`
  like `"reuse"` does, because a worker running in the shared main checkout
  cannot infer its own provenance from git (`--git-dir` and
  `--git-common-dir` agree there, so every "am I a worktree" detector answers
  no) — a planner emitting `"main"` owes the worker its path/branch
  explicitly rather than leaving it to self-discovery.

- `backend.py` is what a **backend** *is*: two `runtime_checkable` Protocols,
  `DispatchBackend` (`name`, `supported_modes: frozenset[str]`,
  `provisions_worktrees: bool`, `open_session(name) -> DispatchSession`) and
  `DispatchSession` (`launch`, `workers`, `describe`, `wait`, `ack`, `reply`,
  `stop`, `close`) — both sync, deliberately, because every real backend's
  actual wait is one multiplexed blocking call and async would buy no
  concurrency that matters here. Plus a discriminated event union
  (`WorkerDone | WorkerQuestion | Escalation | Heartbeat`, each a frozen
  dataclass with its own `kind: Literal[...]` so `match ev.kind` is
  exhaustive under `mypy --strict`) and the closed vocabularies
  `WORKER_STATES` and `EVENT_KINDS`.

Invariants a backend implementation is expected to uphold, encoded here as
contract, not code:

- `open_session(name)` **binds-or-creates** by name — a coordinator restart
  must be a no-op against an already-running session, not a second one.
- `DispatchSession.launch()` must **refuse** a `key` it already launched in
  this session (raising `BackendError`) rather than answering from cache —
  silently returning the cached record would make a re-dispatch after a
  failure read as a success. This is the one behavior the README says is
  "proven identically across implementations by `workflow-local`'s shared
  conformance suite" — i.e. `workflow-local` owns a conformance test any new
  backend should run against, not this package.
- `DispatchSession.workers()` returns **every** worker ever launched in the
  session, live or settled — a dedupe check needs to see settled keys too.
- `DispatchSession.wait(timeout_s=...)` returns `[]` on timeout, never
  raises, so a coordinator's outer loop stays a plain `while`.
- `ack(event)` takes the event, not a bare id, so a backend with no
  at-least-once delivery concept can no-op on `event.delivery_id is None`
  without the caller branching on backend type.
- `"unknown"` in `WORKER_STATES` is not a synonym for `"failed"` — it is what
  a backend reports when it holds a record but cannot presently corroborate
  the process behind it (the resume case: a recorded pid no longer answers).
  Collapsing it into `"failed"` makes a recoverable worker unrecoverable.
- `provisions_worktrees` is `False` on every backend this package ships (i.e.
  none — this is documentation for implementers). It exists so a backend that
  obtains its **own** worktrees (Orca's `worker-start --worktree new-child`,
  per `workflow-orca`) can declare that capability on the shared Protocol
  instead of growing a new method — a coordinator checks the flag before
  planning, rather than discovering the gap when a `PlannedDispatch` that
  worked against one backend raises `WorktreeNotProvisioned` against another.

Do not add an implementation of `DispatchBackend`/`DispatchSession` inside
this package. If you find yourself importing `subprocess`, a worktree
library, or anything Orca- or local-CLI-specific here, that code belongs in
`workflow-local` or `workflow-orca` instead.

### The generic run path (`adapters.py`, `runner.py`)

`Adapter[ReaderT]` and `LoopAdapter[ReaderT]` are the two shapes a concrete
per-role behavior implements: `Adapter.prepare` returns a `Prepared` (real
prompt + optional parser) for one item and `Adapter.items` returns the real
worklist for `--all`-style fan-out; `LoopAdapter.run` is for adapters that are
themselves full agentic loops (no per-call token footer, since usage spans
many model calls). `RunContext[ReaderT]` is generic over its graph reader
rather than a structural Protocol, bounded only by `Closeable` — the
alternative (a narrow Protocol mirroring the reader's return types) would be
a shadow model of the graph living in the one package defined by not knowing
about graphs. Python 3.12 has no type-parameter defaults and
`mypy --strict` implies `disallow_any_generics`, so a bare `RunContext` is
always a type error — every annotation must be parameterized, even
`RunContext[FakeReader]` in this package's own tests. `open_reader=None`
builds a context for an adapter that never queries the graph;
`ctx.graph_reader()` on such a context raises `NoGraphReader` (naming the
keyword to pass) rather than an `AttributeError` on `None`.

`runner.py`'s `run_single`/`run_all`/`run_loop` are three entry points that
all take their model and spec from a `RoleBinding`, never construct one.
`stream_and_parse` is the one place this package streams a real
`BaseChatModel` (`llm.astream(...)`, constructing `SystemMessage`/
`HumanMessage` from `langchain_core.messages`) and it never raises on a
parser failure — a bad parse lands in `RunOutcome.parse_error`, mirroring the
content-path tolerance pattern used elsewhere in this workspace (see the root
`AGENTS.MD` on okf-io's "nothing on the content path raises," which this
mirrors independently).

### Boundaries are tested, not just documented

`tests/test_boundaries.py` walks `src/subagents_io` with `ast` and asserts,
per module, with **zero exemptions**:

1. no module imports `os` (the trace dir is a constructor arg; nothing here
   reads an environment variable);
2. no module imports another workspace package, including `models_io` —
   siblings are derived from the filesystem (`packages/*/src/*`), not
   hardcoded, so a package added later is covered automatically;
3. `langchain_core` is the **only** non-stdlib root any module imports, at
   runtime or under `TYPE_CHECKING` — this is an allowlist subtracted from
   `sys.stdlib_module_names`, not a watchlist, so a brand-new third-party
   import fails here before it ever reaches `pyproject.toml`;
4. the module list (`ALL_MODULES` in that test file) is exactly the files
   under `src/subagents_io` — adding a module without listing it there fails
   loudly.

The root `[tool.importlinter]` config runs the workspace half of the same
rule (an `independence` contract over all five band-1 packages) — it catches
an edge introduced from the *other* side (a sibling importing
`subagents_io` in a forbidden direction) that this package's own AST walk,
which only inspects its own imports, cannot see. Keep both: adding a new
band-1 package is automatically covered by the AST walk here but needs a
manual addition to the import-linter contract's `modules` list.

If you're about to add a second runtime dependency, or an import of
`models-io` "just for `cost_for_usage`," or an `os.environ` read for
convenience — stop. `test_boundaries.py` will fail, and that is the point:
the whole value of "cost is injected, not imported" collapses the moment
this package imports a price table itself.
