---
type: Feature
title: Accepted with no plan
description: Accepted below a leaf at the design phase with no plan table at all.
status: stable
work_status: accepted
phase: design
effort: medium
opened: 2026-03-30
updated: 2026-04-01
affects:
  - packages/example
depends_on:
  - path: work/bug-no-such-item
    blocks: execute
    needs: resolved
  - path: work/test-gap-wontfix
    blocks: build
    needs: resolved
---

## Notes / log

No `## Plan` section at all: `sections.missing` from tier 2 owns that fact, so
the lane declares no code of its own for it.
