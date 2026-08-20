---
name: finishing-relay
description: Use when the finish stage of a work item is dispatched under auto-drive with mode relay — sends one orca orchestration ask carrying the merge/PR/hold/discard decision instead of finishing-a-development-branch's interactive menu, executes the chosen outcome, and settles the item. Dispatched by the graph-works:workflow skill when the `Auto-drive context:` line appears in this session's own dispatch prompt; never invoked directly by a human.
---

# Finishing a Development Branch — Relay Mode

## Overview

Relay the merge/PR/hold/discard decision to the auto-drive coordinator via
one `orca orchestration ask` instead of `finishing-a-development-branch`'s
interactive menu — this session is a dispatched worker with no human in the
terminal.

**Core principle:** Verify tests → Detect state → One ask → Execute the
choice → Settle the item and report.

**Announce at start:** "I'm using the finishing-relay skill to relay the
merge/PR/hold/discard decision for `<slug>`."

**Detection is the caller's job, not this skill's.** This skill is
dispatched only when the `graph-works:workflow` skill (or `/graph-works:next`)
already found the `Auto-drive context:` line in this session's own dispatch
prompt and routed here instead of `finishing-a-development-branch`. Nothing
in this skill re-checks that condition.

Every `orca orchestration` command below uses **this session's own**
`--from`, `--dispatch-capability`, `--task-id`, and `--dispatch-id` — the
values printed in the dispatch preamble that launched this session — never
the example values shown in this skill or in any other document.

## R1 — Verify tests

Run the project's test suite (same discovery approach as
`finishing-a-development-branch` Step 1 — `npm test` / `cargo test` /
`pytest` / `go test ./...`, whichever the repo uses).

**If tests fail:** do not stop silently and do not present options. Enter
the **Escalation path** (below) with the failure output in the escalation
body. Wait for the coordinator's instructions before doing anything else.

**If tests pass:** continue to R2.

## R2 — Detect state

Read the merge target verbatim from the `Auto-drive context:` line in this
session's dispatch prompt — never guess it via `git merge-base`:

```
Auto-drive context: relay the merge/PR/hold/discard decision via one
`orca orchestration ask`; merge target is `<branch>`.
```

Classify the current git state:

```bash
git rev-parse --abbrev-ref HEAD
git worktree list --porcelain
```

- **Detached HEAD** (`git rev-parse --abbrev-ref HEAD` prints `HEAD`): drop
  `merge` from the ask's options in R3 — the same reduction
  `finishing-a-development-branch` Step 4 makes for its 3-option menu.
- **Trunk case** (current branch **is** the merge target — a shared epic
  worktree, or a main-mode item that never had a dedicated branch to begin
  with): the stage's commits already sit on the merge target — there is
  nothing to merge. The `merge` choice in R4 resolves to "confirm and
  advance" with `resolved_in` = current HEAD SHA (`git rev-parse HEAD`).
- **Forked-child case** (current branch differs from the merge target, not
  detached): find the worktree that has the merge target checked out by
  scanning `git worktree list --porcelain` for the block whose `branch`
  line reads `refs/heads/<merge target>`. The R4 merge executes there, not
  in this worker's own worktree.

**Known gap:** the design spec names "dirty state" (uncommitted changes) as
an Escalation-path trigger alongside failing tests and merge conflicts, but
does not specify a detection procedure, and this plan didn't operationalize
one — R2 does not check `git status` for uncommitted changes. In practice
every task in this plan's own workflow ends with a commit, so a dirty
worktree at finish-stage would itself be anomalous; if it's observed, treat
it as a reason to escalate manually rather than proceeding, but there's no
automated check for it here. **Live-validation item:** decide whether to add
one before this skill's first real relay run.

Carry forward into R3: the merge target, the classified case, the target
worktree path (forked-child case only), the current HEAD SHA (`git rev-parse
HEAD`; this is `resolved_in` for the trunk-case merge), the
full commit list with commit count and one-line summary (`git log
<merge-base>..HEAD --oneline` against the merge target — the full list feeds
the discard re-ask in R4, the one-line summary feeds the R3 question text),
and R1's test result.

## R3 — One ask

Send exactly one `orca orchestration ask`, using this session's own
`--from` / `--dispatch-capability` from its dispatch preamble:

```
orca orchestration ask --from <this session's --from> \
  --dispatch-capability <this session's --dispatch-capability> \
  --question "Finish stage for <slug> on branch <current branch> ready to settle. <N> commit(s): <one-line summary>. Tests: <pass/fail summary>. Merge target: <merge target>. How should this be handled?" \
  --options "<merge,pr,hold,discard — or pr,hold,discard on detached HEAD>" \
  --timeout-ms 600000
```

`ask` blocks until the coordinator replies and prints the reply body — there
is no separate poll/fetch step. **Live-validation item:** if the call times
out or disconnects, the resume mechanism is not a documented flag in this
session's own preamble; check `orca orchestration ask --help` for the real
resume syntax before sending a second, duplicate question.

The reply body is one of the option labels (`merge`, `pr`, `hold`,
`discard`). Any other reply text: treat it as `hold` and note the verbatim
reply in the R5 report — don't guess at unrecognized intent.

## R4 — Execute the choice

### `merge`

- **Trunk case:** no-op merge — the commits are already on the merge
  target. Skip straight to R5 with `resolved_in` = the HEAD SHA captured in
  R2.
