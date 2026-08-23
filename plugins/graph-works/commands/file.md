---
name: file
description: Interactively file a new path-native work item — gathers title, kind, summary, and affects conversationally, then invokes `gw work file` with the assembled values. Usage /graph-works:file
---

# /graph-works:file

Interactively create a new work item in the workspace's OKF `work/` lane.

## Usage

```
/graph-works:file
```

Gathers required fields conversationally, then invokes `gw work file`.

## What happens

1. Prompt for **title** (required) — a short description of the issue or feature.
2. Prompt for **kind** (required) — one of: `Release`, `Epic`, `Feature`, `Bug`, `TechDebt`, `TestGap`, `Spike`.
3. Prompt for **summary** (required) — one line, <=100 chars.
4. Prompt for **affects** (required) — comma-separated paths or package names (e.g. `packages/graph-io, packages/wiki-io`).
5. **Estimate effort** — based on the title, kind, and summary, propose an effort value (`xtra-small|small|medium|large|xtra-large`) with a one-line rationale. Present it to the user: "I'd estimate this as **medium** — multiple files across packages, likely one PR. Does that sound right?" The user can accept or name a different size.
6. **Propose a stable name** — pick concise intelligible words from the title and present them alongside the effort estimate. The CLI normalizes them and prefixes the item kind.
7. Optionally prompt for: parent path, complete dependency edges, `blast-radius` (file|package|domain|system), version, target date, owner, and tags.
8. Auto-sets `work_status: open` and `opened: <today>`.
9. Invoke:

```bash
gw work file \
  --title "..." \
  --kind "..." \
  --summary "..." \
  --affects "..." \
  --effort "..." \
  --name "..." \
  [--parent-path <work-path>] \
  [--dep "path=<work-path>,blocks=execute,needs=resolved"] \
  [--blast-radius ...] [--version ...] [--target-date YYYY-MM-DD] \
  [--owner ...] [--tags ...] \
  --json
```

10. Read `path` from the JSON result and report that canonical path.

## Effort scale

| Value | Anchor |
|---|---|
| `xtra-small` | minutes — one-line change, no test, no review needed |
| `small` | hours — single file, tests, single PR |
| `medium` | days — multiple files, possibly cross-package, single PR |
| `large` | weeks — multiple PRs, possibly an epic |
| `xtra-large` | months — multi-epic, large team or quarter-long scope |

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/wiki-schema.md`
→ `graph-works/references/lifecycle-rules.md`
