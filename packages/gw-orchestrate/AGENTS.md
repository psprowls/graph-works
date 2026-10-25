# AGENTS.md — gw-orchestrate

Band 3 application slice. Permitted dependencies: gw-kernel, gw-pipeline and declared lower packages.
Forbidden edges: gw-work, gw-wiki and interfaces at runtime.
See the [root band table](../../AGENTS.md) and the root `pyproject.toml` contracts
"The application band is a tree of five slices beneath the interfaces" and
"Pipeline consumers never import the wiki slice".

Band 3 application slice. Receives resolved `gw_kernel.workspace` primitives;
workspace discovery remains in kernel; shared pipeline primitives live in gw-pipeline.
Imports are restricted to stdlib, this package, `gw_kernel.workspace`, `gw_pipeline` and declared lower packages. No kernel facade API, other sibling runtime import or kernel reverse dependency.
`gw_work` is a dev-only dependency for consumer integration tests.

## Commands

```bash
just check-pkg gw-orchestrate
uv run --package gw-orchestrate pytest packages/gw-orchestrate/tests
```

The package gate runs strict mypy on linux and win32 and branch coverage at 95%.
Fixtures are explicitly re-exported from `gw_kernel.testing`; pytest is a dev
dependency only. Keep `GateEnv` and `env` in the tests-root conftest, and private
scenario helpers local; never import kernel test modules.

## Modules

`commands.py` and `stage_advance.py` own planning and stage advancement;
`placing.py` is the one adapter to the gw-pipeline placement engine (compute, resolve, and `realize` with `verify_only`).
Workspace writes land on the primary checkout: no module prepares, stamps or merges a workspace branch.
`finish_receipt.record_finish_in` uses an existing
owner lock and never reacquires ownership. `gate.py` resolves a gate request
(item, repository, worktree, clean tree), shares one pending run per
`RunKey` (`repo`, `tree`, `scope`, `command`) across the whole workspace in the flat
`cache_dir/gate/runs/`; a joining item is appended to the record's `requesters`, and join,
queue order, recording progress and pruning are decided under `runs/.dir.lock`. It spawns
`gate_runner.py` detached; the runner holds `<run>.lock` for its whole life (the only
liveness signal), waits for a `gate/slots/slot-<i>.lock` when `workflow.gate.max_concurrent`
(in the workflow file) is set, and appends the result to every requester's `03-gate-receipts.md` through
`gate_receipts.record_gate_run`. With `--notify`, `gate run` registers the caller's `ORCA_TERMINAL_HANDLE` in the record's `waiters`; after every receipt the runner types `gate_wake.RESUME_LINE` into each waiter's terminal (`gate_wake.py`, one `orca terminal send`, never importing workflow-orca) and records `woken`. `gate_wait_facts` derives a per-terminal `running`/`woken`/`wake_failed`/`orphaned` fact from the runner lock and the record, which `gw work wait` joins onto timeout liveness rows by their `terminal`. Receipt lookups read through `gate_index.py`, a
stat-validated cache at `cache_dir/gate/receipts.json`. With `gate.units` configured, `gate_units.py` validates the repository's unit manifest and hashes each unit's input closure from `git ls-tree`; `gate_receipts.evaluate` accepts a unit green recorded by any item for the same hash (only `ran: true` entries of clean, unchanged runs, D-004), and the runner executes setup, repo-wide, then the stale units on a pool with per-unit logs under `<git-dir>/gw-gate/<item>/<run_id>/`. Repositories without `gate.units` are one implicit `tree` unit running `gate.full`. `gate_git.py` is the gate's only git reader and
is strict. `stage_advance._receipt_gate` refuses execute→finish without a
satisfying receipt. See "The dispatch seam" in `../gw-kernel/README.md`.

`integrate.py` is its code-repository sibling: `gw work integrate` resolves the strategy (`--strategy`, else `repositories.<name>.finish.strategy`, else `squash`), merges in the unique clean target worktree with exact flags (`--ff-only`, `--no-ff`, or `--squash` plus one commit), restores the target on any failure, and records the v2 receipt entry through `record_finish_in(..., evidence=)` under the same owner lock. `accept_integration.py` writes an `attested`/`accepted` receipt entry and an answered decision-ledger entry in one mutation, for evidence gw cannot verify; it never advances.

## Invariants

- **`gw_orchestrate.commands.plan()` is pure**: no filesystem, Git or clock.
  `run_orchestrate()` gathers repository and branch-tip observations, finish
  plans and decision holds before calling it. The shell's hold scan walks the
  whole vault and reads the owning epic's decisions ledger twice (routing and
  plan fields); those reads are not one atomic snapshot.

- **`gw_orchestrate/wait.py` owns the coordinator wait**: it filters heartbeats,
  acknowledges deliveries with no real event, and returns real deliveries
  unacknowledged. It never nudges or calls `worker_read` / `worker_show`, and
  takes an injected `WaitClock` instead of reading the clock itself. It can
  inspect worker and task lists to recognize already released completions.

