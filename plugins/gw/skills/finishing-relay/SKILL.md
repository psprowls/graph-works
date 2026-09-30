---
name: finishing-relay
description: Use when the finish stage of a work item is dispatched under auto-drive with mode relay — sends one orca orchestration ask carrying the integrate/PR/hold/discard decision instead of finishing-a-development-branch's interactive menu, executes the chosen outcome, and settles the item. Dispatched by the gw:workflow skill when the `Auto-drive context:` line appears in this session's own dispatch prompt; never invoked directly by a human.
---

# Finishing a Development Branch — Relay Mode

## Overview

Relay the integrate/PR/hold/discard decision to the auto-drive coordinator via
one `orca orchestration ask` instead of `finishing-a-development-branch`'s
interactive menu — this session is a dispatched worker with no human in the
terminal.

**Core principle:** Verify tests → Detect state → One ask → Execute the
choice → Settle the item and report.

**Announce at start:** "I'm using the finishing-relay skill to relay the
integrate/PR/hold/discard decision for `<work-path>`."

**Detection is the caller's job, not this skill's.** This skill is
dispatched only when the `gw:workflow` skill (or `/gw:workflow`)
already found the `Auto-drive context:` line in this session's own dispatch
prompt and routed here instead of `finishing-a-development-branch`. Nothing
in this skill re-checks that condition.

Every `orca orchestration` command below uses **this session's own**
`--from`, `--dispatch-capability`, `--task-id`, and `--dispatch-id` — the
values printed in the dispatch preamble that launched this session — never
the example values shown in this skill or in any other document.

An empty `finish_targets` list is missing evidence, never proof of integration.
Enter the Escalation path and hold the finish stage until targets are verified.

## R1 — Verify tests

For every code target, except a target R2's already-contained rule settles (run `git log <target_branch>..<source_branch>` first; empty means no gate), run `gw work gate check <work-path> --json` in its source worktree (`--worktree <source worktree>` when it is not the item's stamped worktree). `satisfied` skips the gate: quote the receipt's `match.owner` and `match.run_id` in R3's question. `unsatisfied` runs `gw work gate run <work-path>` (with the same `--worktree <source worktree>` when you passed one to `check`) and then `gw work gate wait <work-path>` until it returns `finished`; `running` means call `wait` again, and `orphaned` means start a new run. A red `finished` enters the Escalation path with `log_tail`. Never run the repository's check command directly as the gate.
The `_workspace` target has no test suite; its merged-result check is
`gw wiki lint --workspace <main workspace>` for main integration, or
`gw wiki lint --workspace <anchor worktree>` for epic integration.
Keep `GRAPH_WORKS_DIR` on the main workspace for work and receipt verbs.

**If the gate is red:** do not stop silently and do not present options. Enter
the **Escalation path** (below) with the failure output in the escalation
body. Wait for the coordinator's instructions before doing anything else.

**If the gate is satisfied or passes:** continue to R2.

## R2 — Detect state

Read the complete `finish_targets` list supplied by workflow (or from
`gw work next <work-path> --json`). Each entry supplies repository, worktree,
source_branch, target_branch and target_worktree. `worktree` is the source
checkout; `target_worktree` is the checkout holding the merge target. Validate
every entry; missing evidence or any blocker enters the Escalation path and
holds the entire finish stage. A `null` target_worktree means the target is not
checked out; hold at R2 without merging.
Use each entry's target_branch verbatim. The scalar Auto-drive merge target
is context only; it cannot replace this complete set or justify trunk fallback.

**Already contained.** For a code target, when `git log <target_branch>..<source_branch>` in the source worktree prints nothing, the source is already contained in the target. That target settles on the confirm path with no R1 or R4 gate; carry `resolved_in` = the target branch tip.

For each target, in its source worktree:

Classify the current git state:

```bash
git rev-parse --abbrev-ref HEAD
git worktree list --porcelain
```

