---
name: auto-drive
description: Use when driving a work item's full pipeline unattended via Orca-supervised workers — an epic's entire dependency graph or a lone item's phase sequence — instead of walking one /gw:workflow stage at a time by hand. Runs a stateless plan/act/wait coordinator loop over `gw work orchestrate` and the Orca orchestration CLI — binds a Run, dispatches ready stages as supervised workers, handles the design (attend) and finish (relay) human gates, processes worker_done/question/escalation, and resumes cleanly after a crash or restart by re-deriving everything from Orca + the vault.
---

# Auto-Drive Coordinator

Drive a work item's pipeline end-to-end by dispatching each ready stage as a
supervised Orca worker and looping until the item is terminal. The CLI owns
every decision (`gw work orchestrate` computes readiness, worktree, agent, model,
and the full worker prompt) — this skill only relays: it never picks a
worktree, an agent, a model, or a stage on its own.

**Announce at start:** "I'm using the auto-drive skill to run the coordinator
loop for `<work-path>`."

This session **is** the coordinator — a human-attended session, not itself a
dispatched worker. Wherever this skill says "ask the user," that means the
native `AskUserQuestion` tool, talking to the person running this session.
`orca orchestration ask`/`send` is a *different* channel: it carries messages
a dispatched *worker* sends up to this coordinator — it is never how this
skill talks to its own human. The channel back down is asymmetric by message
type: a `question` (raised via `ask`) comes up on a `dispatch:…` sender
handle and goes back down through `reply --id` (see §4.3); an `escalation`
(raised via `send`) comes up on a bare terminal handle and goes back down
through `send --to dispatch:<id>` instead (see §4.4) — `reply` on that
handle reaches a passive mailbox, not the worker.

`gw` being on PATH doesn't prove it's this repo's build — a stale entry point can
own the name. Verify identity, not presence: `gw util describe-surface --json
>/dev/null 2>&1 || echo "gw is not graph-works-cli — use: uv run --package
graph-works-cli gw …"`.

## 0. Preconditions

Check once at the start of the session. Any failure stops with a plain
explanation — no degraded mode, no partial loop:

1. `orca status` — must show a reachable runtime. If unreachable: stop and
   tell the user to run `orca open`, then retry.
2. `orca orchestration run-current --json` — probes that Orca's
   orchestration layer is available on this install. An "unknown command" or
   feature-disabled error means the Experimental orchestration feature isn't
   enabled; stop and say so (do not try to work around it).
3. `gw` resolvable **as this repo's build**: `gw util describe-surface --json
   >/dev/null 2>&1` exits 0. If it doesn't — absent from PATH, or present but
   naming a different CLI — fall back to `uv run --package graph-works-cli gw
   --help` from the workspace's repo root.
4. Workspace resolves: `GRAPH_WORKS_DIR` is set, or discovery from cwd
   succeeds. `gw work status` fails loudly if not — treat that failure as a
   precondition failure, not a mid-loop error.

Resolve the repo selector once here, too — it's static for the whole
session: `orca repo list --json`, find the entry whose path matches the
resolved repo, and remember its selector for `--repo` in dispatch mechanics
(§3). This is environment lookup, not orchestration state, so caching it for
the session does not violate the "never trust session memory" rule below —
that rule is about *Run/task* state, not static repo identity.

## 1. Run bind

The Run's objective string is the stable join key for this path:
`auto-drive:<work-path>`. Nothing else maps path → Run — this lookup **is** the
entire resume mechanism. Re-running `/gw:auto-drive <work-path>` always
re-derives the Run this way; there is no separate `--resume` flag.

1. `orca orchestration run-list --json` and scan for an entry whose
   `objective` exactly equals `auto-drive:<work-path>`.
2. Found → `orca orchestration run-use --id <run_id>`.
3. Not found → `orca orchestration run-create --objective "auto-drive:<work-path>"`
   (this also binds this terminal to the new Run).

Every command in the rest of this skill passes `--run <run_id>` explicitly —
don't rely on implicit terminal binding once other dispatches may exist.

## 2. The cycle

Loop until Wrap-up (§5) or the user says stop. Each iteration is
self-contained — never trust anything from a previous iteration in this same
session; re-derive from Orca + the vault every time. This is what makes
crash/compaction resume the same code path as a normal cycle.

### 2.1 Derive live keys

1. `orca orchestration task-list --run <run_id> --json`. Every task's
   `--task-title` was set to a dispatch key (`<work-path>#<phase>`) at creation
   (§3) — this task mirror is the dedupe ledger for the whole loop and the
   §2.6 dispatch-diff source.
2. `orca orchestration worker-list --run <run_id> --json` — one call for the
   whole Run, returning `workers[]` of `dispatchId`, `taskId`, `runId`,
   `workerState`, `dispatchStatus`, `agentTerminalHandle`, `terminalState`,
   `resource{…}`. This is the task→dispatch join. Task records carry **no**
   dispatch-id field of their own, so a per-task `worker-show --dispatch
   <id>` loop cannot even be constructed — this call replaces that loop.
