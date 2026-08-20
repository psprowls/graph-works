---
type: Feature
title: Accepted with no plan
description: Accepted at the design phase with no plan table at all.
status: stable
workflow_status: accepted
phase: design
effort: medium
opened: 2026-03-30
updated: 2026-04-01
affects:
  - packages/example
parent: 2026-07-03-spike-wrong-parent-type
depends_on:
  - slug: 2026-06-05-bug-no-such-item
    blocks: build
  - 2026-07-06-test-gap-wontfix
---

## Notes / log

No `## Plan` section at all: `sections.missing` from tier 2 owns that fact, so
the lane declares no code of its own for it.
