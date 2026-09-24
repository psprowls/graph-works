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

No repository selector is resolved here. A creation's repository comes from
each dispatch's own `repo.path` (§2.2), matched to an Orca repository by
`launch-worker.py place` at launch time (§3).
No launch reads the coordinator's location.

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
   `--task-title` was copied exactly from `dispatches[].key` at creation (§3).
   The key is the planner's session name, `gw-<phase>-<stem>` (maximum 64
   characters, retaining the phase and eight-hex path hash). Copy that same
   value back for dedupe and `--live`; never reconstruct a key from a path.
   This task mirror is the dedupe ledger for the whole loop and the §2.6
   dispatch-diff source.
2. Start with `orca orchestration worker-list --run <run_id> --json`, then
   follow each opaque `result.page.nextCursor` with another `worker-list`
   call using `--cursor <nextCursor>` until `result.page.hasMore` is false.
   Require every page's `total` to be the same exact non-negative integer,
   every nonterminal page to add workers and advance to a new nonempty cursor,
   and the final collected worker count to equal that stable total. Assemble
   one full snapshot containing all collected `workers[]`; only after every
   page was read set its truthful final metadata to `hasMore: false`,
   `nextCursor: null` and `total` equal to the assembled worker count. Do not
   inspect worker-show or any terminal metadata until this pagination is
   complete. The rows contain `dispatchId`, `taskId`, `runId`, `workerState`,
   `dispatchStatus`, `agentTerminalHandle`, `terminalState`, `resource{…}`
   and `projection.liveness`.
   This is the task→dispatch join. Task records carry **no** dispatch-id field
   of their own, so a per-task `worker-show --dispatch <id>` loop cannot even
   be constructed before this full snapshot exists.
3. Save those full responses. First discover every settlement recovery record
   beneath the driven subtree —
   `find <workspace>/okf/<work-path> -path '*/references/orca-settlement/*.json' -type f`
   — then every park checkpoint beneath it —
   `find <workspace>/okf/<work-path> -path '*/references/*-checkpoint-D-*.md' -type f`
   — and pass each resolved file with its own flag (path discovery stays here;
   the helper only receives files):

   ```
   python3 references/launch-worker.py classify-restart \
     --tasks <task-list-json> --workers <worker-list-json> \
     --recovery-record <record-json> [--recovery-record <record-json> ...] \
     --checkpoint <checkpoint-md> [--checkpoint <checkpoint-md> ...]
   ```

   A checkpoint file is named `NN-<phase>-checkpoint-D-nnn.md` and its
   frontmatter names the `item:` and `phase:` it was written for; the helper
   joins those against each Task's `display_name` (`<work-path> · <phase>`,
   written by §3 step 2) because a dispatch key is not reversible to a path.

   It joins by `taskId`. **The latest attempt** of a Task — the one meaning of
   "latest" everywhere in this skill — is its **first** row in this snapshot:
   `worker-list` is newest first, so the last row is the *oldest* attempt.
   When a Task has more than one attempt the helper cross-checks that row
   against `worker-show --dispatch <id> --json` → `result.dispatch` for every
   attempt: its `createdAt` (UTC; the zone-less form is UTC) must be the
   newest, a tie broken only by `retryOfDispatchId` (a retried Dispatch is
   never the latest), and no other attempt may name it as retried. Any
   disagreement, unbreakable tie or failed read is `recovery-inspection` with
   reason `latest-attempt-ambiguous` — never a guess. Its actions are
   durable-state decisions, not guesses from terminal presence:
   - `live`: `workerState` is `ready` or `running` and dispatch status is not
     `outcome_unknown`.
   - `settled`: `workerState` is `succeeded`, the full untruncated task spec
     has a valid frozen envelope whose `dispatch_key` matches `task_title`,
     and `worker-show --dispatch <dispatch_id> --json` supplies matching
     `result.worker.startOptions.launch.requested` and `.effective` proof.
     The classifier performs this read for each succeeded worker, comparing
     the agent and every explicit model/effort choice against the envelope.
   - `deliberate-skip`: task status is `blocked` and either no worker exists or
     the latest attempt is positively terminal `failed`/`stopped`, **and no
     park checkpoint claims this task's `(path, phase)`**. The Skip branch in
     §4.1 writes the durable marker after that terminal attempt.
   - `parked`: everything `deliberate-skip` requires, plus one of the
     `--checkpoint` files naming this task's own `(path, phase)`. A park and a
     skip leave bit-for-bit identical Orca state — §2.5.2's `worker-stop`
     produces `workerState: stopped`, `dispatchStatus: failed`, Task
     `blocked`, exactly the skip signature — and §2.5.2 writes no marker of
     its own, so without the checkpoint join a restarted coordinator would
     report every parked item as "skipped by a human." It is neither: it is
     work waiting on an answer. Never re-dispatch a `parked` key as a fresh
     dispatch and never count it as a skip in §5's summary; §2.6's resume
     amendment owns it, and it is the **one** key class exempt from §2.6's
     dedupe rule.
   - `recovery-inspection`: every other no-worker task, including a crash after
     task reservation but before start; unmarked `failed`/`stopped` and unknown
     worker states; `outcome_unknown` in either worker or dispatch state;
     and succeeded workers with missing, malformed, truncated or mismatched
     envelopes/receipts, or an unsuccessful worker-show read; and any Task
     whose latest attempt is `latest-attempt-ambiguous`.
     Live and unknown evidence takes precedence over a task's `blocked` status,
     so a stale/partial skip update cannot hide an active or ambiguous attempt.
   - `recovered-settled`: a recovery record (§4.1.1) for the latest attempt
     is at `completed-verified` with no unresolved reason, and fresh state
     re-verifies it. Run, Task, Dispatch, frozen key and spec hash match. The
     Dispatch is positively settled. `terminalState` and
     `resource.releaseState` are `released`. The Task is `completed`. Every
     recorded file hash and commit re-verifies. Worker-show launch proof
     passes. Task completed plus worker stopped alone never earns it. This is
     finished stage work whose terminal is already released: never retry it,
     release it again, or advance its item again.
   - Any row a record names carries `recovery: {checkpoint, reason}`. A
     matching record takes precedence over the `deliberate-skip` rule:
     stopped/blocked pending recovery is recovery inspection, not a skip.
     `live` and accepted `settled` evidence still win over every record — a
     `live` row carrying `recovery` stays in `--live` and keeps its affects
     reservation. A `live` or `recovery-inspection` row carrying `recovery`
     resumes §4.1.1 from its saved checkpoint rather than Orca start recovery
     or the failure question.

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
Run — there's nothing live yet). An unknown `--live` key makes the command
exit nonzero and yields no usable plan. Stop dispatching from that result,
inspect each named key against the task and vault state, and correct the
mismatch before replanning. Do not silently omit the key or retry with an
empty live list.

Repository selection comes from the item's nearest `repo:` metadata, including
physical ancestors, and the repositories declared in `workspace.yaml`. For
example, `repo: graph-works` selects that declared name. A workspace with
several repositories needs an unambiguous selection; an existing branch stamp
does not supply one. Do not add `--repo-name` to planner or placement calls.
Never choose from cwd, `affects:` paths, the first declared repository, or an
existing branch stamp.

On a repository workspace refusal, surface the affected item and the command's
repair guidance. For a prelaunch refusal, use a coordinator `AskUserQuestion`
to obtain a human choice of a declared repository.
Correct only the affected item's `repo:` metadata or the intended ancestor's
metadata within the scope agreed in that answer, then replan before acting.
Never launch workers from an error envelope.

On success, the result contains:

- `terminal` (bool), `max_parallel` / `slots_free` (ints), `supervise_merges`
  (bool, default `false`; display only — §4.3 reads each dispatch's
  `auto_merge`, never this), and `live` (the echoed input list).
- `repo` — `{"name": "<declared name>", "path": "<code repository>", "source": "frontmatter|flag|sole|fallback"}`,
  root metadata for the repository `workspace.yaml` declares, or `null` when none resolves.
  Each dispatch carries its own `repo={name,path,source}`; use that path for placement. A
  `null` repo never plans a creation: those items arrive in `blocked[]` as
  `worktree-unprovable`. `source` says why that repository was chosen;
  `frontmatter` means the item (or an ancestor) sets `repo:`.