3. Save those full responses and run the shipped restart classifier:

   ```
   python3 references/launch-worker.py classify-restart \
     --tasks <task-list-json> --workers <worker-list-json>
   ```

   It joins by `taskId` and uses the last worker row for retries. Its actions
   are durable-state decisions, not guesses from terminal presence:
   - `live`: `workerState` is `ready` or `running` and dispatch status is not
     `outcome_unknown`.
   - `settled`: `workerState` is `succeeded`, the full untruncated task spec
     has a valid frozen envelope whose `dispatch_key` matches `task_title`,
     and `worker-show --dispatch <dispatch_id> --json` supplies matching
     `result.worker.startOptions.launch.requested` and `.effective` proof.
     The classifier performs this read for each succeeded worker, comparing
     the agent and every explicit model/effort choice against the envelope.
   - `deliberate-skip`: task status is `blocked` and either no worker exists or
     the latest attempt is positively terminal `failed`/`stopped`. The Skip
     branch in §4.1 writes the durable marker after that terminal attempt.
   - `recovery-inspection`: every other no-worker task, including a crash after
     task reservation but before start; unmarked `failed`/`stopped` and unknown
     worker states; `outcome_unknown` in either worker or dispatch state;
     and succeeded workers with missing, malformed, truncated or mismatched
     envelopes/receipts, or an unsuccessful worker-show read.
     Live and unknown evidence takes precedence over a task's `blocked` status,
     so a stale/partial skip update cannot hide an active or ambiguous attempt.

   Recovery inspection retains and prints the known task and dispatch IDs.
   Read the full task spec and the failed/ambiguous start recovery evidence.
   With no worker row there may be no dispatch ID to inspect; that absence is
   unresolved reservation state, never proof of a skip or authority to start a
   replacement. Follow Orca's recovery verdict, or stop when no verdict can
   establish whether resources were allocated. A missing terminal is normal
   and never changes this classification.
4. Build the `--live` key list for §2.2 from every task classified live.
   The classifier reads worker-show for successful settlement proof. Other
   worker-show calls are targeted liveness/recovery follow-ups, including
   `result.dispatch.lastHeartbeatAt` used by §3 step 5's probe.

### 2.2 Plan

`gw work orchestrate <work-path> --live <key,...> --json` (workspace resolves via
`GRAPH_WORKS_DIR`; omit `--live` on the very first plan call of a fresh
Run — there's nothing live yet). The result:

- `terminal` (bool), `max_parallel` / `slots_free` (ints), and `live` (the
  echoed input list).
- `dispatches[]` — each entry: `key` (`<work-path>#<phase>`), `path`, `phase`,
  `kind`, `effort`, `skill`, `mode` (`autonomous` | `attend` | `relay`),
  `agent`, `model` (`null` = selected agent default, omit `--model`),
  `reasoning_effort`, `provenance` (the winning origin and reason for every
  profile field),
  `worktree` (`action`: `reuse` | `fork-child` | `create-top-level` | `main`,
  `path`, `branch`, `base_branch`, `exists`), `merge_target`, `prompt`.
- `advances[]` — each: `path`, `reason`, `worktree`/`branch` (the epic's
  already-known worktree, when one exists — `null` otherwise, e.g. before any
  worker has ever been dispatched for this epic).
- `blocked[]` — each: `path`, `kind` (one of exactly `deps`, `capacity`,
  `affects-overlap`, `effort-required`, `decisions`, `human`,
  `relay-untailed`, `worktree-pending`, `worktree-unsupported`,
  `worktree-unprovable`, `worktree-ambiguous`, `invalid`),
  `reason`. The closed vocabulary is `BLOCKED_KINDS` in
  `graph_works_core.orchestrate.commands` — if a `kind` arrives that isn't in
  this list, treat it as this skill being out of date, print it, and act on
  nothing.
- `warnings[]` — plain strings (e.g. a stale `--live` key matching nothing, or
  a malformed decisions-ledger entry). Print these as notes; they are not
  blockers.
- `decisions` — the ledger resolved from the nearest owning parent:
  `owner_path` (str or `null`), `ledger_path` (absolute path or `null`),
  `open[]` / `assumed[]`
  (each entry: `id` (the full `D-nnn` string used below), `number` (its bare
  integer), `question`, `status`, `affects[]`, `decided`, `supersedes`,
  `prose`), and `counts` (whole-ledger
  rollup by status plus `invalid` and `total`). Scope is the **whole owning
  ledger**, not just the subtree you asked to plan. `counts`
  is `{}` — an empty dict with no keys — when `decisions.owner_path` is `null`,
  but a fully-zeroed six-key dict when the epic exists and only its ledger
  file is missing. Read it with `.get()`; indexing `counts["open"]`
  directly will fail in the first case.

### 2.3 Terminal?

`terminal: true` → go to Wrap-up (§5), including its fresh launch-proof gate,
then stop looping. A terminal plan does not establish successful launch proof.
Nothing else in this cycle runs.

### 2.4 Advances

For every entry in `advances[]`: `gw work advance <path from entry>`, adding
`--worktree <entry.worktree> --branch <entry.branch>` whenever the entry
carries them (non-`null`). **This is applied from the coordinator's own
checkout, not from inside any worktree** — without the explicit flags, the
item's worktree/branch provenance and git-derived facts (`phase_started_commit`,
execute-results) silently go unstamped or get recorded against the wrong
checkout. Omit the flags only when the entry's `worktree`/`branch` are `null`
(no worker has been dispatched for this epic yet, so there's nothing to
pass). If `advances[]` was non-empty, the plan you just read is now stale —
restart the cycle at §2.1 (skip §2.5–2.7 this iteration; don't act on a plan
you know is out of date).

