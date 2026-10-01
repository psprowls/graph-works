# Task 1 report: literal-ban suite

## Changes

- Added `plugins/gw/tests/test-no-stage-artifact-literals.sh`. It scans every file under `plugins/gw` except `tests/`, `node_modules`, and `.git`, reports each matching line, and supports `PLUGIN_ROOT_OVERRIDE` for scope tests.
- Added the suite invocation to `just test-plugin` immediately after `test-finish-obligations-docs`.

## Verification

- `bash plugins/gw/tests/test-no-stage-artifact-literals.sh` returned exit 1 with exactly 20 `[FAIL]` lines and `STATUS: FAILED (20 hit(s))`. The reported paths and line numbers matched the Task 1 expected list. This failure is expected until Tasks 2–6 remove the literals.
- Scope self-test using a copied plugin tree:
  - After replacing the three artifact names outside `tests/`: exit 0, `STATUS: PASSED`.
  - After adding `hooks/probe.json` containing `01-design.md`: exit 1, one hit at `hooks/probe.json:1`.
  - After removing that probe and adding `tests/fixture.md` containing `02-plan.md`: exit 0, `STATUS: PASSED`.
- `bash -n plugins/gw/tests/test-no-stage-artifact-literals.sh` passed.
- `git diff --check` passed.

## Status

Task 1 is implemented. The live plugin scan remains intentionally red with 20 hits for the follow-up tasks to resolve.
