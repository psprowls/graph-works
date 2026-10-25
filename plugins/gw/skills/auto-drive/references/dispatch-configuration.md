# Dispatch configuration and worker preferences

The manifest's `workflow_config` names the workflow file, a shared YAML document
holding `workflow:` (including the auto-drive limits in `workflow.auto_drive`) and
`pipeline:`. Its optional `.local` sibling overlays it and appends rules after shared rules. Core owns resolution;
auto-drive consumes `gw work orchestrate --json` fields and provenance directly.
See the repository's [complete dispatch guide](../../../../../packages/gw-kernel/docs/dispatch-rules.md)
for attribute vocabulary, YAML examples, init/ignore behavior and manual cutover.
The same document's `pipeline.path` and `pipeline.artifacts` blocks set which
stages an item walks and each stage's artifact file; see the guide's Pipeline
path and Stage artifacts sections.

Report the selected agent/model/reasoning effort, including defaults and reset
origins. Attended workflow reports these preferences for the planned stage;
it does not reconfigure the currently running agent. Work-item `effort` is an
estimate, and Python `roles.*` controls a separate model pool.

`gw work dispatch` encodes the full original profile and the computed placement (`--worktree path:<worktree>`) before task-create, then launches from the saved task spec; `launch-worker.py decode` reads a saved spec back. The `GW_LAUNCH_V1` envelope is shared with `workflow-orca`. Models and effort are opaque; omit both for agent defaults. Effort without a model is refused. Permissions remain the selected agent's existing settings.

Compare both requested and effective receipts. Missing proof or an ambiguous start requires recovery inspection with task/dispatch IDs, never a duplicate start. After restart, retain the original preferences despite rule edits. A retry is `gw work dispatch <key> --retry-of <dispatch_id>`, which verifies the item's computed worktree without refreshing it and relaunches the same Task there. Never allocate another worktree merely because output is missing. `worker_done` owns settlement; terminal access is optional.
