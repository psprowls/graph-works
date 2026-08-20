---
name: file
description: Interactively file a new work item into the wiki — gathers title, kind, summary, and affects conversationally, then invokes `gw work file` with the assembled values. Usage /graph-works:file
---

# /graph-works:file

Interactively create a new work item in `wiki/work/`.

## Usage

```
/graph-works:file
```

Gathers required fields conversationally, then invokes `gw work file`.

## What happens

1. Prompt for **title** (required) — a short description of the issue or feature.
2. Prompt for **kind** (required) — one of: `bug`, `tech-debt`, `test-gap`, `security`, `perf`, `feature`, `epic`, `spike`.
3. Prompt for **summary** (required) — one line, <=100 chars.
4. Prompt for **affects** (required) — comma-separated paths or package names (e.g. `packages/graph-io, packages/wiki-io`).
5. **Estimate effort** — based on the title, kind, and summary, propose an effort value (`xtra-small|small|medium|large|xtra-large`) with a one-line rationale. Present it to the user: "I'd estimate this as **medium** — multiple files across packages, likely one PR. Does that sound right?" The user can accept or name a different size.
6. **Propose slug words** — pick 4 intelligible words from the title (e.g. title "Shorten work-item slugs" → `shorten work item slugs`) and present them alongside the effort estimate: "Slug words: **shorten work item slugs** — good, or adjust?" These become the filename's word suffix; the CLI derives the `<kind>`/`epic-<kind>` prefix automatically.
7. Optionally prompt for: `blast-radius` (file|package|domain|system), `target` (YYYY-QN or YYYY-MM), `owner`, `tags`.
8. Auto-sets `status: open` and `opened: <today>`.
9. Invoke:

```bash
gw work file \
  --title "..." \
  --kind "..." \
  --summary "..." \
  --affects "..." \
  --effort "..." \
  --slug-words "..." \
  [--blast-radius ...] [--target ...] [--owner ...] [--tags ...]
```

10. Report the filed page path.

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
