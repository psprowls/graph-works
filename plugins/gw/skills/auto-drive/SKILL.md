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
dispatched worker. Wherever this skill says "ask the user," that means the native
`AskUserQuestion` tool, talking to the person running this session — for the
coordinator's **own** decisions (§2.5 blockers, the §4.1 failure question).
A **worker's** question is never asked that way: §4.3 prints it and takes the
human's typed answer later, so one slow decision never blocks the loop.
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
`gw work dispatch` at launch time (§3).
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
crash/compaction resume the same code path as a normal cycle. The one named
exception to §2's rule is §2.7's carried delivery id; losing it only causes a
replay, which §4 already tolerates. Run §2.5.3
after §2.5.2 on every cycle that reaches park handling; terminal plans reconcile
cards in §5 before ending.

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
   — then every dispatch record beneath the workspace —
   `find <workspace>/okf -path '*/references/orca-dispatch/*.json' -type f`
   — then every park checkpoint beneath it —
   `find <workspace>/okf/<work-path> -path '*/references/*-checkpoint-D-*.md' -type f`
   — and pass each resolved file with its own flag (path discovery stays here;
   the helper only receives files):

   ```
   uv run --no-project --python 3.12 python references/launch-worker.py classify-restart \
     --tasks <task-list-json> --workers <worker-list-json> \
     --recovery-record <record-json> [--recovery-record <record-json> ...] \
     --dispatch-record <file> [--dispatch-record <file> ...] \
     --checkpoint <checkpoint-md> [--checkpoint <checkpoint-md> ...]
   ```

   Classifier rows carry `task_title` and `display_name` directly, so use the
   row's `task_title` as its key without joining back to `task-list`. A
   `rerouted` action identifies a superseded Task and carries `reroute:
   {reason, at}`; it is neither a human Skip nor a live key. A superseded
   Task with a live worker is `recovery-inspection` with reason
   `rerouted-but-live`: inspect and stop before dispatching its replacement.

   A checkpoint file is named `NN-<phase>-checkpoint-D-nnn.md` and its
   frontmatter names the `item:` and `phase:` it was written for; the helper
   joins those against each Task's `display_name` (`<work-path> · <phase>`,
   written by §3) because a dispatch key is not reversible to a path.

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
     A durable reroute may subsequently change that completed Task to
     `blocked`. If its recorded Run, key, superseded Dispatch, work path and
     phase match this recovery record, the classifier accepts that specific
     status change while rechecking every other recovery condition above.
     The result is `rerouted` with `recovery.reason: verified`; its terminal
     is still already released.
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
   `result.dispatch.lastHeartbeatAt` used by §3's probe.

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

- `terminal` (bool), `max_parallel` / `slots_free` (ints — the autonomous/relay
  pool only; a live attended session does not reduce it), `max_attend` /
  `attend_slots_free` (ints — the separate pool for `mode: attend` dispatches,
  default 1), `supervise_merges`
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
  `worktree` (`action`: `reuse` | `fork-child` | `create-top-level` | `main` | `pin-detached`,
  `path`, `branch`, `base_branch`, `exists`, `parent_path` — the existing
  worktree a created one is linked beneath, `null` when none — and `start_sha`,
  a full 40- or 64-character lowercase hex commit object ID for `pin-detached`,
  `null` otherwise; `branch` is `null` for `pin-detached`, never `HEAD` or `""`), `merge_target`,
  `auto_merge` (bool — core's verdict that §4.3 step 0 may answer this
  dispatch's finish-relay `merge` question itself; true only for a non-root
  item at `finish` whose merge target is its owner's integration branch, with
  `supervise_merges` off), `prompt`.
- `advances[]` — each: `path`, `reason`, `mode` (`advance`, `return`, or `repair`), `worktree`/`branch` (the epic's
  already-known worktree, when one exists — `null` otherwise, e.g. before any
  worker has ever been dispatched for this epic).
- `workspace_preparations[]` — workspace branch anchors to prepare before
  launching their owners. Each entry carries `owner_path`, `owner_phase`,
  `worktree`, `branch`, and `base_branch`.
- `blocked[]` — each: `path`, `kind` (one of exactly `deps`, `capacity`,
  `affects-overlap`, `effort-required`, `decisions`, `human`,
  `relay-untailed`, `worktree-pending`, `workspace-pending`, `worktree-unsupported`,
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

Peak workers are `max_parallel + max_attend` (the pools are separate, epic
decision 005). Tune either down in `workspace.local.yaml`
(`workflow.auto_drive.max_parallel` / `workflow.auto_drive.max_attend`). The
coordinator still passes only `--live` keys; the planner classifies them.
For each live key, it tries every variant of that key's stage and both values
of `has_spec` and `has_plan` with the item's current non-artifact attributes.
If any resolution is `attend`, the live worker consumes the attend pool. This
keeps its slot when it writes a spec or plan while still running. New
candidates resolve against their actual current attributes.

#### Prepare repository integration anchors before launching

`preparations[]` contains `owner_path`, `owner_phase`, `repo`, `branch`,
`base_branch`, and `worktree`. It reserves no worker slot. Save the complete
plan, then process preparations **serially**:

```bash
uv run --no-project --python 3.12 python "$PLUGIN_ROOT/skills/auto-drive/references/launch-worker.py" prepare \
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

**Workspace preparations.** `workspace_preparations[]` carries `owner_path`,
`owner_phase`, `worktree`, `branch` and `base_branch`. It reserves no worker
slot, and its items are blocked `workspace-pending`. Process the entries
serially, after the code preparations, with
`gw work prepare-workspace <owner_path> --apply --json`. gw creates the
worktree itself, runs no Orca command, and records `repo_stamps[_workspace]`.
A `note` (workspace placement disabled) means the entry would not have been
emitted: replan. A refusal (`workspace-unprovable`, `workspace-ambiguous`,
`stamp-refused`, `not-entitled`) blocks that owner's dependents until a human
repairs it. **Replan after every attempt**, exactly as for code preparations.

### 2.3 Terminal?

`terminal: true` → go to Wrap-up (§5), including its fresh launch-proof gate,
then stop looping. A terminal plan does not establish successful launch proof.
Nothing else in this cycle runs.

### 2.4 Advances

Before each entry, run `gw work next <path from entry> --json` for that exact
path and capture `expected-phase` from its `phase` before mutating anything.
JSON null maps to CLI `none`.
Revalidate the planned gate, return, or repair condition against the fresh next result before acting.
For `mode: advance`, require empty `blockers`, null `action`, and a non-null
`on_complete` whose destination is the intended transition.
For `mode: repair`, require `blocker_kinds` exactly `["phase-off-path"]`, null
`action`, and a fresh §2.2 plan with a matching entry for this exact path,
`mode: repair`, and reason. Run the normal guarded advance below without
`--return` or placement flags; it moves only to the next on-path phase and
performs no skipped-stage completion effects. Any extra blocker, including
`dispatch-preflight`, means report it and replan without advancing.
For `mode:
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
carries them (non-`null`) and its mode is not `repair`. **`--no-infer-worktree` is what keeps it location-independent**: the advance
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
  `human`, `relay-untailed`, `worktree-pending`, `workspace-pending`, `worktree-unsupported`,
  `worktree-unprovable`, `worktree-ambiguous`, `cross-repo-child`, `invalid`):
  print one line each (`blocked <work-path> (<kind>): <reason>`) and take no action.
  `workspace-pending` means the workspace branch is not yet prepared: process
  `workspace_preparations[]` under §2.2 and replan.
  Readers at `design`/`plan` require provable repository and committed-ref
  evidence for `pin-detached`; missing evidence is `worktree-unprovable` and
  a provisioning capability gap is `worktree-unsupported`. Never fall back to
  a mutable anchor. A code stage that may have run but whose worktree cannot
  be found (a lost stamp at `execute`/`finish`) needs a human decision. `capacity` and
  `worktree-pending` resolve themselves next cycle as slots/worktrees free
  up. A `capacity` reason names its pool: `no worker slot free` is `max_parallel`,
  `no attend slot free` is `max_attend`. An attend-pool block is expected while
  a design session waits on the human; it is not a stall.
  `deps`, `affects-overlap`, `human`, `cross-repo-child`, and `invalid`
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
  `affects-overlap` means another live or already-planned dispatch holds an
  overlapping write claim; the reason names the scope and its holder. Only
  execute and finish hold `affects` claims, so a design or plan stage is never
  `affects-overlap` and never causes one (it reads a pinned commit). An item
  with empty `affects` claims its whole repository (the reason says
  `<repo> (whole repository)`), and a `gw:workspace` item claims the
  workspace (`workspace`), so either can serialize behind, or ahead of, its
  executing or finishing siblings. `decisions` means an open ledger entry is
  holding the item's re-dispatch; the entry itself is named in `open_decisions[]` below,
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
map this session never holds). The durable route is the one §3 already
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
   session could never have called this itself even if it tried. Save the output
   and branch on `classify-lifecycle --op stop --authority park`
   (**Release and stop receipts**).
3. Only for `done`, `worker-stop` settles the Task to `blocked` as a side effect (spike O4-O7:
   `workerState: stopped`, `dispatchStatus: failed`, Task `blocked`) — issue no
   separate `task-update` call for this. For `abandon-then-close`, Orca settles
   `stop_unknown` to `blocked`; follow the shared abandon/close rules but do
   not claim a verified stop. For `inspect`, report the unresolved receipt.
4. Do not release the worker (`worker-release`) here. A park is not a
   completion; beyond the classifier-authorized abandon/close, leave the
   terminal/resource alone until a resume (§2.6) or an explicit human decision.
