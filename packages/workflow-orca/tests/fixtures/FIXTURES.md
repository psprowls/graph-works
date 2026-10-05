# Fixtures

Most JSON was captured verbatim from the live `orca` CLI on 2026-08-14, with ids and
paths left exactly as returned; hand-authored exceptions are named below. Nothing re-captures these automatically — a
change to Orca's JSON surfaces at the next live run, not at `just check`
(the accepted risk in the design spec's §7).

Re-capture with these commands, replacing the ids with live ones:

| File | Command |
|---|---|
| `run_list.json` | `orca orchestration run-list --limit 2 --json` |
| `run_list_page2.json` | `orca orchestration run-list --limit 2 --cursor <nextCursor> --json` |
| `run_create.json` | `orca orchestration run-create --objective auto-drive:probe --json` |
| `task_list.json` | `orca orchestration task-list --run <run> --json` |
| `task_create.json` | `orca orchestration task-create --run <run> --spec probe --task-title k#p --json` |
| `worker_list.json` | `orca orchestration worker-list --run <run> --json` |
| `worker_start.json` | `orca orchestration worker-start --task <task> --agent claude --run <run> --json` |
| `worker_show_live.json` | `orca orchestration worker-show --dispatch <live ctx> --json` |
| `worker_show_settled.json` | `orca orchestration worker-show --dispatch <settled ctx> --json` |
| `worker_read_transcript.json` | `orca orchestration worker-read --dispatch <ctx> --limit 1 --json` |
| `worker_read_empty.json` | as above, against a worker that has not spoken |
| `worker_read_receipt.json` | Hand-authored delivery receipt regression: synthetic ids and content, exact-source metadata, payload clipping, and user/assistant/tool blocks. No live worker delivery is claimed. |
| `check_batch.json` | `orca orchestration check --run <run> --wait --types worker_done,escalation,question,heartbeat --timeout-ms 1000 --json` |
| `check_timeout.json` | as above, with no traffic on the Run |
| `check_heartbeat_only.json` | `orca orchestration check --run <run> --wait --types worker_done,escalation,question --timeout-ms 1000 --json` with only heartbeat traffic (hand-authored) |
| `check_consumer_fenced.json` | the same `check` from a terminal not bound to the Run (hand-authored) |
| `worker_show_created.json` | `orca orchestration worker-show --dispatch <ctx that provisioned a worktree> --json` |
| `worktree_show_renamed.json` | `orca worktree show --worktree id:<repoId>::<path> --json` (note: **not** an `orchestration` subcommand) |
| `repo_list.json` | `orca repo list --json` (hand-authored) |
| `worktree_list.json` | `orca worktree list --repo id:<repoId> --json` (hand-authored, trimmed: the 2026-09-27 live shape is `worktrees[]`, `totalCount`, `truncated`, `hostScope.omittedHostIds`, with `comment` on every row) |
| `worktree_create.json` | `orca worktree create --name <n> --repo id:<repoId> --base-branch <ref> --no-parent --setup skip --comment <marker> --json` (hand-authored from `worktree_show_renamed.json`'s row shape plus `comment`) |
| `terminal_send.json` | `orca terminal send --terminal <term> --text "" --enter --json` (hand-authored) |
| `worker_start_effects.json` | `orca orchestration worker-start --task <task> --agent claude --run <run> --json` (hand-authored from `worker_start.json` with `effects` and launch receipt, omitting `agentTerminalHandle` to exercise the live fallback) |
| `inbox_run_questions.json` | `orca orchestration inbox --terminal run:<run> --limit 1000 --json` (hand-authored, trimmed) |
| `inbox_dispatch_replies.json` | `orca orchestration inbox --terminal dispatch:<ctx> --limit 1000 --json` against a Dispatch whose question was answered (hand-authored, trimmed) |
| `inbox_dispatch_empty.json` | as above, against a Dispatch with no replies (hand-authored) |
| `worker_list_questions.json` | `orca orchestration worker-list --run <run> --json` (hand-authored, trimmed) |

**`check_batch.json`, `check_heartbeat_only.json`, `check_consumer_fenced.json`, `repo_list.json`, `terminal_send.json`, `worker_start_effects.json`, `worktree_list.json`, and `worktree_create.json` are hand-authored, not captured verbatim.** A `check` batch
requires the capturing terminal to be the Run's bound coordinator with live
traffic, which the plan author did not have. It is hand-authored from two
verified sources: the flag vocabulary of `orca orchestration send --help`
(`--task-id`, `--dispatch-id`, `--outcome`, `--files-modified`, `--report-path`,
`--phase`) and the settled-task `result` payload in `task_list.json`, whose keys
(`outcome`, `subject`, `body`, `filesModified`, `reportPath`, `completedAt`)
were captured live. Re-capture it at the first live run and expect field drift.
The heartbeat-only batch has the `check_batch.json` shape with its payload as the JSON string current Orca emits; the fenced envelope uses the `consumer_fenced` code documented in `backend.py` (lines 9–12). The epic's live acceptance run is the re-capture point.

**The four `inbox_*`/`worker_list_questions` fixtures are hand-authored, trimmed from a live 2026-09-27 `inbox --json` read on Orca 1.4.211.** Ids are synthetic; field names, the `run:`/`dispatch:` handle shapes, the JSON-string `payload`, and the reply linkage (a `status` message, subject `Re: Question`, from `run:<id>` to `dispatch:<ctx>`, `thread_id` equal to the question id) are as observed. That linkage is Orca behaviour, not a published contract — if it changes, `test_map.py`'s join tests are where it shows.

`worktree_show_renamed.json` is trimmed rather than verbatim — the live
`worktree` object carries ~30 keys (lineage, linked issue/PR fields for six
forges, UI sort state) that this package never reads. What it preserves
exactly is the pair the fixture exists for: `displayName` holding the
requested `--name` verbatim while `branch` holds Orca's own
`refs/heads/<user>/<slug>` derivation.

## Liveness captures (2026-09-27)

Captured from live dispatch `ctx_259a906bc061` on Orca 1.4.211 by the coordinator, using these exact commands (no new capture in Task 3):

| File | Command |
|---|---|
| `worker_show_live_terminal.json` | `orca orchestration worker-show --dispatch ctx_259a906bc061 --json` |
| `worker_read_latest.json` | `orca orchestration worker-read --dispatch ctx_259a906bc061 --limit 1 --json` |

Sources: `.superpowers/sdd/02-plan/live-show.json` and `live-read.json`. These are live captures with approved redactions, not synthetic fixtures. Camel-case fields, timestamps, identities, paths, cursors, and structural fields are retained; the historical snake-case fixture remains unchanged. JSON is reserialized with two-space indentation and a final newline. Every content edit is listed below; no lifecycle authority/capability token remains in the committed captures.

- `worker_show_live_terminal.json`: `result.terminal.preview` replaced with `"(redacted)"`.
- `worker_read_latest.json`: `result.transcript.messages[0].blocks[0].input` replaced with `"(redacted)"`.

### Newest-first retry ordering (final heartbeat-progress fix)

`worker_list.json` contains the synthetic failed predecessor `ctx_000000000001`
for the captured task `task_5ca8c19ffa5e`. It now follows the captured rows, so
the captured successful retry is selected under the installed Orca 1.4.211
newest-first contract. This is a synthetic ordering correction, not a fresh
live capture; all row fields are unchanged. The unedited `test_workers.py`
retry test still pins the successful handle, although its historical name
says "last". Focused synthetic page/retry tests pin first-attempt selection
explicitly, including across page boundaries.
