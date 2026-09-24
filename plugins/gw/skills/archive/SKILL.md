---
name: archive
description: Invoked explicitly as /gw:archive [work-path...]. Archives terminal-status work items (resolved/wontfix/superseded) via `gw work archive` — sweep mode with no arguments, targeted mode with canonical paths — presenting the plan and asking for confirmation before executing. Mutates the workspace; explicit invocation only, never on inference.
---

# Archive terminal work items

Move terminal **top-level** work items, with their whole subtree, into `work/_archive/`. Children are never archived on their own; they move with their root.

## Usage

```
/gw:archive          # Claude Code
$archive                      # Codex
/gw:archive work/release-r1
```

Without arguments: sweep mode — every top-level item whose whole subtree is terminal.
With canonical-path arguments: targeted mode — those top-level items only. A child path is refused with `not-top-level`, which names the root to archive instead.

## What happens

1. Run `gw work archive --dry-run [WORK_PATHS...]` to build the plan.
2. Present the plan: roots to move (with their subtrees) and any wikilink referrers that will become broken. Sweep prints moves and silently skips roots with open descendants; targeted mode prints each refused path and its kind (for example `not-top-level`) to stderr and exits non-zero.
3. Ask for confirmation before executing.
4. On confirmation, run `gw work archive [WORK_PATHS...]` (without `--dry-run`).
5. Report moved canonical paths and reconciled indexes.

Terminal statuses: `resolved`, `wontfix`, `superseded`.

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/lifecycle-rules.md`
