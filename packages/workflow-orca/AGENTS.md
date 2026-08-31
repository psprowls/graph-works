# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

The `orca` implementation of `subagents_io.backend.DispatchBackend` — the
protocol a coordinator drives to launch, enumerate, wait on, and resume agent
workers, defined in `packages/subagents-io/src/subagents_io/backend.py`. Its
sibling `workflow-local` drives bare subprocesses; this package drives
**Orca**, an external orchestration system reached exclusively through the
`orca` CLI binary via `subprocess` — there is no Python SDK, no HTTP client,
no file-based IPC. Every `orca orchestration …` and `orca terminal …`
invocation this package builds is enumerated in `backend.py`'s docstring and
`_cli.py`; that argv list *is* the vendor contract, not any façade around it.

Read `packages/subagents-io/src/subagents_io/backend.py` and `dispatch.py`
before touching this package — the `DispatchSession`/`DispatchBackend`
Protocols, `WorkerRecord`, the four `WorkerEvent` kinds, and `PlannedDispatch`
are all defined there and are not re-documented here.

## Commands

```bash
# types (strict), scoped to this package
uv run --package workflow-orca mypy --strict packages/workflow-orca/src

# full suite
uv run --package workflow-orca pytest packages/workflow-orca/tests

# gated coverage (95% floor), scoped
uv run --package workflow-orca pytest packages/workflow-orca/tests \
  --cov=workflow_orca --cov-branch --cov-report=term-missing --cov-fail-under=95

# subsets
uv run --package workflow-orca pytest packages/workflow-orca/tests/test_nudge.py
uv run --package workflow-orca pytest packages/workflow-orca/tests/test_conformance_orca.py
uv run --package workflow-orca pytest -k "nudge or ack"
```

`lint`, `contracts` (import-linter), and `sync` are workspace-wide (`just
lint`, `just contracts`, `just sync` from the repo root) — nothing about them
is package-specific. `uv run --package workflow-orca …` is required, not
optional: a bare `uv run pytest` runs the *root* `testpaths` (okf-io/okf-ext),
never this package's tests.

`pyproject.toml` sets `pythonpath = ["tests", "../workflow-local/tests"]` —
`test_conformance_orca.py` imports `workflow-local`'s `conformance.py` and
`test_conformance.py` as plain sibling modules rather than copying them, so a
scenario added to the sibling suite (e.g. a new resume case) runs here
unedited. Don't duplicate a scenario that already lives in
`workflow-local/tests/`; extend it there.

## Architecture

### What an "Orca Run" is here

One `OrcaBackend.open_session(name)` binds to exactly one Orca **Run**, found
by paging `run-list` for a row whose `objective` string equals `name`
(`OrcaBackend._find_run`), or created fresh if none matches. The Run *is* the
durable session — there is no local state file. This is what makes a
coordinator restart a no-op: calling `open_session` again with the same name
re-binds the same Run, and `OrcaSession.workers()` reconstructs every worker's
state by re-querying `task-list` / `worker-list`, never from anything cached
in the process.

Within a Run, one **task** (`task-create`) maps to one dispatch key
(`dispatch.key`, stored as the task's `--task-title` — literally used as the
lookup field, not just a label), and one **worker** (`worker-start`) is one
attempt at that task. `launch()` refuses to create a second task for a key
already present in `task-list` — this is the mechanism behind
`DispatchSession.launch()`'s documented "refuse a duplicate key" contract,
enforced here via a live Orca query rather than a local set.

### launch / enumerate / wait / resume, concretely

