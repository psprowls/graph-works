# Grace-period / checkpoint / park protocol

Referenced by every dispatch mode whose worker may block on `orca orchestration
ask` and a human might not answer in time (`GRACE_PERIOD_TAIL` in
`graph_works_core.workspace.pipeline`, folded into both `ATTEND_TAIL` and
`RELAY_TAIL_SEED`). One copy, so `finishing-relay/SKILL.md` and every
attend-mode stage skill's dispatch prompt agree on what "the worker gives up"
means.

**Operator note — an existing workspace does not get the `relay` pointer for
free.** `ATTEND_TAIL` is a packaged constant, so every `attend`-mode dispatch
picks the pointer up automatically. `RELAY_TAIL_SEED` is not: core only seeds it
into a **new** workspace's dispatch document at `gw bootstrap`/init time and
preserves an authored one, so a workspace created before this protocol shipped
still dispatches `relay` (finish-stage) workers with its old tail and no
grace-period obligation. There is deliberately no migration — the relay tail is
a value the workspace owns. To upgrade one, append `GRACE_PERIOD_TAIL`'s text to
the `branch`-variant `prompt_tail` in the dispatch document named by
`workflow.dispatch_rules` (or its `dispatch.local.yaml` sibling), then run
`gw config sync`. Until that is done, a finish-stage worker in that workspace
follows this protocol only because `finishing-relay/SKILL.md`'s R3 names it
directly.

**The problem this exists to prevent.** Ending this Dispatch while a question
is still pending closes the question — Orca closes every pending question on a
Dispatch the moment it ends, by any of: accepted success, accepted failure,
`worker-stop`, or `worker-abandon`. Once closed, a human's answer can never
reach `reply`; it is silently lost. **While any question you asked is still
unanswered: never call `worker_done`, and never call `worker-stop` or
`worker-abandon` on yourself** — only the coordinator may end this Dispatch,
and it will, once it sees you park (below).

## The loop

1. **Record when you first asked.** Note the wall-clock instant of your
   *first* `orca orchestration ask --question ...` call for this decision.
   Every subsequent re-arm measures its remaining budget from that original
   instant, never from the most recent timeout.
2. **Grace period: 30 minutes**, unless the dispatch prompt or the work item
   names a different value.
3. **On timeout,** compute elapsed time since the original ask.
   - Elapsed < grace period: re-arm with
     ```
     orca orchestration ask --resume <original message_id> \
       --timeout-ms <remaining budget in ms, capped at 600000> \
       --from <this session's --from> --dispatch-capability <this session's --dispatch-capability>
     ```
     This blocks again — it is not a poll. Repeat this step on every
     subsequent timeout, shrinking the remaining budget each time, until
     either an answer arrives or the grace period is exhausted.
   - Elapsed >= grace period: go to **Checkpoint and park**, below, instead
     of giving up and calling `worker_done --outcome failed`.

## Checkpoint and park

1. **Draft the checkpoint**, matching this exact template
   (`packages/work-tracker-okf/src/work_tracker_okf/assets/checkpoint.md`)
   byte-for-byte in structure — every frontmatter key and every section
   heading below is required:

   ```markdown
   ---
   title: 'Checkpoint: <item title> (<phase>)'
   item: <canonical item path>
   decision: pending
   phase: <phase this stage is running>
   dispatch_key: <this dispatch's key, from your dispatch preamble>
   branch: <branch you are on>
   worktree: <your worktree's absolute path>
   base: <merge target / base branch>
   head: <commit sha your worktree is at, or "none">
   created: <ISO-8601 instant, e.g. 2026-09-15T14:30:00Z>
   ---

   ## Completed work

   <What you finished before parking.>

   ## Remaining actions

   <What resume must still do, in order.>

   ## Question

   <The exact question you asked, verbatim, including its options.>

   ## Placement

   <worktree, branch, base, head, and whether the tree was clean.>

   ## Validation evidence

   <Commands you ran and their results, or `none run` with a reason.>
   ```

   Leave `decision: pending` — the CLI in the next step stamps it with the
   real decision id. Write this draft to a scratch path inside your
   worktree (for example `/tmp/checkpoint-draft.md`); its permanent home is
   computed and written by that CLI call, not by you.

2. **File the park:**
   ```
   gw work decision add <canonical item path> \
     --question "<the exact question you asked>" \
     --hold park --phase <phase this stage is running> \
     --checkpoint <path to your draft from step 1> \
     --json
   ```
   A refusal (`hold-checkpoint`, `checkpoint-invalid`, `hold-phase-mismatch`,
   `checkpoint-exists`) means something about the draft or the item's current
   state is wrong. Read the refusal's `detail`, fix exactly that, and retry —
   never retry blindly, and never drop `--checkpoint` to make a refusal go
   away.
3. **Report and stop.** Send one final status message so the coordinator's
   transcript records why you stopped, then end your turn:
   ```
   orca orchestration send --from <this session's --from> \
     --dispatch-capability <this session's --dispatch-capability> \
     --type status --subject "Parked: grace period exceeded" \
     --body "Filed park hold <decision id from step 2's JSON> on <item path> at phase <phase>; checkpoint at <checkpoint resource from step 2's JSON>." \
     --task-id <this session's --task-id>
   ```
   Do not call `worker_done`. Do not call `worker-stop` or `worker-abandon`.
   Do not send any further messages or make any further changes. Your
   Dispatch stays live in Orca's model until the coordinator observes the
   fresh park hold and stops it — that is the coordinator's authority alone.

## Resume

A parked item is resumed by the coordinator once a human answers the hold
(`gw work decision answer <owner-path> D-nnn --answer ...`) — see
`auto-drive/SKILL.md`'s park-resume handling. You never resume yourself: a
**new** Dispatch attempt against the same Task is what gets launched, and it
receives the checkpoint's recorded context plus the answer folded straight
into its prompt, so it does not need to re-ask.
