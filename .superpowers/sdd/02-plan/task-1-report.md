# Task 1 report: SDD ledger reader

Status: DONE

## Scope and result

Implemented `SddProgress` and `find_progress` in the pure `workflow_orca._progress` module. The reader selects the newest ledger at or after the injected dispatch time, counts distinct completion and plan task numbers, keeps the last nonblank ledger line, and returns a note with unavailable data. Added the module to the package boundary allowlist and added focused filesystem tests. No liveness row, wait verb, protocol, or work-item state was changed.

The ledger header is parsed from the actual first line, including when a later line looks like a valid header. An `OSError` during ledger discovery or candidate stat returns `ledger unreadable` when there is no usable fresh ledger; ledger text read and UTF-8 failures also return that note. Missing or unreadable plans preserve the ledger facts and return `plan unreadable`.

## RED

Command:

```text
uv run --package workflow-orca pytest packages/workflow-orca/tests/test_progress.py -q
```

Exit 2, during collection:

```text
E   ModuleNotFoundError: No module named 'workflow_orca._progress'
1 error in 0.09s
```

This was run after writing `test_progress.py` and before creating `_progress.py`.

## GREEN and gates

```text
uv run --package workflow-orca pytest packages/workflow-orca/tests/test_progress.py packages/workflow-orca/tests/test_boundaries.py -q
35 passed in 0.11s

uv run --package workflow-orca pytest packages/workflow-orca/tests -q
338 passed, 1 skipped in 1.38s

uv run --package workflow-orca pytest packages/workflow-orca/tests --cov=workflow_orca --cov-branch --cov-report=term-missing --cov-fail-under=95 -q
338 passed, 1 skipped in 0.73s
TOTAL 766 statements, 5 missed; 248 branches, 5 partial; 99.01% coverage
_progress.py: 58 statements, 0 missed; 8 branches, 0 partial; 100.00% coverage

uv run --package workflow-orca mypy --strict --platform linux packages/workflow-orca/src
Success: no issues found in 7 source files

uv run --package workflow-orca mypy --strict --platform win32 packages/workflow-orca/src
Success: no issues found in 7 source files

uv run ruff check packages/workflow-orca/src/workflow_orca/_progress.py packages/workflow-orca/tests/test_progress.py packages/workflow-orca/tests/test_boundaries.py
All checks passed!

uv run ruff format --check packages/workflow-orca/src/workflow_orca/_progress.py packages/workflow-orca/tests/test_progress.py packages/workflow-orca/tests/test_boundaries.py
3 files already formatted

git diff --check
Exit 0, no output
```

The first scoped Ruff run found an import ordering issue and a long test line. The next run found one formatting change. Both were fixed; the final commands above passed.

## Files and self-review

- `packages/workflow-orca/src/workflow_orca/_progress.py`: stdlib-only reader and frozen data type.
- `packages/workflow-orca/tests/test_progress.py`: fresh/stale selection, tie breaking, distinct counts, first-line identity, missing and invalid files, note strings, plan paths, and truncation.
- `packages/workflow-orca/tests/test_boundaries.py`: adds `_progress.py` to the module inventory.
- `.superpowers/sdd/02-plan/task-1-report.md`: this report.

Self-review: checked the change against the brief's Task 1 interface and file scope. The implementation reads no clock, process state, heartbeat content, or package outside the stdlib. It preserves completed count and last line when plan metadata cannot be read. A directory listing that silently omits entries due to filesystem behavior cannot be distinguished from an empty directory by `pathlib.glob`; raised discovery errors are handled explicitly.

## Remaining work

The liveness row and wait verb integration belong to later tasks. No live Orca probe was run for this pure filesystem reader. No broad workspace gate was run; verification is limited to the workflow-orca package, scoped Ruff, and its two mypy platform arms.

Commit message: `feat(workflow-orca): read SDD ledger progress from a dispatch worktree`.

## Fix round 1 — Important I1 only

Added a real-filesystem regression with `docs/\x00plan.md` in a fresh UTF-8 ledger header. It asserts the retained ledger path, plan metadata, completed count, and last line, together with `total=None` and `plan unreadable`. The minimal source fix catches `ValueError` at the plan read boundary; this also covers `UnicodeDecodeError`, its subclass, preserving the existing decode-failure handling.

RED, before the source fix:

```text
uv run --package workflow-orca pytest packages/workflow-orca/tests/test_progress.py -k malformed_plan_path -q
Exit 1
FAILED packages/workflow-orca/tests/test_progress.py::test_malformed_plan_path_keeps_ledger_facts
E       ValueError: embedded null byte
1 failed, 18 deselected in 0.09s
```

GREEN, after the source fix:

```text
uv run --package workflow-orca pytest packages/workflow-orca/tests/test_progress.py packages/workflow-orca/tests/test_boundaries.py -q
Exit 0
36 passed in 0.10s

git diff --check
Exit 0, no output
```

Self-review: only the plan-read exception handler and the new regression changed in code. Minor M1 remains deferred to final review. No full package/workspace suites, coverage, mypy, or live probe were rerun in this fix round; the earlier gates above remain historical implementation evidence. No subagents or work-item advancement were used.

Fix commit message: `fix(workflow-orca): degrade malformed progress plan paths`.