5. For `done`, print `parked <key>: stopped <dispatch_id>, hold <decision.id> at
   <decision.checkpoint>, action <action>`. Otherwise print
   `parked <key>: <dispatch_id>, hold <decision.id> at <decision.checkpoint>, action <action>`
   and its unresolved outcome; never label an unverified stop as stopped.
   Refresh §2.1 before §2.5.3 or further dispatch planning.

A key whose dispatch was already stopped in an earlier cycle has already left
the live-key map (its Task is `blocked`), so this step is naturally a no-op for
it on later cycles — nothing to track between cycles.

### 2.5.3 Attend cards

**Unconditional, every cycle, after §2.5.2.** A worktree's card is
`in-review` exactly while at least one **live** `mode: attend` dispatch uses
it. Nothing remembers this between cycles: derive it from a fresh task-list
and classifier output, so a crash, a restart or a replayed `worker_done`
cannot leave it wrong.

**Refresh §2.1 after mutations before reconciling cards**: after park,
settlement, release, abandon, close, Task updates or dispatch, re-read the full
task-list, collect every worker-list page and rerun classify-restart with the
current recovery records, dispatch records and checkpoints. Save that matching
task-list and classification for the command below; never reuse pre-mutation
live rows. This also applies to immediate dispatch and wrap-up reconciliation.
Without a complete fresh snapshot, report inspection and move no cards.

```
uv run --no-project --python 3.12 python references/launch-worker.py attend-cards --tasks <task-list-json> --classification <classify-restart-json>
```

It prints `set_in_review`, `release_candidates` (worktrees used by an attend
Task in this Run with no live attend dispatch left) and `skipped` (Tasks whose
frozen envelope does not decode, predates `mode`, or names no `path:`
worktree — they never move a card). The frozen V2 `mode` and `worktree_path` are the
source, never the current plan or an inferred path. Then:

1. For each `set_in_review` path:
   `orca worktree set --worktree path:<p> --workspace-status in-review`
   (idempotent).
2. For each `release_candidates` path, read
   `orca worktree show --worktree path:<p> --json`. Only when its
   `result.worktree.workspaceStatus` still reports `in-review`, run
   `orca worktree set --worktree path:<p> --workspace-status in-progress`.
   A card the human moved (to `completed`, say) is never overwritten.
3. Print one line per change (`card <p>: in-review` / `card <p>: in-progress`)
   and one line per `skipped` entry.

"Live" is §2.1's `live` class and nothing else: parked, stopped, abandoned,
skipped and settled attend dispatches all free the card once no other live
attend dispatch shares the worktree. Several attend designs on `main` keep it
`in-review` until the last one settles.

### 2.6 Dispatch diff

Dispatch only `dispatches[]` entries whose key has no Task other than
`rerouted` ones — `gw work dispatch` enforces the same rule itself. A key with
a current Task is live, settled, or an intentional skip (§4.1); leave it alone.
For each undispatched entry, run Dispatch mechanics (§3).

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
   `gw work wait` intervals. Derive the answer from the Task's own Dispatch
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
5. Recover the original saved dispatch evidence joined to the Task from step 2:
   `gw work dispatch` saved the entry at
   `references/orca-placement/<key>.dispatch.json` and journaled the attempt
   at its `record_path`. For `pin-detached`, require the original saved reader
   dispatch input and preparation evidence (that entry and that attempt), and
   verify that its key/repository match this Task and that its
   `start_sha` matches the baseline in the frozen Task prompt and the
   journaled `placement.start_sha`. Missing, malformed or mismatched original
   evidence refuses resume: leave the Task/checkpoint and checkout evidence
   intact and surface the refusal for inspection. Never substitute this
   cycle's planner baseline, even if the source branch has advanced. Use that
   original dispatch JSON for `prepare-reader` (the legacy recipe's reader
   preparation in `references/dispatch-checks.md`, step 1), with a fresh preparation identity
   and fresh output path; never reuse the parked reader's checkout. Preserve
   the original evidence before saving the new result. Require exit 0 before
   building the resume spec or launching. For non-reader actions, run `place`
   for this cycle's own `dispatches[]` entry for this path (its `action` will
   read `reuse`, like any other re-dispatch of an item with a recorded
   placement), redirecting stdout as the legacy recipe does:

   ```
   uv run --no-project --python 3.12 python references/launch-worker.py place --dispatch <dispatch-json> \
     --repo-path <dispatch repo.path> --out-placement <placement-json-file> \
     > <workspace>/okf/<dispatch path>/references/orca-placement/<key>.json
   ```

   record-placement commits this file with the placement; never commit it yourself.

   `settle-placement` hard-requires `--placement-result` to exist and parse
   — even for `reuse`/`main` — so this redirect is not optional here either.
   `<placement-json-file>` is the successful helper output (for a reader, use
   its fresh output file), holding `["--worktree", "path:<resolved path>"]` —
   `launch --recovery-placement` opens its argument **as a path** and will
   not accept an inline JSON literal. Then build the resume spec:
   ```
   uv run --no-project --python 3.12 python references/launch-worker.py resume-spec \
     --spec <the original spec file from step 2> \
     --checkpoint <the checkpoint file path> \
     --answer "<the answer text from step 3>" \
     --placement <placement-json-file> \
     > <new-spec-file>
   ```
6. Launch as a retry of the **same** Task, not a fresh one — skip the legacy
   recipe's create and encode steps in `references/dispatch-checks.md`
   (build/encode/create) entirely for this key:
   ```
   uv run --no-project --python 3.12 python references/launch-worker.py launch --spec <new-spec-file> \
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
8. Continue at the legacy recipe's step 4 in `references/dispatch-checks.md`
   (`settle-placement`, then verify and record observed placement) —
   everything from there is identical to an ordinary legacy dispatch. For
   non-readers, use this cycle's dispatch and `place` result with
   `<start-json>`. For readers, use
   `settle-placement --dispatch <original-saved-reader-dispatch-json>` (the
   saved entry) with this resume attempt's successful preparation result and
   `<start-json>`. Record the reader receipt with the original `start_sha`,
   the freshly verified path and the actual resumed task/dispatch IDs through
   `gw work record-reader`; retain that evidence together. Neither settlement nor
   recording may use this cycle's planner entry.

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

When all three hold, the loop has nothing to wait *for*: `gw work wait` would
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
gw work wait --run <run_id> [--ack <delivery_id>] --timeout-s 600 --json
```

One call waits, and acknowledges the previous event first. The result carries
`status` (`event` or `timeout`), `delivery_id`, `messages[]`, `absorbed[]`,
`self_acked`, `rebound`, `sleep_gap` (`{seconds}` or null), `waited_s`,
`pending_questions` (null when the read failed), `warnings`, and `liveness`
(rows on positive timeout only, else null). A positive-timeout wait strips
heartbeats and self-acks heartbeat-only or absorbed-only deliveries. A zero
timeout preserves the entire probe delivery unacked, including heartbeats.

- **Carried state: the delivery id to ack.** This is
  the one named exception to §2's self-contained-iteration rule. Carry `delivery_id` into the next
  call's `--ack` only once every message in its batch is handled under the
  rules below. A deferred ack is simply the next call without `--ack`; Orca
  replays the same batch. Losing the id (restart, compaction) causes that same
  replay. There is no standalone ack, and `--ack` is ignored at
  `--timeout-s 0`.
- **On `status: event`:** process `messages[]` under §4. For each
  `absorbed[]` entry print `absorbed duplicate worker_done <dispatch_id>` and
  take no action. Report `rebound: true` (the verb re-bound a fenced consumer)
  and any `sleep_gap` (the host slept; wall-clock ages across it are not idle
  time).
- **On delivery, before mirroring:** obtain fresh pending labels with
  this wait result's own `pending_questions`; no separate read is needed
  here. Match each question's
  `message_id` to this read's `pending_questions` entry and use its `label`
  in §4.3; suffix collisions can change labels between reads.
  Never invent a label or reuse a cached label when the refresh fails.
  If `pending_questions` is `null`, or a question needing mirroring has no
  matching entry, report the failed refresh or missing entry and inspect the
  disposition below: answered and ended questions will never regain a label.
  Without positive closure/reply evidence, leave its delivery unacked for replay.
  Defer mirroring until a successful fresh read supplies a label; an unprinted
  question is not mirrored.
  Continue handling other messages under §4, including the existing step 0
  policy-answer path and its reply guards for questions not proven closed;
  do not count a deferred question as handled. A successfully policy-answered
  question needs no mirroring.
