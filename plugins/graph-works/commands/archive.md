---
name: archive
description: Archive terminal-status work items (resolved/wontfix/superseded) — sweep mode by default, or target specific slugs. Presents the plan and asks for confirmation before executing. Invokes `gw work archive`. Usage /graph-works:archive [slug...]
---

# /graph-works:archive

Move terminal work items from `wiki/work/` to `wiki/work/_archive/`.

## Usage

```
/graph-works:archive
/graph-works:archive 2026-01-15-fix-parser-bug 2026-02-03-drop-old-api
```

Without arguments: sweep mode — all terminal-status items.
With slug arguments: targeted mode — those items only.

## What happens

1. Run `gw work archive --dry-run [SLUGS...]` to build the plan.
2. Present the plan: items to move, items skipped (with reasons), any wikilink referrers that will become broken.
3. Ask for confirmation before executing.
4. On confirmation, run `gw work archive [SLUGS...]` (without `--dry-run`).
5. Report moved items and regenerated sidecar.

Terminal statuses: `resolved`, `wontfix`, `superseded`.

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/lifecycle-rules.md`