### 2.5 Blockers

- **`effort-required`**: ask the user — via `AskUserQuestion`, this is the
  coordinator's own human, not a worker relay — to size the item
  (xtra-small / small / medium / large / xtra-large). Run
  `gw work advance <work-path> --effort <value>`, then restart the cycle at §2.1.
- **Every other kind** (`deps`, `capacity`, `affects-overlap`, `decisions`,
  `human`, `relay-untailed`, `worktree-pending`, `worktree-unsupported`,
  `worktree-unprovable`, `worktree-ambiguous`, `invalid`): print one line each
  (`blocked <work-path> (<kind>): <reason>`) and take no action. `capacity` and
  `worktree-pending` resolve themselves next cycle as slots/worktrees free
  up; `deps`, `affects-overlap`, `human`, and `invalid` need a human decision
  outside this loop; `decisions` is a third case — it neither self-resolves
  nor needs a decision outside this loop, it's resolved *inside* this loop
  by the coordinator's own CLI call, but only once the user tells you to —
  see §2.5.1. `relay-untailed` and `worktree-unsupported` are a fourth: both
  are configuration faults that will recur every cycle until someone edits
  something outside this loop, so report them once and don't wait on them.
  `relay-untailed` means a relay-mode variant has no `prompt_tail`, so a
  dispatched worker would drop into an interactive menu unattended — the fix
  is a matching rule with `prompt_tail` in the shared dispatch document named
  by `workflow.dispatch_rules`, or its local sibling (`dispatch.local.yaml`
  for the default path), followed by `gw config sync`. The reason string names
  the variant. `worktree-unsupported` means the plan
  needs a worktree provisioned and the target backend can't; `gw work
  orchestrate` passes `provisions_worktrees=True` today and exposes no flag
  to change it, so this kind should not reach this skill through the CLI —
  if one arrives, say so rather than working around it. Note:
  `affects-overlap`
  fires both on a real overlap
  *and* on an item with an empty `affects` list (declaring `affects` is what
  unlocks parallel dispatch) — don't report an empty-`affects` block to the
  user as "another dispatch is using this," the reason string already says
  which case it is. `decisions` means an open ledger entry is holding the
  item's re-dispatch; the entry itself is named in `open_decisions[]` below,
  and answering it clears the block on the next cycle.

- **Decisions**: after the blocker lines, print one line per entry in
  `open_decisions[]`, then one per entry in `assumed_decisions[]` — both from
  §2.2's plan JSON already in hand, no extra `gw` call:

  ```
  decision D-nnn (open, affects: <path,...>) <question> — needs a human answer
  decision D-nnn (assumed, affects: <path,...>) <question> — if wrong: <text>
  ```

  Render `affects: (none)` when the entry's `affects[]` is empty (permitted
  — `--affects` defaults to empty) rather than leaving a dangling
  "affects: ". The `if wrong:` text is the entry's `prose` sliced to its
  `**If wrong:**` block: the text after that bold label up to the next blank
  line or the next bold label, whichever comes first; omit the `if wrong:`
  clause entirely when the block isn't present. Then take no action — this is
  informational, exactly like the blocker lines. Print them **every** cycle
  they are non-empty; do not track "already shown" or suppress repeats. A
  printed line costs nothing to skip past, and suppressing it risks a human
  losing track of an assumed decision that scrolled off screen hours earlier
  in a long-running epic.

  Print nothing at all when both lists are empty, and note that
  `decisions.owner_path: null` (a lone item with no owning parent) is normal,
  not a fault — ledgers are epic-owned.

### 2.5.1 Decision confirm / overturn (human-initiated)

**This step is non-blocking.** If no confirm/overturn instruction is already
waiting in this session's input, do nothing here and continue straight to
§2.6 — never pause the cycle waiting for one. §2.5.1 fires only when the
user has already typed something; it is not a step the cycle must complete
before moving on.

Printing in §2.5 is passive. **Never** force an `AskUserQuestion` for a
surfaced decision, never treat silence as confirmation, and never flip an
`assumed` entry to `answered` on your own initiative — the coordinator does
not guess answers.

When the user, having read those lines, tells you in this session (free text
— "confirm D-nnn", "overturn D-nnn, the real boundary is X, file a follow-up
called Y") to act, run the CLI call **yourself**. Do not tell the user to go
run `gw` in their own terminal. The trigger is external human free text, not
anything read off the plan JSON — but the shape matches §2.5's
`effort-required` branch in one respect: no worker in the loop, the
coordinator executing the command directly. `attend`/`relay` modes exist for
*workers* that cannot reach a human directly; a ledger edit has no worker in
it. Collect any missing fields free-form, as §4.4 already does for
escalations — not as a forced multiple-choice prompt.

`<owner-path>` below is `decisions.owner_path` from §2.2's plan JSON; you
already have it, so never re-resolve it.

**Confirm:**

```
gw work decision answer <owner-path> D-nnn --answer "<the answer text>" \
    [--rationale "..."] --json
