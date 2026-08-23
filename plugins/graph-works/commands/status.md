---
name: status
description: Show a one-screen path-native work item rollup — counts by work status/type plus the canonical path worth resuming. Invokes `gw work status`. Usage /graph-works:status
---

# /graph-works:status

One-screen work item rollup from the live OKF work tree.

## Usage

```
/graph-works:status
```

## What happens

1. Run `gw work status --json`.
2. Present `total`, `by_work_status`, `by_type`, `by_phase`, and the `resume.primary.path` plus alternatives.

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/wiki-schema.md`