- **launch**: `task-create --spec <prompt> --task-title <key>` then
  `worker-start --task <id> --agent <agent> [worktree flags] [--model
  --effort]`. `--effort` is only ever sent alongside `--model` (Orca's CLI
  rejects it alone) — `_worktree_flags` builds the four Orca worktree modes
  (`reuse`/`main` → `path:<path>`, `fork-child` → `new-child`,
  `create-top-level` → `new-top-level` + `--repo`), which is why
  `OrcaBackend.provisions_worktrees = True`: Orca itself creates the
  worktree, this backend does not.

  **Orca's `--name` is a worktree display name, not a branch name.** There is
  no branch flag anywhere on the CLI; Orca derives the branch as
  `<host git user slug>/slugify(name)`, unconditionally — not on collision,
  not only for slash-containing names. So a `fork-child` or `create-top-level`
  launch costs **two more calls today** (`worker-show --dispatch <ctx>` for the
  worker's `worktree_id`, then the top-level `orca worktree show --worktree
  id:<id>`) — one, if the `worker-start` payload ever carries the id itself —
  to record the real `worktree_path` / `worktree_branch` on the
  returned `WorkerRecord`. `reuse`/`main` pay neither call: they report the
  path they were handed and leave the branch `None`.

  This is **read-back only** — `_resolve_worktree` never compares the result
  against the plan, never warns, and degrades every failure to `None`,
  because `launch()` has already started a real worker by then. Comparing
  actual against planned is a caller's business and is tracked separately.
- **enumerate** (`workers()`): costs `2 + L` CLI calls (`task-list`,
  `worker-list`, plus one `worker-show` per live worker) — read
  `WorkerRecord.last_heartbeat_at`'s and `workers()`'s docstrings in
  `backend.py` before "optimizing" this away; the per-live-worker call is
  deliberate because a nudge decision (below) treats "has ever heartbeat" as
  a hard veto and a lazily-`None` heartbeat would silently defeat it.
- **wait**: one blocking `check --wait --types
  worker_done,escalation,question,heartbeat --timeout-ms <ms>` call,
  translated by the pure `_map.py` (`event_from_message`) into the protocol's
  closed `WorkerEvent` union. Never raises on quiet — timeout and an empty
  batch are the same `[]` to the caller.
- **resume after a crash**: nothing to do beyond calling `open_session` again
  with the same name. A settled task's `result` field carries the entire
  `worker_done` payload (`parse_task_result` in `_map.py`), so a session
  reconstructs completely from `task-list` with no live process anywhere —
  this is the load-bearing design point, not an incidental convenience.

### Three things folded into `ack()` / `close()` / `wait()`, not exported

The `DispatchSession` Protocol has eight methods and nothing else — no
Orca-specific ninth method exists, deliberately, because a coordinator that
needed one could no longer swap backends. Three pieces of Orca-specific
housekeeping are folded into existing protocol calls instead:

1. **Terminal release** — `ack()` calls `worker-release` after a
   `WorkerDone`, and `close()` sweeps any handle enumeration proved settled
   but never released (`_unreleased`, re-earned on every `workers()` call
   rather than persisted — a worker that settled across a coordinator
   restart never went through `launch()`'s own bookkeeping).
2. **Ledger settlement** — `ack()` also calls `task-update --status
   completed|failed` so a settled key stops looking live to the next
   `workers()` call.
3. **The unsent-prompt nudge** (`_nudge_sweep`, only run when `wait()`
   returns nothing) — `worker-start` sometimes leaves a prompt typed but
   never submitted, so the worker reports `running` forever and `wait()`
   would block forever with no exit. The fix is a bare Enter
   (`terminal send --text "" --enter`) into the worker's agent terminal, but
   only after four ordered vetoes checked in `_nudge_sweep`: any heartbeat
   ever (hard veto — an unsubmitted worker cannot have one), an unreadable
   `worker-read`, a `"source": "terminal"` degraded read, or a non-empty
   transcript (a dialog on screen is itself transcript activity). At most
   one nudge per worker per session, tracked in `_nudged` — deliberately
   **not** persisted, so a genuinely-hung worker stays nudgeable across the
   coordinator restart that is the loop's own recovery path. `test_nudge.py`
   is the map of these five outcomes if you need to change the ordering.

### Other gotchas that need cross-file reading

- `run-use --id <id>` is called only on the *reuse* path in `open_session`;
  `run-create` binds implicitly. Every subsequent call in the session passes
  `--run <run_id>` explicitly, but read-only calls (`task-list`,
  `worker-list`) work unscoped — only `check --run <id>` actually enforces
  binding (`consumer_fenced` otherwise), which is why re-binding isn't done
  defensively everywhere.
- `orca terminal send` (`_call_top_level`) is not an `orchestration`
  subcommand — different argv prefix (`["orca", ...]` vs
  `["orca", "orchestration", ...]`) — see `_call` vs `_call_unscoped` vs
  `_call_top_level` in `backend.py` and don't collapse them.
- `unwrap()` in `_cli.py` is the single place an `{"id","ok","result"}`
  envelope is opened; a non-zero exit, `ok: false`, and non-JSON stdout all
  become one `OrcaCliError`. `check --wait` interleaves a JSON keepalive on
  **stderr** every 15s while the real payload is on stdout — `OrcaResult`
  keeps the two streams apart for exactly this reason; never merge them in a
  custom runner.
- `tests/fixtures/*.json` is captured verbatim from a live `orca` CLI run
  (see `FIXTURES.md` for the exact commands and the one exception,
  `check_batch.json`, which is hand-authored from verified flag/field
  vocabulary rather than a live capture). Nothing re-captures these
  automatically — a change to Orca's JSON surfaces at the next live run, not
  at `just check`. If Orca's CLI output shape changes, these fixtures go
  stale silently.
- `test_boundaries.py` enforces by AST walk that this package imports at most
  one workspace package (`subagents_io`) and no third party at all — `orca`
  is reached only via `subprocess` inside `_cli.py`. Don't add a dependency
  here without expecting that test to fail on purpose.