- **Missing delivery question: prove disposition before ack.** Use the original
  delivered message id and sender `dispatch:<dispatch_id>`, not a label or a
  guessed current attempt. Read
  `orca orchestration worker-show --dispatch <dispatch_id> --json` successfully.
  Match `result.dispatch.id`, `runId`, and `taskId` to the delivered
  question's Dispatch, this Run, and its Task (join through §2.1's complete
  worker-list when the payload lacks `taskId`). Conflicting or unprovable
  identities mean inspection and deferral, never acknowledgment.
  With those identities proved, either of these is positive evidence:
  - **Already answered:** read
    `orca orchestration inbox --terminal dispatch:<dispatch_id> --limit 1000 --json`.
    Require an actual reply row with
    `thread_id == <message_id>`, `from_handle == run:<run_id>`,
    `to_handle == dispatch:<dispatch_id>`, and `type == status`
    (the existing `reply --id` linkage, subject `Re: Question`). Report the
    matching message id and that its question was already answered, including
    after restart; a typed answer file alone does not prove delivery to Orca.
  - **Dispatch ended:** require matching `result.worker.dispatchId` and that
    `result.worker.state` is `succeeded`, `failed`, or `stopped`.
    PTY exit, a missing worker, or an omitted pending entry is not this proof.
    Report the original message id, Dispatch id and observed terminal state.
  Absence, a warning, a failed/truncated read, or an unknown state alone
  never proves closure. A matching positive row still proves its own fact in
  a bounded read; missing rows prove nothing. Show warnings and retain an
  inconclusive question for replay while handling other messages.

  For a positively ended Dispatch with no proven reply, apply §4.3 step 2a's
  path resolution and fresh park check before counting the question handled.
  A matching park/checkpoint is reported with its decision id as awaiting a
  decision; without a human answer, do not write a decision answer or claim
  the item is resumable. If an actual human answer is available, save it only
  to that verified hold as step 2a requires. No park → route through §4.2's
  failure flow, even for an ended success with an unanswered question.
  An already-answered question does not waive any failed/stopped Dispatch's
  recovery or any completion validation. If recovery remains unresolved,
  preserve the applicable §4 recovery record before any ack; that record
  cannot substitute for the positive question-disposition evidence above.
  After reporting the evidence and completing the required routing, count
  this question as handled without mirroring it as pending or re-running
  §4.3 step 0's policy answer.
  Do not create a label, send a new reply, or require a new answer.
  Closure handles only this question; every other batch message still needs
  its handling and recovery record under the next two bullets before ack.
  These checks use Orca reads and session memory only, not a new pending file.

  Protocol scenarios (all retain the batch-wide ack gate):
  - Ended-before-mirror: positive ended evidence → fresh park/failure routing
    → report closure → question handled without a pending label.
  - Answered-before-ack/restart: matching reply evidence → report already answered
    → question handled without another reply or a remembered label.
  - Inconclusive read: no matching positive evidence → defer, keep delivery unacked
    → handle other messages and retry the reads on replay.
- **Handle, then acknowledge:** process **every** message in the batch (§4)
  before acking, preserving the recovery-record requirement below. Only when
  every message is handled, acknowledge by passing the result's `delivery_id`
  as the next wait's `--ack <delivery_id>` (a bound Run replays the same
  delivery until acked — don't ack before every message in the batch is
  handled). For a question, *handled* means *mirrored to the human*, not *answered*.
  The positive closure/reply path above also handles an already closed question.
  Once §4.3 has printed it, it satisfies the mirroring requirement; the question
  stays pending in Orca until `reply --id` answers it or its Dispatch ends.
  Continue to the pending display below, even if ack was deferred.
- **Rejected or unprovable completion:** a delivery holding a
  `claimed-unconfirmed` report (§4.1) may be acked only once that report's
  recovery record is written with its identities, the evidence so far, and
  either an `unresolved` reason or a later checkpoint, and every other
  message in the batch is processed. Without that record, do not ack — the
  replayed delivery is the only copy. After the ack, each cycle's §2.1 row
  carrying `recovery` resumes §4.1.1. A timeout never launches a replacement,
  and an unresolved active worker keeps its live key.
- **On `status: timeout`, triage from `liveness[]`**, one row per still-live
  dispatch, instead of a per-dispatch `worker-show` sweep. Rows carry facts
  (`key`, `handle`, `state`, heartbeat/transcript/output instants and ages,
  `worktree_path`, `progress`, `notes[]`, `terminal`, `gate_wait`), never a verdict. A `failed` or
  `stopped` row → failure flow (§4.2), then continue to the pending display
  below. A `worker-show failed` note or an unknown state enters inspection
  without nudging.
  - **A row with a non-null `gate_wait` is decided by `gate_wait.state` first, before the probe.** `running`: the worker is parked on a gate (`gw work gate run --notify`); do not probe or nudge it, and print `waiting on gate <run_id>` as its progress line. `woken`: an ordinary live row. `wake_failed` or `orphaned`: the runner's wake did not land, so type the resume line yourself, once, with `orca terminal send --terminal <gate_wait.terminal> --text "<gate_wait.resume_line>" --enter --json`, print `resumed gate <run_id>`, and do not also probe that row this cycle; the worker's own `gate wait` then settles it, so the next timeout no longer reports it. A null `gate_wait` is an ordinary row.
  Any other `ready`/`running` row → before looping back into another
  wait, run §3's **Manual ordered probe** on that dispatch, unchanged: it
  takes its own fresh reads and keeps its heartbeat veto. An `attend` dispatch
  may legitimately be waiting on a dialog, while an unsent prompt can report
  `running` indefinitely; elapsed idle time decides neither case. Print one
  progress line per row: key, state, heartbeat age, and SDD task counts when
  `progress` is present. With a `sleep_gap`, ages are not idle time.
- **Verb failure.** A nonzero exit carries `reason: refused` and payload
  `{run_id, code}`; nothing unprocessed was acked.
  Verb failure with code `consumer_fenced` means the verb's own rebind failed: re-run §1's bind once
  and retry the wait. Any other code → report it and stop the loop;
  re-running `/gw:auto-drive <work-path>` resumes cleanly.
- **On both delivery and timeout, display pending before restarting.**
  After the handling above, refresh the pending set:
  `gw work wait --run <run_id> --timeout-s 0 --json` — a zero
  timeout probes binding without waiting, acks nothing, and derives the Run's
  unanswered questions. If the refresh returns an event, process its entire
  batch under the handling rules above and retain its delivery ID for later
  ack; display that result's pending set without another recursive refresh.
  A timeout whose triage sent, stopped
  or replied to nothing may display its own result's `pending_questions`
  instead. Print its `pending_questions` as
  §4.3 step 1a says, and show its `warnings`. A failed read prints the refresh
  failure notice, not an invented pending list. If this display finally mirrors
  a deferred delivery question, or positive evidence handles its closure,
  acknowledge only after every message meets
  the handling and recovery-record safeguards above.
  Only after the pending display, restart the cycle at §2.1.

**Orca behaviour the verb does not absorb.**

- The "You have N orchestration messages" text Orca types into the
  coordinator terminal is noise. Do not run `check` or `inbox` in response;
  the next `gw work wait` receives the messages (stablyai/orca#14910,
  stablyai/orca#16822).
- A zero-timeout `gw work wait` probes the Run binding with a non-blocking
  `check`, recovers a reported consumer fence once, and fences the pending
  read too. `pending_questions: null` plus warnings means unknown; a successful
  empty read is `[]`. This is bounded recovery, not an atomic snapshot across
  the independent check and inbox calls.
- A zero-timeout refresh can return a delivery, including heartbeat or duplicate
  completion messages. Handle its entire batch under the rules above before
  later ack; the refresh preserves every message and ignores any supplied ack.
- Raw reply-proof `inbox --terminal` reads can still return `count: 0` under
  a fence (stablyai/orca#21226). An empty raw read is "unknown", not "none"; retain the
  positive-evidence safeguards above before ack.

## 3. Dispatch mechanics

For each planned-but-undispatched entry from §2.6, save this cycle's
`gw work orchestrate --json` output once (e.g. `<scratch>/plan.json`), then run:

```
gw work dispatch <key> --plan <scratch>/plan.json --run <run_id> --json
```

**Execute checkpoint notice (warn, never gate).** Before running
`gw work dispatch` for an entry whose `phase` is `execute`, read that entry's
`human_checkpoints` from the saved plan and print exactly one line:

- `status: declared` with items →
  `execute <path> will stop for a human <N> time(s): <item>; <item>`
- `status: missing`, `malformed` or `unreadable` →
  `execute <path>: human checkpoints unknown (<status>)`
- `status: declared` with no items (`None.`), or `status: no-plan` → print nothing.

Then dispatch as usual. The checkpoints are the plan's intended behaviour, so
this is a heads-up for the human, not a question; never hold the dispatch on it.

It places, creates the Task (title `<key>`, display name `<work-path> · <phase>`),
launches from the frozen v2 envelope, reads back and verifies the placement,
records it on eligible items, sets attend worktrees `in-review`, probes
submission, and marks the Task `dispatched`. It never re-plans. Re-running it
with the same key resumes a journaled attempt or reports the existing Task; it
never creates a second one. Why each check exists: `references/dispatch-checks.md`.
Permissions come from the selected agent's existing settings; dispatch rules
do not select or promise a permission mode.

Follow `references/dispatch-checks.md` steps 3–5 for recording and refusal
handling, including workspace-only no-fork dispatches and Epic/Release readers.

**Readers (`pin-detached`).** A `design`/`plan` dispatch is a reader placed
on a dedicated checkout detached at the plan's `worktree.start_sha`. The verb
prepares it before the Task exists: it lists the repository's Orca worktrees
for a checkout already carrying this attempt's marker comment (a crash between
creation and the journal write is recovered this way, and an ambiguous or
unverifiable marked checkout refuses — it is never reset), otherwise creates
one through `orca worktree create --no-parent --setup skip --comment <marker>`,
verifies the new checkout is clean, correctly rooted and not an integration
checkout, detaches only that new checkout at `start_sha`, and verifies
repository, root, detached HEAD, commit and cleanliness again. Settlement
verifies the observed path equals the prepared one, no parent was attached,
and the content check still holds; a branch name is never consulted.
A `pin-detached` dispatch — root or descendant — records a reader receipt
under `layout.cache_dir / "reader-receipts"` (the `gw work record-reader`
contract: attempt-keyed, phase-checked, replay-safe) instead of a placement
stamp; the item page, its stamps and `updated` are untouched.
Never call `record-placement` for a `pin-detached` dispatch. A later attempt
at the same key and SHA gets a new marker, so a launched checkout is never
shared. A
preparation refusal is `placement-refused` at step `place` with no Task
created: handle it under §4.2.1, not the four-option failure question.

**On success** print the dispatch's agent, model and reasoning effort (`default`
for null), using the attempt matching `task_id`/`dispatch_id` in the durable
dispatch record at `record_path` and its frozen envelope (or the legacy Task
spec when there is no record). `DispatchResult` does
not carry those profile fields. Print their `provenance` lines from this cycle's
saved plan entry; report a reroute override as such, without treating plan
provenance as its source. If `status: existing` has `placement: null`, print
`existing <task_id> for <key>; placement was not read back`. Report a Dispatch ID
only if present; do not substitute the saved plan for unobserved placement.
Run §2.5.3 once now, refreshing §2.1 after the dispatch mutation, so the card
moves without waiting a cycle. Preserve §4.3's structured ask and §4.4's
needs-you notice: the human answers in the worker terminal, not here.
When placement is present, print `dispatched <key> -> <placement.path> on
<placement.branch>` and one `note <key>: <text>` per `placement.notes` entry.
For a reader (`placement.branch` is `null` and `placement.start_sha` is set)
print the detached commit instead:

