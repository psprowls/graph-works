---
name: archive
description: Archive terminal-status work items (resolved/wontfix/superseded) — sweep mode by default, or target specific canonical paths. Presents the plan and asks for confirmation before executing. Invokes `gw work archive`. Usage /graph-works:archive [work-path...]
---

# /graph-works:archive

Move terminal work items from their active lanes to the corresponding local `_archive/` lane.

## Usage

```
/graph-works:archive
/graph-works:archive work/release-r1/children/epic-e1/children/bug-parser
```

Without arguments: sweep mode — all terminal-status items.
With canonical-path arguments: targeted mode — those items only.

## What happens

1. Run `gw work archive --dry-run [WORK_PATHS...]` to build the plan.
2. Present the plan: items to move, items skipped (with reasons), any wikilink referrers that will become broken.
3. Ask for confirmation before executing.
4. On confirmation, run `gw work archive [WORK_PATHS...]` (without `--dry-run`).
5. Report moved canonical paths and reconciled indexes.

Terminal statuses: `resolved`, `wontfix`, `superseded`.

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/lifecycle-rules.md`