- **Orchestrate admission is one claim gate** (`gw_orchestrate/claims.py`).
  `affects` overlap is segment containment (`packages/a` holds
  `packages/a/src`, not `packages/ab`), checked against live and
  already-accepted claims alike through `claims.conflicts()`/`first_conflicts()`.
  Which stages hold `affects` claims is one policy, `claims.claims_for()`:
  only `CODE_WRITE_PHASES` (execute, finish) do; design and plan hold none
  (no code, no workspace claim) and skip the unknown-repository fail-closed
  check (epic D-002).
  Finish claims include each source checkout and `FinishTarget.target_worktree`,
  including the checkout holding the default base for a root finish and every
  foreign-repository target. Same-target finishes serialize; independent targets
  can proceed. Authoritative live finish keys gather evidence outside the
  requested subtree and after page phase changes. `FinishPlan.occupancy`
  records target inventory independently of source/target admission checks,
  retaining root/default-base and foreign targets after revalidation failures.
  Incomplete live target evidence refuses mutable admission; it never releases
  a target for reuse or changes computed placement. Known target claims
  still use the shared conflict predicate and take diagnostic precedence.
  Only emitted dispatches reserve claims; refused or capacity-dropped candidates
  reserve nothing. Live items are never re-proposed or reported blocked against
  themselves. Placement bookkeeping does not introduce another conflict policy.

- **Admission order is dependency rank first** (`gw_orchestrate/rank.py`).
  `plan()` sorts candidates by `(-transitive dependents, PICK_ORDER,
  opened, path)` (epic D-003). The graph is the run root plus every active
  nonterminal descendant -- blocked, held and live included, since routing
  has already dropped the dependents that make an upstream matter -- with
  in-run `depends_on` edges only; each dependent counts once, terminal and
  out-of-run items neither count nor bridge, and the walk is cycle-safe.
  Rank is order only: it gates nothing and reserves nothing. Manual
  `--descend` keeps plain `PICK_ORDER`; there is no `--prefer` -- human
  priority is expressed by editing `depends_on`.

- **Placement is computed, not chosen.** `plan()` asks the placement engine's pure functions (`gw_pipeline.placement.placement_for` / `placement_chain`, through `gw_orchestrate/placing.py`) where each item belongs and states the answer: every code dispatch carries a `placement` block (`branch`, `worktree`, `parent_branch`, `parent_worktree`, `repo`) and `PlannedDispatch.worktree` is a `WorktreePlacement(path, branch, parent_path)`. There is no worktree action vocabulary, no reader pinning and no `preparations`: design and plan run in the item's own computed worktree like every other stage. A null placement (a workspace-only item, or no repository evidence) launches in the workspace primary checkout and claims no worktree. Every step of the chain (owners outermost first, then the item) is checked as a `WorktreeScope` write claim against live and accepted claims, and a conflict is `worktree-pending` naming the owner path and holder.

- **Dispatch realizes the placement before any Orca call.** `gw work dispatch` (`dispatch.py`) recomputes the placement, refuses a plan that differs (`plan-invalid`), and calls `placing.realize` so the engine creates, adopts or refreshes the checkout (`placement-refused` carries the engine's refusal kind and journals nothing). Only then does it create the Task and launch with `--worktree path:<computed>`. A retry or park-resume relaunches through `gw work dispatch --retry-of <dispatch-id>`, which re-verifies the placement with `realize(verify_only=True)`: verify-only never creates, adopts or refreshes, and a missing worktree is refused as `foreign-checkout`. The stamp is recorded after settle (`placement-unrecorded` when an owner already holds a different pair); read-only phases record nothing.

- Multi-repository orchestration resolves assignments and observations in the
  shell, then uses one frontier and global worker budget. Affects reservations
  are keyed by Git common-directory identity. Placement is computed per item and
  realized at dispatch; no deferred preparation stands between a child and its launch. `gw_pipeline.finish` is the
  shared target/receipt reader; `gw_orchestrate.finish_receipt` is the writer. Receipt v2 entries carry `strategy`/`evidence`/`target_before`; `integration_shape` verifies each strategy's history shape (squash by single parent plus `git merge-tree --write-tree` tree equality) and `discover_integration` rediscovers a landed merge, fast-forward or squash for `record`.
  Finish targets are computed from placement (`placement_resolve.item_placement`), one per repository, with no `_workspace` target and no stamp-derived anchors; a legacy Orca-named branch with no overlay surfaces as a finish-plan blocker. Finish targets include foreign-only and nested owner sources. Partial proof
  blocks advance until every target's current Git ancestry is verified; scalar
  `resolved_in` prefers the owner's repository, falling back to the first
  verified target in deterministic order. Explicit `repo=Path`
  remains single-repository compatibility, not a multi-repository resolver.

- `gw work advance` and `gw work decision *` share the decision owner's lock (`gw_pipeline.decision_owner.locked_decision_owner`). It is always taken before `apply_mutation`'s locks, never while holding them, and a non-dry-run advance holds it across route, commit gate and write.
