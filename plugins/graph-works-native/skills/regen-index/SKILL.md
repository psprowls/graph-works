---
name: regen-index
description: Invoked explicitly as /gw:regen-index. Reconciles every path-native work-lane index against the filesystem via `gw work regen-index`, then reports the index paths and any warnings or refusals. Run after out-of-band work-item edits. Explicit invocation only.
---

# Reconcile the work-lane indexes

Reconcile the Markdown indexes at every active and archive work lane.

## Usage

```
/gw:regen-index          # Claude Code
$regen-index                      # Codex
```

## What happens

1. Run `gw work regen-index --json`.
2. Report the `indexes` paths and any warnings or refusals.

Run this after:
- Filing new work items via means other than `gw work file`.
- Manually editing work item frontmatter.
- Archiving items if the auto-regen didn't fire.

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/wiki-schema.md`
