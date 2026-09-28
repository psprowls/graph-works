# Task 6 implementation report

Base: `990c7c2b7` (verified clean at start).
Implementation commit: `10132d79` — `feat(gw): prepare and stamp workspace worktrees outermost first`.
Scope: Task 6 only. No finish/merge, work advancement, subagents, reviewers, or external workspace edits.

## Delivered

- `gw work prepare-workspace PATH` plans by default; only `--apply` provisions and stamps.
- Outermost-first workspace owners, followed by the requested execute/finish item; disabled workspace placement yields a note.
- Exact clean existing checkouts can be adopted; verified replay writes nothing and reports `applied=False`.
- Unknown inventory/branches/identity, malformed stamps (including explicit null), dirty/replaced checkouts, occupied or dangling paths, and branch-only states refuse safely.
- Checkout common-directory identity and actual branch are checked independently of worktree registration. New checkouts are observed again before stamping.
- Stamping reuses `run_record_placement(repo="_workspace")`, its decision-owner → bundle lock order, preparation guard, and transaction commit authority. The real race regression changes phase during Git creation and verifies the guarded stamp refuses.
- `applied` records actual effects, including partial Git creation and earlier successful steps before a later refusal. An unchanged verified prefix does not count as an effect.
- Commit failures remain `stamp-refused`, retain their warning text in refusal detail, and never report recorded/placement success. Replay of a pending uncommitted stamped page refuses with a repair instruction. Tracked-file plus Git diff checks preserve CRLF behavior.
- Added explicit wire projection and four contract samples; CLI JSON refusal envelopes and human rendering; core module map; intentional surface golden (new command only).

## TDD evidence and iteration history

Commands below were run from the dispatched checkout; all behavior tests that mutate Git use pytest temporary repositories.

1. `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_prepare_workspace.py -v`
   - RED: collection failed with `ModuleNotFoundError: graph_works_core.orchestrate.workspace_prepare`.
   - Initial implementation rerun exposed a test fixture duplicate `repositories` key (7 failed, 1 passed); fixed fixture to replace the initialized empty mapping.
2. `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_prepare_workspace.py -q --tb=short`
   - Initial baseline GREEN: 8 passed.
   - Added malformed-state/effect/commit regressions: 3 failed, 21 passed; demonstrated explicit-null refusal mismatch, failed-commit replay incorrectly succeeding, and unnecessary parent-directory creation on failed Git add.
   - After fixes: 24 passed.
3. `uv run --package graph-works-cli pytest packages/graph-works-cli/tests/test_work_cli_writes.py -k prepare_workspace -q --tb=short`
   - RED: 2 failed because `prepare-workspace` was unregistered.
   - Intermediate: 1 failed, 1 passed because fixture mutated `fm_raw` without `Document.set`; corrected fixture to use the document writer.
   - GREEN: 2 passed, 76 deselected; expanded CLI coverage later passed 7 tests.
4. `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_prepare_workspace.py -k 'registered_path or partial_effect or real_preparation or dirty_created' -q --tb=short`
   - RED: 3 failed, 1 passed. Exposed replaced-clean-repository adoption, lost partial Git effects, and dirty newly-created checkout stamping. Real preparation guard already refused the phase race.
   - Fixed those paths; the complete focused module then passed 28 tests.
5. `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_prepare_workspace.py -q --cov=graph_works_core.orchestrate.workspace_prepare --cov-branch --cov-report=term-missing --cov-fail-under=0`
   - 28 passed; intermediate coverage 89.95%. Added meaningful ambiguity, entitlement, failed-application, disabled mode, and replay cases.
6. `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_prepare_workspace.py -q --cov=graph_works_core.orchestrate.workspace_prepare --cov-branch --cov-report=term-missing --cov-fail-under=95`
   - 37 passed, 100% coverage before final CRLF regression.
7. `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_prepare_workspace.py -k 'committed_crlf or record_workspace' -q --tb=short`
   - First CRLF fixture failed to create a commit because Git normalized EOLs; disabled text normalization in that temporary repository's `.gitattributes`. Disabled direct workspace recording test passed.
8. `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_prepare_workspace.py -k committed_crlf -q --tb=short`
   - RED after fixture repair: 1 failed, 38 deselected; committed CRLF page falsely reported pending commit. Replaced text comparison with Git tracked/diff checks.
   - A full focused coverage attempt during fixture correction also failed on the fixture; superseded by the final clean run below.

