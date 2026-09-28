# Dispatch checks — why each one exists

The legacy `launch-worker.py` sequence below explains the checks now owned by
`gw work dispatch`. Use its primitive commands only when inspecting or resuming
an old attempt; new dispatches use §3's single verb.
No launch reads the coordinator's location.

For each planned-but-undispatched entry from §2.6, first print its selected
agent, model, and reasoning effort (`default` for a null model or effort), plus
the corresponding winning entries from `provenance`. Permissions come from the
selected agent's existing settings; dispatch rules do not select or promise a
permission mode.

The legacy executable recipe was
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

   **Reader preparation (`pin-detached`).** Use `prepare-reader` in place of
   `place`, before task-create and launch. Allocate a fresh random UUID for every new dispatch attempt
   and durably save it before preparation under
   `references/orca-placement/<key>/<preparation-attempt-id>/attempt.json`.
   This preparation identity is distinct from the later Orca `dispatchId`:
   worker-start has not assigned that ID yet. Do not synthesize a dispatch ID.
   `--attempt-id` accepts `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`; a UUID fits.

   ```
   python3 references/launch-worker.py prepare-reader --dispatch <dispatch-json> \
     --attempt-id <preparation-attempt-id> --out-placement <fresh-placement-json> \
     > <workspace>/okf/<dispatch path>/references/orca-placement/<key>.json
   ```

   Use a fresh output path for every invocation, including recovery, for example
   `references/orca-placement/<key>/<preparation-attempt-id>/placement-<invocation-uuid>.json`.
   Save the dispatch input, attempt identity, invocation paths, exit status and
   stdout/stderr as this attempt's evidence. Archive the previous `<key>.json`
   under its attempt directory before another invocation redirects to that
   exact result path. Preserve successful preparation JSON there too; it holds
   `attempt_id`, `path`, `start_sha`, `repo_id`, `placement_argv` and `reused`.
   Persist launch intent before launch and its actual request, task and dispatch
   IDs and start receipt when available, joined to this preparation evidence.
   Never store a preamble or dispatch capability in this evidence.

   Require exit 0 before encode, task-create or launch. File existence alone never authorizes launch.
   On success, `<fresh-placement-json>` contains exactly
   `["--worktree", "path:<canonical-prepared-path>"]`; use it as `<placement-json>`
   in encode below. Launch uses that exact prepared path. Do not substitute an
   integration anchor, a shared epic checkout or a new-worktree creation flag.
   The helper creates a dedicated Orca checkout with setup skipped, detaches
   only that new checkout at `start_sha`, then verifies repository, path, HEAD
   and cleanliness. Never `place` a reader or detach/reset any other checkout.
   A non-zero exit refuses preparation: report
   `PREPARATION REFUSED <key>: <reason>` (or the actual command error), do not
   encode or start, and leave any allocated checkout visible for recovery.
   Before task-create, use only §4.2.1's pre-task decision flow. When a Task
   already exists (resume/retry), use the existing-Task failure question (§4.2).

   Reuse this identity only to recover the same conclusively unlaunched allocation.
   If launch is uncertain, reconcile authoritative Orca request/dispatch state
   before proceeding; a missing response or receipt is not proof of no launch.
   Until that state is conclusive, enter inspection without preparing or launching
   another checkout. Never share a previously launched checkout with a later
   dispatch, even at the same key and SHA. A later dispatch gets a new identity.
   Recovery verifies an already clean detached checkout at the saved SHA;
   never re-detach, reset or remove it. A create-before-detach crash leaves a
   branched allocation that the helper refuses and an operator must inspect.

   (`gw work dispatch` does the same for a reader in process: its marker is
   derived from the key, SHA, repository and attempt ordinal instead of a
   saved UUID, and its evidence is the journaled attempt at `record_path`.)

   Then encode the immutable task spec from the successful placement output:

   ```
   python3 references/launch-worker.py encode \
     --dispatch <dispatch-json> --placement <placement-json> > <spec-file>
   ```

   This writes `GW_LAUNCH_V1 ` plus compact version-2 JSON on the first line,
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

   `place` or `prepare-reader` builds the placement argv; never assemble it by hand. No launch
   reads the coordinator's location — a coordinator in the code repository's
   primary checkout, the wiki repository or the epic worktree issues identical
   calls. (Orca's caller context is the Orca terminal's worktree, not the
   shell's cwd, so `cd` would not change it either.)
   - `pin-detached` → run `prepare-reader --dispatch <dispatch-json>
     --attempt-id <preparation-attempt-id> --out-placement <fresh-placement-json>`
     in place of `place` in step 1, saving stdout at the same
     `references/orca-placement/<key>.json` result path. This dedicated,
     verified detached checkout must be prepared successfully before encode,
     task-create or launch; the resulting argv selects its exact path.
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
   it compares paths only. For `pin-detached`, it compares the prepared and
   observed paths and verifies the detached commit and clean state; it prints
   `branch: null` and `start_sha`. It otherwise prints `path`, `branch` (already stripped of
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
   | `pin-detached` | `settle-placement`: observed path equals the prepared path; `HEAD` equals `start_sha`; HEAD is detached; the checkout is clean. |
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

   For readers, print the detached commit instead:

   ```
   dispatched <key> -> <observed path> detached at <start_sha>
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

   then go directly to the failure question — the current four options
   (*retry* / *reroute* / *skip this item* / *stop the run*) every other dead-or-wrong
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
   2. **Verify the branch.** For non-reader actions, normalize the readback by stripping `refs/heads/`.
      Where the observed path is reachable from this host, run
      `git -C <observed path> branch --show-current`; it must print exactly the
      normalized branch. Empty output (detached HEAD), a missing branch, a
      disagreement or unverifiable evidence is a placement mismatch: halt into
      §4.2 as above. Never record the planned `worktree.branch` in its place.
      For `pin-detached`, require step 4's successful settlement checks instead:
      detached HEAD, clean checkout, observed path and `start_sha` match the
      saved preparation. An empty branch is expected only for that action.
   3. **Record, or skip.** A `pin-detached` dispatch — root or descendant — records a reader receipt
      instead of a placement, only after settlement and identity binding above:

      ```
      gw work record-reader <slug> --root <work-path> --phase <dispatch phase> \
        --task-id <task_id> --dispatch-id <dispatch_id> --dispatch-key <key> \
        --repo <dispatch repo.name> --worktree <observed path> --start-sha <start_sha> --json
      ```

      Use actual task/dispatch IDs from worker-start (or authoritative recovery
      readback) and the SHA verified by settlement, never the preparation UUID.
      Success is exit 0 with `written` or `replayed` true and no refusal/conflict.
      Keep `receipt_path` and `start_sha` in this dispatch's evidence alongside
      the preparation identity and saved result. Receipts live under
      `layout.cache_dir / "reader-receipts"`; they never change phase, stamps or
      `updated`. Never call `record-placement` for a `pin-detached` dispatch.
      `refusal`/`attempt-mismatch` → `PLACEMENT UNRECORDED <key>` and inspection,
      exactly like step 5; include the returned refusal detail or conflict.
      Other non-success, including exit 0 without `written`/`replayed`, follows
      step 6. Reader success goes directly to the submission probe, not the
      scalar placement check below.

      For non-reader actions, an orchestration root's placement is recorded at
      any phase except an Epic/Release root at `design` or `plan`, which is never
      placement-recorded (`read-only-owner`). With current reader pinning, the
      root's placement is recorded only at `execute`/`finish` in practice:
      `design`/`plan` stages are `pin-detached` and use the reader receipt above.
      A descendant's non-reader placement is recorded at `execute`/`finish`.
      Run:

      ```
      gw work record-placement <slug> --root <work-path> --phase <dispatch phase> \
        --worktree <observed path> --branch <normalized branch> \
        --start-sha <the checkout's HEAD read before the worker launched> --json
      ```

      `--start-sha` is the execute baseline the commit gate reads; read it from the checkout before the worker launches and never abbreviate it.

      When `dispatch.repo.name` is `_workspace`, add `--repo _workspace`
      before `--json` in that command. This is a workspace-only item's no-fork
      placement; its workspace stamp already exists from preparation.
      A matching observation is an unchanged no-op. Use the same `--repo`
      on the `--dry-run` verification in step 4.

      A descendant dispatched at `design` or `plan` is never recorded as a
      placement: the current planner places it as a `pin-detached` reader,
      which records the receipt above instead, and an older plan's shared
      read-only descendant records nothing.
   4. **Check.** Scalar placements only. Success is exit 0 with `refusal: null` and `after` equal to the
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
      coordinator's own reading of this dispatch is wrong. `read-only-owner` is expected for an Epic/Release reader; nothing to record
      as a scalar placement. Confirm the action was `pin-detached` and the
      reader receipt above succeeded, then continue to the submission probe.
      Without that proof, halt into inspection; do not reinterpret a refusal
      on a non-reader dispatch as a successful record.
      - `invalid-baseline` — the observed `--start-sha` is not a full lowercase commit OID; re-read it from the checkout, never abbreviate it.
      - `baseline-conflict` — the item already records a different `start_sha` for this same worktree and branch; inspect which commit this stage's work started from before recording again.
      - `baseline-missing` — a code placement would end up with no `start_sha` and none was proved; re-run preparation from a checkout still at its base tip, or record one explicitly.

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

   A lost response is not a refusal. For scalar placements, after a timeout, disconnect or restart,
   repeat step 1 and the `--dry-run` read before recording again; an
   identical replay is a no-op only once attempt, phase and observation are
   re-established. Until then, enter inspection and preserve the task,
   dispatch key and allocated worktree. Do not call `gw work advance` to stamp it,
   do not start another fork, and do not stop or release the worker without
   the authority §4.1.1 requires. For a reader, re-run `settle-placement` with its
   saved preparation, repeat binding, and replay `record-reader` with the same
   actual IDs, phase, path and SHA; an identical receipt returns `replayed: true`.
   Never allocate another checkout to repair a missing receipt. Step 5's submission probe runs only after recording
   succeeded, or for an older plan's non-reader read-only descendant that records nothing.

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
      regardless of how idle the terminal looks. Require a successful read
      for the same dispatch and an explicit heartbeat field; a failed or
      incomplete read leaves the heartbeat unknown and enters inspection.
   2. `orca orchestration worker-read --dispatch <dispatch_id> --limit 5 --json`
      → `result.transcript.messages` non-empty ⇒ **submitted. Never nudge.**
      Require a successful structured read. A transcript is structured JSON, immune to the interleaved redraws
      that make `terminal read` unreadable mid-render. This is also the
      dialog-safety guarantee: putting a dialog on screen is itself agent
      activity and appears in the transcript, so an **empty** transcript from
      source `transcript` supports a nudge below — idle-looking terminals do
      not. A failed or incomplete read enters inspection.
   3. `result.source == "terminal"` (with `fallbackReason` set) ⇒ compare
      against sibling dispatches in the same run. If other workers are
      producing heartbeats and transcripts and this one has produced
      neither since it started, that comparison is decisive on its own —
      treat it as unsent and proceed to the nudge below, regardless of what
      `source` reports. Only fall back to reporting and letting the human
      decide when there are no healthy siblings to compare against (e.g.
      this is the only live dispatch this cycle). Two rules bound this
      branch: the heartbeat veto (case 1) is absolute — a dispatch that has
      ever heartbeat is never nudged, however idle it looks.
   4. For either the healthy-sibling branch or heartbeat null **and**
      transcript empty **and** `result.source == "transcript"`, re-run
      `orca orchestration worker-show --dispatch <dispatch_id> --json`
      immediately before Enter. Only a fresh successful read for the same
      dispatch with explicitly null `result.dispatch.lastHeartbeatAt`
      permits a single nudge; a non-null heartbeat vetoes it, and a failed
      or uncertain read enters inspection. With a verified real terminal
      handle and no intervening command, nudge **once**:

      ```
      orca terminal send --terminal <agent_terminal_handle> --text "" --enter --json
      ```

      **Do not nudge a worker you have not probed.** A bare Enter into a
      live agent answers whatever is on screen with its highlighted
      default — and `mode: attend` dispatches exist to ask the human
      questions, so they are simultaneously the likeliest to look idle and
      the costliest to nudge blind.
   5. Re-run step 2. Non-empty transcript ⇒ recovered, continue normally.
      Two failed nudges → failure flow (§4.2), with the current four options.

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
