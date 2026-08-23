---
name: regen-index
description: Reconcile every path-native work-lane index against the filesystem. Run after out-of-band work-item edits. Invokes `gw work regen-index`. Usage /graph-works:regen-index
---

# /graph-works:regen-index

Reconcile the Markdown indexes at every active and archive work lane.

## Usage

```
/graph-works:regen-index
```

## What happens

1. Run `gw work regen-index --json`.
2. Report the `indexes` paths and any warnings or refusals.

Run this after:
- Filing new work items via means other than `gw work file`.
- Manually editing work item frontmatter.
- Archiving items if the auto-regen didn't fire.

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/wiki-schema.md`
