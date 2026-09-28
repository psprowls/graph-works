# Final fix wave — workspace branch placement

Base: `3ced6f0b52cf9c00e08265b31a0762cd50a0566a`.

Scope: Important I1, I2, I3 from the complete final review and the latest controller rulings. No subagents, live Orca probe, external workspace mutation, work-item advance, broad suite, coverage gate, or full `just check`. The existing dirty `task-6-report.md` was not edited or staged. All optional minor cleanup remains deferred; the I2 regressions cover the requested reader-progress/replan subset.

## I1 — Actual dispatch recording

`orchestrate/dispatch.py` now interprets `_workspace` as the observed stamp destination (`repo=`), leaving code repository resolution (`repo_name=`) alone. Both the live recording and dry verification use the same selector. Ordinary code dispatches retain their previous selector behavior.

`test_workspace_launch.py` drives `run_orchestrate`, real workspace preparation, then the production `run_dispatch` entry point used by `gw work dispatch`, with a fake Orca port and actual recording/journal/Git operations. Execute and finish succeed with `recorded == unchanged`, byte-identical item pages, and no Orca worktree creation. An additional execute case preserves an existing scalar code placement alongside the prepared foreign workspace stamp. These tests exercise the executable recorder, not a mocked recorder or manual placement command; they do not invoke the Typer wrapper or a live Orca worker.

RED command:

```sh
uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_workspace_launch.py -q --tb=short
```

After correcting two test harness mistakes (`OrchestratePlan.path`, required fake row `display_name`), the six initial cases failed for the intended reasons: execute and finish returned `placement-unrecorded` with `--repo-name '_workspace' conflicts with repo 'code'`; root readers selected code; epic readers emitted no dispatch. The first implementation rerun caught an incorrect import module name, corrected before the six-case GREEN (6 passed). Harness/import failures are not counted as regression evidence.

## I2 — Workspace-only pinned reader progress

For enabled workspace-only design/plan items, the planner passes the workspace repository/context into the existing verified reader selector. An enclosing owner's `_workspace` foreign stamp supplies the integration branch; an unanchored root uses the workspace default base. Existing identity, inventory, exact stamp/path, full committed SHA and detached-backend checks remain in force. This creates no code anchor, code fork, or code claim. Dirty owner checkouts supply only their committed branch tip, through the existing detached-reader policy.

Reader receipt recording also accepts the reserved workspace repository only when workspace placement is enabled. This is necessary to complete the real launch: planner-only success would otherwise leave every workspace reader at `placement-unrecorded`.

Regression coverage:

- Fresh unstamped epic with workspace-only design or plan child; prepare only the initial requested epic workspace anchor, replan, and require a detached reader with the exact Git commit of that branch. No code preparation or child workspace preparation is invented.
- Both nested cases author a second, otherwise unused code repository on the child and still read the workspace lineage. Code repository branches remain unchanged.
- Unanchored root design and plan pin the committed workspace default branch.
- All four cases launch through the fake Orca port, create a real detached Git checkout at the planned SHA, write an actual reader receipt, and preserve item bytes.
- A pure mixed-sibling regression admits the workspace reader assigned to an unused code repo alongside a code-writing sibling, without requesting an unused code anchor or blocking the sibling's checkout.
- Missing, malformed, or wrong-branch owner workspace stamps refuse. A dirty but verified anchor yields a detached reader at its committed SHA.

The same RED command, after extending the four reader cases through actual launch, produced **4 failed, 2 passed**: every reader failed recording with `repo '_workspace' names no declared repository`. After the enabled-workspace receipt fix it produced **6 passed**. Later safety and preservation additions produced **11 passed in 9.97s**. The sibling fixture initially failed because its unused context accidentally advertised `/code` as its own checkout; fixing the fixture's inventory and checkout map yielded **6 passed** for `test_epic_anchor_timing.py`. No production change was made for that fixture error.

## I3 — Merged-target wiki lint