- `dispatches[]` — each entry: `key` (the exact planner session name,
  `gw-<phase>-<stem>`, at most 64 characters with the path hash retained), `path`, `phase`,
  `kind`, `effort`, `skill`, `mode` (`autonomous` | `attend` | `relay`),
  `agent`, `model` (`null` = selected agent default, omit `--model`),
  `reasoning_effort`, `provenance` (the winning origin and reason for every
  profile field),
  `worktree` (`action`: `reuse` | `fork-child` | `create-top-level` | `main`,
  `path`, `branch`, `base_branch`, `exists`, `parent_path` — the existing
  worktree a created one is linked beneath, `null` when none), `merge_target`,
  `auto_merge` (bool — core's verdict that §4.3 step 0 may answer this
  dispatch's finish-relay `merge` question itself; true only for a non-root
  item at `finish` whose merge target is its owner's integration branch, with
  `supervise_merges` off), `prompt`.
- `advances[]` — each: `path`, `reason`, `mode` (`advance` or `return`), `worktree`/`branch` (the epic's
  already-known worktree, when one exists — `null` otherwise, e.g. before any
  worker has ever been dispatched for this epic).
- `blocked[]` — each: `path`, `kind` (one of exactly `deps`, `capacity`,
  `affects-overlap`, `effort-required`, `decisions`, `human`,
  `relay-untailed`, `worktree-pending`, `worktree-unsupported`,
  `worktree-unprovable`, `worktree-ambiguous`, `cross-repo-child`, `invalid`),
  `reason`. The closed vocabulary is `BLOCKED_KINDS` in
  `graph_works_core.orchestrate.commands` — if a `kind` arrives that isn't in
  this list, treat it as this skill being out of date, print it, and act on
  nothing.
- `warnings[]` — plain strings (e.g. a malformed decisions-ledger entry).
  Print these as notes; they are not blockers. Unknown live keys are command
  refusals, not warnings.
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
- `holds[]` — every item in the planned subtree currently held by an open
  decision, from any owning ledger (not just this root's own — unlike
  `open_decisions`/`assumed_decisions`, which are scoped to the nearest
  owning ledger only). Each entry: `path`, `owner_path`, `ledger_path`,
  `decision` (the full entry: `id`, `number`, `question`, `status`,
  `affects`, `decided`, `supersedes`, `hold` — `"park"`, `"skip"`, or `null`
  for a plain `question` hold — `phase`, `checkpoint` — a park's checkpoint
  resource, root-absolute, or `null` — `prose`). A held item never appears in
  `dispatches[]`: holding excludes it from the candidate set. It **does**
  appear in `blocked[]`, as a `"decisions"`-kind entry keyed by the *item*
  path — the routing layer returns a blocker for a held item, exactly as it
  does for one blocked on an unanswered plain decision. So a held item prints
  **two** lines per cycle, and that is correct, not a duplicate: the
  `blocked[]` line says *this item cannot be dispatched and why*, keyed by
  item path; the `holds[]` entry says *which decision on which ledger is
  holding it*, keyed by `owner_path`, and is the only place the `hold` shape,
  `phase` and `checkpoint` are carried. §2.5's park/skip rendering and
  §2.5.2's park handling both read `holds[]` for exactly those three fields;
  `blocked[]` has none of them.

#### Prepare repository integration anchors before launching

`preparations[]` contains `owner_path`, `owner_phase`, `repo`, `branch`,
`base_branch`, and `worktree`. It reserves no worker slot. Save the complete
plan, then process preparations **serially**:

```bash
python3 "$PLUGIN_ROOT/skills/auto-drive/references/launch-worker.py" prepare \
  --plan-file <saved-plan.json> --owner <owner_path> --repo-name <repo.name> \
  --workspace <workspace-path>
```

The repository name selects the emitted preparation; it never overrides an
item's assignment. The helper captures an owner/ancestor/configuration guard,
re-fetches `gw work orchestrate` with the same root/live keys, and refuses a
changed preparation. It adopts one proven deterministic checkout or creates
an explicitly repository-selected top-level worktree with setup skipped and
no agent. A durable Orca comment marker identifies its creation across crashes.
If Orca prefixes the name, only that marked, clean checkout at the expected
base tip may be renamed with non-force `git branch -m`. Existing branches are
never overwritten; ambiguous inventory, marker, or missing checkout requires
repair. Creation errors trigger observation, never another suffixed create.

