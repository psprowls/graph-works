# Executing a finish cleanup plan

One contract for every caller: `finishing-relay` R5, the `auto-drive` Success branch and wrap-up, and attended `workflow` step 5. The plan comes from core; every removal is `gw work place --remove`, so no caller names a git or Orca command.

## Get the plan

```bash
uv run --package gw-pipeline python <plugin>/skills/finishing-relay/references/finish-receipt.py cleanup <work-path> --workspace <workspace> --runner-cwd <cwd>
```

`<cwd>` is the directory the calling session runs in. Exit 1 means `refusal` is set: report it as one line and stop. The item has already resolved, so a refusal is never an escalation and never changes item state.

The JSON is `{"rows": [...], "refusal": null}`. Each row carries `repo`, `worktree` (absolute, or `""` when none remains), `branch`, `action` (`remove` / `deferred` / `skip`), `reason` and `command`, which is `gw work place <work-path> --remove --repo-name <repo>`.

## Execute each row, in order

- `remove`: run `<command> --json` and report the `chain[]` row actions it returns.
- `deferred`: the row's worktree contains the caller's own directory, so the caller never removes it. Print `command` for the human to run from another directory (attended), or leave the row for the coordinator (relay).
- `skip`: report `reason` exactly as the plan gives it.
- A `place` refusal on a `remove` row (`dirty-worktree`, `not-finished`, `foreign-checkout` and the other kinds) is reported as `skipped: <kind>` and is never retried, never forced: never `--force`, and no other flag.

Branches stay until the item is archived: `gw work archive` deletes the merged branches through `gw work place --prune`.

## Report

One line per row: `removed <worktree> (<branch>)`, `skipped <worktree> (<branch>): <reason>`, or `deferred <worktree> (<branch>)`.

Re-running the plan is safe. Removed rows drop out and nothing unmerged is ever offered for removal.