```
dispatched <key> -> <observed path> detached at <start_sha>
```

`probe: inconclusive` can mean either the heartbeat read or transcript read
failed. Run the manual probe below; the result alone never authorizes Enter.

`delivery` is separate from `probe`. `verified` means this attempt's worker echoed its receipt line (task, dispatch, key and the token from the end of its frozen brief) in assistant text. `unverified` means no matching receipt was seen in a complete transcript window, including a worker that asked for its brief, a heartbeat-only worker, or a legacy attempt without a token; `inconclusive` means the read could not prove absence (terminal source, clipped window, failed read). A new attempt with `--no-probe` reports `skipped`; an existing dispatch invoked with `--no-probe` skips the transcript re-read and preserves its prior journal observation. None of these authorizes Enter, a re-paste, a replacement dispatch or cleanup: report `delivery unverified for <key> (<dispatch_id>)` and follow the missing-receipt runbook in `references/dispatch-checks.md`. For a completed, previously probed attempt with a persisted token and `unverified` or `inconclusive` delivery, re-running the same `gw work dispatch <key>` with probing enabled reads the transcript once and can upgrade a later matching receipt to `verified` without launching anything. An originally skipped probe, a tokenless legacy attempt, or an already verified observation keeps its recorded facts and is not re-read.

### Manual ordered probe (timeout or inconclusive)

For the affected dispatch, allow a short settle interval after launch and run
these checks in order. Stop at the first decisive submitted signal:

1. Run `orca orchestration worker-show --dispatch <dispatch_id> --json`.
   Require a successful response for this dispatch and read
   `result.dispatch.lastHeartbeatAt`. Any non-null heartbeat proves submission:
   never nudge. A failed read, missing field, or uncertain identity enters
   inspection without nudging. Require a real `agentTerminalHandle` from the
   saved worker-list or worker-show response before considering terminal
   commands; absence of a handle means report and inspect, not a guessed
   terminal.
2. Run `orca orchestration worker-read --dispatch <dispatch_id> --limit 5 --json`.
   Require a successful structured response. Non-empty
   `result.transcript.messages` proves submission: never nudge. An empty
   transcript with `result.source == "transcript"` supports one Enter nudge.
   If the read fails or its messages are unknown, enter inspection. If
   `result.source == "terminal"` with `fallbackReason`, compare sibling
   dispatches in the same Run. Nudge only when healthy siblings have shown
   heartbeats or transcripts and this dispatch has shown neither; with no
   healthy sibling, report and let the human decide. An unexpected source
   enters inspection.
3. Immediately before either allowed Enter branch, run a fresh successful
   `worker-show` with
   `orca orchestration worker-show --dispatch <dispatch_id> --json`.
   Require the same dispatch and an explicitly null
   `result.dispatch.lastHeartbeatAt`. If that read fails or its heartbeat is
   unknown, enter inspection without nudging.
   A non-null heartbeat vetoes Enter even if the earlier read was null.
   After this check, with no intervening command, nudge once:

   ```
   orca terminal send --terminal <agent_terminal_handle> --text "" --enter --json
   ```

4. Re-run the `worker-read` command from step 2. A non-empty transcript means
   recovered; continue normally. If it is still empty, repeat the entire
   probe once, including the fresh heartbeat check immediately before a
   second Enter. After two failed nudges, use the failure question (§4.2).
   Never nudge a dispatch that has ever heartbeat. Why these checks exist:
   `references/dispatch-checks.md`.

**On failure** stdout is `{"error": {..., "payload": <envelope>}}`. Branch on
`error.payload.failure.reason`:

| `reason` | Do |
|---|---|
| `placement-refused` | Print `PLACEMENT REFUSED <key>: <detail>`. No Task was created. For a `pin-detached` entry this is a reader preparation refusal: go to §4.2.1 (a checkout may have been allocated; it is left for inspection). Otherwise plan nothing that depends on this item; surface it; the key is re-proposed once the cause is fixed. |
| `placement-mismatch` | Print `PLACEMENT MISMATCH <key>: <detail>` and go to the failure question (§4.2). |
| `placement-unrecorded` | Print `PLACEMENT UNRECORDED <key>: <refusal or detail>` and enter inspection: preserve the Task, key and worktree; do not advance, re-record, fork, stop or release. |
| `launch-failed`, `receipt-mismatch`, `outcome-unknown`, `unsent`, `task-create-failed`, `task-update-failed` | Failure question (§4.2), with `failure.task_id`/`failure.dispatch_id` reported. For `outcome-unknown` and any recovery uncertainty, inspect the full Task spec, ambiguous start response and its `stage`/`failedStage`/`recovery` hints, plus fresh `worker-show` evidence. A missing terminal proves nothing. Retain IDs and resources; the failure question never authorizes blind retry. |
| `recovery-inspection`, `record-invalid` | Inspection: report every id in `failure`, the `record_path`, and `failure.step`. Never re-run `dispatch` blind. |
| `plan-invalid`, `key-not-in-plan`, `override-invalid` | The plan or a reroute is wrong: report it and replan next cycle. |

A payload of `null` means the command failed before dispatching (workspace,
usage): `error.reason`/`error.message` say why.

The `launch-worker.py` primitives (`place`, `prepare-reader`, `encode`,
`create`, `launch`, `settle-placement`) remain callable for a coordinator
resumed on the old recipe, and for the park-resume (§2.6) and retry (§4.1)
paths, which relaunch an existing Task; new dispatches do not use them.

### Gates

The coordinator runs no gates. Workers gate through `gw work gate run` and `gw work gate wait`; gw records the receipt. A worker resumed after a crash or park calls `gw work gate wait <work-path>` before anything else, so an in-flight run is reused rather than restarted. An advance refused `no-gate-receipt` or `no-gate-configured` reaches the human through the same skip-gate question as every other fail-closed gate refusal: the human either answers with `--skip-gate <code> --reason "…"` or sends the worker back to run the gate (or to have `repositories.<name>.gate.full` configured). A worker parked on `gw work gate run --notify` is woken by the runner; §2.7's `gate_wait` rule keeps the coordinator from nudging it.

## 4. Delivery processing

Handle every message in the `gw work wait` batch (§2.7) — one at a time —
before acking.

### 4.1 `worker_done`

Classify each `worker_done` before reading its outcome. Save the message
object from the wait result's `messages[]` and run:

```
uv run --no-project --python 3.12 python references/launch-worker.py classify-report --message <message-json>
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

**Gate refusal.** A `worker_done --outcome failed` whose subject starts with `gate refused: <code>` is the `execute -> finish` gate failing closed, not a crashed worker. Do not re-dispatch the stage blindly. Show the human the code and the body's detail and ask whether to fix the cause (re-dispatch execute) or bypass exactly that code. On a bypass, run `gw work advance <path> --from execute --skip-gate <code> --reason "<the human's reason>" --actor <the human's handle> --no-infer-worktree` yourself — workers never pass `--skip-gate` — then continue the loop from `gw work orchestrate`.

- **Success branch**: refresh the full task-list and worker-list and run
  §2.1's classifier before acknowledging the delivery. Require its `settled`
  result for this exact task/dispatch pair: this validates the full frozen
  envelope and key plus durable requested/effective proof from worker-show.
  Missing or mismatched proof enters recovery with both IDs; do not ack or
  release it. A valid `worker_done` already settles its Task and Dispatch;
  never issue `task-update completed` (§4.1.1 step 5 is the single recorded
  coordinator exception). Then run
  `orca orchestration worker-release --dispatch <dispatch_id> --json` (no
  `--run` flag), save its output, and branch on
  `classify-lifecycle --op release` (**Release and stop receipts**).
  The coordinator commits nothing in the workspace.
  Refresh §2.1 and run §2.5.3 after these mutations to free the attend card.
  → if the settled dispatch's phase was `execute`, run the coverage read
  (**Coverage read**, below): resolve the work path and phase from the settled
  task's `display_name` in the full task-list this branch just refreshed —
  split on the first ` · `, left half the path, right half the phase, exactly
  as §2.5.2 does; never parse the dispatch key. A row whose `display_name` is
  missing or does not split cannot be resolved: say so in one line and
  continue — the read is best-effort, never a reason to hold the delivery
  → if the settled dispatch's phase was `finish`, then after `worker-release`
  run **Finish cleanup** (below) only when the release classifier action is
  `done`. For every other action, skip Finish cleanup and report the retained
  or uncertain terminal for wrap-up. The same `display_name` split resolves
  the work path; it is best-effort and never holds the delivery
  → nothing else; the next cycle's plan (§2.2) picks up the new state
  naturally.

### 4.1.1 Completion claimed, settlement unconfirmed

