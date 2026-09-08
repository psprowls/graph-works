---
name: status
description: Invoked explicitly as /gw:status. Shows a one-screen path-native work item rollup from `gw work status` — totals and counts by work status, type, and phase, plus the canonical path worth resuming and its alternatives. Explicit invocation only.
---

# Work item status rollup

One-screen work item rollup from the live OKF work tree.

## Usage

```
/gw:status          # Claude Code
$status                      # Codex
```

## What happens

1. Run `gw work status --json`.
2. Present `total`, `by_work_status`, `by_type`, `by_phase`, and the `resume.primary.path` plus alternatives.

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/wiki-schema.md`
