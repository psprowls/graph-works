---
type: Bug
title: Cycle B
description: Depends on Cycle A, which depends on it.
status: stable
work_status: open
effort: small
opened: 2026-07-08
updated: 2026-08-01
affects:
  - packages/example
depends_on:
  - path: work/bug-cycle-a
    blocks: execute
    needs: resolved
---

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