The helper uses `record-preparation.py snapshot|record` as a thin adapter to
core. In the source plugin it selects an absolute source project with
`uv run --project <source-root> --package graph-works-core python`; installed
plugins discover the `gw` Python interpreter and probe guarded preparation
capability. An incompatible or undiscoverable runtime refuses explicitly.
Neither path resolves the runtime from the coordinator's working directory. The record call carries `--root <owner_path> --phase <owner_phase>
--repo <repo.name>` and the captured guard. Core rechecks owner/ancestor and
repository configuration bytes under the existing decision-owner lock and
re-reads that set under the bundle mutation lock immediately before effects,
before recording scalar or foreign `repo_stamps`. Orca runs outside that lock. A
refused stamp leaves a discoverable checkout and authorizes no child launch.

**Replan after every attempt**, successful or refused, before processing
another preparation or dispatch. Launch children only from a fresh plan after
the anchor stamp persists. Failure blocks its dependents; unrelated valid
fresh dispatches can still consume the shared free slots. Do not manufacture
a worker/task for preparation. Use `dispatch.repo.path` for both normal and
retry placement arguments; the top-level plan `repo` is root metadata only.

### 2.3 Terminal?

`terminal: true` → go to Wrap-up (§5), including its fresh launch-proof gate,
then stop looping. A terminal plan does not establish successful launch proof.
Nothing else in this cycle runs.

### 2.4 Advances

Before each entry, run `gw work next <path from entry> --json` for that exact
path and capture `expected-phase` from its `phase` before mutating anything.
JSON null maps to CLI `none`.
Revalidate the planned gate or return condition against the fresh next result before acting.
For `mode: advance`, require empty `blockers`, null `action`, and a non-null
`on_complete` whose destination is the intended transition. For `mode:
return`, `gw work next` does not expose `repair`: require `phase: finish`, the
specific `waiting on children filed after finish` blocker, and a fresh
§2.2 plan using the same root and live-key convention with a matching entry
for this exact path, `mode: return`, and reason. That mode's defined
transition is `finish` to `execute`; the next response alone cannot prove it.
If the route output cannot establish the intended return condition, replan
rather than guessing. Compare the planned mode and transition destination to
this fresh evidence. Do not treat a newly observed phase alone as
authorization for an entry planned earlier.

For every applicable entry in `advances[]`: `gw work advance <path from entry> --from <expected-phase> --no-infer-worktree`,
adding `--return` when `mode: return` and forwarding any other planned
return/mode flags. Add `--worktree <entry.worktree> --branch <entry.branch>` only when the entry
carries them (non-`null`). **`--no-infer-worktree` is what keeps it location-independent**: the advance
never infers a placement from wherever the coordinator happens to be running.
Only the orchestration root's entry can carry a
pair — `plan()` never attaches the epic worktree to a descendant's advance, and
a descendant's own recorded placement survives an advance that names none.
Preserve the captured expectation across retries, including sizing answers.
If the planned action is no longer applicable or advance returns phase-mismatch,
discard the stale action and restart planning at section 2.1. Never remove the
guard or replace its expectation merely to make a retry succeed. After any
successful automatic advance, restart the cycle at §2.1 before considering
another planned entry.

If `advances[]` was non-empty, the plan you just read is now stale —
restart the cycle at §2.1 (skip §2.5–2.7 this iteration; don't act on a plan
you know is out of date).

### 2.5 Blockers

- **`effort-required`**: ask the user — via `AskUserQuestion`, this is the
  coordinator's own human, not a worker relay — to size the item
  (xtra-small / small / medium / large / xtra-large). First run
  `gw work next <work-path> --json` for that exact path, capture
  `expected-phase` from its `phase`, and verify that effort sizing is still
  required with no unrelated blocker before asking. The expectation captured
  before the answer stays fixed. Run
  `gw work advance <work-path> --from <expected-phase> --effort <value> --no-infer-worktree`, then restart the cycle at §2.1.
  If sizing is no longer required or advance returns phase-mismatch, discard
  this action and replan at §2.1 without changing the captured expectation.
  Never add `--worktree`/`--branch` to it: sizing is not a placement.
- **Every other kind** (`deps`, `capacity`, `affects-overlap`, `decisions`,
  `human`, `relay-untailed`, `worktree-pending`, `worktree-unsupported`,
  `worktree-unprovable`, `worktree-ambiguous`, `cross-repo-child`, `invalid`):
  print one line each (`blocked <work-path> (<kind>): <reason>`) and take no action.
  A `worktree-unprovable` for an unstamped root with no stale epic anchor whose
  earlier stages all ran attended no longer occurs: an item at `design`, `plan`,
  or `execute` with `work_status: accepted` has no code work to lose, and is
  placed like a first dispatch. This is narrower than "read-only refusals are
  gone": a `worktree-ambiguous` search, a stamped worktree that has vanished, and
  a descendant at `plan`/`design` with no epic anchor ("dispatch the subtree root
  first") still refuse. What remains of the root case is a code stage that may
  have run whose worktree cannot be found (a lost stamp at `execute`/`finish`),
  which is a human decision. `capacity` and
  `worktree-pending` resolve themselves next cycle as slots/worktrees free
  up; `deps`, `affects-overlap`, `human`, `cross-repo-child`, and `invalid`
  need a human decision outside this loop; `decisions` is a third case — it
  neither self-resolves nor needs a decision outside this loop, it's resolved
  *inside* this loop by the coordinator's own CLI call, but only once the
  user tells you to — see §2.5.1. `relay-untailed` and `worktree-unsupported`
  are a fourth: both are configuration faults that will recur every cycle
  until someone edits something outside this loop, so report them once and
  don't wait on them. `cross-repo-child` means a child resolves (via `repo:`)
  to a different repository than its epic; placing it is not supported yet —
  report it and move on.
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

- **Holds**: after the decision lines, print one line per `holds[]` entry
  (§2.2) whose `path` was not just handled by §2.5.2's stop:

  ```
  hold <path> <decision.id> (park at <decision.phase>): resumable from checkpoint <decision.checkpoint> -- answer via `gw work decision answer <owner_path> <decision.id> --answer ...`
  hold <path> <decision.id> (skip at <decision.phase>): deliberately skipped -- answer to re-enable
  ```

  Pick the line by `decision.hold`. For a plain `question` hold
  (`decision.hold` is `null`), print `work_tracker_okf.workflow.hold_reason`'s
  own rendered text instead of inventing new wording — that function is
  already shape-generic (`hold.shape` defaults to `"question"`), so its output
  for this case already reads correctly; do not duplicate its logic here.
  Informational only, exactly like the decision lines — take no action.

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

### 2.5.2 Park handling: stop a dispatch that just parked

**Unconditional, every cycle — not human-initiated, unlike §2.5.1.** Cross-
reference this cycle's `holds[]` (§2.2) against §2.1's live-key map.

**Resolving a live key back to a work path — use the Task's `display_name`.**
A key (`gw-<phase>-<stem>`) is a one-way hash-bearing name: nothing recovers a
path by parsing one, and `graph_works_core.orchestrate.commands` says so
outright (`session_index` is the only reverse, and it is a planner-internal
map this session never holds). The durable route is the one §3 step 2 already
creates: every Task is created with
`--display-name "<work-path> · <phase>"`, and §2.1's `task-list --run <run_id>
--json` returns that string verbatim in each row's `display_name`. Split it on
the first ` · ` — the left half is the canonical work path, the right half the
phase. Join on that path, never on the key. A row whose `display_name` is
missing or does not split (a Task created before this convention, or by
something other than §3) cannot be resolved: print it and skip it here rather
than guessing.

For every live key whose resolved path has a `holds[]` entry with
`decision.hold == "park"`:

1. This dispatch has already stopped working (it followed
   `references/grace-period-protocol.md`) but its Dispatch is still live in
   Orca's model — nothing else in this cycle reflects that yet.
2. `orca orchestration worker-stop --dispatch <dispatch_id> --json`, using the
   `dispatch_id` §2.1's live-derivation already bound to this key. This is the
   coordinator's exclusive authority: `worker-stop --help` takes no `--from`/
   `--dispatch-capability`, unlike `ask`/`send`, so a dispatched worker's own
   session could never have called this itself even if it tried.
3. `worker-stop` settles the Task to `blocked` as a side effect (spike O4-O7:
   `workerState: stopped`, `dispatchStatus: failed`, Task `blocked`) — issue no
   separate `task-update` call for this.
4. Do not release the worker (`worker-release`) here. A park is not a
   completion; leave the terminal/resource alone until a resume (§2.6) or an
   explicit human decision.
5. Print `parked <key>: stopped <dispatch_id>, hold <decision.id> at
   <decision.checkpoint>` for the scrollback.

A key whose dispatch was already stopped in an earlier cycle has already left
the live-key map (its Task is `blocked`), so this step is naturally a no-op for
it on later cycles — nothing to track session-locally.

### 2.6 Dispatch diff

Dispatch only `dispatches[]` entries whose `key` has **no existing task** in
§2.1's `task-list` output — the task mirror is the sole dedupe ledger. A key
with a task is live, settled, or an intentional skip (§4.1); in every case,
leave it alone. For each undispatched entry, run Dispatch mechanics (§3).

**Exactly one exemption:** a key §2.1 classified `parked`. Its Task exists and
is `blocked`, so this rule would filter it out, but it is none of those three
things — it is a stopped attempt holding a checkpoint. See "Resuming a
previously-parked item," below, which owns those keys entirely.

A `recovered-settled` key already has its Task, so it is left alone like any
other; an unresolved recovery keeps its Task too, so nothing re-proposes it.

**Resuming a previously-parked item.**

**The dedupe rule above does not apply to a `parked` key.** This is its one
exemption, and it has to be stated explicitly: a parked item's Task still
exists (§2.5.2 stopped it to `blocked`; it was never deleted), so the ordinary
"a key with a task is live, settled, or an intentional skip — leave it alone"
rule would filter every parked item out before this amendment could ever see
it, and the answer a human just gave would never reach anything. §2.1's
`parked` classification is what makes the exemption decidable rather than a
judgement call: a `parked` key is none of those three things.

So: for each `dispatches[]` entry whose `key` §2.1 classified `parked`, run
this amendment instead of both the dedupe rule and the ordinary §3 mechanics.
Every other key obeys the dedupe rule unchanged, and an entry with no `parked`
classification is an ordinary fresh dispatch — run §3 unmodified.

1. Read the checkpoint §2.1 joined to this key: its frontmatter (`item`,
   `phase`, `dispatch_key`, `decision`, `created`) and its `## Question`
   section.
2. Find the checkpointed attempt's Task/Dispatch: refresh
   `task-list --run <run_id> --json`, find the Task whose title equals the
   checkpoint's `dispatch_key`, and take its latest attempt's Dispatch id
   (§2.1's definition — the classifier row's `dispatch_id`) and
   its full untruncated `spec` text — the same lookup the Failure Question's
   Retry branch (§4.1) already performs, reused here rather than
   re-implemented. Save that spec text to a temp file; it is step 5's
   `--spec`.
3. Read the decision named by the checkpoint's `decision:` key:
   `gw work decision list <owner-path> --json`. **Still `open` → stop here and
   leave the key alone**; the park is still waiting and §2.5's hold line
   already reports it. `answered` → take the entry's `prose` `**Answer:**`
   block as the answer text and continue.
4. **Has this checkpoint already been resumed?** Check before launching
   anything. Nothing writes a "resumed" marker, and `answered` is permanent —
   a decision is never un-answered — so without this check a resumed attempt
   that parks again or dies would leave the same answered decision and the
   same checkpoint on disk and be resumed again every cycle, forever, at
   `check --wait` intervals. Derive the answer from the Task's own Dispatch
   history rather than adding a new write path: every Dispatch on this Task is
   already in §2.1's worker-list snapshot (rows joined by `taskId`). For each,
   read `orca orchestration worker-show --dispatch <id> --json` →
   `result.dispatch.createdAt`, and compare it against the checkpoint's
   `created:` instant. Normalize both to UTC first — Orca renders some of
   these as `YYYY-MM-DD HH:MM:SS` with no zone suffix, and those are UTC.
   The parked attempt necessarily started *before* it wrote its own
   checkpoint, so **any Dispatch created at or after the checkpoint's
   `created` instant is a resume that already ran.** If one exists, leave this
   key alone and print
   `parked <key>: already resumed as <dispatch_id>, leaving it alone` —
   ordinary state (live, or blocked again with a *new* checkpoint and a *new*
   open decision) governs it from here.