- **Forked-child case:**
  ```bash
  git -C <target worktree path from R2> merge <this worker's branch>
  ```
  **Conflicts:** never auto-resolve (parent-epic policy). Enter the
  **Escalation path** with the conflict file list in the body; wait for
  instructions.
  **On a clean merge:** re-run the test suite (R1's command) in the target
  worktree, on the merged result. **Failing tests post-merge:** enter the
  Escalation path — the merge already happened, so the escalation body must
  say so explicitly (don't let the coordinator think it's still pending).
  Continue to R5 with `resolved_in` = the merge commit SHA
  (`git -C <target worktree path> rev-parse HEAD`).

### `pr`

```bash
git push -u origin <this worker's branch>
gh pr create --title "<slug title>" --body "$(cat <<'EOF'
## Summary
<2-3 bullets of what changed>

## Test Plan
- [ ] <verification steps>
EOF
)"
```

`<slug title>` is the work item's frontmatter `title:` field. Reuses
`finishing-a-development-branch`'s Option 2 body template verbatim. Continue
to R5 with the PR URL.

### `hold`

Nothing to execute. Continue to R5.

### `discard`

Send a second, option-less `ask` asking for exact confirmation:

```
orca orchestration ask --from <this session's --from> \
  --dispatch-capability <this session's --dispatch-capability> \
  --question "Confirm discard of <slug> branch <branch> (<N> commits: <list>). Reply exactly 'discard' to confirm, anything else cancels." \
  --timeout-ms 600000
```

- Reply is exactly `discard` → confirmed. **Discard is recorded, not
  executed**: delete nothing. Continue to R5 with the branch name and commit
  list for the report — the human removes the branch/worktree later, after
  Orca releases it (see Worktree & branch ownership, below).
- Any other reply → downgrade to `hold`; say so explicitly in the R5 report
  (state the reply that caused the downgrade).

## R5 — Settle the item and report

- **`merge`:**
  ```bash
  gw work advance <slug> --resolved-in <resolved_in from R4>
  ```
  Then send `worker_done --outcome succeeded` (this session's own dispatch
  preamble command, `--task-id`/`--dispatch-id` filled in from it) with a
  body naming the merge target, the resolved-in reference, and a one-line
  summary of what shipped.
- **`pr` / `hold` / `discard`:** **no `gw work advance` call** — the item
  stays at `phase: finish` for a later attended pass. Send
  `worker_done --outcome succeeded` with a body stating exactly what
  happened:
  - `pr` → the PR URL.
  - `hold` → the branch name and that it's untouched.
  - `discard` (confirmed) → the branch name and commit list, noting the
    branch was **not** deleted (recorded only).
  - `discard` (downgraded to hold) → the branch name and the verbatim reply
    that caused the downgrade.

## Escalation path (failure handling)

Entered from R1 (failing tests) and R4 (merge conflicts, post-merge test
failure):

1. Send an escalation with the concrete failure output (test failures,
   conflict file list) in the body:
   ```
   orca orchestration send --from <this session's --from> \
     --dispatch-capability <this session's --dispatch-capability> \
     --type escalation --subject "Blocked: <one-line reason>" \
     --body "<failure output>" --task-id <this session's --task-id>
   ```
2. Poll for a reply about every 60 seconds, for a bounded window of ~30
   minutes:
   ```
   orca orchestration check --terminal <this session's terminal handle>
   ```
   Send a heartbeat (`--type heartbeat`, `--phase "waiting"`, this session's
   own command shape) roughly every 5 minutes while polling — this
   session's own dispatch rules require a heartbeat on this cadence
   whenever it's active and waiting, regardless of whether this particular
   coordinator wait path consumes it.
   **Live-validation item:** confirm on the first real run which field of
   the `check` output carries the reply body for an escalation reply — not
   documented in `--help` output for this address form.
3. A reply with instructions (e.g. "fix the tests", "merge anyway") →
   follow it, then re-enter the flow at R1 so the checks re-run against the
   new state.
4. No reply within the window, or a reply saying give up → send
   `worker_done --outcome failed` with the failure summary. The
   coordinator's existing failure question (retry / skip / stop) takes over
   from there.

## Worktree & branch ownership

Relay mode never removes worktrees and never deletes branches — not for
`merge`, not for `discard`. Orca owns worker worktree lifecycle: the
coordinator releases this session's terminal on `worker_done`, and any
child-worktree removal after merge-back is the coordinator's or the human's
concern, not this skill's. `finishing-a-development-branch`'s Step 6
cleanup logic is intentionally absent here — every auto-drive worktree is
host-managed by definition.

## Out of scope

- Coordinator-side changes — `auto-drive` SKILL.md §4.3 (question
  mirroring) and §4.4 (escalation) already handle both message types this
  skill sends; nothing here needs a coordinator change.
- `gw work orchestrate` changes — the `Auto-drive context:` prompt line and
  the `merge_target` field it carries already ship
  (`packages/work-io/src/work_io/orchestrate.py:313-329`).
- Changes to `finishing-a-development-branch`'s own behavior — this skill
  is fully self-contained; the stock skill is untouched.
- Automatic `wontfix` on discard, auto-retry, and automatic merge-conflict
  resolution — all explicit policy (Escalation path, `discard`
  recorded-not-executed), not gaps.
