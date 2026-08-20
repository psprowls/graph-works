---
name: regen-index
description: Rebuild wiki/work-index.json from the current wiki/work/*.md state. Run after filing or archiving work items, or when `gw work status` reports a missing sidecar. Invokes `gw work regen-index`. Usage /graph-works:regen-index
---

# /graph-works:regen-index

Rebuild `wiki/work-index.json` from current `wiki/work/*.md` state.

## Usage

```
/graph-works:regen-index
```

## What happens

1. Run `gw work regen-index --json`.
2. Report the item count and sidecar path.

Run this after:
- Filing new work items via means other than `gw work file`.
- Manually editing work item frontmatter.
- Archiving items if the auto-regen didn't fire.

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/sidecar-schema.md`