5. Run `place` for this cycle's own `dispatches[]` entry for this path (its
   `action` will read `reuse`, like any other re-dispatch of an item with a
   recorded placement), redirecting stdout exactly as §3 step 1 does:

   ```
   python3 references/launch-worker.py place --dispatch <dispatch-json> \
     --repo-path <dispatch repo.path> --out-placement <placement-json-file> \
     > <workspace>/okf/<dispatch path>/references/orca-placement/<key>.json
   ```

   `settle-placement` hard-requires `--placement-result` to exist and parse
   — even for `reuse`/`main` — so this redirect is not optional here either.
   `<placement-json-file>` holds `["--worktree", "path:<worktree.path>"]` —
   `launch --recovery-placement` opens its argument **as a path** and will
   not accept an inline JSON literal. Then build the resume spec:
   ```
   python3 references/launch-worker.py resume-spec \
     --spec <the original spec file from step 2> \
     --checkpoint <the checkpoint file path> \
     --answer "<the answer text from step 3>" \
     --placement <placement-json-file> \
     > <new-spec-file>
   ```
6. Launch as a retry of the **same** Task, not a fresh one — skip §3 steps 1-2
   (build/encode/create) entirely for this key:
   ```
   python3 references/launch-worker.py launch --spec <new-spec-file> \
     --task <task_id from step 2> --dispatch-key <key> --run <run_id> \
     --retry-of <dispatch_id from step 2> \
     --recovery-placement <the same placement-json-file from step 5> > <start-json>
   ```
7. **Send the resume context to the new dispatch. This step is what actually
   delivers the answer — without it the resume is a no-op.** `--retry-of`
   reuses the **original, frozen** Task spec and its original prompt:
   `worker-start`'s `--task` and `--spec` are mutually exclusive, and a retry
   takes `--task`, so nothing in `<new-spec-file>`'s *prompt* half ever
   reaches the worker. (The file is still required — `launch` reads the
   frozen `agent`/`model`/`effort` envelope out of it, and `resume-spec`
   composes the resume text in exactly one place.) The resumed worker
   therefore starts on the unmodified original prompt and, left alone, would
   re-ask the very question this whole protocol exists to stop it re-asking.
   Take the new dispatch id from step 6's `worker-start` JSON, slice the
   `## Resume after park` section out of `<new-spec-file>` (everything from
   that heading to end of file), and send it down the dispatch handle — the
   same mechanism, and the same flag shape, §4.4 already uses to answer an
   escalation:
   ```
   orca orchestration send --to dispatch:<new dispatch_id> --type status \
     --subject "Resume after park: <decision.id>" \
     --body "<the ## Resume after park section from <new-spec-file>>" \
     --run <run_id>
   ```
   A non-zero exit here is a failed resume, not a cosmetic one: say so and
   route the dispatch into the failure flow (§4.2) rather than leaving a
   worker running on a prompt that does not know the answer.
8. Continue at §3 step 4 (`settle-placement` with this cycle's `place` result
   and `<start-json>`, then verify and record observed placement) — everything
   from there is identical to an ordinary dispatch.

### 2.6.1 Self-park: stop the run when only parked work remains

Checked at the end of every cycle, **after** §2.6 has finished dispatching and
resuming, and **before** entering §2.7's wait. Stop the run when all three
hold at once:

- Nothing is live: no dispatch was started or resumed this cycle, and §2.1's
  live-key list is empty.
- At least one item is parked: some key is classified `parked` (§2.1), or some
  `holds[]` entry has `decision.hold == "park"`.
- Nothing independent is left to dispatch: every remaining `dispatches[]`
  entry was consumed above or is `parked`-and-still-`open`, and every
  `blocked[]` kind present is one that cannot clear without a human
  (`decisions`, `deps`, `affects-overlap`, `human`, `invalid`,
  `relay-untailed`, `worktree-unsupported`) — a `capacity` or
  `worktree-pending` blocker means slots or worktrees free up on their own, so
  keep looping.

