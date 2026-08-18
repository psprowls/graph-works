# Fixtures

JSON captured verbatim from the live `orca` CLI on 2026-08-14, with ids and
paths left exactly as returned. Nothing re-captures these automatically — a
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

**`check_batch.json` is the one file not captured verbatim.** A `check` batch
requires the capturing terminal to be the Run's bound coordinator with live
traffic, which the plan author did not have. It is hand-authored from two
verified sources: the flag vocabulary of `orca orchestration send --help`
(`--task-id`, `--dispatch-id`, `--outcome`, `--files-modified`, `--report-path`,
`--phase`) and the settled-task `result` payload in `task_list.json`, whose keys
(`outcome`, `subject`, `body`, `filesModified`, `reportPath`, `completedAt`)
were captured live. Re-capture it at the first live run and expect field drift.
