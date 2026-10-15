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

`commands.py`, `stage_advance.py` and `placement.py` own planning,
stage advancement and observed placement; `workspace_prepare.py` plans or creates/adopts
workspace worktrees outermost first and records committed, guarded stamps.
`merge_workspace.py` merges workspace branches into the default base under decision-owner
then bundle locks and records a receipt using fresh post-merge content; nested children
merge in their epic anchor instead. `finish_receipt.record_finish_in` uses an existing
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
  a target for reuse or changes pinned-reader placement. Known target claims
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

- **`READER_ACTION = "pin-detached"`** for `design` and `plan`, including roots
  with historical stamps. `RepositoryContext.branch_tips` supplies the full
  committed `start_sha`; the action has `path=None`, `branch=None` and
  `exists=None`. The nearest owner's verified repository-local anchor supplies
  the source branch; only an unanchored root can use the default base. Missing
  or ambiguous repository/ref evidence is `worktree-unprovable`; inability to
  provision is `worktree-unsupported`, never mutable-anchor reuse. Dirty anchors
  are valid sources of committed refs: pinning promises stability, not freshness.
  The launcher prepares one dedicated detached checkout per dispatch before
  injection: `gw work dispatch` (`dispatch._prepare_reader`) creates it
  through the Orca port with an attempt-derived marker comment, detaches
  only that new checkout at `start_sha`, verifies it by content at settle
  time, and records a reader receipt (`_record_reader`) instead of a
  placement stamp. Readers claim no mutable checkout, including historical
  stamps, and hold no `affects` claims (`claims_for`).
  Epic/Release roots at design and plan also use this detached reader path;
  their repository and workspace anchors are prepared when they enter execute,
  before a child reaches execute. They never dispatch `create-top-level` for
  the owner anchor.

- **Reader receipts are observations, not placement stamps.**
  `gw_orchestrate.placement.run_record_reader` backs `gw work record-reader` and
  writes under `layout.cache_dir / "reader-receipts"`, keyed by item digest and
  dispatch ID. It never changes phase, `updated`, scalar placement or
  `repo_stamps`, and never stores preambles or dispatch capabilities. Live calls
  revalidate under the decision-owner lock shared with advance. Current-phase
  refusal precedes replay; identical eligible evidence replays without writing,
  while changed evidence for the same attempt returns `attempt-mismatch`.
  Receipt recording trusts the caller's verified Git observation; it does not
  itself prove repository identity, detachment or the checkout's SHA.

- Multi-repository orchestration resolves assignments and observations in the
  shell, then uses one frontier and global worker budget. Affects reservations
  are keyed by Git common-directory identity. Preparations defer launch and
  stamp the owning item's phase, never the child's. `gw_pipeline.finish` is the
  shared target/receipt reader; `gw_orchestrate.finish_receipt` is the writer. Receipt v2 entries carry `strategy`/`evidence`/`target_before`; `integration_shape` verifies each strategy's history shape (squash by single parent plus `git merge-tree --write-tree` tree equality) and `discover_integration` rediscovers a landed merge, fast-forward or squash for `record`.
  Finish targets include foreign-only and nested owner sources. Partial proof
  blocks advance until every target's current Git ancestry is verified; scalar
  `resolved_in` prefers the owner's repository, falling back to the first
  verified target in deterministic order. Explicit `repo=Path`
  remains single-repository compatibility, not a multi-repository resolver.

- `gw work advance` and `gw work decision *` share the decision owner's lock (`gw_pipeline.decision_owner.locked_decision_owner`). It is always taken before `apply_mutation`'s locks, never while holding them, and a non-dry-run advance holds it across route, commit gate and write.
