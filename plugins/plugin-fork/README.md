# Optional plugin-fork workflows

These local companion skills guide the standalone `plugin-fork` CLI:

- [fork-skills](skills/fork-skills/SKILL.md): inspect, select, fork or adopt, then
  prepare and approve agent installation.
- [review-fork-update](skills/review-fork-update/SKILL.md): compare incoming changes,
  preserve local intent, reconcile optional review, approve acceptance or rollback.

The CLI must be available separately. See the [package documentation](../../packages/plugin-fork-io/README.md)
for commands, JSON examples, portable state and platform limitations. Reading these
files does not register or install them. No plugin manifest, hook, existing skill
replacement, or GW runtime integration is required. Use them locally as requested;
installation into an agent is a separate user choice.

Their metadata and CLI examples are checked by the package's pytest gate in
`test_companion_contracts.py`. Behavioral scenarios and outcomes are recorded in
`.superpowers/sdd/02-plan/task-9-behavioral-evidence.md` during development.
