# Dispatch configuration and worker preferences

The manifest's `workflow.dispatch_rules` names a shared YAML document. Its
optional `.local` sibling appends rules after shared rules. Core owns resolution;
auto-drive consumes `gw work orchestrate --json` fields and provenance directly.
See the repository's [complete dispatch guide](../../../../../packages/graph-works-core/docs/dispatch-rules.md)
for attribute vocabulary, YAML examples, init/ignore behavior and manual cutover.

Report the selected agent/model/reasoning effort, including defaults and reset
origins. Attended workflow reports these preferences for the planned stage;
it does not reconfigure the currently running agent. Work-item `effort` is an
estimate, and Python `roles.*` controls a separate model pool.

Use `launch-worker.py` to encode the full original profile and exact placement
before task-create, then launch from the saved task spec. The six-field
`GW_LAUNCH_V1` envelope is shared with `workflow-orca`. Models and effort are
opaque; omit both for agent defaults. Effort without a model is refused.
Permissions remain the selected agent's existing settings.

Compare both requested and effective receipts. Missing proof or an ambiguous
start requires recovery inspection with task/dispatch IDs, never a duplicate
start. After restart, retain the original preferences despite rule edits.
Retry also requires explicit recovery-approved placement, which can reuse an
already allocated worktree. Never allocate another worktree merely because
output is missing. `worker_done` owns settlement; terminal access is optional.
