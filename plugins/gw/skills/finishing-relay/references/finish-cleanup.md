# Executing a finish cleanup plan

One contract for every caller: `finishing-relay` R5, the `auto-drive`
Success branch and wrap-up, and attended `workflow` step 5. The plan comes
from core; the removal happens here, in prose, because core never calls Orca.

## Get the plan

```bash
uv run --package graph-works-core python <plugin>/skills/finishing-relay/references/finish-receipt.py cleanup <work-path> --workspace <workspace> --runner-cwd <cwd>
```

`<cwd>` is the directory the calling session runs in. Exit 1 means `refusal`
is set: report it as one line and stop. The item has already resolved, so a
refusal is never an escalation and never changes item state.

The JSON is `{"rows": [...], "refusal": null}`. Each row carries `repo`,
`worktree` (absolute, or `""` when only a branch remains), `branch` (or `""`),
`target_branch`, `action` (`remove` / `deferred` / `skip`) and `reason`.

## Execute each `remove` row, in order

1. When `orca` is on PATH, and the row has a worktree, run
   `orca worktree show --worktree path:<worktree> --json`.
2. **Orca-managed** (the show succeeded) →
   `orca worktree rm --worktree path:<worktree> --json`. Pass no other flags:
   never `--force`, and no hooks flags. Orca deletes the branch only when it
   can prove it merged; a branch Orca keeps is reported as retained.
3. **Otherwise** → `git -C <repo> worktree remove <worktree>` (never
   `--force`), then `git -C <repo> branch -d <branch>` (never `-D`). `<repo>`
   is the declared checkout of the row's `repo`: for a code repository, the
   `repositories.<repo>.path` entry in `<workspace>/workspace.yaml`; the
   reserved `_workspace` row is the workspace's own Git checkout
   (`<workspace>`), never a `repositories.<repo>` entry. The main checkout is
   never a row's worktree.
4. A row with an empty `worktree` runs only `git -C <repo> branch -d <branch>`.
5. Any refusal (a dirty worktree, an unmerged branch, a branch Orca
   retained) is reported as `skipped: <reason>` and never retried with force
   or any other flag.

## Report

One line per row: `removed <worktree> (<branch>)`, `skipped <worktree>
(<branch>): <reason>`, or `deferred <worktree> (<branch>)`. Report `skip` and
`deferred` rows exactly as the plan gives them. A `deferred` row is the
caller's own cwd: the relay leaves it to the coordinator, and an attended
session names it for the user.

Re-running the plan is safe. Removed rows drop out, a deleted branch counts
as merged, and nothing unmerged is ever offered for removal.