## Final verification

Broad prescribed suites ran once each with `-n 4`; later work used affected reruns only.

| Exact command | Result |
| --- | --- |
| `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate -q -n 4` | 1778 passed in 95.11s |
| `uv run --package graph-works-cli pytest packages/graph-works-cli/tests -q -n 4` | 988 passed, 1 skipped in 67.67s |
| `uv run --package graph-works-wire pytest packages/graph-works-wire/tests -q` | 269 passed in 1.79s |
| `just contracts` | 14 kept, 0 broken |
| `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate/test_prepare_workspace.py -q --cov=graph_works_core.orchestrate.workspace_prepare --cov-branch --cov-report=term-missing --cov-fail-under=95` | FINAL: 39 passed in 17.68s; 152 statements and 58 branches, none missing, 100% |
| `uv run --package graph-works-cli pytest packages/graph-works-cli/tests/test_work_cli_writes.py -k prepare_workspace -q --tb=short` | FINAL: 7 passed, 76 deselected in 2.68s |
| `uv run --package graph-works-core mypy --strict --platform linux packages/graph-works-core/src` | FINAL: success, 135 source files |
| `uv run --package graph-works-core mypy --strict --platform win32 packages/graph-works-core/src` | FINAL: success, 135 source files |
| `uv run --package graph-works-wire mypy --strict --platform linux packages/graph-works-wire/src` | success, 10 source files |
| `uv run --package graph-works-wire mypy --strict --platform win32 packages/graph-works-wire/src` | success, 10 source files |
| `uv run --package graph-works-cli mypy --strict --platform linux packages/graph-works-cli/src` | success, 46 source files |
| `uv run --package graph-works-cli mypy --strict --platform win32 packages/graph-works-cli/src` | success, 46 source files |
| `git diff --check` | success |

Initial core mypy runs found two errors (optional workspace path captured in closure, optional commit reason assigned to string); both were corrected and both platform arms rerun. Ruff initially identified formatting, import ordering, and an unused test variable; corrected before commit.

Final exact scoped Ruff commands (both succeeded, nine Python files):

```sh
uv run ruff check packages/graph-works-core/src/graph_works_core/orchestrate/{workspace_prepare,placement}.py packages/graph-works-core/tests/orchestrate/test_prepare_workspace.py packages/graph-works-wire/{src/graph_works_wire/work.py,tests/samples_work.py,tests/test_contract.py} packages/graph-works-cli/{src/graph_works_cli/work_cli/main.py,src/graph_works_cli/work_cli/rendering.py,tests/test_work_cli_writes.py}
uv run ruff format --check packages/graph-works-core/src/graph_works_core/orchestrate/{workspace_prepare,placement}.py packages/graph-works-core/tests/orchestrate/test_prepare_workspace.py packages/graph-works-wire/{src/graph_works_wire/work.py,tests/samples_work.py,tests/test_contract.py} packages/graph-works-cli/{src/graph_works_cli/work_cli/main.py,src/graph_works_cli/work_cli/rendering.py,tests/test_work_cli_writes.py}
```

Surface golden regeneration used:

```sh
uv run --package graph-works-cli python - <<'PY'
from pathlib import Path
from typer.testing import CliRunner
from graph_works_cli.cli import app
result = CliRunner().invoke(app, ['util', 'describe-surface', '--json'])
assert result.exit_code == 0, result.output
Path('packages/graph-works-cli/tests/fixtures/surface.golden.json').write_text(result.stdout, encoding='utf-8', newline='')
PY
```

Commit command: `git commit -m "feat(gw): prepare and stamp workspace worktrees outermost first"` (explicitly staged the eleven authorized implementation/test/doc files).

## Limits and concerns

- Full `just check`, full-package core coverage, and work advancement are controller-owned and were not run or claimed. The 100% coverage figure is only for the new preparation module.
- Broad suites preceded the final CRLF fix and extra focused cases. The final changed behavior was covered by the final focused core/CLI runs and both core type arms; broad suites were not repeated.
- Failed commit recovery intentionally refuses a pending stamped page rather than silently declaring replay success. Repair/commit the pending workspace page before retrying; this task adds no finish/merge recovery machinery.
- Git provisioning is intentionally outside the locks; guarded recording revalidates under the existing lock order. A refused stamp can leave a successfully-created checkout for safe adoption on retry.
- No outstanding test failure or known blocker within Task 6 scope. External live workspace was never used for mutation verification.