- **Detached HEAD** (`git rev-parse --abbrev-ref HEAD` prints `HEAD`):
  drop `squash`, `merge` and `ff` from the ask's options in R3 — the same reduction
  `finishing-a-development-branch` Step 4 makes for its 3-option menu.
- **Trunk case** (current branch **is** the merge target — a shared epic
  worktree, or a main-mode item that never had a dedicated branch to begin
  with): the stage's commits already sit on the merge target — there is
  nothing to merge. Any integration choice (`squash`, `merge` or `ff`) in R4 resolves to "confirm and
  advance" with `resolved_in` = current HEAD SHA (`git rev-parse HEAD`).
  Never merge a branch into itself.
- **Integration-branch case** (current branch differs from the merge target,
  not detached): this target's source branch must be merged into the merge target.
  That covers a forked child merging into its epic's branch and a stamped Epic or Release root finishing its own integration branch
  into the base its dispatch named. The mechanics are identical, and the
  target is always that entry's `target_branch`. Find the
  worktree that has the merge target checked out by scanning
  `git worktree list --porcelain` for the block whose `branch` line reads
  `refs/heads/<merge target>`. The supplied `target_worktree` must be non-null
  and equal the unique path found by this scan. The R4 merge executes there,
  not in this worker's own worktree. No worktree has the merge target checked out,
  or more than one does:
  enter the **Escalation path**. A missing or mismatched supplied path also
  enters the **Escalation path**. Never check the target out yourself and
  never merge from this worker's worktree.
  For `_workspace` targeting workspace `main`, R4 uses
  `gw work merge-workspace <work-path> --apply --json` instead of `git merge`.
  For code repositories, R4 uses `gw work integrate`; this worker never runs `git merge` for a code repository.
  For `_workspace` targeting an epic workspace anchor, R4 merges in that
  anchor worktree and records the result with `finish-receipt.py record --repo _workspace`.

**Dirty state blocks the whole set.** Run `git status --porcelain` in each source
and target checkout and require clean output. Every
stage ends with a commit, so uncommitted changes at finish-stage are
anomalous — if you notice them, enter the **Escalation path** rather than
proceeding.

Carry forward into R3: the merge target, the classified case, the target
worktree path (integration-branch case only), the current HEAD SHA (`git rev-parse
HEAD`; this is `resolved_in` for the trunk-case merge), the
full commit list with commit count and one-line summary (`git log
<merge-base>..HEAD --oneline` against the merge target — the full list feeds
the discard re-ask in R4, the one-line summary feeds the R3 question text),
the diffstat (`git diff --stat <merge-base>..HEAD` against the merge target,
in the source worktree), and R1's test result, for every repository target.

## R3 — One ask

Ask once for the entire target set. Include each repository, source/target
branch, commit summary and test result in the question. The answer applies
to all targets; remove the integration options if any target is detached or unverified.

Prepare the ask with `gw work ask`, then send exactly the strings it prints.
Write the question to a scratch markdown file with exactly these sections, in
this order, and pass it as the question:

1. `## What the diff does` — drafted from each target's commit list and
   diffstat, reading the changed files where a subject line is not enough.
   Every claim names a changed file. Nothing is taken from the design or the
   plan: a behaviour the diff does not show is not claimed.
2. `## Finish obligations` — each line of the `finish_obligations`
   carried-context slot, verbatim, or `None.` when the slot is absent or
   empty. Read it from the brief's `## Carried context` block, or from
   `gw work next <work-path> --json` → `carried_context.slots.finish_obligations.lines`.
   A `deferred` obligation is not performed here; the human's answer decides it.
3. Per target: repository, source/target branch, commit summary, test result
   and default strategy.

The summary is a one-line headline; when there are `k > 0` obligations it
adds `; <k> finish obligation(s)` before the merge target:

