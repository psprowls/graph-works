---
name: file
description: Invoked explicitly as /gw:file. Interactively files a new path-native work item — gathers title, kind, summary, and affects conversationally, estimates effort, then invokes `gw work file` with the assembled values and reports the canonical path. Explicit invocation only, with one exception — the skill-doc-routing hook's auto-file clause invokes it for a standalone brainstorming session; do not otherwise trigger this from a natural-language description of work.
---

# File a work item

Interactively create a new work item in the workspace's OKF `work/` lane.

## Usage

```
/gw:file          # Claude Code
$file                      # Codex
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

## Auto-file mode (hook-triggered)

The one non-explicit entry into this skill. A standalone `brainstorming` session
— one that carries no work-item brief — is pointed here by the
`skill-doc-routing` hook's auto-file clause, so that a design cannot leak outside
the tracked pipeline. This section is self-sufficient: a session that reads only
this and never re-reads the hook's clause still does the right thing at all three
anchor points.

**Mode check.** If the brainstorming invocation carries a work-item brief (a
title / kind / summary block) or the line *"STOP after writing the spec"*, a work
item already exists and the pipeline is driving. Do not auto-file; do nothing
here at all. Auto-file applies to the **Architectural** path only — a Spike or a
Bounded task writes no spec document and files nothing.

### Anchor 1 — file now, on one confirm

Unlike the interactive flow above, auto-file does not prompt field by field. It
**derives** title, kind, summary, stable name, `affects` and effort from the
opening request and presents them in a single confirm, after exploring project
context and before asking the clarifying questions:

> "I'll track this as a work item — **title** / **kind** / **summary** / name:
> **stable words** / affects / effort. Good, or adjust? (or say 'don't file')"

That one confirm does two things:

- **It locks the stable basename**, from which `gw work file` derives the
  permanent canonical path. Identity is the extensionless bundle path the CLI
  returns, never a page stem.
- **It is the opt-out.** "Don't file" means plain brainstorming with nothing
  tracked — proceed with the ordinary standalone flow and ignore the rest of
  this section.

On confirm:

```bash
gw work file --json --title "<title>" --kind <Kind> --summary "<summary>" \
  --name "<stable words>" --affects "<paths or packages>" --effort <effort>
```

Read `path` from the JSON result and announce: *"Auto-filed as `<work-path>`."*
Then continue the ordinary brainstorming flow — clarifying questions, approaches,
sectioned design — unchanged.

**Error fallback:** if `gw work file` fails (a duplicate path, a validation
error), report the error verbatim and fall back to plain brainstorming with no
work item. Do not block the session.

### The Epic/Release hand-off

**When the confirmed kind is `Epic` or `Release`, stop after filing.** Do not
continue the brainstorming flow. Emit exactly:

> "Filed as `<work-path>`. Clear context (`/clear`) and run
> `/gw:workflow <work-path>` to continue."

The pipeline then dispatches `gw:epic-design`, which owns the design
stage for those two types. Letting a general brainstorming flow finish an epic
design would produce exactly the shape `epic-design` exists to prevent — a
per-child design instead of a thin child index. The cost is one `/clear` at a
boundary where the item is already filed and nothing is lost.

Anchors 2 and 3 below apply to every other kind.

### Anchor 2 — at spec time

When the design is approved, write the spec as follows instead of to any
default location:

1. **Write the design artifact** to
   `<workspace>/okf/<work-path>/references/01-design.md`. The owned directory
   already exists — `gw work file` created it. The `skill-doc-routing` hook
   injects the resolved absolute workspace path into your context; use it.
2. **Advance the item:** `gw work advance <work-path> --effort <confirmed-effort>`.
   This is the same design-complete transition the `workflow` skill applies: it
   stamps the canonical `design` source and advances the phase.

**Error fallback:** if `gw work advance` fails, report it. The design is already
at the canonical path, so the user recovers with
`/gw:workflow <work-path>`.

### Anchor 3 — terminal

Once auto-filed, brainstorming follows pipeline rules: **STOP after the spec — do
not invoke `writing-plans`.** End with the pipeline hand-off line:

> "Phase advanced. Clear context (`/clear`) and run
> `/gw:workflow <work-path>` to continue."

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/wiki-schema.md`
→ `../graph-works/references/lifecycle-rules.md`