```

**Overturn:**

```
gw work decision overturn <owner-path> D-nnn --answer "<the new answer>" \
    --follow-up-title "<the follow-up's title>" \
    [--follow-up-affects a,b] [--follow-up-kind tech-debt] --json
```

`--answer` and `--follow-up-title` come from different parts of the user's
message — the new answer versus what to call the filed follow-up item. Don't
paste the whole utterance into both; that produces a nonsensical title.
`--follow-up-title` does double duty: besides naming the filed item, it
becomes the `question` of the superseding ledger entry, so pick a string
that reads well both as a work-item title and as a decision question.

After either call:

1. Summarize the JSON result instead of quoting an `[ok]` line — `--json`
   prints raw JSON, and the `[ok]` lines exist only in the CLI's non-JSON
   branch. Name the fields that are actually there: `owner_path`, `requested_path`, the
   entry's `id` and `status`, `superseded`, and — for overturn —
   `follow_up.path` and `follow_up.page_path`. If the result carries
   `warnings[]`, print them — overturn deliberately keeps the ledger edit
   even when filing the follow-up fails, and the warning names the id that
   still needs one.
2. **For overturn, say plainly that the follow-up is not part of this Run.**
   It is filed without a parent or dependencies — a root peer, not
   one of its children — so it will never appear in this root's `dispatches[]`
   or `blocked[]`. Say so: "filed `<follow-up-path>` — it's a peer item, not
   wired into this run; drive it separately, e.g. a fresh
   `/gw:auto-drive <follow-up-path>`." Otherwise it reads as having
   silently vanished.
3. **Restart the cycle at §2.1** — the same rule §2.4 applies after
   `advances[]`. The ledger just changed, and the routing layer recomputes its
   open-decision gate from the ledger on every planning pass, so an item that
   was `blocked` on the now-settled entry may be dispatchable on the very next
   plan call. Never act on a plan you already know is stale.

### 2.6 Dispatch diff

Dispatch only `dispatches[]` entries whose `key` has **no existing task** in
§2.1's `task-list` output — the task mirror is the sole dedupe ledger. A key
with a task is live, settled, or an intentional skip (§4.1); in every case,
leave it alone. For each undispatched entry, run Dispatch mechanics (§3).

### 2.7 Wait

```
orca orchestration check --run <run_id> --wait \
  --types worker_done,escalation,question --timeout-ms 600000 --json
