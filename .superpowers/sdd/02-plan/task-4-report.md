# Task 4 report

## Result

Implemented workspace commit intents for stage advance, record placement, and finish receipt. Unchanged placement now commits pending item references under the existing decision-owner and bundle lock order. `PlacementRecord.pending_commit` carries that outcome, and `PlacementRecord.warnings` exposes a failed pending commit with the same `workspace commit failed: ` warning used by normal mutation applications.

Implementation and tests commit: `8b957503` (`feat(graph-works-core): commit orchestration workspace writes`).

## RED

- `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate -q -k "commit or unchanged_placement"` — 4 failed, 7 passed, 494 deselected. The three live verbs retained the seed commit or had `application.commit is None`; unchanged placement lacked `pending_commit`.
- `uv run --package graph-works-core pytest -q packages/graph-works-core/tests/orchestrate/test_record_placement.py::test_unchanged_placement_reports_failed_pending_commit packages/graph-works-core/tests/work/test_transactions_commit.py -k "pending"` — 1 failed, 3 passed, 8 deselected. The unchanged placement result lacked `pending_commit`.

## GREEN

- `uv run --package graph-works-core pytest -q packages/graph-works-core/tests/orchestrate/test_record_placement.py packages/graph-works-core/tests/orchestrate/test_finish_receipt.py packages/graph-works-core/tests/orchestrate/test_orchestrate_shell.py -k "commit or unchanged_placement"` — 11 passed, 309 deselected (rerun after formatting).
- `uv run --package graph-works-core pytest packages/graph-works-core/tests/orchestrate -q` — 506 passed. This is the prescribed orchestrate suite, run once after implementation.
- `uv run --package graph-works-core pytest -q packages/graph-works-core/tests/work/test_transactions_commit.py` — 11 passed (rerun after formatting). Added pending-helper bundle-lock and timeout coverage from Task 3 review.
- `uv run ruff check` and `uv run ruff format --check` on all seven changed Python files — passed.
- `uv run --package graph-works-core mypy --strict --platform linux packages/graph-works-core/src` and the same command with `--platform win32` — both passed, 125 source files.
- `git diff --check` — passed.

## Remaining concern

Task 7 still needs to render commit outcomes. Its placement payload should read `application.commit` when there is an application, otherwise `pending_commit`, and surface `PlacementRecord.warnings` so a failed unchanged-placement commit is visible. Native Windows locking was not run on a Windows host; the POSIX lock tests skip there.

## Review fix round 1

Finish receipt now retains `application.commit` and `application.warnings` in `FinishReceiptResult` after a successful mutation. A failed workspace commit still leaves the receipt written and returns `refusal=None`, `changed=True`; the failed `CommitOutcome` and `workspace commit failed: <reason>` warning remain available to callers. Fix commit: `e2d52658` (`fix(graph-works-core): retain finish receipt commit outcome`).

### RED

- `uv run --package graph-works-core pytest -q packages/graph-works-core/tests/orchestrate/test_finish_receipt.py::test_failed_workspace_commit_preserves_receipt_and_warning` — 1 failed as expected: `FinishReceiptResult` had no `commit` attribute, after assertions confirmed the receipt mutation succeeded.

### GREEN

- `uv run --package graph-works-core pytest -q packages/graph-works-core/tests/orchestrate/test_finish_receipt.py` — 17 passed.
- `uv run ruff check` and `uv run ruff format --check` on the two changed Python files — passed.
- `uv run --package graph-works-core mypy --strict --platform linux packages/graph-works-core/src` and the same command with `--platform win32` — both passed, 125 source files.
- `git diff --check` — passed.
