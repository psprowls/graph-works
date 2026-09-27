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

**`check_batch.json`, `check_heartbeat_only.json`, `check_consumer_fenced.json`, `repo_list.json`, `terminal_send.json`, `worker_start_effects.json`, `worktree_list.json`, and `worktree_create.json` are hand-authored, not captured verbatim.** A `check` batch
requires the capturing terminal to be the Run's bound coordinator with live
traffic, which the plan author did not have. It is hand-authored from two
verified sources: the flag vocabulary of `orca orchestration send --help`
(`--task-id`, `--dispatch-id`, `--outcome`, `--files-modified`, `--report-path`,
`--phase`) and the settled-task `result` payload in `task_list.json`, whose keys
(`outcome`, `subject`, `body`, `filesModified`, `reportPath`, `completedAt`)
were captured live. Re-capture it at the first live run and expect field drift.
The heartbeat-only batch has the `check_batch.json` shape with its payload as the JSON string current Orca emits; the fenced envelope uses the `consumer_fenced` code documented in `backend.py` (lines 9–12). The epic's live acceptance run is the re-capture point.

`worktree_show_renamed.json` is trimmed rather than verbatim — the live
`worktree` object carries ~30 keys (lineage, linked issue/PR fields for six
forges, UI sort state) that this package never reads. What it preserves
exactly is the pair the fixture exists for: `displayName` holding the
requested `--name` verbatim while `branch` holds Orca's own
`refs/heads/<user>/<slug>` derivation.
