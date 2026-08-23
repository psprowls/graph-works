---
name: lint
description: Run a health check on the Code Wiki — mechanical (orphans, broken links, stale pages, missing frontmatter, duplicates, log gap), semantic (contradictions, cross-reference gaps, stale claims, ADR chain), code-drift (packages on disk vs. in vault, exports mismatch), and work lifecycle (32 rules for work item lifecycle state). Workspace and repo discovered automatically. Usage /graph-works:lint
---

# /graph-works:lint

Health-check the wiki. Includes **code-drift detection** on top of generic wiki checks — surfaces when the vault has fallen out of sync with the code.

**Reports, doesn't silently fix.** You decide what to change.

Run weekly, after every `/graph-works:scan`, and after batch ingests.

## Usage

```
/graph-works:lint
```

No arguments. The staleness and log-gap thresholds are `gw wiki lint`'s own, not
something this command threads through.

Workspace and repo are discovered automatically via `workspace_io`.

## What happens

### Pass 1 — Mechanical (scripts)

- `gw wiki lint` — orphans, broken links, stale, missing frontmatter, duplicate titles, log gap, **+ code drift** (packages missing from vault, vault pages for deleted packages, exports drift), **+ sync drift** (`package_sync_drift` for package/app pages whose source changed since `last_sync_commit`; never-synced stubs flagged separately), **+ Obsidian render** (markdown that breaks Obsidian's renderer), **+ guidance frontmatter** (`wiki/guidance/` pages), **+ scanner heading drift** (renamed/dropped deterministic sections), **+ source path drift** (archived `raw/` sources)
- `gw wiki stats` — hubs, sinks, components
- Work lifecycle — the catalog in `lifecycle-rules.md` runs against every path-native work item via `gw work lint`; findings are keyed by canonical `path`.

### Pass 2 — Semantic (LLM)

- Contradictions between vault pages and between vault and code
- Stale claims on recently-touched packages
- Concepts mentioned across 3+ pages without their own page
- ADR chain health (supersedes / superseded_by)
- Cross-reference gaps
- Index drift

### Pass 3 — Report

Markdown report grouped under `## Wiki lint` header with suggested actions, then appends a `lint` entry to `log.md`.

## Sub-agent

Dispatches the `linter` sub-agent. See `agents/linter.md`.

## Frequency

| Trigger | Pass |
|---|---|
| Weekly | Mechanical only |
| After `/graph-works:scan` | Full — catches drift |
| After batch ingest | Full |
| Monthly | Full + structural review |
| Before sharing the wiki | Full + extra review |

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/lint-workflow.md`