When all three hold, the loop has nothing to wait *for*: `check --wait` would
block for its full ten minutes with zero live dispatches, time out, re-derive
an identical plan, and do it again indefinitely — never escalating to the one
human whose answer is the only thing that can unblock it. So **stop the run**,
reusing the failure question's Stop branch shape exactly (§4.1): exit the loop
and report run state (what's done, what's live — nothing, by this condition's
own precondition — and what's blocked). Name every parked item explicitly with
its checkpoint path, its decision id, and the question text, and say plainly
that answering those decisions (`gw work decision answer <owner-path> D-nnn
--answer "..."`) and re-running `/gw:auto-drive <work-path>` is what resumes
them. There is no live dispatch left to offer a `worker-stop` for.

This is D-004's "the coordinator parks itself when nothing else can proceed."
It is not a terminal plan — §2.3's `terminal: true` path and §5's wrap-up are
for finished work, and a parked run is not finished. Do not run §5.

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
- **Rejected or unprovable completion:** a delivery holding a
  `claimed-unconfirmed` report (§4.1) may be acked only once that report's
  recovery record is written with its identities, the evidence so far, and
  either an `unresolved` reason or a later checkpoint, and every other
  message in the batch is processed. Without that record, do not ack — the
  replayed delivery is the only copy. After the ack, each cycle's §2.1 row
  carrying `recovery` resumes §4.1.1. A timeout never launches a replacement,
  and an unresolved active worker keeps its live key.
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

1. Save the complete `dispatches[]` entry as JSON, then resolve its placement
   before anything is created:

   ```
   python3 references/launch-worker.py place --dispatch <dispatch-json> \
     --repo-path <dispatch repo.path> --out-placement <placement-json> \
     > <workspace>/okf/<dispatch path>/references/orca-placement/<key>.json
   ```

   Omit `--repo-path` only when the dispatch's `repo.path` is `null` (then only `reuse`
   and `main` can be planned). Create the `orca-placement/` directory first.
   The result file is durable on purpose: §5 re-reads it to repair lineage
   after a restart. A non-zero exit prints `PLACEMENT REFUSED <key>: <reason>`:
   nothing was created and no task exists. Print it, delete the (truncated,
   empty) redirected result file, create no task, plan nothing that depends
   on this item, and surface it to the user; the key is re-proposed once the
   cause is fixed. Only a genuine plan/reality contradiction refuses: a
   cross-repo parent, or a repository-resolution problem (unregistered or
   duplicated Orca repository, or no code repository known at all). An
   unknown-to-Orca or main-checkout parent does *not* refuse — `place`
   degrades that to a parentless top-level creation with a note on stderr, so
   see step 4 for how a placement gains no lineage without failing anything.
   Never fall back to the coordinator's repository, the wiki repository or
   trunk.

   Then encode the immutable task spec from the placement `place` wrote:

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
     --task <task_id> --dispatch-key <key> --run <run_id> > <start-json>
   ```

   The recipe passes the planned agent, optional model/effort, and each
   placement value as a distinct argv element. It checks the returned
   `launch.requested` and `launch.effective` blocks against every explicit
   choice (`reasoning_effort` maps to receipt `effort`). Missing or mismatched
   proof enters recovery and never authorizes another worker.

   What `place` produces, and why:

   `place` builds the placement argv; never assemble it by hand. No launch
   reads the coordinator's location — a coordinator in the code repository's
   primary checkout, the wiki repository or the epic worktree issues identical
   calls. (Orca's caller context is the Orca terminal's worktree, not the
   shell's cwd, so `cd` would not change it either.)
   - `reuse` and `main` → `--worktree path:<worktree.path>` only, with no Orca
     call — no creation flags (`--name`/`--repo`/`--base-branch`), which the
     CLI rejects for an existing worktree.
   - `fork-child` and `create-top-level` → `--worktree new-top-level --name
     <worktree.branch> --base-branch <worktree.base_branch> --repo id:<repo
     id>`, where the repo id is the single `orca repo list --json` entry whose
     path resolves to the dispatch's `repo.path` (zero or several matches refuse).
     Orca's caller-context child mode is never used: it takes both the
     repository and the parent from the calling terminal. For a non-null
     `worktree.parent_path`, `place` also resolves `orca worktree show
     --worktree path:<parent_path>`. A parent in another Orca repository
     refuses outright — plan and reality genuinely contradict. A parent Orca
     doesn't know, or that is the repository's own checkout, instead
     degrades: the creation still launches top-level, but with no
     `parent_worktree_id` and a note on stderr — lineage is presentational,
     the repository is what's load-bearing. The lineage itself is set after
     start, in step 4.
   - `main` is the repository's own checkout —
     `_resolve_worktree` in
     `packages/graph-works-core/src/graph_works_core/orchestrate/commands.py`
     chooses `main` over `reuse` whenever the resolved worktree equals the
     code repo's own checkout. No worker states or infers its own placement in
     either case; step 4 records what Orca actually placed.
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

   **Where the truth comes from, and lineage.** Run:

   ```
   python3 references/launch-worker.py settle-placement --dispatch <dispatch-json> \
     --placement-result <workspace>/okf/<dispatch path>/references/orca-placement/<key>.json \
     --start <start-json>
   ```

   It takes the worktree id from `worker-start`'s `effects[]` (else
   `orchestration worker-show --dispatch <dispatch_id>`), reads `orca worktree
   show --worktree id:<id> --json`, and for a creation asserts the observed
   `repoId` is the placed repository and `isMainWorktree` is false. When
   `place` resolved a parent and the new worktree has none, it runs `orca
   worktree set --worktree id:<created id> --parent-worktree id:<parent id>`
   exactly once and re-reads; the observed `parentWorktreeId` must then equal
   the placed parent id, or be `null` when none was placed. For `reuse`/`main`
   it compares paths only. It prints `path`, `branch` (already stripped of
   `refs/heads/`), `display_name`, `repo_id`, `parent_worktree_id` and
   `lineage_set`. A non-zero exit prints `PLACEMENT MISMATCH <key>: <reason>`
   — halt into §4.2 exactly as below. Re-running it is safe: it repairs a
   missing parent and never launches anything.

   **The assertion — never a branch-name comparison.** The upstream slug
   transform is undocumented and unversioned; an assertion built on it would
   keep passing against a formula that no longer describes reality the day
   Orca changes it. Nothing below consults a branch name.

   | Planned `worktree.action` | Assertion |
   |---|---|
   | `reuse`, `main` | `settle-placement`: the observed `path` equals the planned `worktree.path`, compared after resolving symlinks on both sides. No worktree was created, so nothing else is checked. |
   | `fork-child`, `create-top-level` | `settle-placement`: observed `repoId` is the placed repository, `isMainWorktree` is false, and `parentWorktreeId` equals the placed parent (`null` when `parent_path` was `null`). Then, here: the observed `path` is not one already claimed by another dispatch this Run; and `worktree.base_branch` resolves in the observed worktree and is an ancestor of its HEAD — one `git -C <observed path> merge-base --is-ancestor <base_branch> HEAD`. |

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

   This line lets a human reading the scrollback hours later find the branch
   a stage's work is on without reconstructing anything. The verified pair
   is also recorded on eligible items below.

   **Observed placement is provenance; the merge target is not.** The item
   records the observed pair (below). The requested placement, the frozen
   launch envelope and `merge_target` stay the planner's values in every
   report and in §5's merge-back summary.

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

   **Record the observed placement** — once the assertion passes, before
   step 5 and before any other dispatch this cycle:

   1. **Bind.** The observation binds to this `task_id`/`dispatch_id`: the
      Task's latest attempt (§2.1's definition) is this Dispatch and its `task_title`
      is the frozen dispatch key. If a newer attempt supersedes it, or identity
      cannot be established, record nothing — report every ID you have and
      enter inspection.
   2. **Verify the branch.** Normalize the readback by stripping `refs/heads/`.
      Where the observed path is reachable from this host, run
      `git -C <observed path> branch --show-current`; it must print exactly the
      normalized branch. Empty output (detached HEAD), a missing branch, a
      disagreement or unverifiable evidence is a placement mismatch: halt into
      §4.2 as above. Never record the planned `worktree.branch` in its place.
   3. **Record, or skip.** For the orchestration root (the dispatch `slug`
      equals `<work-path>`) at any phase, and for a descendant dispatched at
      `execute` or `finish`, run:

      ```
      gw work record-placement <slug> --root <work-path> --phase <dispatch phase> \
        --worktree <observed path> --branch <normalized branch> --json
      ```

      A descendant dispatched at `design` or `plan` is never recorded: it only
      reads from the shared epic worktree and must not be pinned to it.
   4. **Check.** Success is exit 0 with `refusal: null` and `after` equal to the
      observation. A placement preview validates repository selection too.
      `--dry-run` is read-only. The live call re-reads metadata under the item
      lock, so its result is authoritative if metadata changes after a preview.
      Even an unchanged receipt can now refuse missing, unknown, or ambiguous
      repository metadata. The placement command's `--repo`, when supplied,
      names the observed stamp destination; it does not replace the item's
      own `repo:` metadata. Re-read with the same command plus `--dry-run`
      after recording: `changed: false` proves the item now carries the pair.
      Keep a receipt reference (the command and where its JSON is kept) with
      this dispatch's evidence; never store a preamble or a dispatch capability.
   5. **Refused.** When `refusal` is non-null, print
      `PLACEMENT UNRECORDED <key>: <refusal.reason> — <refusal.detail>` and
      halt this item into inspection. `phase-mismatch` means the worker took
      the item lock and finished its stage first. Preserve the task, the
      dispatch key and the allocated worktree; plan nothing that depends on
      this item; surface the mismatch to the user. Do not call `gw work advance` to stamp it,
      do not re-record with the new phase, do not start another fork, and do
      not stop or release the worker without the authority §4.1.1 requires.
      Independent items continue only where live-key and hold rules already
      allow. `unknown-path`, `unknown-root`, `outside-root`, `invalid-item`,
      `invalid-phase`, `invalid-pair`, `terminal`, `entry-unprovable` and
      `read-only-descendant` are the same inspection halt: each means the
      coordinator's own reading of this dispatch is wrong.

   6. **Application failed or other non-success.** Every other non-success,
      including when `refusal: null`, enters inspection: a failed application,
      nonzero exit, malformed or missing JSON, mismatched `after`, or failed dry-run verification.
      Print `PLACEMENT UNRECORDED <key>: <failure details>` with the exit status
      and available JSON; do not read `refusal.reason` from a null refusal.
      Preserve the task, dispatch key and allocated worktree; plan nothing
      that depends on this item and surface the failure to the user.
      Do not call `gw work advance` to stamp it, do not start another fork,
      do not re-record with a new phase, and do not stop or release the worker
      without the authority §4.1.1 requires. Independent items continue only
      where live-key and hold rules already allow.

   A lost response is not a refusal. After a timeout, disconnect or restart,
   repeat step 1 and the `--dry-run` read before recording again; an
   identical replay is a no-op only once attempt, phase and observation are
   re-established. Until then, enter inspection and preserve the task,
   dispatch key and allocated worktree. Do not call `gw work advance` to stamp it,
   do not start another fork, and do not stop or release the worker without
   the authority §4.1.1 requires. Step 5's submission probe runs only after recording
   succeeded, or for a read-only descendant that records nothing.

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

Classify each `worker_done` before reading its outcome. Save the message
object from `result.messages[]` and run:

```
python3 references/launch-worker.py classify-report --message <message-json>
```

It reads `payload` (`taskId`, `dispatchId`, `outcome`) and checks Orca's
`_orcaLifecycleRejection` marker **first** — a rejected report keeps type
`worker_done` and its original `outcome: succeeded`:

- `accepted-success` → **Success branch** below.
- `accepted-failure` → the **failure question**, below.
- `claimed-unconfirmed` — a rejection marker, a malformed marker, a legacy
  `Rejected worker_done:` subject, an unreadable payload, or missing identity
  → §4.1.1. Never the failure question by default: retry/skip/stop is wrong
  for work that may already be done.

- **Success branch**: refresh the full task-list and worker-list and run
  §2.1's classifier before acknowledging the delivery. Require its `settled`
  result for this exact task/dispatch pair: this validates the full frozen
  envelope and key plus durable requested/effective proof from worker-show.
  Missing or mismatched proof enters recovery with both IDs; do not ack or
  release it. A valid `worker_done` already settles its Task and Dispatch;
  never issue `task-update completed` (§4.1.1 step 5 is the single recorded
  coordinator exception). Then run
  `orca orchestration worker-release --dispatch <dispatch_id>` (no `--run`
  flag — `worker-release` takes only `--dispatch` and `--retry-request`)
  → if this key was attend-pending (§3), flip the card back:
  `orca worktree set --worktree <selector> --workspace-status in-progress`
  → if the settled dispatch's phase was `execute`, run the coverage read
  (**Coverage read**, below): resolve the work path and phase from the settled
  task's `display_name` in the full task-list this branch just refreshed —
  split on the first ` · `, left half the path, right half the phase, exactly
  as §2.5.2 does; never parse the dispatch key. A row whose `display_name` is
  missing or does not split cannot be resolved: say so in one line and
  continue — the read is best-effort, never a reason to hold the delivery
  → nothing else; the next cycle's plan (§2.2) picks up the new state
  naturally.

### 4.1.1 Completion claimed, settlement unconfirmed

A rejected completion is still a claim that stage work finished, but neither
its delivery nor its `outcome` settles the Dispatch. Orca's caller checks —
capability, pane/leaf, process incarnation — are authority boundaries: never
resend the report for the worker, never rewrite `--from`, and
never infer identity from a terminal-handle prefix.
The historical caller-identity cause remains unverified upstream; this branch
recovers without explaining it.

Recovery records live at
`<workspace>/okf/<dispatched-item-path>/references/orca-settlement/<dispatch_id>.json`
and are written only through:

```
python3 references/launch-worker.py record-write --path <record-file> --record <record-json>
```

`record-write` validates the schema and keeps identity immutable. It refuses
checkpoint regression, history/mutation rewrites, evidence changes without a
`reattested:` history note, and any Dispatch capability. It replaces the file
atomically (UTF-8, LF). For `stopped-verified`, `released-verified` and
`completed-verified` it re-reads `task-list` and every `worker-list` page
itself, rechecks file hashes and commits at every verified checkpoint, and
checks worker-show launch proof at completion only after assembling the full
worker snapshot. It refuses renewed verification unless current state proves
the checkpoint. A refused write stops the sequence; it is not a formatting
problem. Every changed write appends one `history` entry naming the checkpoint
and what was observed; identical verified writes still recheck current proof.

A refusal-only annotation changes only `history` and `unresolved`, retaining the same checkpoint and every protected identity, evidence, judgment, placement, mutation and stop-authority field.
This narrow append-only write does not reverify historical progress, so a
later artifact drift or unavailable readback can be recorded even at
`completed-verified`. Keep the latest nonempty refusal reason; never move
backward or advance to an unperformed checkpoint to save it. Clearing a
refusal or advancing requires fresh proof of the last verified checkpoint;
changing evidence still requires a new `reattested:` history note and cannot
use the refusal-only exception. An unresolved record never earns
`recovered-settled`.

Record-derived success requires fresh `projection.liveness.verdict: exited`; explicit `live` keeps the key live, and missing, malformed or `unverifiable` evidence keeps recovery inspection and occupancy.
The helper conservatively does not implement execution-host overrides: even
a saved exit/stop authorization or a live PTY cannot turn a missing fleet
verdict into exit proof. If the version-served recovery guide permits stronger
execution-host evidence, inspect it explicitly and refresh the fleet snapshot;
until it proves exit this helper cannot verify a recovery checkpoint.

1. **Bind identity.** Refresh full `task-list --run <run_id> --json`, assemble
   the complete paginated `worker-list --run <run_id> --json` snapshot exactly
   as in §2.1, and only then run
   `worker-show --dispatch <dispatch_id> --json`. The report's
   `taskId`/`dispatchId` must name a Task in this Run whose latest attempt
   (§2.1's definition) is that Dispatch, whose `task_title` is the frozen dispatch key, and
   whose full untruncated spec validates. A late report from an earlier
   attempt cannot recover, stop or complete the current one. Missing
   identity (a legacy wrapper without payload IDs) is unresolved inspection:
   report every ID you have to the user.
2. **Check for accepted success.** Run §2.1's classifier. `settled` for this
   exact pair means the rejection was a stale duplicate: take the Success
   branch. With a record supplied, this requires current Task `completed`,
   latest same-Run Dispatch `completed`, worker `succeeded`, and the existing
   frozen-spec/launch proof. This accepted completion is independent of a
   saved recovery claim: its reporting agent may still idle live awaiting
   normal release. A lone `workerState: succeeded` and launch receipt cannot
   bypass recovery liveness checks when Task/Dispatch acceptance disagrees.
   Legacy classification without records keeps its existing behavior.
   A `ready`/`running` latest worker is still live: keep waiting, or
   ask the user for an explicit stop decision. A timeout, the rejection,
   a missing terminal, a null agent wait or unverifiable liveness is never
   stop authority.
3. **Earn the judgment.** Inspect the dispatched item
   (`gw work next <item-path> --json`), its required stage artifact and phase
   transition, and — in its worktree — the branch, commits and validation
   evidence the stage's acceptance criteria require. An existing file, an
   advanced phase or the worker's prose is not enough. Bind the evidence to
   this attempt: an independent actor may have advanced the item. A finish
   stage keeps merge/PR/hold/discard semantics, and Task completion never
   resolves a graph-works item. Compute the spec hash with
   `python3 references/launch-worker.py spec-hash --tasks <task-list-json> --task <task_id>`
   and file hashes with `shasum -a 256 <file>`; commits are full 40-hex ids.
4. **Record inspection before any mutation.** Write the record at
   `inspection`:

   ```json
   {
     "schema": "gw-orca-settlement", "version": 1,
     "run_id": "<run_id>", "task_id": "<task_id>", "dispatch_id": "<dispatch_id>",
     "dispatch_key": "<task_title>", "work_path": "<dispatched item path>", "phase": "<dispatched phase>",
     "spec_sha256": "<spec-hash output>",
     "placement": {"worktree": "<observed worktree>", "branch": "<observed branch or null>"},
     "report": {"message_id": "<message id>", "outcome": "<succeeded|failed|null>",
                "rejection": {"code": "<code>", "reason": "<reason>"}, "reason": "<classify-report reason>"},
     "evidence": [
       {"kind": "file", "path": "<absolute artifact path>", "sha256": "<hash>"},
       {"kind": "commit", "repo": "<absolute worktree>", "sha": "<40-hex commit>"},
       {"kind": "validation", "command": "<command>", "exit": 0, "receipt": "<where its output is kept>"}
     ],
     "judgment": "<succeeded|unestablished>",
     "stop_authority": null,
     "checkpoint": "inspection",
     "mutations": [],
     "history": [{"checkpoint": "inspection", "at": "<UTC time>", "note": "<what was inspected>"}],
     "unresolved": "<what is still missing, or null>"
   }
   ```

   Store receipt *references*, never raw preambles or capabilities. If
   success cannot be established, keep `judgment: unestablished`, set
   `unresolved`, ask the user for the missing evidence or disposition, and do
   not complete the Task. Stop authority is one of:
   - `exit-evidence`: positive exit as the version-served Orca recovery
     reference defines it, honouring its execution-host precedence when
     worker-show PTY status and fleet agent liveness disagree.
   - `user-authorized`: an explicit user decision to stop a known live
     worker.
   - `already-settled`.

   Unverifiable stays unverifiable.
5. **Settle, release, complete — each verified.** Only with judgment
   `succeeded`, file or commit evidence, and stop authority recorded:
   1. Refresh every worker-list page as in §2.1, assemble and validate the
      complete snapshot, then refresh worker-show; confirm no newer attempt
      (by §2.1's latest-attempt definition)
      exists.
   2. If the latest attempt is not already positively settled:
      Before invoking worker-stop, persist its intent at `stop-requested` using the operation journal below.
      Run `orca orchestration worker-stop --dispatch <dispatch_id> --json`.
      After worker-stop returns, append its original request ID and sanitized receipt reference using the operation journal below.
      Never issue a redundant stop against a settled attempt.
   3. Record `stopped-verified`. The tested control read `workerState:
      stopped`, `dispatchStatus: failed` and Task `blocked`; a zero exit code
      alone is not settlement.
   4. Before invoking worker-release, persist its intent at `release-requested` using the operation journal below.
      Run `orca orchestration worker-release --dispatch <dispatch_id> --json`.
      After worker-release returns, append its original request ID and sanitized receipt reference using the operation journal below.
      Then record `released-verified`. Treat `retained`/`identity_unproven`,
      `release_pending`, `release_unknown` or an unverifiable outcome as
      unresolved resource recovery: follow the receipt's literal next action
      and set `unresolved`. There is no automatic abandon fallback.
   5. Before invoking task-update, persist its intent at `completion-requested` using the operation journal below.
      Then run
      `orca orchestration task-update --id <task_id> --status completed --run <run_id> --json`
      — the one recorded coordinator exception to "never issue
      `task-update completed`", not a second `worker_done`.
      After task-update returns, append its original request ID and sanitized receipt reference using the operation journal below.
      Then record `completed-verified`.

   **Operation journal (all three actual mutations).** Before invocation,
   atomically write the requested checkpoint and append a `mutations` entry
   with the exact `action`, `request_id: null`, `receipt` naming a durable
   sanitized output location reserved for this invocation, and UTC `at`.
   Append history recording intent and the exact nonsecret command arguments
   (Run/Task/Dispatch are already bound), and set `unresolved` to
   `original request identity unknown: <action>`. If this write fails, do not
   invoke the command. An already positively settled attempt skips stop and
   creates no fictitious stop intent or request ID.

   Capture the command's output durably at that location, including refusals.
   After return, extract the original `result.mutation.requestId` (or the
   request identity explicitly named by the runtime's error receipt), verify
   its association with this operation, and append a second entry for the
   same action with that ID and the sanitized receipt reference. Append
   same-checkpoint history; never replace the null intent entry. Persist this
   receipt before proceeding to verification or another operation. At the
   same requested checkpoint, appending only receipt/history entries while
   retaining `unresolved` needs no renewed state proof; unavailable readback
   must not prevent saving the original identity. Keep a refusal in
   `unresolved`; clear it only after the original identity is known
   and fresh state/evidence proves historical progress. A successful command
   exit or recovered request ID alone does not verify settlement.

   If invocation or receipt persistence is interrupted with no original request ID, retain the requested checkpoint, null-ID intent and unresolved reason; inspect the reserved output and supported runtime evidence to recover that exact original identity.
   If no original identity can be recovered, stay unresolved even if current
   state looks settled. Do not advance, retry, or launch a replacement. A crash
   before invocation is intentionally indistinguishable from a lost response
   until supported evidence resolves it. The helper can validate the journal
   and block unknown IDs; the coordinator must establish receipt provenance.
   Never invent a request ID or pass a new ID as a first-use `--retry-request`; the current public guide/help establishes retries of existing identities, not request preallocation.
   With the original ID recovered and durably appended, use
   `orca orchestration request-show --request <original_request_id> --json`:
   `completed` means inspect its recorded receipt and fresh state without
   rerunning; `pending` means wait for a still-running original command, or
   follow the runtime's recovery direction with the original command and
   `--retry-request <original_request_id>`; `absent` requires inspection,
   since it does not prove nothing happened. Journal each recovered/replayed
   receipt with the same original identity before proceeding. Keep the
   unknown-ID reason if request identity remains unproven.

   Any refusal interrupts the sequence. Record `unresolved` with the refusal,
   re-read fresh state, then decide. A failed stop never authorizes release;
   only an independent readback that establishes settlement meets the
   release precondition, so never replay the historical stop-refused →
   release-succeeds ordering as a ritual. A lost mutation response is recovered with Orca's request-show / `--retry-request` using the recorded request id, never a blind new mutation.
6. **Finish.** Restore an attend-pending card as in the Success branch. Do
   not run `gw work advance` — the worker already advanced the item. The next
   cycle's classifier reports this key `recovered-settled`.

A replayed delivery or a restart re-enters at step 1 and resumes from the
record's checkpoint against fresh state. It never repeats a verified stop,
release, Task completion or stage advance.

### Coverage read (execute dispatches only)

Runs from the Success branch only — on `accepted-success`, after §2.1's
`settled` result and after `worker-release`, never on `claimed-unconfirmed`
(§4.1.1 owns that path) and never before the ack rules. The producer is
`EXECUTE_TAIL`: every execute dispatch is told to write
`<workspace>/okf/<path>/references/03-execute-coverage.md`, one `- [x]` /
`- [ ]` line per design-spec `## Acceptance` item, and to pass it as
`--report-path`. Nothing gates on its contents; this step is what makes the
report worth writing.

