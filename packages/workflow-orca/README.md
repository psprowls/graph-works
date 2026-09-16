# workflow-orca

The Orca `DispatchBackend`. One dependency: `subagents-io`.

`workflow-<backend>` is the naming rule for the layer where vendor and system
coupling is allowed: `-io` is band-1 foundation, `-okf` is OKF-aware band 2,
and this is neither. `workflow-local` is its sibling and drives subprocesses;
this one drives Orca.

## Surface

`OrcaBackend(*, run=…, repo_selector=None)` opens a named,
durable session over an Orca Run — `open_session(name)` binds an existing Run
whose `objective` equals `name`, or creates one. `repo_selector` is required
for any dispatch that creates a worktree (`fork-child` or `create-top-level`),
since both launch top-level in that repository; a planned `parent_path` is
then linked with `orca worktree set`. `OrcaSession` implements the
eight `DispatchSession` methods. Agent, model and reasoning effort come from each
`PlannedDispatch`; there is no session-wide agent choice. Explicit models require
`orchestration.worker-launch-preferences.v1` on `orca status --json`. Model IDs
and effort values are opaque. Effort without a model is refused before creating
a task; null settings are omitted from argv.

Before worker-start, task-create persists a frozen version-1 launch envelope in
`--spec`. The first line is `GW_LAUNCH_V1 ` followed by compact JSON containing
`version`, `dispatch_key`, `agent`, `model`, `reasoning_effort`, and the exact
`placement_argv` array. A newline separates it from the unchanged human prompt.
`workflow_orca._launch` exposes `launch_preferences`, `encode_launch_spec`,
`decode_launch_spec`, and `check_launch_receipt`. The latter takes the envelope
and Orca's `launch` block: both `requested` and `effective` must prove every
explicit choice, mapping `reasoning_effort` to Orca's `effort` field. Null model
or effort makes no claim about the agent's configured defaults.

A fresh session reads untruncated task specs and `worker-show`'s durable
`worker.startOptions.launch`. Missing, malformed, or mismatched evidence appears
as unverified configuration in `WorkerRecord.detail`, alongside task and dispatch
IDs. Lifecycle state remains Orca's actual state. Duplicate dispatch keys are
refused even after failed or ambiguous starts and across coordinator restarts.
The protocol has no retry method: external recovery must use the frozen envelope,
never freshly resolved configuration, and first inspect the existing attempt.

`OrcaCliError.receipt` retains the full JSON response and `.details` preserves
result/error recovery data, including nonzero exits whose JSON says `ok: true`.
Start exceptions add `taskId`, `dispatchKey`, and `launchRequest` to `.details`.
No dispatch ID means task recovery inspection, never an automatic second start.

Orca's accepted `worker_done` owns task settlement. `ack()` verifies successful
completion before acknowledging the delivery and releases the settled worker;
it does not issue `task-update`. A delivery containing an unverified successful
completion remains unacknowledged, even if another event in that delivery is
acknowledged first. `close()` retains unverified successful workers for recovery.
Terminal-free workers use the same orchestration lifecycle and transcripts.
Nudges require a matching actual `worker-show.terminal` object in addition to all
heartbeat/transcript vetoes; an agent handle alone does not prove a terminal.

## Platform

This is the native-Windows auto-drive backend. Orca itself ships a native
Windows build (D-004), and `workflow-local`'s pid-based liveness probe maps
`os.kill(pid, 0)` to `TerminateProcess` on Windows — a refusal it enforces at
construction (see `workflow-local`'s README) rather than something a caller
works around. `OrcaBackend` drives Orca's own process management instead of
signalling pids directly, so it carries no such restriction and runs
unmodified on Windows.

## Fixtures

`tests/fixtures/` is JSON captured verbatim from the live `orca` CLI. Nothing
re-captures it automatically, so a change to Orca's JSON surfaces at the next
live run rather than at `just check`. `tests/fixtures/FIXTURES.md` records the
exact command behind each file. Historical captures remain unchanged. New launch
edge cases use explicitly synthetic receipts in `test_launch_preferences.py`,
`fake_orca_cli.py`, and `SyntheticEvidenceRunner`. Their field vocabulary was
checked against installed Orca 1.4.200 guides and packaged runtime source; they
are not evidence that a live provider launch honored the settings.

Current Orca `check` messages may store `payload` as embedded JSON text. The
adapter normalizes that shape and historical mapping payloads before task
attribution or batch completion checks. Malformed payloads refuse the delivery
for recovery, retaining acknowledgement. Release eligibility uses explicit
verified receipt state, never words in `WorkerRecord.detail`.

The integration tests compare the backend's frozen envelope and receipt
semantics directly with the shipped plugin launch recipe. See the
[configuration guide](../graph-works-core/docs/dispatch-rules.md) for the
upstream shared/local profile contract and explicit cutover.