```

- **On delivery:** process **every** message in the batch (§4) before
  acking. Then acknowledge with the delivery id from the response:
  `orca orchestration check --run <run_id> --ack <delivery_id>`, reading the
  id from `result.deliveryId` (a bound Run replays the same delivery until
  acked — don't ack before every message in the batch is handled). If a
  future runtime version reports the id under a different key, read it off
  the first real `check --wait --json` response rather than trusting this
  name blindly. Restart the cycle at §2.1.
- **stderr note:** `--wait` emits JSON keepalive lines to **stderr** every
  15s so the caller can tell the process is alive — stdout carries only the
  real response. Don't merge streams (`2>&1`) when capturing this call; if a
  merge is unavoidable, filter with `jq "select(._keepalive|not)"`. The
  `_heartbeat` alias inside this keepalive stream is unrelated to worker
  heartbeat messages and to `lastHeartbeatAt` (§3 step 5) — three
  unrelated things share the name.
- **On timeout with nothing delivered:**
  `orca orchestration worker-show --dispatch <id> --json` for every
  still-live dispatch. Any `failed`/`stopped`/unreachable → failure flow
  (§4.2), then restart the cycle at §2.1. Still `ready`/`running` → before
  looping back into another `--wait`, run §3 step 5's full probe on each
  still-live dispatch — the same ordered probe as at initial dispatch, with
  the same heartbeat veto decisive: a dispatch that has *ever* heartbeat is
  never nudged here either, no matter how long it has looked idle. This is
  the riskiest point to nudge — a dialog may legitimately be up by now (an
  `attend` dispatch waiting on the human looks exactly like a stuck one) —
  which is why the probe's transcript check, not elapsed idle time, has to
  be what decides. An unsent prompt reports `running` forever, so without
  this the loop has no exit — it waits ten minutes at a time on a worker
  that was never asked anything.

## 3. Dispatch mechanics

For each planned-but-undispatched entry from §2.6, first print its selected
agent, model, and reasoning effort (`default` for a null model or effort), plus
the corresponding winning entries from `provenance`. Permissions come from the
selected agent's existing settings; dispatch rules do not select or promise a
permission mode.

The executable recipe for this section is
`references/launch-worker.py`. It transports a resolved dispatch and never
matches or resolves rules. Treat model IDs and effort strings as opaque.

1. Build the placement argv list exactly once from the worktree mapping below
   and save it as a JSON list. Save the complete `dispatches[]` entry as JSON,
   then encode the immutable task spec:

   ```
   python3 references/launch-worker.py encode \
     --dispatch <dispatch-json> --placement <placement-json> > <spec-file>
   ```

   This writes `GW_LAUNCH_V1 ` plus compact version-1 JSON on the first line,
   then the unchanged prompt. The envelope freezes the dispatch key, agent,
   model, reasoning effort, and exact placement argv before any start. Effort
   with a null model is refused; do not repair it locally.

2. ```
   python3 references/launch-worker.py create --spec <spec-file> \
     --run <run_id> --task-title "<key>" \
     --display-name "<work-path> · <phase>"
   ```
   Capture `task_id` from the forwarded JSON. The helper passes the spec as one
   exact argument, including trailing newlines. Never use `task-list --brief`;
   truncation destroys the durable launch proof.
3. Launch from that same spec instead of rebuilding argv:

   ```
   python3 references/launch-worker.py launch --spec <spec-file> \
     --task <task_id> --dispatch-key <key> --run <run_id>
   ```

   The recipe passes the planned agent, optional model/effort, and each
   placement value as a distinct argv element. It checks the returned
   `launch.requested` and `launch.effective` blocks against every explicit
   choice (`reasoning_effort` maps to receipt `effort`). Missing or mismatched
   proof enters recovery and never authorizes another worker.

   Build placement argv as follows:
   - Worktree action `reuse` → `--worktree path:<worktree.path>` only — no
     creation flags (`--name`/`--repo`/`--base-branch`), which the CLI
     rejects for an existing worktree.
   - Worktree action `fork-child` → `--worktree new-child --name <worktree.branch> --base-branch <worktree.base_branch>`.
     `worker-start` has no `--parent-worktree` flag: `new-child` infers its
     parent from this coordinator's own worktree context, which is the epic
     worktree. Step 3's assertion is what verifies where it landed.
   - Worktree action `create-top-level` → `--worktree new-top-level --name <worktree.branch> --base-branch <worktree.base_branch> --repo <repo selector resolved in §0>`.
   - Worktree action `main` → `--worktree path:<worktree.path>`, same mapping
     as `reuse` — the main checkout already exists, there is nothing to
     create. Its `dispatches[].prompt` carries an extra line telling the
     worker to pass `--worktree`/`--branch` explicitly on its own
     `gw work advance` calls, since it is running directly in the main
     checkout and cwd-based worktree inference cannot detect that case.
     `_resolve_worktree` in
     `packages/graph-works-core/src/graph_works_core/orchestrate/commands.py`
     chooses `main` over `reuse` whenever the resolved worktree equals the
     code repo's own checkout, and `_prompt` in the same module appends the
     extra "record the worktree as … and the branch as …" line the worker
     needs.
   - `--name` is a *display* name, not a branch name. Orca derives the git
     branch itself as `<host git user slug>/slugify(name)` —
     unconditionally, for every `--name`, on every creation — and
     `worker-start`, `orca worktree create` and `orca worktree set` expose
     no branch control at all. A slash-containing `worktree.branch` is
     therefore never the branch Orca creates. Do not compare the two; step 4
     below defines what is verified instead.
   - A non-zero exit, or a result reporting `failed`/`outcome_unknown`, is a
     failed dispatch: go straight to the failure flow (§4.2) — surface the
     JSON's `stage`/`failedStage`/`recovery` hints to the user, don't
     silently retry.
4. **Read back where the dispatch actually landed.** Do this *before* the
   submission probe: nudging a worker that is sitting in the wrong worktree
   only makes it produce work nobody will look for. A failed placement never
   reaches step 5.

   **Where the truth comes from** — prefer the response already in hand:

   1. `worker-start --json`'s `effects[]`. A worktree effect reads
      `{kind: "worktree", action: "created_child" | …, id: "<repoId>::<path>"}`.
      When that entry is present the path is the segment after `::`, and
      **no extra Orca call is made.**
   2. Otherwise `orca orchestration worker-show --dispatch <dispatch_id> --json`
      → `result.worker.worktreeId` (the full `<repoId>::<path>` value).
   3. Then `orca worktree show --worktree id:<worktreeId> --json` for the
      fields the assertion needs: `path`, `branch`, `displayName`,
      `parentWorktreeId`, `isMainWorktree`.

   `branch` comes back fully qualified (`refs/heads/<name>`). **Strip
   `refs/heads/` before printing it** — a raw paste reads as a different
   name than the one the human will see in `git branch`.

   **The assertion — never a branch-name comparison.** The upstream slug
   transform is undocumented and unversioned; an assertion built on it would
   keep passing against a formula that no longer describes reality the day
   Orca changes it. Nothing below consults a branch name.

   | Planned `worktree.action` | Assertion |
   |---|---|
   | `reuse`, `main` | The observed `path` equals the planned `worktree.path`, compared after resolving symlinks on both sides. No worktree was created, so nothing else is checked. |
   | `fork-child` | `parentWorktreeId` is non-null; the observed `path` is not one already claimed by another dispatch this Run; and `worktree.base_branch` resolves in the observed worktree and is an ancestor of its HEAD — one `git -C <observed path> merge-base --is-ancestor <base_branch> HEAD`. |
   | `create-top-level` | `isMainWorktree` is false; the observed `path` is unclaimed this Run; and the same base-branch ancestry check. |

   The ancestry check is what replaces the branch-name comparison: it asks
   the question the name was only ever a proxy for — *is this worker forked
   off the work it was supposed to be forked off* — and it asks git, which
   cannot drift.

   `displayName` preserves the requested `--name` verbatim and is the only
   place the plan's intent survives on the Orca side. **Report it; assert
   nothing on it** — Orca may truncate or uniquify a display name, and doing
   so does not mean the placement is wrong.

   **Always print the placement line**, mismatch or not, for every dispatch:

   ```
   dispatched <key> -> <observed path> on <observed branch>
   ```

   The coordinator plans a branch name but never sees the one Orca actually
   creates, so this line is its only record. It means a human reading the
   scrollback hours later can find the branch a stage's work is on without
   reconstructing anything.

   **Do not substitute the observed values downstream.** §2.4's
   `gw work advance --worktree/--branch` and §5's merge-back summary keep
   using the planner's values; reconciling the two is the planner lane's
   work, not this read-back's.

   **On assertion failure, halt into §4.2.** Print the mismatch loudly:

   ```
   PLACEMENT MISMATCH <key>: planned <action> at <planned path>,
     actual <observed path> on <observed branch>
   ```

   then go directly to the failure question — the same three options
   (*retry* / *skip this item* / *stop the run*) every other dead-or-wrong
   dispatch gets — and **skip step 5's submission probe entirely** for this
   dispatch. This is a halt, not a raise: it routes into an existing
   human-decision path, so a false positive costs one question, not a lost
   run.

   **A `-N` suffixed duplicate worktree is a note, not a halt.** Dispatching
   a stage for an item whose earlier worktree still exists makes Orca create
   a suffixed duplicate (`…-2`) rather than reusing or refusing. Such a
   worktree passes the assertion — it is new, correctly based, and the work
   in it is real — and it is findable, because the placement line printed
   its actual path. Report it and continue:

   ```
   note <key>: worktree name uniquified by Orca (requested "<displayName>",
     landed at <path>)
   ```

   Halting here would block legitimate dispatches over a cosmetic surprise.

5. **Confirm the prompt was actually submitted when a terminal exists.** A
   terminal is optional. Worker-read/show, lifecycle state, and orchestration
   messages are the normal progress path for every agent. If worker-show has
   no real terminal object or terminal handle, do not run terminal commands
   and do not infer failure.

   When a real terminal handle exists, `worker-start` exposes no
   flag that guarantees submission, so this probe runs on every dispatch that
   has a proven terminal handle.

   A zero exit means the terminal was created and the prompt was typed into
   it, **not** that it was submitted. `worker-start` leaves the prompt
   sitting unsent in the input box, and nothing downstream can tell that
   apart from a thinking worker: the dispatch reports `running`, §2.7 waits
   its full timeout, `worker-show` still says `running`, and the loop goes
   round again. This is the default case, not an intermittent one: **assume
   unsent until proven otherwise.**

   Allow a short settle interval before probing — transcript messages
   appear within seconds of a real submission, but heartbeats take 46–60s
   to show up even on a healthy worker, so an immediate probe can only ever
   be answered by signal 2 below.

   Run this ordered probe. The first decisive answer wins — stop at it:

   1. `orca orchestration worker-show --dispatch <dispatch_id> --json`
      → `result.dispatch.lastHeartbeatAt` non-null ⇒ **submitted. Never
      nudge.** This is a hard veto: a worker that never submitted cannot
      have heartbeat, so any heartbeat, however late, proves submission —
      regardless of how idle the terminal looks.
   2. `orca orchestration worker-read --dispatch <dispatch_id> --limit 5 --json`
      → `result.transcript.messages` non-empty ⇒ **submitted. Never nudge.**
      A transcript is structured JSON, immune to the interleaved redraws
      that make `terminal read` unreadable mid-render. This is also the
      dialog-safety guarantee: putting a dialog on screen is itself agent
      activity and appears in the transcript, so an **empty** transcript is
      what proves no dialog can be up — this is what makes the nudge below
      safe, not a guess about idle-looking terminals.
   3. `result.source == "terminal"` (with `fallbackReason` set) ⇒ compare
      against sibling dispatches in the same run. If other workers are
      producing heartbeats and transcripts and this one has produced
      neither since it started, that comparison is decisive on its own —
      treat it as unsent and proceed to the nudge below, regardless of what
      `source` reports. Only fall back to reporting and letting the human
      decide when there are no healthy siblings to compare against (e.g.
      this is the only live dispatch this cycle). Two rules bound this
      branch: the heartbeat veto (case 1) is absolute — a dispatch that has
      ever heartbeat is never nudged, however idle it looks — and "never
      nudge a worker you have not probed" (case 4, below) applies before
      any nudge.
   4. Heartbeat null **and** transcript empty **and** `result.source ==
      "transcript"` ⇒ treat as unsent ⇒ nudge **once**:

      ```
      orca terminal send --terminal <agent_terminal_handle> --text "" --enter --json
      ```

      **Do not nudge a worker you have not probed.** A bare Enter into a
      live agent answers whatever is on screen with its highlighted
      default — and `mode: attend` dispatches exist to ask the human
      questions, so they are simultaneously the likeliest to look idle and
      the costliest to nudge blind.
   5. Re-run step 2. Non-empty transcript ⇒ recovered, continue normally.
      Two failed nudges → failure flow (§4.2), same three options.

6. **Attend dispatches only** (`mode: attend` — the design stage), after a
   successful start:
   - Set the worktree to `in-review`. Include a join instruction only when a
     real terminal handle exists; otherwise report the dispatch ID and use
     worker-read/show plus orchestration messages.
   - Remember this dispatch's key as attend-pending for this Run, so its
     `worker_done` (§4.1) triggers the flip-back to `in-progress`.
   - This is the one piece of session-local state in this skill — if the session crashes before `worker_done` arrives, flip the worktree back manually with `orca worktree set --worktree <selector> --workspace-status in-progress` if it looks stuck at `in-review` after a resume.
7. `orca orchestration task-update --id <task_id> --status dispatched --run <run_id>`
   so the next cycle's `task-list` (§2.1) reflects it as an existing task.
   (Valid `--status` values are `pending, ready, dispatched, completed,
   failed, blocked` — `in_progress` is not one of them; `dispatched` is the
   closest fit for "handed to a worker".)

## 4. Delivery processing

Handle every message in the `check --wait` batch (§2.7) — one at a time —
before acking.

### 4.1 `worker_done`

- **`outcome: succeeded`**: refresh the full task-list and worker-list and run
  §2.1's classifier before acknowledging the delivery. Require its `settled`
  result for this exact task/dispatch pair: this validates the full frozen
  envelope and key plus durable requested/effective proof from worker-show.
  Missing or mismatched proof enters recovery with both IDs; do not ack or
  release it. A valid `worker_done` already settles its Task and Dispatch;
  never issue `task-update completed`. Then run
  `orca orchestration worker-release --dispatch <dispatch_id>` (no `--run`
  flag — `worker-release` takes only `--dispatch` and `--retry-request`)
  → if this key was attend-pending (§3), flip the card back:
  `orca worktree set --worktree <selector> --workspace-status in-progress`
  → nothing else; the next cycle's plan (§2.2) picks up the new state
  naturally.
- **`outcome: failed`**: run the **failure question**, below.

### Failure question

Used from two places: `worker_done --outcome failed` (above) and a dead
worker discovered outside any `worker_done` message (§2.1 or §2.7's
wait-timeout `worker-show`) — the design's no-auto-retry policy applies to
both identically, so it's specified once here, not duplicated.

One `AskUserQuestion` with exactly three options — *retry* / *skip this
item* / *stop the run*:

- **Retry**: retry only when Orca's failure response and recovery inspection
  explicitly authorize it. Read the existing task from full
  `task-list --run <run_id> --json`, save its untruncated `spec`, and validate
  the envelope. Missing, malformed, truncated, or wrong-key state blocks
  recovery. Never consult edited dispatch configuration or a fresh plan for
  this task. Preserve the original requested placement and observed allocated
  resource evidence; follow Orca's recovery verdict so an allocated worktree
  is reused rather than duplicated. Then run:

  ```
  python3 references/launch-worker.py launch --spec <saved-task-spec> \
    --task <task_id> --dispatch-key <task-title> --run <run_id> \
    --retry-of <dispatch_id> \
    --recovery-placement <recovery-approved-placement-json>
  ```

  The frozen envelope retains the originally requested placement; the explicit
  recovery placement is the argv Orca authorized after accounting for observed
  allocated resources (for example, `path:<allocated path>` instead of a second
  `new-child`). The recipe repeats the frozen agent/model/effort and verifies
  the new requested/effective receipt. `--retry-of` alone does not inherit
  those values. Timeout, absent output, missing terminal, or ambiguous start never
  authorizes retry; preserve task/dispatch identities and follow recovery.
  An explicit reroute is a new deliberate dispatch decision and task identity.
- **Skip**: `orca orchestration task-update --id <task_id> --status blocked
  --run <run_id>`. The durable `blocked` status distinguishes this deliberate
  choice from a task reserved before a crash but never started. The task stays
  in the Run, so §2.6 never re-proposes the key.
- **Stop the run**: exit the loop. Report run state (what's done, what's
  live, what's blocked). For each still-live dispatch, ask (plain text) if
  the user wants `orca orchestration worker-stop --dispatch <id>`,
  then stop.

### 4.2 Failure flow (dead worker found outside a `worker_done` message)

Identical to the `outcome: failed` branch above — run the failure question.
Triggered from §2.1's live-derivation or §2.7's wait-timeout `worker-show`.

### 4.3 `question` (finish-stage relay)

A worker in `relay` mode (the finish stage) sends this via its own
`orca orchestration ask` when it needs the merge/PR/hold/discard decision —
this coordinator only relays it, it does not interpret the question —
deciding what the options mean is the worker's job.

1. Mirror the message's question text and options to the user as one
   `AskUserQuestion` in this session.
2. `orca orchestration reply --id <message_id> --body "<the user's answer>" --run <run_id>`.
   This works here because a `question` is sent via `orca orchestration ask`,
   whose sender handle is `dispatch:…` — `reply` addresses whatever handle
   the original message was sent *from*, and Orca relays a reply on a
   `dispatch:…` handle into the live worker session, unblocking its
   blocking `ask` call.
3. A typed-`discard` confirmation some finish flows require is just a
   second question/reply round-trip initiated by the worker — handle it the
   same way, no special-casing here.

### 4.4 `escalation`

Surface the message body to the user and ask, free-form (not a forced
multiple-choice `AskUserQuestion`), how to proceed. If the user wants to
send something back to the worker, **do not use `reply --id`** — an
escalation is sent via `orca orchestration send` from a bare terminal handle
(`--from term_<uuid>`, the form every dispatched worker's preamble hands
it), and `reply` addresses that handle literally: a bare terminal handle is
a passive mailbox, not a push channel, so the reply sits there until the
worker independently calls `orca orchestration check` — which a worker
blocked mid-task never does. Send it instead on the dispatch handle:

```
orca orchestration send --to dispatch:<dispatch_id> --type status \
  --subject "Re: <escalation subject>" --body "<the answer>" --run <run_id>