A rejected completion is still a claim that stage work finished, but neither
its delivery nor its `outcome` settles the Dispatch. Orca's caller checks —
capability, pane/leaf, process incarnation — are authority boundaries: never
resend the report for the worker, never rewrite `--from`, and
never infer identity from a terminal-handle prefix.
The historical caller-identity cause remains unverified upstream; this branch
recovers without explaining it. The verb absorbs a late duplicate only for a released single-attempt completion
(§2.7's `absorbed[]`); every other rejected or late report still arrives here.

Recovery records live at
`<workspace>/okf/<dispatched-item-path>/references/orca-settlement/<dispatch_id>.json`
and are written only through:

```
uv run --no-project --python 3.12 python references/launch-worker.py record-write --path <record-file> --record <record-json>
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
   `uv run --no-project --python 3.12 python references/launch-worker.py spec-hash --tasks <task-list-json> --task <task_id>`
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
      Branch on `classify-lifecycle --op stop --authority <recorded stop authority>`
      (**Release and stop receipts**): `exit-evidence` / `user-authorized` map
      directly; `already-settled` never reaches a stop. Journal the action.
      `abandon-then-close` / `abandon-report` journal `worker-abandon` as an
      additional mutation with the same intent → receipt protocol; journal
      any exact close outcome separately. Step 5.3 does not record
      `stopped-verified`: set `unresolved: stop_unknown` and follow step 5's
      refusal rule. `inspect` also remains unresolved and interrupts the sequence.
      Never issue a redundant stop against a settled attempt.
   3. Record `stopped-verified`. The tested control read `workerState:
      stopped`, `dispatchStatus: failed` and Task `blocked`; a zero exit code
      alone is not settlement — `stop_unknown` is the named case. Require
      positive fresh readback before recording this checkpoint.
   4. Before invoking worker-release, persist its intent at `release-requested` using the operation journal below.
      Run `orca orchestration worker-release --dispatch <dispatch_id> --json`.
      After worker-release returns, append its original request ID and sanitized receipt reference using the operation journal below.
      Branch on `classify-lifecycle --op release` (**Release and stop receipts**)
      and journal its `action` in history beside the receipt. `done` can record
      `released-verified` after fresh verification. `close-terminal` can record
      it only after successful exact-handle close and positive confirmation;
      missing handle or failed/unverifiable close remains unresolved. Journal
      close intent and its original receipt/outcome separately. `report-retained`,
      `follow-receipt` and `inspect` are unresolved resource recovery: follow
      the receipt's literal next action and set `unresolved`.
      There is no automatic abandon fallback.
   5. Before invoking task-update, persist its intent at `completion-requested` using the operation journal below.
      Then run
      `orca orchestration task-update --id <task_id> --status completed --run <run_id> --json`
      — the one recorded coordinator exception to "never issue
      `task-update completed`", not a second `worker_done`.
      After task-update returns, append its original request ID and sanitized receipt reference using the operation journal below.
      Then record `completed-verified`.

   **Operation journal (every actual mutation, including abandon and close).** Before invocation,
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
6. **Finish.** Refresh §2.1 and run §2.5.3 to reconcile attend cards. Do
   not run `gw work advance` — the worker already advanced the item. The next
   cycle's classifier reports this key `recovered-settled`.

A replayed delivery or a restart re-enters at step 1 and resumes from the
record's checkpoint against fresh state. It never repeats a verified stop,
release, Task completion or stage advance.

### Release and stop receipts

Save every `worker-release --json` and `worker-stop --json` output, including
nonzero/error receipts, and branch on the helper, never on your own reading
of the receipt. A missing/unreadable receipt or failed classifier is unresolved
inspection; do not infer success from command exit. Use these full commands
at every call site (the shorter references above name the operation/authority):

```
uv run --no-project --python 3.12 python references/launch-worker.py classify-lifecycle --op release --result <receipt-json> --mode <mode>
uv run --no-project --python 3.12 python references/launch-worker.py classify-lifecycle --op stop --result <receipt-json> --authority <park|user-authorized|exit-evidence|none>
```

`<mode>` is the `mode` in the dispatch's frozen envelope
(`launch-worker.py decode --spec <saved-task-spec>`), never the current plan.
A legacy or undecodable envelope supplies no attend
authority: omit `--mode` and inspect/report retention without closing.

| `--op release` action | Receipt | Do |
|---|---|---|
| `done` | `released`, `already_released` | nothing more |
| `close-terminal` | `retained` / `user_takeover`, attend dispatch | read `orca orchestration worker-show --dispatch <dispatch_id> --json`; take its exact `result.worker.agentTerminalHandle`; close and verify as below, then print `closed joined attend terminal <handle> (<key>)`. No handle → print `retained, no handle: <dispatch_id> (<key>)` and close nothing. No question: the human was told to answer in that terminal and the stage is settled. |
| `report-retained` | `retained`, any other reason or mode | print `retained (<reason>): <handle> (<key>) — the human's terminal`; act on nothing |
| `follow-receipt` | `release_pending`, `release_unknown` | follow the receipt's literal next action; never `terminal close` for `release_pending` or `release_unknown` |
| `inspect` | anything else | print the receipt; act on nothing |

For release, `terminal close` requires an accepted settlement of that exact
dispatch. For stop, it requires the authorized `abandon-then-close` branch
below and a confirmed abandon. In either case, read worker-show successfully,
verify its Dispatch/Task identity, and use only its exact
`result.worker.agentTerminalHandle` — never a prefix or `--worktree … --all`.
Run `orca terminal close --terminal <handle> --json`, saving its receipt.
The classifier's `close-terminal` action is an instruction, not proof:
require successful exact-handle close and positive confirmation from the
receipt or supported authoritative readback that this exact terminal closed.
A zero exit code or terminal absence alone is insufficient; a missing handle
or failed/unverifiable close remains unresolved. Do not print a closed message
or record `released-verified` until that evidence is saved. In §4.1.1, journal
close intent, original request identity, receipt and confirmation separately
using its operation journal; a missing original identity remains unresolved.

**Recovery journal and verification.** Journal abandon as `worker-abandon`
and exact close as `terminal-close` in separate `mutations` entries at the
current requested checkpoint (`stop-requested` or `release-requested`). Both
use the same null-ID intent, original-ID receipt and append-only history rules
as stop/release; neither creates a new verified checkpoint.
If record-write rejects an abandon/close intent, do not invoke it: keep the
existing checkpoint unresolved, append the refusal to its supported history,
and surface the refusal. Never disguise abandon/close as a release. Likewise, verified release
checkpoints require fresh `terminalState: released` and
`resource.releaseState: released`, plus the existing exit evidence.
A confirmed close does not override a record-write verification refusal:
retain its receipt/history and stay unresolved if resource state is still
retained or unverifiable. These constraints apply to §4.1.1 recovery; ordinary
accepted settlements still use the receipt branches above.

| `--op stop` action | Receipt | Do |
|---|---|---|
| `done` | `stopped`, or `alreadySettled` with a terminal worker state | continue the call site's existing path |
| `abandon-then-close` | `stop_unknown` under park, `user-authorized` or `exit-evidence` authority | run `orca orchestration worker-abandon --dispatch <dispatch_id> --json`, save and verify the abandon receipt, then close the exact `result.worker.agentTerminalHandle` from worker-show under the checks above. Only after positive close confirmation print `stopped by close: <handle> (<key>)`; missing handle or failed abandon/close remains unresolved |
| `abandon-report` | `stop_unknown`, no such authority | run `orca orchestration worker-abandon --dispatch <dispatch_id> --json`, save and verify the receipt, then print `live and muted: <handle> (<key>) — orca #21688` and leave the terminal alone |
| `inspect` | `stopping` or anything else | print the receipt; act on nothing |

`stop_unknown` never records `stopped-verified`, even after an authorized
abandon and verified exact close. Recovery keeps `unresolved: stop_unknown`
and follows §4.1.1 step 5's refusal rule; no completion or release follows
from this receipt. Journal abandon and exact close as separate mutations,
including their outcomes; a failed abandon authorizes no close.

`stop_unknown` is never counted as stopped. Orca settles it with
`processAction: none` when the worker terminal is user-owned, external, or its
process could not be verified, and it revokes the Dispatch capability anyway:
the agent may still be running but can no longer report (orca #21688).
This is the defensive contract for that receipt, not an observed takeover
trigger from the pending Task 2 matrix. Abandon fences orchestration state;
only the verified close establishes that the terminal was closed.

**What marks a terminal user-owned** — installed **Orca 1.4.211**.
Task 2's [receipt evidence](../../tests/fixtures/orca-lifecycle/README.md)
includes ordinary Task 3 cleanup and a later controlled live matrix. The
table reports the full stimulus sequence for each case; a retained release
does not identify which action in a combined sequence marked the terminal.

| Trigger/scenario | Observed receipt | Limit / cleanup |
| --- | --- | --- |
| No interaction | succeeded; release `released` / `closed_agent_terminal`; repeat `already_released` | Controlled baseline, separate from Task 3 cleanup |
| CLI tab switch followed by physical pane click, no typing | succeeded; release `retained` / `user_takeover` | Focus actions were not isolated; exact terminal close `ptyKilled: true` and readback exited |
| Physical Space after readiness, no Enter | succeeded; release `retained` / `user_takeover` | Exact terminal close `ptyKilled: true` and readback exited |
| Key before readiness | timing **unverified**; no valid early-input result | Human could not confirm timing; worker settled and closed solely for cleanup |
| Controlled restart with no pane input | worker resumed and succeeded; release `released` / `closed_agent_terminal` | External observer verified app PID/runtime change; human confirmed no input. Successful script was bound inside Orca and continued after restart |
| Clean active stop | `stopped` / `closed_agent_terminal`, `close.ptyKilled: true`; repeat `alreadySettled: true` | Script paused for manual recovery; `worker-list` proved exited and directed release, which returned `released` |
| User-joined active stop after physical Space | `stop_unknown` / `processAction: none` | Controller explicitly abandoned, then checked identity and closed exact terminal; never count as stopped |
| Context-only release; `release_pending` / `release_unknown` | not observed | Synthetic classifier contracts remain necessary |

The original external-shell restart launch failed with
`no_active_sender_terminal` before worker creation. The completed restart
trial used a dedicated bound Orca shell and separate read-only observer;
after restart, the controller re-listed handles and resumed its ask by saved
message ID before continuing the surviving script. It was not an end-to-end
external script run.

### Coverage read (execute dispatches only)

Runs from the Success branch only — on `accepted-success`, after §2.1's
`settled` result and after `worker-release`, never on `claimed-unconfirmed`
(§4.1.1 owns that path) and never before the ack rules. The producer is
`EXECUTE_TAIL`: every execute dispatch is told to write the item's execute
stage artifact, one `- [x]` / `- [ ]` line per design-spec `## Acceptance`
item, and to pass it as `--report-path`. Nothing gates on its contents; this
step is what makes the report worth writing.

1. Run `gw work next <path> --json --file ""` and read the file at its
   `artifacts.execute.path`. Absent → one-line note and continue. The obligation
   is unenforced, and a missing file is not a failure.
2. Surface the enumeration **as-is** — print the file's lines, do not
   summarize them.
3. If any line is `- [ ]` (a marker scan, not comprehension), raise one
   `AskUserQuestion` with exactly two options — *send it back* / *accept
   anyway*. No retry option: a dispatch that succeeded is not a recovery case,
   and the failure question's Retry is authorized only by Orca's failure
   response.
   - **Send it back**:
     1. Run `gw work next <path> --json` and require `phase: finish`. If the item is not at `finish`, report that plainly and do not advance.
     2. If the send-back is for work beyond the unchecked coverage lines (for example new plan tasks or review findings), name each piece with `--return-scope "<one line>"`. Otherwise omit it to return the coverage obligations.
     3. Run `gw work advance <path> --from finish --return [--return-scope …] --no-infer-worktree` and require success. On `return-scope-required`, ask for scope. On `return-pending`, inspect and do not retry with different scope. After success, run `gw work next <path> --json` and read `carried_context.slots.execute_return.data.return_id` for `<return-id>`.
     4. Read the execute key's attempt state fresh using §2.1. If a settled Task exists for the key, run `gw work reroute <key> --run <run_id> --reason "execute return <return-id>"` with no agent, model or effort overrides and require success. If there is no prior Task, skip the reroute. On a live, outcome-unknown or ambiguous attempt, stop and report.
     5. Replan (§2.2) and dispatch.

     A phase change alone never relaunches execute.
   - **Accept anyway**: continue; the coverage file stands as the record. The
     advance already recorded each unchecked line as an `origin: coverage`
     entry in the item's `finish_obligations`, and the finish question lists
     them; the coordinator writes nothing.

What this does and does not claim: it makes an omission visible to a human at
the moment the worker settles, and gates nothing. It cannot repair a model
that marks a box `- [x]` dishonestly.

### Finish cleanup (finish dispatches only)

Runs from the Success branch only — on `accepted-success`, after §2.1's
`settled` result and after `worker-release`, and only when the release
classifier action is `done` (`released` or `already_released`); never on
`claimed-unconfirmed`. For every other classifier action, skip Finish cleanup
and report the retained or uncertain terminal for wrap-up's confirmed sweep.

1. Run `gw work next <path> --json`. Continue only when `gw work next <path> --json` reports `work_status: resolved`
   (with `phase: done`). A finish that held (`pr` / `hold` / `discard`) runs
   nothing.
2. From the coordinator's own cwd, run
   `finish-receipt.py cleanup <path> --workspace <workspace> --runner-cwd "$PWD"`
   and execute the plan per
   [finish-cleanup.md](../finishing-relay/references/finish-cleanup.md),
   including the relay's `deferred` row when it now returns `remove`: its
   terminal is already released. If the coordinator cwd is inside an item worktree, its row comes back `deferred`;
   leave it for a later sweep from another directory.
3. Print one line per removed or skipped row. A refusal is one line. Nothing
   here changes item state, retries, or holds the delivery.

### Failure question

Used from two places: `worker_done --outcome failed` (above) and a dead
worker discovered outside any `worker_done` message (§2.1 or §2.7's
wait-timeout `worker-show`) — the design's no-auto-retry policy applies to
both identically, so it's specified once here, not duplicated.
This question requires an existing Task. Reader preparation failure before
task-create goes to §4.2.1 instead; it cannot use this question's Skip branch.

One `AskUserQuestion` with exactly four options — *retry* / *reroute* /
*skip this item* / *stop the run*:

- **Retry**: retry only when Orca's failure response and recovery inspection
  explicitly authorize it. Read the existing task from full
  `task-list --run <run_id> --json`, save its untruncated `spec`, and validate
  the envelope. Missing, malformed, truncated, or wrong-key state blocks
  recovery. Never consult edited dispatch configuration or a fresh plan for
  this task. Preserve the original requested placement and observed allocated
  resource evidence. For non-reader actions, follow Orca's recovery verdict
  so an allocated worktree is reused rather than duplicated.

  For `pin-detached`, reconcile authoritative Orca request/dispatch state
  before preparing anything. Uncertain launch stays in inspection; neither a
  timeout nor a missing receipt proves the checkout was unlaunched. A new
  dispatch attempt requires a fresh preparation identity: never reuse a previously launched reader checkout,
  even if its worker has settled and the key/SHA are unchanged. Recover the
  same conclusively unlaunched allocation only with its durably saved identity.
  In both cases invoke the legacy recipe's `prepare-reader` (step 1 in
  `references/dispatch-checks.md`) using the saved dispatch entry
  (`references/orca-placement/<key>.dispatch.json`) and a fresh output path;
  require exit 0 before launch. Retain the original SHA and profile. As in
  §2.6's reader resume, require original dispatch/preparation evidence
  matching the frozen Task prompt's baseline;
  missing or mismatched evidence refuses rather than using a current plan.
  Preserve the prior preparation result under
  its attempt directory before saving the new result at
  `references/orca-placement/<key>.json`. Use the successful fresh argv file
  as `<recovery-approved-placement-json>` below. Never `place` a reader or
  derive authorization from an old output file.

  For an existing Task with an authorized retry, run:

  ```
  uv run --no-project --python 3.12 python references/launch-worker.py launch --spec <saved-task-spec> \
    --task <task_id> --dispatch-key <task-title> --run <run_id> \
    --retry-of <dispatch_id> \
    --recovery-placement <recovery-approved-placement-json> > <start-json>
  ```

  `--recovery-placement` opens its argument **as a path** holding a bare JSON
  argv list — the same shape `place --out-placement` or `prepare-reader`
  writes, not the keyed object its stdout redirect produces. Readers use only
  the successful fresh output just prepared. For non-readers, get that argv the sanctioned way: either
  re-run `place` against the recovery-approved worktree to regenerate it, or
  lift the `placement_argv` array out of the saved `place` result
  (`references/orca-placement/<key>.json`) into its own file. Never
  hand-assemble it (`references/dispatch-checks.md`).

  The frozen envelope retains the originally requested placement; the explicit
  recovery placement is the argv Orca authorized after accounting for observed
  allocated resources (for example, `path:<allocated path>` instead of a second
  creation). The recipe repeats the frozen agent/model/effort and verifies
  the new requested/effective receipt. `--retry-of` alone does not inherit
  those values. Timeout, absent output, missing terminal, or ambiguous start never
  authorizes retry; preserve task/dispatch identities and follow recovery.
  An explicit reroute is a new deliberate dispatch decision and task identity.
  Then run `settle-placement` with the saved `place` result for non-readers,
  or this attempt's successful `prepare-reader` result for readers
  (`references/orca-placement/<key>.json`), and the retry's `<start-json>`.
  Readers pass the saved reader dispatch entry to settlement and record the
  original `start_sha` with the retry's actual task/dispatch IDs through
  `gw work record-reader`. The allocated worktree still owes its placement
  checks and, for a code creation, planned lineage. Once
  the retry's own placement assertion passes, record its observed placement exactly as `references/dispatch-checks.md` describes; never change its frozen envelope.
- **Reroute**: ask for a reason and optional agent, model, and reasoning effort.
  This deliberately supersedes the settled Task and creates a new Task identity
  on the next cycle. Run:

  ```
  gw work reroute <key> --run <run_id> --reason "<reason>" \
    [--agent A] [--model M] [--effort E] --json
  ```

  On `reroute-live`, explain that the worker must be stopped first through
  the existing Stop path; retain its Task and Dispatch IDs until settlement
  is proven. On success, the next cycle re-proposes the key and `gw work
  dispatch` applies the journaled override (a rerouted reader gets a fresh
  checkout under its new attempt marker). An uncertain outcome or
  `recovery-inspection` requires inspection, never a blind reroute.
- **Skip**: `orca orchestration task-update --id <task_id> --status blocked
  --run <run_id>`. The durable `blocked` status distinguishes this deliberate
  choice from a task reserved before a crash but never started. The task stays
  in the Run, so §2.6 never re-proposes the key.
- **Stop the run**: exit the loop. Report run state (what's done, what's
  live, what's blocked). For each still-live dispatch, ask (plain text) if
  the user wants `orca orchestration worker-stop --dispatch <id> --json`.
  Only after yes, save the output and branch on
  `classify-lifecycle --op stop --authority user-authorized`
  (**Release and stop receipts**). Refresh §2.1, reconcile §2.5.3, report any
  unresolved or live-and-muted worker, then stop.

### 4.2 Failure flow (dead worker found outside a `worker_done` message)

Identical to the `outcome: failed` branch above — run the failure question.
Triggered from §2.1's live-derivation or §2.7's wait-timeout `worker-show`.

### 4.2.1 Reader preparation failure before Task creation

`gw work dispatch` reports a reader's preparation refusal as
`placement-refused` at step `place` with `task_id: null`: no Task exists yet,
so use a separate question with exactly two options — *retry preparation* /
*stop the run*. Do not offer Skip, and never call `task-update` or invent
task/dispatch IDs. Save the question and answer in the preparation attempt's
evidence (the result JSON, `record_path` and
`references/orca-placement/<key>.json`, which names the attempt marker)
before acting. Preserve the saved dispatch input, identity and result; leave
any allocated checkout visible for inspection.

- **Retry preparation**: only after the user chooses it and recovery
  inspection authorizes it. Re-run the same `gw work dispatch <key>` call with
  the same saved plan: the verb re-derives this attempt's marker, recovers a
  conclusively unlaunched allocation only when it is still verified (detached
  at `start_sha`, clean, correctly rooted) and otherwise refuses again; it
  never re-detaches, resets or removes a checkout. If launch state is
  uncertain, reconcile authoritative Orca request/dispatch state first —
  a missing response or receipt is not proof of no launch. Another refusal
  returns to this question; uncertainty remains in inspection and authorizes
  no launch. On the legacy recipe, follow its step 1 reader recovery rules
  in `references/dispatch-checks.md` instead.
- **Stop the run**: exit the coordinator loop using the existing Stop branch's
  reporting and still-live-dispatch handling. Include the refused key, reason
  and evidence paths. Do not return to planning and automatically re-propose
  this key. A later explicitly resumed run must inspect the saved preparation
  evidence before trying this key again.

### 4.3 `question`

Any non-attend worker sends this through its own `orca orchestration ask`
when it needs a human decision. A current worker sends a **typed** ask,
prepared with `gw work ask`: its question text ends in a line
`gw-ask: <resource>` naming a `gw.ask/1` payload file that holds the full
question. The finish stage's merge/PR/hold/discard decision is the commonest
one. This coordinator applies **one fixed, published policy to one
structurally identified case** — a child's merge into its owner's integration
branch, step 0 — and relays everything else to the human. It never decides
what the options *mean*; that is the worker's job.

0. **Auto-answer the integration choice?** Answer it yourself, without mirroring, when
   **both** hold. The answer is the dispatch's **strategy token**: the `default_strategy` of the first entry in that dispatch's `finish_targets` (from the same read-only plan entry as `auto_merge`) whose `default_strategy` is non-null, or `merge` when none has one:

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
   2. The question's `options` include that strategy token.

   `auto_merge` already folds in everything about *where* the merge lands:
   `supervise_merges` off, a non-root item, and a merge target that is the
   owner's integration branch rather than the release base. Core computes it
   next to `merge_target`, so this skill never re-derives it from paths or
   frontmatter — do not compare `path` fields or walk parents yourself. An
   Epic or Release root, a lone item, and any merge into the release base
   carry `auto_merge: false` and mirror.

   Guard 2 is the detached-HEAD guard. A worker on a detached HEAD drops
   `squash`, `merge` and `ff` from its own options, and testing the options rather than
   re-deriving git state keeps you out of the worker's business — any case
   the worker itself judged un-mergeable falls through to the human
   automatically. It also excludes the option-less discard-confirmation ask
   of step 4, which must never be auto-answered.

   **`pr`, `hold` and `discard` are never auto-answered, under any
   condition.** The policy chooses only the strategy token: an untyped ask replies with
   that literal string, while a typed ask replies with the recorded answer's
   `reply_body`. Otherwise mirror to the human; there is no third choice.

   **This is not the coordinator guessing an answer** (cf. §2.5.1, which
   forbids exactly that). Nothing is inferred per question. The decision was
   made once, by a human, at design time; it is recorded in this skill's
   prose and in `workflow.auto_drive.supervise_merges`, which that human can
   flip to take every merge question back. §2.5.1 bars inventing an answer
   nobody gave — a standing, published policy applied to a structurally
   verified class of question is the opposite of that.

   When both hold, **on a typed ask** (step 1's marker test), record who
   answered before replying:
   `gw work ask-answer <resource> --choice <token> --by policy:auto-merge --json`.
   Require a successful exit, `ok: true`, and a nonempty string `reply_body`
   in its JSON result. An identical already-recorded answer can satisfy this
   check; `changed: false` alone is not a refusal. Use that `reply_body` as
   the step 2 body.
   On refusal or an unusable `reply_body`, show the result to the human
   and take their direction by label (step 1b); do not print `auto-merged`, reply,
   fabricate a body, overwrite the payload, or automatically resend a
   conflicting answer. For `already-answered`, read and show the recorded
   answer alongside the proposed token, then let the human decide which
   stands. Keep the worker's question
   pending until a valid recorded reply body is available and the human directs
   the relay. Do not fall through to an automatic reply or repeat this policy
   attempt for the same refused ask.

   **On an untyped ask**, the step 2 body is the bare strategy token, preserving
   the legacy fallback. Only after that check, print the `auto-merged` notice
   in this session (for an untyped ask, after selecting the literal body) — no
   human prompt, no outward worktree comment or status push:

   ```
   auto-merged <work-path> -> <merge_target> (child of <root-path>; not mirrored)
   ```

   Go straight to step 2. **Steps 2 and 3 are not optional on this path**: an
   undelivered auto-answer strands the worker exactly as an undelivered human
   answer does, and with no human watching for it. If either guard fails,
   continue to step 1 and mirror as usual.

1. **Mirror it — print, never block.** Every still-pending worker question step 0 did not
   answer is *mirrored*: printed in this session, then left pending while the
   loop runs on. The coordinator never waits on the human for a worker
   question; the human answers later, by label (step 1b), in any order. A
   question's label is its `label` in `gw work wait`'s `pending_questions`
   (an opaque, collision-aware label). Use §2.7's fresh read before delivery
   mirroring, matching by `message_id`; never derive the label yourself. If
   that read fails or has no matching entry, follow §2.7's positive-evidence
   disposition path; defer if inconclusive. Proven closed questions need no mirror.
   Once printed, it is handled for §2.7's ack rule.

   Typed or untyped? Test whether the question text's last line starts with `gw-ask: `.
   - **Typed.** The rest of that line is the payload's root-absolute
     resource. Read the file at `$GRAPH_WORKS_DIR/okf<resource>` as JSON
     (`schema: "gw.ask/1"`).
     1. Print `<label> <key> — <kind>`, then the payload's `question` in this
        session **verbatim**. Never summarize, condense or truncate it.
     2. Print what an answer looks like, by the payload's `kind`:
        - `spec-review`: `approve`, or `request changes: <notes>`; and an
          effort (`xtra-small`/`small`/`medium`/`large`/`xtra-large`) with
          `current_effort` marked — required only when `current_effort` is
          null.
        - `choice`: one line per `options[]` entry, `<token> — <label>`.
        - `free`: `a free-text answer is expected`.
   - **Untyped** (no `gw-ask:` line — a worker from before typed asks):
     print `untyped ask from <key>` first, so the gap stays visible while old
     workers drain. Then print `<label> <key> — untyped`, the question text
     verbatim, and its options; the human's text will be the reply body.

1a. **Each cycle, print the pending block.** After §2.7's pending read, for
    each entry of `pending_questions`: one not yet printed in full in this
    session → print it as step 1; one already printed → one line,
    `<label> <key> — <kind>, waiting since <asked_at>`. Which questions were
    printed is session memory only: after a restart every pending question
    prints in full once more. `[]` prints nothing. `null` means the read
    failed — print `pending questions: refresh failed`, keep the last printed
    list as historical display only, and show the result's `warnings`. Do not
    use that list's labels to mirror a new delivery or mark an unprinted
    question as mirrored. Track printed questions by `message_id`, not label,
    so reminders use the current read's label even when suffixes collide.

1b. **Take a typed answer.** The human types into this terminal whenever they
    like — `q-e862 merge`, `q-1a2b approve, effort medium`,
    `q-77c0 request changes: tighten scope`. It reaches you at your next turn
    boundary, when the current wait returns. Then:
    1. **Resolve the label against a fresh `pending_questions` read** (the
       zero-timeout `gw work wait` of §2.7), never a cached one. An unknown
       label, or a question no longer pending, is reported back to the human
       and nothing is sent. With no label: if exactly one question is
       pending, it is that one; if more than one is, the answer is ambiguous
       — print the pending labels and ask which is meant. Never guess.
    2. **Record.** For a typed ask, map the human's words onto
       `gw work ask-answer <resource> [--choice <token>] [--effort <value>] [--notes "<text>"] --json`
       (default `--by human`): `--choice` takes a `choice` option's `token`,
       or `approve`/`changes` for `spec-review`; `--effort` when the human
       states one; `--notes` carries free text and change requests. The CLI
       is the validator. On a refusal (`answer-choice-invalid`,
       `answer-effort-required`, `answer-effort-invalid`,
       `answer-notes-required`, or `answer-by-invalid` for a blank `--by`),
       print the refusal; the question stays pending and the human answers
       again by label. **Never fix an answer up yourself.**
       `already-answered` means a different answer is already recorded:
       print both and let the human say which stands; never overwrite.
       The `reply_body` from that call is the step 2 `--body`. For an untyped
       ask, the human's text after the label is the body.
    3. Continue with steps 2, 2a and 3 — unchanged.
2. Reply, and **read the response** — `--json` is not optional here:

   ```
   orca orchestration reply --id <message_id> --body "<the step 1 or step 0 body>" --run <run_id> --json
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
       the question was even printed. Run
       `gw work orchestrate <work-path> --json` again now (or
       `gw work decision list <owner-path> --json` when the owner is already
       known) and look for an entry naming this path with
       `decision.hold == "park"` and a non-null `decision.checkpoint`.
    3. **Park found** → the answer is not lost. Write it as that hold's
       decision answer:
       ```
       gw work decision answer <owner_path> <decision.id> --answer "<the human's answer as text: the reply body for a typed ask>" --json
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
   question/reply round-trip initiated by the worker — a `free` typed ask, handled by
   step 1 like any other, assertion included. **It is never auto-answered**:
   step 0 excludes it twice over — by the `pr`/`hold`/`discard` rule, and
   structurally, because a `free` ask carries no options and so fails guard 2.

### 4.4 `escalation`

**Attend ping first.** If the escalation's subject starts with `Needs you at `,
it is an attended worker's single "a human is wanted in my terminal" notice
(its prompt's `ATTEND_TAIL`). Print exactly

```
needs-you <work-path> <phase> at <terminal handle>
```

taking `<work-path>` and `<phase>` from the body's first two words (before the
`:`), and `<terminal handle>` from the subject after `Needs you at `. Raise no
`AskUserQuestion` and continue the loop. The human answers in that terminal,
not here. Card state for attend dispatches is derived by §2.5.3.

Every other escalation (when its body names `no-gate-receipt` or `no-gate-configured`, see `### Gates` in section 3):

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

Orca's batches can carry worker heartbeats, but `gw work wait` (§2.7)
strips them and self-acks heartbeat-only deliveries (`self_acked`), so §4
never sees one; liveness between events comes from the timeout `liveness[]`
rows.

## 5. Resume & wrap-up

**Resume** is just re-running `/gw:auto-drive <work-path>` (§1 re-binds
the same Run by objective). Cycle 1's live-derivation (§2.1) classifies
every existing task — live, settled, or dead — before anything else
happens; dead dispatches enter the failure flow immediately, except rows
carrying `recovery`, which resume §4.1.1 from their checkpoint. Nothing is
reconstructed from conversation memory: a fresh session with zero context
resumes identically to one that's been running for hours. For a journaled
dispatch attempt, rerun `gw work dispatch` with the key and a saved current
plan only when the record permits replay; `recovery-inspection` and
`record-invalid` require inspection, never a blind retry. A legacy dispatch
whose placement is unrecorded after a restart re-enters the check procedure in
`references/dispatch-checks.md`:
write `{"ok": true, "result": {"dispatchId": "<dispatch_id>"}}` as
`<start-json>` (the helper then finds the worktree through `worker-show`),
re-run `settle-placement` against its saved
`references/orca-placement/<key>.json` (a crash between start and the lineage
`set` leaves a correctly based child with no parent, which this repairs
without launching anything), then enter the record block at its step 1.
For a reader, select the preparation result bound to that actual dispatch —
the journaled attempt's `placement` (`path`, `start_sha`) for a verb dispatch,
or the archived legacy preparation output. Recheck its detached SHA and clean
path, then replay `gw work record-reader` with the same actual IDs, phase,
path and SHA (an identical receipt returns `replayed: true`); do not prepare a
new checkout to repair the receipt.

**Wrap-up** (§2.3 reported `terminal: true`):

1. Refresh the full task-list and worker-list and run §2.1's classifier again,
   even when the plan reports `terminal: true`.
   Release eligibility uses both the fresh classifier and its matching worker row:

   | Classifier row | Release rule |
   |---|---|
   | `settled` | Release a verified successful dispatch that still holds an unreleased terminal. |
   | `rerouted` without `recovery` | Release only when `workerState: succeeded` and `dispatchStatus: completed`, with an unreleased terminal; the classifier has reverified its frozen spec and launch receipt. |
   | `rerouted` with `recovery.reason: current-accepted-success` | Apply the same succeeded/completed worker and unreleased-terminal checks. |
   | `rerouted` with `recovery.reason: verified` | Already released; never release again. |
   | Other `rerouted` rows | Retain; reroute alone does not establish successful completion. |

   Join the worker row by both `task_id`/`taskId` and
   `dispatch_id`/`dispatchId`; a missing or ambiguous match requires inspection.
   For each eligible dispatch, run
   `orca orchestration worker-release --dispatch <id> --json` (no `--run` flag)
   for that exact dispatch, save its output and branch on
   `classify-lifecycle --op release` (**Release and stop receipts**).
   Failed/stopped superseded workers never enter this
   successful-completion release path.
   For `recovery-inspection`, retain and print its task/dispatch IDs, inspect
   recovery, and stop without releasing it or reporting verified completion.
   A missing terminal never substitutes for launch proof.
   `recovered-settled` is already released: never issue a second `worker-release` for `recovered-settled`.
   A `recovery-inspection` row carrying `recovery` is unresolved: print its
   task/dispatch IDs, record path, checkpoint and reason, and stop without
   reporting verified completion.
2. **Sweep leftovers under one confirmation.** Build one list:
   - **Retained dispatches:** every dispatch whose `worker-release` in step 1
     reported `retained`, with its terminal handle. A `recovery-inspection`
     row never appears here; step 1 already stopped on it.
   - **Unresolved-release items:** every resolved item in the run's subtree
     whose own dispatch was tracked by step 1's refreshed classifier but did
     **not** reach a `done` release action (`released` / `already_released`)
     and is not already in **Retained dispatches** — i.e. its release action
     was `follow-receipt` (`release_pending` / `release_unknown`) or the row
     stopped as `recovery-inspection`. There is no positive evidence its
     terminal is closed, and the release rule forbids `terminal close` for
     `release_pending` / `release_unknown` outright, so these items are
     never eligible for automatic removal. List them by path and dispatch id
     without computing a cleanup plan for them.
   - **Leftover rows:** for every resolved item in the run's subtree that is
     *not* in **Unresolved-release items** — i.e. its own dispatch (if this
     run tracks one) reached release action `done`, or no dispatch for it is
     tracked by this run at all — run
     `finish-receipt.py cleanup <path> --workspace <workspace> --runner-cwd "$PWD"`
     and collect its `remove` rows. Normally empty, because the Success
     branch already cleaned up; this catches crashes and restarts.
   - **The epic anchor:** only when the root item resolved — its own
     `cleanup` plan covers the anchor.

   Present the whole list with one `AskUserQuestion` (*remove all* /
   *leave them*): everything goes under one confirmation, never one question per
   row. **Unresolved-release items never execute, under either answer** — print
   them as-is, both in the question and in the leftover report; *remove all*
   authorizes only the Retained-dispatches and Leftover-rows entries. On
   *remove all*, close each retained terminal with
   `orca terminal close --terminal <handle>` and positively verify each terminal is closed before executing its rows
   (use the close receipt and fresh terminal readback). If a terminal close fails or cannot be verified, skip its affected rows;
   when its worktree cannot be matched to rows with confidence, retain those rows too.
   Then execute the remaining rows per
   [finish-cleanup.md](../finishing-relay/references/finish-cleanup.md). On
   *leave them*, print the list as the run's leftovers. An empty list asks
   nothing.

   Then print the **unpushed warning**, report-only. For every repository in
   the run's finish targets plus the workspace repository (the workspace's
   own Git checkout, resolved separately from `repositories.<repo>` code
   checkouts), run `git -C <repo> rev-list --count "<target_branch>@{upstream}..<target_branch>"`
   — the named target branch's own upstream, not the repo's currently
   checked-out branch, quoted because bare `@{upstream}..<target_branch>` is
   wrong when the checkout differs. When it is greater than 0, print the
   repo, the branch and the ahead count. If the command fails, print the command failure verbatim
   with the repo and branch; do not assume that every failure means no upstream.
   Wrap-up never pushes.
3. Run §2.5.3 once more: refresh step 1's full task-list, worker-list and
   classifier output after its release/close mutations, so a finished run
   leaves no stale `in-review` card.
4. Print a run summary: items resolved, branches merged back (from each
   settled dispatch's `merge_target`), **auto-answered merges as their own
   line item** — the §4.3 step 0 children, listed separately from the
   questions a human actually answered, so a run that merged twelve children
   unattended says so in one place — anything skipped (§4.1's skip
   choices this run — keys the fresh classifier calls `deliberate-skip`, not
   `parked` or `rerouted`), anything **parked** (keys classified `parked`, each with the
   checkpoint and the decision id still awaiting an answer — say plainly that
   these are not skips and that answering the decision resumes them),
   anything left in `blocked[]`. Report accepted worker
   completions (`settled`), coordinator-recovered completions
   (`recovered-settled`, naming each record), and unresolved recovery
   records as three separate lists. Under a separate **Reroutes** heading,
   list each `rerouted` row's key, reason, overrides, and superseded Task ID.
   Use the row's `task_title` for the key and `reroute.reason` for its reason;
   read overrides and superseded identity from the corresponding durable
   dispatch record's `reroutes` entry. Restart rows carry only `reason` and
   `at` under `reroute`, so do not invent override fields or add them to the
   public classifier schema. Keep these entries separate from human skips.
5. Print the §2.5 decision lines one final time from the terminal plan's
   JSON. §2.3 routes a terminal plan straight here without running §2.5, so
   without this an `assumed` decision nobody ever confirmed would go
   unmentioned at the end of the run — "epic finished with assumed decisions
   nobody looked at" is exactly the silent failure the ledger exists to
   prevent. Printing costs nothing; skip only when both lists are empty.
6. **Dirty-workspace check.** Run `git -C <workspace> status --porcelain -- okf/work`.
   Non-empty → print one warning line listing the dirty paths (a verb whose commit
   failed, or a hook that timed out). Never commit them yourself. Dirty reference
   files for the owning item are swept by its next committing same-item gw verb;
   for other dirty paths, retry the owning operation or run a gw verb that writes those exact paths.
7. Stop. The coordinator performs no merge at wrap-up. A root Epic or Release
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
`orca orchestration worker-stop --dispatch <id> --json` for each one.
Only after yes, save its output and branch on
`classify-lifecycle --op stop --authority user-authorized`
(**Release and stop receipts**), as in §4.1's Stop branch. Refresh §2.1 and
reconcile §2.5.3 before exiting, and report any unresolved or live-and-muted worker.

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