1. Read `<workspace>/okf/<path>/references/03-execute-coverage.md`. Absent →
   one-line note and continue. The obligation is unenforced, and a missing file
   is not a failure.
2. Surface the enumeration **as-is** — print the file's lines, do not
   summarize them.
3. If any line is `- [ ]` (a marker scan, not comprehension), raise one
   `AskUserQuestion` with exactly two options — *send it back* / *accept
   anyway*. No retry option: a dispatch that succeeded is not a recovery case,
   and the failure question's Retry is authorized only by Orca's failure
   response.
   - **Send it back**: run `gw work next <path> --json` and capture `phase`.
     Proceed only when it is `finish`; then run
     `gw work advance <path> --from finish --return --no-infer-worktree`. The
     next cycle's plan (§2.2) redispatches `execute` naturally: the task mirror
     already records the settled dispatch, and §2.6's diff re-proposes the key
     once the phase moves back. If the item is not at `finish`, report that
     plainly and do not advance.
   - **Accept anyway**: continue; the coverage file stands as the record.

What this does and does not claim: it makes an omission visible to a human at
the moment the worker settles, and gates nothing. It cannot repair a model
that marks a box `- [x]` dishonestly.

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
    --recovery-placement <recovery-approved-placement-json> > <start-json>
  ```

  `--recovery-placement` opens its argument **as a path** holding a bare JSON
  argv list — the same shape `place --out-placement` writes, not the keyed
  object its stdout redirect produces. Get that argv the sanctioned way: either
  re-run `place` against the recovery-approved worktree to regenerate it, or
  lift the `placement_argv` array out of the saved `place` result
  (`references/orca-placement/<key>.json`) into its own file. Never
  hand-assemble it (§3).

  The frozen envelope retains the originally requested placement; the explicit
  recovery placement is the argv Orca authorized after accounting for observed
  allocated resources (for example, `path:<allocated path>` instead of a second
  creation). The recipe repeats the frozen agent/model/effort and verifies
  the new requested/effective receipt. `--retry-of` alone does not inherit
  those values. Timeout, absent output, missing terminal, or ambiguous start never
  authorizes retry; preserve task/dispatch identities and follow recovery.
  An explicit reroute is a new deliberate dispatch decision and task identity.
  Then run `settle-placement` with the original dispatch's saved `place`
  result (`references/orca-placement/<key>.json`) and the retry's
  `<start-json>`: the allocated worktree still owes its planned lineage. Once
  the retry's own placement assertion passes, record its observed placement exactly as §3 step 4 does; never change its frozen envelope.
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
`orca orchestration ask` when it needs the merge/PR/hold/discard decision.
This coordinator applies **one fixed, published policy to one structurally
identified case** — a child's merge into its owner's integration branch,
step 0 — and relays everything else to the human unchanged. It never decides
what the options *mean*; that is the worker's job.

0. **Auto-answer `merge`?** Answer it yourself, without mirroring, when
   **both** hold:

   1. The sending dispatch's `auto_merge` is `true`. A live dispatch is
      excluded from the plan's `dispatches[]` (it rides in `--live`), so read
      the verdict from a fresh **read-only** plan that does not count the
      sender as live:
      1. Resolve the sender. The message's sender handle is `dispatch:<id>`;
         join that id to its `taskId` through §2.1's worker-list snapshot,
         then read that Task from `task-list`: its `task_title` is the
         dispatch key, and its `display_name` split on the first ` · ` is the
         path (§2.5.2's key-to-path rule). **Never** parse the question text.
      2. Run `gw work orchestrate <work-path> --live <every live key except
         that one> --json` and take the `dispatches[]` entry whose `key`
         equals the sender's key. Act on nothing else in that plan — launch,
         advance and record nothing from it.
      3. If you cannot resolve the sender with certainty, if no entry carries
         its key, or if more than one does, **mirror** — an unattributable
         question is not a structurally identified case. Otherwise read that
         entry's `auto_merge`.
   2. The question's `options` include `merge`.

   `auto_merge` already folds in everything about *where* the merge lands:
   `supervise_merges` off, a non-root item, and a merge target that is the
   owner's integration branch rather than the release base. Core computes it
   next to `merge_target`, so this skill never re-derives it from paths or
   frontmatter — do not compare `path` fields or walk parents yourself. An
   Epic or Release root, a lone item, and any merge into the release base
   carry `auto_merge: false` and mirror.

   Guard 2 is the detached-HEAD guard. A worker on a detached HEAD drops
   `merge` from its own options, and testing the options rather than
   re-deriving git state keeps you out of the worker's business — any case
   the worker itself judged un-mergeable falls through to the human
   automatically. It also excludes the option-less discard-confirmation ask
   of step 4, which must never be auto-answered.

   **`pr`, `hold` and `discard` are never auto-answered, under any
   condition.** This path produces the single literal string `merge` or it
   mirrors; there is no third outcome.

   **This is not the coordinator guessing an answer** (cf. §2.5.1, which
   forbids exactly that). Nothing is inferred per question. The decision was
   made once, by a human, at design time; it is recorded in this skill's
   prose and in `workflow.auto_drive.supervise_merges`, which that human can
   flip to take every merge question back. §2.5.1 bars inventing an answer
   nobody gave — a standing, published policy applied to a structurally
   verified class of question is the opposite of that.

   When both hold, print exactly one notice in this session — no
   `AskUserQuestion`, no outward worktree comment or status push:

   ```
   auto-merged <work-path> -> <merge_target> (child of <root-path>; not mirrored)
   ```

   Then go straight to step 2 with `merge` as the body. **Steps 2 and 3 are
   not optional on this path**: an undelivered auto-answer strands the worker
   exactly as an undelivered human answer does, and with no human watching
   for it. If either guard fails, continue to step 1 and mirror as usual.

1. Mirror the message's question text and options to the user as one
   `AskUserQuestion` in this session.
2. Reply, and **read the response** — `--json` is not optional here:

   ```
   orca orchestration reply --id <message_id> --body "<the user's answer>" --run <run_id> --json
   ```

   This works because a `question` is sent via `orca orchestration ask`,
   whose sender handle is `dispatch:…` — `reply` addresses whatever handle
   the original message was sent *from*, and it is a *question thread* with a
   dedicated `reply` branch that writes the answer onto the thread and wakes
   the worker blocked inside its `ask`. The response carries
   `{message, question, duplicate}`, and a delivered answer comes back with
   `question.status == "answered"`.
2a. **If `reply` refuses `dispatch_inactive`:** the question's Dispatch has
    ended. **Do not assume that means a park.** All four Dispatch-ending
    events close a pending question the same way — accepted success, accepted
    failure, `worker-stop`, and `worker-abandon` — so a worker that gave up
    the legacy way (`worker_done --outcome failed`, no checkpoint, no hold)
    produces this identical refusal. Treating that as "parked, answer saved"
    would report a real failure as resumable and silently drop the answer.
    Verify before you classify:

    1. **Resolve the path.** The message's sender handle is `dispatch:<id>`;
       join that dispatch id to its `taskId` through §2.1's worker-list
       snapshot, then read that Task's `display_name` from `task-list` and
       split it on the first ` · ` (§2.5.2's key-to-path rule — a key is not
       reversible, the display name is the only durable carrier).
    2. **Re-read hold state fresh — never this cycle's snapshot.** The park
       hold is filed by the worker *after* the question was sent and *after*
       the human spent time answering, so it cannot be in a plan taken before
       the `AskUserQuestion` was even raised. Run
       `gw work orchestrate <work-path> --json` again now (or
       `gw work decision list <owner-path> --json` when the owner is already
       known) and look for an entry naming this path with
       `decision.hold == "park"` and a non-null `decision.checkpoint`.
    3. **Park found** → the answer is not lost. Write it as that hold's
       decision answer:
       ```
       gw work decision answer <owner_path> <decision.id> --answer "<the user's answer>" --json
       ```
       Report that the item is now resumable and will be picked up on the
       coordinator's next planning cycle (§2.6's resume amendment) — do not
       report the reply itself as delivered, since it was refused.
    4. **No park found** → this is a non-park closure, not a park: the
       Dispatch ended by failure, success, stop or abandon with the question
       still open. Say so plainly, name the answer the human gave so it is at
       least in the scrollback, and route this dispatch into the failure flow
       (§4.2) — its failure question is where retry / skip / stop gets
       decided. Never write the answer into an unrelated ledger entry to make
       it look saved.
3. **Assert it landed.** If `question` is absent from the response, or its
   `status` is anything but `"answered"`, the reply took `reply`'s *generic*
   branch instead — it was inserted as a plain message addressed to whatever
   handle the original was sent from, which is a passive mailbox, not a push
   channel. **The worker is still blocked and did not get the answer.** Say
   exactly that to the user and do not report the relay as complete; the
   escalation channel in §4.4 is the way to reach that worker. `reply` returns
   `ok: true` for both branches, so this assertion is the only thing standing
   between a dropped decision and a confident report that it was delivered.
   (A `dispatch_inactive` refusal is step 2a's, not this one.)
4. A typed-`discard` confirmation some finish flows require is just a second
   question/reply round-trip initiated by the worker — handle it the same way,
   assertion included, no special-casing here. **It is never auto-answered**:
   step 0 excludes it twice over — by the `pr`/`hold`/`discard` rule, and
   structurally, because it is sent option-less and so fails guard 2.

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
happens; dead dispatches enter the failure flow immediately, except rows
carrying `recovery`, which resume §4.1.1 from their checkpoint. Nothing is
reconstructed from conversation memory: a fresh session with zero context
resumes identically to one that's been running for hours.
A dispatch whose placement is unrecorded after a restart re-enters §3 step 4:
write `{"ok": true, "result": {"dispatchId": "<dispatch_id>"}}` as
`<start-json>` (the helper then finds the worktree through `worker-show`),
re-run `settle-placement` against its saved
`references/orca-placement/<key>.json` (a crash between start and the lineage
`set` leaves a correctly based child with no parent, which this repairs
without launching anything), then enter the record block at its step 1.

**Wrap-up** (§2.3 reported `terminal: true`):

1. Refresh the full task-list and worker-list and run §2.1's classifier again,
   even when the plan reports `terminal: true`. Only dispatches classified `settled` by this fresh proof check may be released:
   `orca orchestration worker-release --dispatch <id>` (no `--run` flag) for
   each verified successful dispatch that still holds an unreleased terminal.
   For `recovery-inspection`, retain and print its task/dispatch IDs, inspect
   recovery, and stop without releasing it or reporting verified completion.
   A missing terminal never substitutes for launch proof.
   `recovered-settled` is already released: never issue a second `worker-release` for `recovered-settled`.
   A `recovery-inspection` row carrying `recovery` is unresolved: print its
   task/dispatch IDs, record path, checkpoint and reason, and stop without
   reporting verified completion.
2. Print a run summary: items resolved, branches merged back (from each
   settled dispatch's `merge_target`), **auto-answered merges as their own
   line item** — the §4.3 step 0 children, listed separately from the
   questions a human actually answered, so a run that merged twelve children
   unattended says so in one place — anything skipped (§4.1's skip
   choices this run — keys the fresh classifier calls `deliberate-skip`, not
   `parked`), anything **parked** (keys classified `parked`, each with the
   checkpoint and the decision id still awaiting an answer — say plainly that
   these are not skips and that answering the decision resumes them),
   anything left in `blocked[]`. Report accepted worker
   completions (`settled`), coordinator-recovered completions
   (`recovered-settled`, naming each record), and unresolved recovery
   records as three separate lists.
3. Print the §2.5 decision lines one final time from the terminal plan's
   JSON. §2.3 routes a terminal plan straight here without running §2.5, so
   without this an `assumed` decision nobody ever confirmed would go
   unmentioned at the end of the run — "epic finished with assumed decisions
   nobody looked at" is exactly the silent failure the ledger exists to
   prevent. Printing costs nothing; skip only when both lists are empty.
4. Stop. The coordinator performs no merge at wrap-up. A root Epic or Release
   with a scalar branch or foreign `repo_stamps` owns integration targets, so
   orchestration emits a finish dispatch. The worker consumes every
   `finish_targets` entry, records each successful repository integration and
   inspects the durable receipt before exactly one final advance. Partial
   integration survives restart and keeps the owner at `phase: finish`; `pr`,
   `hold` and `discard` also leave it there. An owner without any source stamp
   has nothing to merge and may use a planned advance. Report target-by-target
   integration from the settled worker evidence; `resolved_in` prefers the
   owner's own repository when present, otherwise the first verified repository
   in deterministic order. The receipt holds the complete multi-repository
   evidence. Never merge these targets again at wrap-up.

For disposable native validation and recorded limits, see
[Multi-repository acceptance](references/multi-repo-acceptance.md).

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
  `gw:finishing-relay`. What this skill owns is only *who answers* the
  `question` it receives: the standing auto-merge policy for a dispatch core
  marks `auto_merge`, mirroring for everything else (§4.3). Whether a dispatch
  is `auto_merge` is core's call, never derived here.
- A vault-wide watcher or scheduled sweep mode. This skill drives exactly
  one path per invocation.
- Auto-retry of failed stages, and automatic merge-conflict resolution for
  parallel forks — both explicit policy (see the failure question and the
  `affects`-disjoint rule), not gaps.
- Explaining or fixing why Orca rejected a historical `worker_done` (the
  caller pane/leaf/incarnation divergence). That is upstream; the design's
  upstream report draft is not filed by this skill, and §4.1.1 only recovers.
