# Orca lifecycle receipt evidence

These fixtures are verbatim Orca 1.4.211 JSON receipts from 2026-09-27 UTC.
No receipt needed capability-token redaction. Private raw evidence and operator
notes are in `/tmp/gw-attend-live-20260927/`; the controller's provenance
report is `feature-attend-session-lifecycle/references/03-live-probe.md` in
the Graph Works workspace. Operator notes record physical actions; JSON
receipts establish Orca's result. Fixture names describe scenarios, not extra
fields in their payloads.

## Original Task 3 cleanup receipts

The original three fixtures are coordinator-supplied receipts from a real
attend-cards worker, Task `task_1cbc6a9fc841`, Dispatch `ctx_0d2527d19812`,
Run `run_0f2a6a55c715`. They are separate from the scripted matrix.

| File | Observed result |
| --- | --- |
| `release-released.json` | `released`, `closed_agent_terminal` |
| `release-already-released.json` | repeated release: `already_released`, `none` |
| `stop-already-settled.json` | stop after settlement/release: `succeeded`, `alreadySettled: true`, `none` |

## Controlled live matrix

Each row used a disposable worker and its own Dispatch. The fixtures below
are byte-for-byte copies of the named raw release or stop receipts.

| Scenario and Dispatch | Observed result and durable fixture | Limit / cleanup |
| --- | --- | --- |
| No pane interaction, `ctx_77516d351119` | succeeded; `release-no-interaction.json`: `released`, `closed_agent_terminal`; `release-already-released-no-interaction.json`: repeated `already_released` | Baseline release, separate from Task 3 |
| CLI tab switch **then** human-confirmed physical pane click, no typing, `ctx_6b324994456d` | succeeded; `release-retained-focus-sequence.json`: `retained`, `user_takeover` | Combined focus sequence only; neither action was isolated. Exact terminal close reported `ptyKilled: true`, then readback exited |
| Human-confirmed Space without Enter after readiness, `ctx_f05ec2123dcd` | succeeded; `release-retained-user-takeover.json`: `retained`, `user_takeover` | Exact terminal close reported `ptyKilled: true` and readback exited |
| Attempted key before readiness, `ctx_3c175371c417` | Timing **unverified**; the human could not confirm the key preceded readiness | Script aborted at timing gate. Worker settled solely for cleanup; exact terminal closed. Its retained release is **not** proof of early takeover |
| Clean active stop, `ctx_4ff1c34d57d0` | `stop-stopped.json`: `stopped`, `closed_agent_terminal`, `close.ptyKilled: true`; `stop-already-settled-after-stop.json`: repeat `stopped`, `alreadySettled: true` | Script stopped for manual recovery. `worker-list` proved exited and directed `worker-release`, which returned `released` |
| Human-confirmed Space after readiness, then active stop, `ctx_55c04ebb067c` | `stop-unknown-user-owned.json`: `stop_unknown`, `processAction: none` | Controller explicitly abandoned (`abandoned`, `processAction: none`), checked exact worker identity and handle, closed that terminal (`ptyKilled: true`), then read back exited. `stop_unknown` is **not** `stopped` |
| Controlled restart without pane input, `ctx_7f0a922a9f81` | Resource remained owned; worker resumed and succeeded; `release-released-after-restart.json`: `released`, `closed_agent_terminal` | External read-only observer verified app PID/runtime change; human confirmed no pane input before, during, or after restart |

The first attempt to launch the restart case from an external shell failed with
`no_active_sender_terminal` before creating a worker. The successful trial ran
the probe script in a dedicated bound Orca shell and a separate read-only
observer outside Orca. After restart, the controller re-listed surviving
terminal handles, resumed its ask using the saved message ID, and continued the
surviving script. This is not evidence of an end-to-end external script run.

Context-only release, `release_pending`, and `release_unknown` were not
observed. Classifier tests keep synthetic contract cases for these outcomes.
The verified before-readiness trigger is also outstanding. No JSON fixture is
fabricated for any missing case.

## Running a future manual probe

Use only with separate authorization and a disposable local worktree. Start
`takeover-probe.sh` from a **dedicated bound Orca terminal**: `run-create`
requires an active sender terminal and binds it to the new Run. Use a new
private output directory for each case. The script needs `orca`, `python3`,
interactive stdin and stdout, and a configuration that launches terminal
workers. For example:

```bash
bash plugins/gw/skills/auto-drive/references/probes/takeover-probe.sh \
  /absolute/disposable/worktree /absolute/new/private/probe-no-input \
  no-interaction codex
```

The script does not focus panes or restart Orca. For a restart case, save the
Run, Dispatch, worker and controller terminal identities and receipts before
quitting Orca. Run a **read-only** external observer across the restart; it may
inspect app/runtime identity and the existing worker, but cannot run the
unbound probe script. Do not assume the controller script or worker survives.
After restart, re-list terminal handles and `worker-show`; continue only after
verifying live identity and control. If the script died, preserve receipts for
manual recovery. Never restart the whole case blindly.

The worker writes its exact Dispatch ID to `ready` and waits for the matching
`settle` barrier. The script checks authoritative readiness before later
triggers. Stop cases leave the barrier closed. Early input starts launch
asynchronously; the operator must confirm the key preceded **agent readiness**,
not just the barrier. Uncertain timing is invalid evidence. Settlement waits
are bounded; timeouts preserve the worker for inspection. The script never
automatically abandons a user-owned or unknown worker, closes a terminal,
retries, or removes the worktree. Unknown or retained results end at a manual
cleanup barrier.

Raw stdout is stored by operation with separate stderr and exit-code files.
Barriers and operator notes are not Orca receipts. Inspect raw results before
copying fixtures, preserve bytes except capability-token redaction, and account
for residual resources before another case. The helper is not a `just check`
target.
