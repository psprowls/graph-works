# Cross-Tool Setup

> **Substrate ownership.** This document describes behavior that the graph-works rebuild is
> re-implementing. Identifiers and paths here are retargeted for the `graph-works` namespace, but
> the behavioral truth is owned by [`2026-08-13-epic-feature-workspace-manifest-layout-resolution`](/work/2026-08-13-epic-feature-workspace-manifest-layout-resolution.md) and is re-authored there, not here.
> Treat a disagreement between this page and that item as this page being stale.

The graph-works plugin is tool-agnostic. The **scripts** are pure Python stdlib and run anywhere. Only the **schema loader file** (the file the tool reads to understand conventions) differs per tool.

## How different CLIs discover wiki-level instructions

| Tool | Loader file (inside the wiki) | Notes |
|---|---|---|
| Claude Code | `<workspace>/CLAUDE.md` | Loaded automatically when CC starts in the wiki dir |
| Codex CLI (OpenAI) | `<workspace>/AGENTS.md` | Loaded at session start |
| Cursor (new) | `<workspace>/AGENTS.md` | Modern Cursor reads `AGENTS.md` |
| Cursor (legacy) | `<workspace>/.cursorrules` | Older Cursor versions |
| Google Antigravity | `<workspace>/AGENTS.md` | Standard `AGENTS.md` convention |
| OpenCode / Pi | `<workspace>/AGENTS.md` | Same convention |
| Gemini CLI | `<workspace>/AGENTS.md` | Same convention |
| Aider | `CONVENTIONS.md` or `.aider.conf.yml` | Point Aider at `CLAUDE.md` with `--read` |

`<workspace>` is the graph-works workspace directory (default `<repo>/.works`; workspace path resolved via `gw`). The OKF bundle lives at `<workspace>/okf/`, alongside the control plane `.gw/` and its gitignored `.gw/cache/` and `.gw/worktrees/`.

**Recommendation:** ship **both** `CLAUDE.md` and `AGENTS.md` in every workspace. `gw bootstrap` does not write them — author them once and symlink the second to the first.

## Two CLAUDE.md files — the repo's and the wiki's

Monorepos usually have a root `CLAUDE.md` at the repo root (build commands, package conventions, style rules). When you initialize a graph-works *inside* the repo, there's now a **second** `CLAUDE.md` at `<workspace>/CLAUDE.md` — the workspace's schema file.

Both are active when Claude Code runs from the repo root: CC loads all `CLAUDE.md` files up the tree. They don't conflict because they describe different things:

- **Root `CLAUDE.md`** — how to build, lint, test; package naming conventions; style rules
- **Wiki `CLAUDE.md`** — how the wiki is structured, ingest/scan/lint workflows, page categories

If you want to keep them visually separated, name the workspace one `CLAUDE.works.md` and symlink `<workspace>/CLAUDE.md` → `CLAUDE.works.md`. But most of the time they coexist cleanly.

## Multi-tool workspace

`gw bootstrap` creates the workspace and its OKF bundle; it does not write loader
files. Author `CLAUDE.md` once at the workspace root and point the others at it:

```bash
cd <workspace>
ln -sf CLAUDE.md AGENTS.md
ln -sf CLAUDE.md .cursorrules
```

## Per-tool quickstart

### Claude Code

```bash
cd <repo>             # /graph-works:onboard resolves the workspace via gw
claude
> /graph-works:onboard            # if the workspace isn't initialized
> /graph-works:scan          # detect packages
> /graph-works:ingest raw/specs/auth-migration.md
> /graph-works:query "which packages depend on common-context-node-ts?"
```

### Codex CLI

Codex reads `AGENTS.md` automatically. Then natural language:

```bash
cd <repo>/.works
codex
> scan the monorepo and update package pages
> ingest raw/specs/auth-migration.md into the wiki
> query: which packages depend on common-context-node-ts?
```

Codex doesn't have slash commands, but the schema file teaches it the workflows — natural-language triggers work.

### Cursor

```bash
cd <repo>/.works
cursor .
```

Open Cursor chat. Cursor auto-reads `AGENTS.md`. Same questions.

### Antigravity / OpenCode / Pi / Gemini CLI

Same as Codex — `AGENTS.md` in the wiki root, natural language.

## Running the scripts directly (any tool)

The scripts don't care which tool calls them. Run from a shell any time:

```bash
# from anywhere — workspace and repo discovered automatically via gw
gw scan --json
gw wiki lint
```

Handy aliases:

```bash
alias graph-works-scan='gw scan'
alias graph-works-lint='gw wiki lint'
alias graph-works-search='gw query'
```

## MCP exposure (future)

The wiki can be exposed as an MCP tool so any MCP-capable client can query it. Planned — see `engineering/mcp-design` for the pattern.
