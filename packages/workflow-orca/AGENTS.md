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
# types (strict), scoped to this package -- `just types` runs both platform arms
uv run --package workflow-orca mypy --strict --platform linux packages/workflow-orca/src
uv run --package workflow-orca mypy --strict --platform win32 packages/workflow-orca/src

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

- **launch**: `task-create --spec <frozen-envelope + prompt> --task-title <key>` then
  `worker-start --task <id> --agent <agent> [worktree flags] [--model
  --effort]`. `--effort` is only ever sent alongside `--model` (Orca's CLI
  rejects it alone); an effort-only dispatch is refused before task-create.
  Agent/model/effort are per-dispatch, never session defaults. `_launch.py`
  implements pure argv/envelope/receipt helpers; see README for the exact shared
  `GW_LAUNCH_V1 ` schema and `reasoning_effort` → receipt `effort` mapping.
  `_worktree_flags` always returns `--worktree path:<dispatch.worktree.path>`: the worker is launched into the existing, already-placed checkout, and this backend creates, forks, links and names nothing (no `--repo`, no `--name`, no `orca worktree set`). Placement is computed and realized before the dispatch is built, by the placement engine in `gw-pipeline` driven from `gw-orchestrate`.

  `launch` pays no Orca call to learn where the worker runs. `_resolve_worktree` returns the planned path and leaves the branch `None` rather than trusting the plan, and `WorkerRecord.worktree_path` / `worktree_branch` are read-back fields only.

  The read-back never compares the result against the plan and never raises, because `launch()` has already started a real worker by then.

### Completion, release, and terminal-free workers

`worker_done` is Orca's task-settlement authority. `ack()` does not send
`task-update`; it verifies the full frozen task envelope against durable
`worker.startOptions.launch` before acknowledging successful completion.
Unverified success keeps its delivery and resource for recovery. Because
acknowledgements apply to entire deliveries, every successful completion in a
batch is checked even when the caller acknowledges its heartbeat first.
Failures still acknowledge and release according to actual settlement.
`close()` releases settled workers except unverified successes; unknown state
never proves settlement. `_verified_launches` is rebuilt from durable receipts
on each enumeration; diagnostic text must never decide release eligibility.

`wait()` normalizes current JSON-string and historical mapping payloads through
`_map.normalize_message` before attribution or batch proof. A malformed payload
raises `BackendError` and leaves the delivery for recovery; dropping it could
let a sibling heartbeat acknowledge an unchecked successful completion.

`workers()` always fetches full task specs (no `--brief`) and worker evidence,
including settled workers. Missing or malformed envelopes and missing/mismatched
receipts report unverified configuration with task/dispatch IDs in `detail`.
Actual lifecycle state is kept separately. The task title remains the durable
duplicate-key guard across restarts; there is no new band-1 retry API.

Nudging still happens only during an empty `wait()`, at most once per worker
per session, after all existing heartbeat/read/transcript vetoes. It additionally
requires an actual `worker-show.terminal` whose handle matches worker-list's
`agentTerminalHandle`. The cache is cleared on every enumeration: a vanished
terminal cannot leave stale proof. Structured workers may have an agent handle
without a terminal; their lifecycle and reads use orchestration only.

### Liveness rows

`OrcaSession.liveness(now=…)` returns one `LivenessRow` per live (`pending` /
`running`) dispatch with a handle: facts, never a verdict. It is available for
coordinators after a timeout; wait-verb integration is a separate task, and the
landed wait verb never nudges. Liveness itself neither nudges nor marks nudged.
It reuses `workers()`' per-handle worker-show cache (`_shown` /
`_show_failures`, refreshed with `_agent_terminals`), then adds one
`worker-read --limit 1` per live dispatch. Transcript age comes only from a
`source == "transcript"` read; terminal fallback is a note, never a stand-in.
When terminal worktree path is unavailable, it falls back to `worktree show`
using `worker.worktreeId`. Invalid path values are unknown, not stringified.
`progress` comes from `_progress.find_progress` over
`<worktree>/.superpowers/sdd/*/progress.md`, ignoring ledgers older than the
dispatch's `createdAt`. Worker read failures produce null fields plus notes;
only failed task/worker enumeration raises `OrcaCliError`. Malformed dispatch,
terminal and transcript shapes are treated as unavailable. `now` is required
and must be timezone-aware (otherwise `ValueError`, before any CLI call); this
method never reads the clock. `LivenessRow`, `SddProgress` and `liveness_data`
are public package exports; the protocol remains unchanged.

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
  become one `OrcaCliError`. Nonzero exits fail even with `ok: true`; the
  full JSON stays in `.receipt` and recovery data in `.details`. Start errors
  add the reserved task ID, dispatch key, and frozen request to `.details`.
  `check --wait` interleaves a JSON keepalive on
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
- Synthetic launch receipts in the new tests are labeled as such; see README.
  Installed 1.4.200 uses `dispatch.lastHeartbeatAt` and `worker.worktreeId`
  on worker-show; the historical captures use snake case. Both are read.
- `test_boundaries.py` enforces by AST walk that this package imports at most
  one workspace package (`subagents_io`) and no third party at all — `orca`
  is reached only via `subprocess` inside `_cli.py`. Don't add a dependency
  here without expecting that test to fail on purpose.

`gw work dispatch` launches every stage, in process through this package's `OrcaCliPort`, into the worktree the placement engine already made. The `OrcaPort` surface reads (`worktree_show`, `worktree_list`, `repo_list`) and sets status, and never creates a worktree; names and parent links are `OrcaPlacementNotifier`'s, through `worktree_set`.

`port.py` is the structural `OrcaPort` implementation for core's coordinator
logic. Its `check_wait` builds one blocking `check --wait` call with optional
`--ack`, `check_ack` acknowledges a delivery separately, and `run_use` rebinds
the Run's consumer. The port adds `OrcaTask.result`,
`OrcaWorker.release_state` / `terminal`, and message `payload_raw` to its
normalized rows so core can inspect duplicate completions without losing
malformed payload evidence. Unlike `OrcaSession`, the port rebinds only when
the caller's core logic asks it to.

`placement.py` adds `OrcaPlacementNotifier`, a best-effort adapter for plain-git placement events through a local read-only structural protocol. It checks the repository's worktree base once per notifier instance and repository, waits for automatic discovery, then sets the display name and parent link. Missing parents still receive a display name; CLI and OS failures become notes and never fail placement. It imports no application package.
