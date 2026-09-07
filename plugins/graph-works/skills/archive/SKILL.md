---
name: archive
description: Invoked explicitly as /graph-works:archive [work-path...]. Archives terminal-status work items (resolved/wontfix/superseded) via `gw work archive` — sweep mode with no arguments, targeted mode with canonical paths — presenting the plan and asking for confirmation before executing. Mutates the workspace; explicit invocation only, never on inference.
---

# Archive terminal work items

Move terminal work items from their active lanes to the corresponding local `_archive/` lane.

## Usage

```
/graph-works:archive          # Claude Code
$archive                      # Codex
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

## Root-only

**A work item is archived only as a root. Children ride along, in place.**

- Sweep mode selects only **outermost terminal roots** — never a child, under
  any ancestor state. A resolved child of an open epic stays at
  `children/<name>` until its root archives.
- Targeted mode **refuses** a child whose nearest non-archived ancestor is
  non-terminal, with `ancestor-not-terminal` — *"archive the root instead"*.
  There is no per-child override.
- Archiving a root moves every descendant to `<dest>/children/<basename>`, with
  **no** per-level `_archive` lane. A child already sitting in
  `children/_archive/` is flattened into `children/` along with everything else.
  Every flattened path still parses as archived: `_archive` is sticky at any
  depth.

Nested `children/_archive/` directories under already-archived roots are legacy.
They are left exactly as they are — an archived page is frozen — and nothing can
create that shape any more.

`state.archive-eligible` does not fire on a child the policy holds in place, so
lint no longer asks for a sweep that would be refused.

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/lifecycle-rules.md`
