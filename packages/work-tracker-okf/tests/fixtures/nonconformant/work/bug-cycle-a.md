---
type: Bug
title: Cycle A
description: Depends on Cycle B, which depends on it.
status: stable
work_status: open
effort: small
opened: 2026-07-07
updated: 2026-08-01
affects:
  - packages/example
depends_on:
  - path: work/bug-cycle-b
    blocks: execute
    needs: resolved
---

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
