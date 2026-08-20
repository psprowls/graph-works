---
name: scan
description: Build the code graph and write one page per admitted entity (repository, package, app, agent_plugin, dependency, test_suite) into the wiki's single entities/ folder. Reports created/updated/deleted entities by URI; surfaces deletions for confirmation. Workspace and repo discovered automatically. Usage /graph-works:scan
---

# /graph-works:scan

Build the code graph and write one page per admitted entity into the wiki's single `entities/` folder. This is the **entry point** for a fresh wiki — run it right after `/graph-works:onboard`.

## Usage

```
/graph-works:scan
```

Workspace and repo are discovered automatically via `workspace_io`.

## What happens

1. **Graph build + write** — `gw scan` builds the code graph and writes one page per admitted entity into `<workspace>/wiki/entities/` (kinds: `repository`, `package`, `app`, `agent_plugin`, `dependency`, `test_suite`). Pages use URI-based filenames. The default scan then fills `## Narrative`, file/dir descriptions, overview, `## Purpose`/`## Public API` via a commit-gated subagent fan-out (emit → fan-out → apply). Placeholders (`## Narrative` placeholder, `— TODO` file-map rows) persist only on the structural-only fast path — `gw scan --no-narrate` — which skips the fan-out entirely.
2. **Frontmatter** — scanner-owned keys (`uri`, `kind`, `depends_on`, `language`, …) are replaced from the graph each scan; human keys (`status`, `last_reviewed`, `owner`, `notes`) and a non-empty `summary` are preserved.
3. **Indexes + log** — `index.md` and per-folder sub-indexes are regenerated; a `scan` entry is appended to `log.md`.
4. **Report** — created / updated / deleted entities are reported by URI. Deletions are surfaced for confirmation (with a git-based undo when the wiki is versioned); >10 deletions is a stop-and-ask red flag.

Invoke `gw scan --no-narrate` for the mechanical-only fast path, which skips the fan-out and generates no prose. The default `gw scan` runs the commit-gated fan-out (emit → fan-out → apply).

## Sub-agent

This command dispatches the `scanner` sub-agent. See `agents/scanner.md`.

## Rules

- **Don't silently delete entity pages** — always surface deletions; >10 is a red flag.
- **Default fills prose** — the default scan fills `## Narrative` and file-map descriptions via the read-only fan-out + apply phases; `--no-narrate` skips the fan-out and leaves placeholders.
- **The graph is the source** — entity pages are rendered from the code graph, not hand-written.

## When to run

- Right after `/graph-works:onboard`
- After pulling main (new packages may have landed)
- After a big refactor that added/removed/renamed packages
- Before `/graph-works:lint` (so drift reports are accurate)

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/scan-workflow.md`
