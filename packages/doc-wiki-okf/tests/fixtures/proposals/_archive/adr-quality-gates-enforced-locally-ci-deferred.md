---
kind: adr
mode: create_new
target_slug: quality-gates-enforced-locally-ci-deferred
title: Quality gates are enforced locally by just check; CI is deferred
status: created
origins:
- ref: sources/2026-08-design-spec-build-okf-io-core-through-validation-and-writers
  source: ingest
  rationale: 'The epic spec states this as an aside in §7 (''Enforcement is local by design: this repo has no CI and adding a workflow is out of scope''), but it is a real decision with a shipped mechanism, a deferral history across three children, and two demonstrated failure modes. Nothing in adrs/ records it, so the next contributor meets a 95% gate with no statement of what it is and is not for.'
  evidence:
  - Epic spec §7 requires >=95% branch coverage enforced by just check (--cov-fail-under=95), with enforcement local by design because the repo has no CI.
  - 'The decision shaped the justfile: every recipe is intended to be exactly the command a future CI job calls, so wiring a runner later is mechanical (though lint in fact runs two commands and check is a dependency chain).'
  - 'Deferred twice before landing: child 1 measured 88% ungated (no git remote, so no workflow could run), child 2 measured 94% ungated, child 3 gated at 95.53%.'
  - 'The gate has twice failed to catch a real defect: log.py sat at 100% branch coverage with a live misfiling bug, and the percent-encoded-fragment defect survived 95.53% with all three affected lines exercised. It measures which lines ran, not which shapes were tried.'
  - requires-python = '>=3.11' remains an assertion rather than a tested fact; with no CI the suite runs on whatever interpreter is local, mitigated only by ruff target-version = py311.
  - There is still no .github/ directory at 7e4ecf6, the epic's closing commit.
tokens: 928
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-quality-gates-enforced-locally-ci-deferred`. -->

## Suggested Action

Create new adr page `adrs/quality-gates-enforced-locally-ci-deferred.md`.

## Evidence From Source

- Epic spec §7 requires >=95% branch coverage enforced by just check (--cov-fail-under=95), with enforcement local by design because the repo has no CI.
- The decision shaped the justfile: every recipe is intended to be exactly the command a future CI job calls, so wiring a runner later is mechanical (though lint in fact runs two commands and check is a dependency chain).
- Deferred twice before landing: child 1 measured 88% ungated (no git remote, so no workflow could run), child 2 measured 94% ungated, child 3 gated at 95.53%.
- The gate has twice failed to catch a real defect: log.py sat at 100% branch coverage with a live misfiling bug, and the percent-encoded-fragment defect survived 95.53% with all three affected lines exercised. It measures which lines ran, not which shapes were tried.
- requires-python = '>=3.11' remains an assertion rather than a tested fact; with no CI the suite runs on whatever interpreter is local, mitigated only by ruff target-version = py311.
- There is still no .github/ directory at 7e4ecf6, the epic's closing commit.

## Existing Pages Considered

- No existing pages were cited by the proposal reasoner.

## Reasoning Summary

No reasoning summary was captured.

## Potential Conflicts

- No conflicts identified.

## Implementation Notes

- No implementation notes captured.

## Origins

**ingest · [[sources/2026-08-design-spec-build-okf-io-core-through-validation-and-writers]]**
The epic spec states this as an aside in §7 ('Enforcement is local by design: this repo has no CI and adding a workflow is out of scope'), but it is a real decision with a shipped mechanism, a deferral history across three children, and two demonstrated failure modes. Nothing in adrs/ records it, so the next contributor meets a 95% gate with no statement of what it is and is not for.
