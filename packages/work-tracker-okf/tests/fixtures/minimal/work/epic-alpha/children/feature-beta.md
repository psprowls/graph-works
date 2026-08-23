---
type: Feature
title: Beta feature
description: A feature with both artifacts recorded, a parent, and a dependency.
status: stable
work_status: in-progress
phase: execute
effort: medium
opened: '2026-01-05'
updated: '2026-02-02'
affects:
  - packages/work-tracker-okf
depends_on:
  - path: work/spike-zeta
    blocks: execute
    needs: resolved
tags:
  - fixture
sources:
  - id: design
    resource: /work/epic-alpha/children/feature-beta/references/01-design.md
    title: Design spec — beta feature
    last_modified: '2026-01-06'
  - id: plan
    resource: /work/epic-alpha/children/feature-beta/references/02-plan.md
    title: Plan — beta feature
  - id: execute-transcript
    resource: /work/epic-alpha/children/feature-beta/references/03-execute-transcript.jsonl
    title: Execute-stage transcript
---

## Options considered

Two. The second one.

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
| Ship it | Tests pass | It is a fixture |

## Notes / log
