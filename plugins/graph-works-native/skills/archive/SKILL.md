---
name: archive
description: Invoked explicitly as /gw:archive [work-path...]. Archives terminal-status work items (resolved/wontfix/superseded) via `gw work archive` — sweep mode with no arguments, targeted mode with canonical paths — presenting the plan and asking for confirmation before executing. Mutates the workspace; explicit invocation only, never on inference.
---

# Archive terminal work items

Move terminal work items from their active lanes to the corresponding local `_archive/` lane.

## Usage

```
/gw:archive          # Claude Code
$archive                      # Codex
/gw:archive work/release-r1/children/epic-e1/children/bug-parser
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

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/lifecycle-rules.md`