```
gw work ask <work-path> --kind choice \
  --summary "Finish <work-path>: <N> commit(s) across <T> target(s); tests <pass|fail>[; <k> finish obligation(s)]; merge target <merge target>." \
  --question-file <scratch>/finish-question.md \
  --option "squash=Squash into one commit on the target branch" \
  --option "merge=Merge commit (--no-ff) into the target branch" \
  --option "ff=Fast-forward the target branch (refuses if it diverged)" \
  --option "pr=Open a pull request" \
  --option "hold=Hold at finish" \
  --option "discard=Discard the branch" \
  --json
```

The three integration tokens are listed first, and the targets' default strategy is listed first among them: the `default_strategy` of the first `finish_targets` entry that has a non-null one, or `squash` when none does. The other two follow in the order `squash`, `merge`, `ff`; then `pr`, `hold`, `discard`. The example above shows default `squash`; reorder the first three lines for another default. The question file states each code target's `default_strategy`, and that `_workspace` targets always integrate with a merge commit whatever token is chosen. One answer applies to every code target. Drop `squash`, `merge` and `ff` when any target is detached or unverified. Then send
one `orca orchestration ask` with this session's own `--from` /
`--dispatch-capability` from its dispatch preamble, passing `orca.question`
and `orca.options` from that JSON **unchanged**. Never hand-write `--options`:

```
orca orchestration ask --from <this session's --from> \
  --dispatch-capability <this session's --dispatch-capability> \
  --question "<orca.question>" \
  --options "<orca.options>" \
  --timeout-ms 600000
```

`ask` blocks until the coordinator replies and prints the reply body — there
is no separate poll/fetch step. If the call times out or disconnects, check
`orca orchestration ask --help` for the resume syntax before sending a
second, duplicate question — never re-send blind.

**If it times out:** follow
`../auto-drive/references/grace-period-protocol.md`'s loop — re-arm with
`ask --resume` for the remaining grace-period budget, and if the budget
is exhausted, checkpoint and park exactly as that doc describes rather than
giving up. Do not call `worker_done` while this question is unanswered: the
finish stage has already verified tests and detected merge state by the time
R3 sends its ask (R1/R2), so a park's `## Completed work` section is "tests
verified, merge target `<target>`, ready to settle" and its `## Remaining
actions` section is "re-send R3's ask (reusing its payload — never call
`gw work ask` twice for one question), or resume from the recorded answer,
and execute R4/R5." Reuse the prepared payload on a resume of any typed
ask in R3, R4 or R5; never prepare a second payload for the same question.

The reply body is JSON: read `choice` from the JSON reply (`squash`, `merge`, `ff`, `pr`,
`hold` or `discard`). A reply that is not JSON, including a bare option token, is treated as `hold`.
A `choice` that is not one of the options you sent is also treated as `hold`.
Note the verbatim reply in the R5 report — don't guess at unrecognized intent.

## R4 — Execute the choice

Process each supplied target in order using its own worktree and branches.
Collect before/after commit evidence and merged-result checks for each
target. Any conflict or failed check holds the whole stage; explicitly report
what already integrated. Cross-repository atomicity is not promised.

### `squash`, `merge` or `ff`

- **Trunk case:** no-op merge — the commits are already on the merge
  target. Skip straight to R5 with `resolved_in` = the HEAD SHA captured in
  R2.
- **Integration-branch case:**
  For `_workspace` → workspace `main`, run
  `gw work merge-workspace <work-path> --apply --json` and never record its
  receipt separately: the verb merges and records it in one locked step.
  A refusal enters the Escalation path and holds the entire finish. Run
  `gw wiki lint --workspace <main workspace>` on the merged main checkout;
  a failure also enters Escalation.
  For `_workspace` → an epic anchor, run
  `git -C <anchor worktree> merge <source_branch>`, check the merged result
  with `gw wiki lint --workspace <anchor worktree>` on that merged anchor
  checkout, then run
  `finish-receipt.py record <work-path> --workspace <workspace> --repo _workspace`.
  A conflict, failed lint, or refusal enters the Escalation path. For code
  repositories, run:

      gw work integrate <work-path> --repo <this target's repository> --strategy <choice> --apply --json

  Never run `git merge` for a code repository: gw chooses the flags for the strategy, merges in the target worktree and records the receipt in the same locked step. `outcome: already-integrated` continues. Any refusal enters the **Escalation path** with its `reason` and `detail`, and holds the entire finish. `conflict` lists the paths in `conflicts` (never auto-resolve them — parent-epic policy). `not-fast-forward` means `ff` cannot apply. `nothing-to-integrate` means the target already carries these changes another way, and a human decides whether to record that with `gw work accept-integration`. `receipt-refused` means the integration landed at `result_commit` and only its receipt is missing: the body must say so, and the retry is `finish-receipt.py record --repo <name>`.
  **On a successful integrate:** run `gw work gate check <work-path> --worktree <target worktree>`. A fast-forward, or a squash onto an unmoved target, reproduces the gated tree and is `satisfied`, so nothing re-runs. Otherwise run `gw work gate run <work-path> --worktree <target worktree>` and `gw work gate wait <work-path>`; the receipt is recorded under this item. **A red gate post-merge:** enter the
  Escalation path — the merge already happened, so the escalation body must
  say so explicitly (don't let the coordinator think it's still pending).
  Continue to R5 with each code target's `result_commit` from `gw work integrate`; for workspace `main`, use the merge result returned by gw.

### `pr`

```bash
git push -u origin <this target's source_branch>
gh pr create --title "<path title>" --body "$(cat <<'EOF'
## Summary
<2-3 bullets of what changed>