`finishing-relay/SKILL.md` now requires `gw wiki lint --workspace <main workspace>` after workspace main integration and `gw wiki lint --workspace <anchor worktree>` after child integration into the epic anchor. `GRAPH_WORKS_DIR` stays on the main workspace for work/receipt verbs. The existing workspace-branch plugin contract now requires both spellings/targets and the control-plane instruction, and rejects the obsolete root `gw lint` spelling.

RED/GREEN command:

```sh
bash plugins/gw/tests/test-workspace-branch-docs.sh
```

RED: 4 failures (both target commands missing, control-plane instruction missing, obsolete lint command present). GREEN: every assertion passed, exit 0. Also ran `bash plugins/gw/tests/test-dispatch-profile-contract.sh`: 1 test and 87 tests passed, `dispatch profile contract: ok`.

## Verification and limits

Both commands passed, each reporting no issues in 136 source files:

```sh
uv run --package graph-works-core mypy --strict --platform linux packages/graph-works-core/src
uv run --package graph-works-core mypy --strict --platform win32 packages/graph-works-core/src
```

The final changed-file checks passed:

```sh
uv run ruff check packages/graph-works-core/src/graph_works_core/orchestrate/{commands,dispatch,placement}.py packages/graph-works-core/tests/orchestrate/{test_workspace_launch,test_epic_anchor_timing}.py
uv run ruff format --check packages/graph-works-core/src/graph_works_core/orchestrate/{commands,dispatch,placement}.py packages/graph-works-core/tests/orchestrate/{test_workspace_launch,test_epic_anchor_timing}.py
git diff --check
just contracts
```

Ruff: all checks passed, five files formatted. Contracts: **14 kept, 0 broken**. No source change followed the type/contract checks. Final package coverage, full `just check`, and scoped independent re-review remain controller-owned and are not claimed passing.

### Covering focused tests

```sh
uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_workspace_launch.py packages/graph-works-core/tests/orchestrate/test_epic_anchor_timing.py packages/graph-works-core/tests/orchestrate/test_reader_pinning.py packages/graph-works-core/tests/orchestrate/test_record_reader.py packages/graph-works-core/tests/orchestrate/test_orchestrate_dispatch.py packages/graph-works-core/tests/orchestrate/test_record_placement.py packages/graph-works-core/tests/orchestrate/test_workspace_placement.py packages/graph-works-core/tests/orchestrate/test_workspace_e2e.py packages/graph-works-core/tests/orchestrate/test_anchor_preparations.py packages/graph-works-core/tests/orchestrate/test_placement_refusals_skill_mirror.py -q --tb=short
```

Result: **705 passed, 2 failed in 140.72s**. The failures were the existing `test_readers_get_no_workspace[design]` and `[plan]`: their fixtures supplied only a code-repository tip, whereas the controller's new policy requires a verified workspace tip. Updated that test to provide the workspace SHA explicitly for success and added both-phase refusal assertions when workspace tip observations are unknown; it continues to assert no mutable workspace placement/content-root prompt. No implementation was changed after this covering run.

Affected rerun (also includes the final added scalar-preservation case and all new regressions):

```sh
uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_workspace_placement.py packages/graph-works-core/tests/orchestrate/test_workspace_launch.py packages/graph-works-core/tests/orchestrate/test_epic_anchor_timing.py -q --tb=short
uv run ruff check packages/graph-works-core/tests/orchestrate/test_workspace_placement.py
uv run ruff format --check packages/graph-works-core/tests/orchestrate/test_workspace_placement.py
```

Result: **47 passed in 9.44s**, Ruff check and formatting passed. The previously green covering files were not redundantly rerun after this test-only fixture repair. Thus all covering files have passing evidence, but there is no claim of a single final all-covering invocation or full-package/full-gate pass.

No structural contradiction to the controller's I2 ruling was found. Remaining concerns are the explicitly deferred minor items and controller-owned final gate/re-review, not known open Important findings in this wave.

CLI surface check: `uv run --package graph-works-cli gw wiki lint --help` exited 0 and displayed `Usage: gw wiki lint [OPTIONS]` with `--workspace <str>`. This was help-only; no external workspace lint/write was performed.