```

The escalation's envelope carries no dispatch id directly — get one from
this coordinator's own path → dispatch mapping (§2.1's live-derivation),
joined on the escalation's sender terminal handle.

Otherwise just note it and continue — an escalation doesn't have to block
the loop unless the user says so.

Worker heartbeats are never in `--types` (§2.7), so they're never delivered
here; liveness between deliveries is checked only via `worker-show` on
wait-timeout.

## 5. Resume & wrap-up

**Resume** is just re-running `/gw:auto-drive <work-path>` (§1 re-binds
the same Run by objective). Cycle 1's live-derivation (§2.1) classifies
every existing task — live, settled, or dead — before anything else
happens; dead dispatches enter the failure flow immediately. Nothing is
reconstructed from conversation memory: a fresh session with zero context
resumes identically to one that's been running for hours.

**Wrap-up** (§2.3 reported `terminal: true`):

1. Refresh the full task-list and worker-list and run §2.1's classifier again,
   even when the plan reports `terminal: true`. Only dispatches classified `settled` by this fresh proof check may be released:
   `orca orchestration worker-release --dispatch <id>` (no `--run` flag) for
   each verified successful dispatch that still holds an unreleased terminal.
   For `recovery-inspection`, retain and print its task/dispatch IDs, inspect
   recovery, and stop without releasing it or reporting verified completion.
   A missing terminal never substitutes for launch proof.
2. Print a run summary: items resolved, branches merged back (from each
   settled dispatch's `merge_target`), anything skipped (§4.1's skip
   choices this run), anything left in `blocked[]`.
3. Print the §2.5 decision lines one final time from the terminal plan's
   JSON. §2.3 routes a terminal plan straight here without running §2.5, so
   without this an `assumed` decision nobody ever confirmed would go
   unmentioned at the end of the run — "epic finished with assumed decisions
   nobody looked at" is exactly the silent failure the ledger exists to
   prevent. Printing costs nothing; skip only when both lists are empty.
4. Stop. Merging the epic branch to `develop` is **not** this coordinator's
   job — it happens inside the root item's finish-relay stage, not here.

**User stop** (mid-run, on explicit instruction): exit the loop between
cycles — never mid-dispatch. Live workers keep running independently; offer
`orca orchestration worker-stop --dispatch <id>` for each one
before exiting — same mechanics as the failure question's Stop branch
(§4.1).

## Out of scope

- Any dispatch-decision logic — readiness, worktree choice, model, prompt
  assembly, parallelism caps, `affects` serialization — all owned by
  `gw work orchestrate`, whose engine is
  `graph_works_core.orchestrate.commands` (`plan()` and the `_resolve_worktree`
  ladder it calls). A wrong-looking plan (bad worktree action, a missing
  blocker kind, a bad prompt) gets fixed there or filed as a work item;
  never patched around in this skill's prose.
- Finish-stage relay behavior *inside* the worker — deciding what the
  merge/PR/hold/discard options mean and sending the `ask` — belongs to
  `gw:finishing-relay`. This skill only mirrors the `question` it receives
  (§4.3).
- A vault-wide watcher or scheduled sweep mode. This skill drives exactly
  one path per invocation.
- Auto-retry of failed stages, and automatic merge-conflict resolution for
  parallel forks — both explicit policy (see the failure question and the
  `affects`-disjoint rule), not gaps.