## Test Plan
- [ ] <verification steps>
EOF
)"
```

`<path title>` is the work item's frontmatter `title:` field. Continue to R5
with the PR URL.

### `hold`

Nothing to execute. Continue to R5.

### `discard`

Send a second, option-less typed ask asking for exact confirmation:

```
gw work ask <work-path> --kind free \
  --summary "Confirm discard of <work-path> branch <branch>." \
  --question "Discard <work-path> branch <branch> (<N> commits: <list>). Reply exactly 'discard' to confirm; anything else cancels." \
  --json
```

Then `orca orchestration ask` with its `orca.question` and **no `--options`**
(a `free` ask prints `orca.options: null`), same `--from` /
`--dispatch-capability` / `--timeout-ms 600000` as R3:

```
orca orchestration ask --from <this session's --from> \
  --dispatch-capability <this session's --dispatch-capability> \
  --question "<orca.question>" \
  --timeout-ms 600000
```

- The reply JSON's `notes` is exactly `discard` → confirmed. **Discard is
  recorded, not executed**: delete nothing. Continue to R5 with the branch name and commit
  list for the report — the human removes the branch/worktree later (see
  Worktree & branch ownership, below).
- Anything else (other notes, unparseable reply) → downgrade to `hold`; say
  so explicitly in the R5 report (state the reply that caused the downgrade).

## R5 — Settle the item and report

The shared rule is: only a verified integration resolves — same rule
as attended `workflow` step 5. The trunk-case confirmation counts as
integration into the merge target; PR, hold and discard do not.

- **`squash` / `merge` / `ff`:** Code targets were recorded by `gw work integrate` and the workspace `main` target by `merge-workspace`; do not record them again. Record each workspace-anchor target immediately using the helper procedure below. Only after every
  target is proven integrated and all checks
  pass, inspect the complete workflow-owned finish receipt and use its
  `resolved_in` for advancement. Missing evidence holds the entire stage. Run the advance below exactly once
  for the item, never once per target.
  **Release items only — the date.** `gw work advance` refuses to resolve a
  Release without `released_at`. Read the item's frontmatter first: if
  `released_at:` is set to a valid `YYYY-MM-DD` date, advance as below. A
  malformed frontmatter date enters the **Escalation path**. If the date is
  missing, prepare a typed ask —
  `gw work ask <work-path> --kind free --summary "Release date for <work-path> (YYYY-MM-DD)?" --question "<the merge that happened, and that a YYYY-MM-DD release date is needed to resolve>" --json`
  — and send its `orca.question` through one `orca orchestration ask` (this
  session's own `--from` / `--dispatch-capability`, no `--options`). Read the
  date from the reply JSON's `notes`, and add `--released-at <date>` to the
  advance. Never invent a release date, and never retry an advance that
  refused `released_at required`; a missing or malformed
  reply enters the **Escalation path**, whose body must say the merge already
  happened (or, in the trunk case, the commits were already on the target).
  `pr`, `hold` and `discard` do not resolve, so they never need a date.
  ```bash
  gw work advance <work-path> --from finish --no-infer-worktree --resolved-in <resolved_in from receipt inspection> [--released-at <date>]
  ```
  On phase-mismatch, enter the **Escalation path** with the refusal output;
  retain the `--from finish` guard and do not retry using a newly observed
  phase. The merge may already have happened, so include the merge SHA in the
  escalation body (or say the commits were already on the target in the trunk case).
  Do not claim settlement or send `worker_done` while escalating.
  **Cleanup (`squash` / `merge` / `ff` only, after the successful advance).** Before sending
  `worker_done`, run the cleanup plan from this session's own cwd and execute
  it per [finish-cleanup.md](references/finish-cleanup.md):
  ```bash
  uv run --package graph-works-core python <plugin>/skills/finishing-relay/references/finish-receipt.py cleanup <work-path> --workspace <workspace> --runner-cwd "$PWD"
  ```
  This session's own worktree comes back `deferred`; leave it for the
  coordinator. Write one line per removed, skipped and deferred row (path,
  branch, and reason for any skip or deferred) to the run-specific report
  `<workspace>/.gw/cache/auto-drive/<run_id>/finish-cleanup/<dispatch_id>.md`.
  Set `cleanup_report` to that path using this session's dispatch ID and its
  matching run ID (from the dispatch context, or from a
  `worker-show --dispatch <dispatch_id> --json` readback after matching its
  task and dispatch IDs to this session). Then
  `mkdir -p "$(dirname "$cleanup_report")"` before writing it. This is in
  the main workspace's gitignored `.gw/cache` runtime area, outside the
  removable item worktrees and tracked `okf` content; it survives this
  session's worktree. A refused plan is one line in that report, never an
  escalation. The item has already resolved.
  Only after a successful advance, send `worker_done --outcome succeeded` (this session's own dispatch
  preamble command, `--task-id`/`--dispatch-id` filled in from it) with
  `--report-path` set to the file above and the required exactly-three-sentence
  body: name the merge target and the resolved-in reference, give a one-line
  summary of what shipped, and summarize the cleanup counts (removed / skipped
  / deferred) instead of listing rows.
- **`pr` / `hold` / `discard`:** **no `gw work advance` call** — the item
  stays at `phase: finish` for a later attended pass. `pr` / `hold` / `discard` run no cleanup: nothing is removed and discard stays recorded, not executed.
  Send
  `worker_done --outcome succeeded` with a body stating exactly what
  happened:
  - `pr` → the PR URL.
  - `hold` → the branch name and that it's untouched.
  - `discard` (confirmed) → the branch name and commit list, noting the
    branch was **not** deleted (recorded only).
  - `discard` (downgraded to hold) → the branch name and the verbatim reply
    that caused the downgrade.

  Include the merge target in every held-outcome body so a later attended
  pass knows which branch must contain the work before the item can resolve.

## Escalation path (failure handling)

Entered from R1 (failing tests), R2 (no single worktree has the merge target
checked out), R4 (an integrate refusal, merge conflicts, post-merge test failure) and R5 (no usable
release date or phase-mismatch):

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
   coordinator wait path consumes it. Which field of the `check` output
   carries an escalation reply's body is not documented for this address
   form — read it off the first real reply rather than assuming a key.
3. A reply with instructions (e.g. "fix the tests", "merge anyway") →
   follow it, then re-enter the flow at R1 so the checks re-run against the
   new state.
4. No reply within the window, or a reply saying give up → send
   `worker_done --outcome failed` with the failure summary. The
   coordinator's existing failure question (retry / skip / stop) takes over
   from there.

## Worktree & branch ownership

The relay removes merged worktrees and branches only through the cleanup
plan (`finish-receipt.py cleanup`, executed per
[finish-cleanup.md](references/finish-cleanup.md)), only after a resolved
advance, and never its own worktree: the plan reports that row as
`deferred`, and the coordinator removes the relay's own worktree after
`worker-release` (`auto-drive` §4.1). `pr`, `hold` and `discard` remove
nothing. `finishing-a-development-branch`'s Step 6 cleanup logic still does
not apply here.

## Out of scope

- Coordinator-side handling — `auto-drive` SKILL.md §4.3 (question
  mirroring) and §4.4 (escalation) own both message types this skill sends.
- `finishing-a-development-branch`'s own behavior — this skill is fully
  self-contained and never modifies the stock skill.
- Automatic `wontfix` on discard, auto-retry, and automatic merge-conflict
  resolution — all explicit policy (Escalation path, `discard`
  recorded-not-executed), not gaps.

## Finish receipt verification and recovery

Before presenting the integration choice, state each code target's default strategy. `gw work integrate` performs every code-repository integration and records a receipt whose verification matches its strategy: `ff` proves the target now contains the source commit itself, `merge` proves a merge commit whose parents are the pre-merge target and the source, and `squash` proves a single-parent commit on the pre-merge target whose tree equals Git's merge of the source into it. An integration gw cannot verify (a rebase, a squash made elsewhere, a PR merged upstream) is never forced through. Hold, and route it to the human, who may record it with `gw work accept-integration <work-path> --repo <name> --evidence <sha> --reason "<why>" --by <human> --apply` (attested evidence, marked `accepted`). Workers never run `accept-integration` and never hand-advance.

Resolve `<plugin>` to this installed plugin's absolute directory. Run the helper
with the core environment available: the command below works from the source
workspace; from an external checkout add `--project <graph-works source root>`
to `uv run`, or use the installed interpreter containing graph-works-core. Never
assume the worker's current directory is the Graph Works source checkout.

```bash
uv run --package graph-works-core python <plugin>/skills/finishing-relay/references/finish-receipt.py inspect <work-path> --workspace <workspace>
uv run --package graph-works-core python <plugin>/skills/finishing-relay/references/finish-receipt.py record <work-path> --workspace <workspace> --repo <name>
uv run --package graph-works-core python <plugin>/skills/finishing-relay/references/finish-receipt.py cleanup <work-path> --workspace <workspace> --runner-cwd <cwd>
```

Inspect before any integration. `gw work integrate` records code targets itself; record each workspace-anchor target immediately after its merge and merged-result
checks pass.
`record` commits the receipt itself; do not commit the workspace.
`record` derives commit evidence itself; never hand-author completion
claims. Preserve source branches and worktrees until the item resolves: the
cleanup plan, run only after the resolved advance, is the only thing that
ends that preservation.

If a later repository fails, hold the entire finish and report already verified
entries. If a merge succeeded but receipt persistence failed, run `record` again:
it rediscovers the merge commit, fast-forward or squash in the target without another merge.
Inspection returns only currently verified entries; stale entries remain historical
receipt content and block completion until refreshed. A malformed receipt requires
repair, not replacement. No cross-repository atomicity is promised.

Run `inspect` again after recording every target. Only `complete: true` authorizes
the single final advance; use its `resolved_in` verbatim and
`gw work advance <work-path> --no-infer-worktree --resolved-in <resolved_in>`
(with the required Release date when applicable). An incomplete inspection exits
nonzero and names blockers. No helper mode merges, advances or removes
anything; `cleanup` only prints the plan.
