# graph-works 

> **Substrate ownership.** This document describes behavior that the graph-works rebuild is
> re-implementing. Identifiers and paths here are retargeted for the `graph-works` namespace, but
> the behavioral truth is owned by [`epic-graph-works-core`](/work/_archive/epic-graph-works-core.md) and is re-authored there, not here.
> Treat a disagreement between this page and that item as this page being stale.

> **Maintained documentation for a source code repository — single package, monorepo, or hybrid.**
> An adaptation of [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) targetting source code repositories.


Turn any LLM CLI into a disciplined wiki maintainer for your repo. graph-works works on any repo shape — single package, workspace-style monorepo (Turborepo / pnpm / Nx / Bazel / Cargo / Go workspaces), or a hybrid. It builds a code graph and renders one page per entity (repository, package, app, agent_plugin, dependency, test_suite) into a single `entities/` folder. The LLM walks your code, cross-references packages and concepts, ingests specs and articles and PRs, and keeps everything current as the code evolves.

## When to use

- Single-package libraries or services
- Monorepos (Turborepo, pnpm workspaces, Nx, Cargo workspaces, Go workspaces)
- Hybrid repos (a primary app plus loose internal libs, or mixed-language trees)
- Any repo where you want a compounding, cross-referenced wiki maintained alongside the code

## The idea in one paragraph

READMEs go stale. Architecture diagrams drift. Comments rot. This skill turns an LLM into a disciplined wiki maintainer that **reads the code**, **ingests your specs, PRs, and articles**, and **writes a persistent, interlinked Obsidian-compatible vault** alongside the repo. Every package has a summary page. Every decision has an ADR. Every ingested article gets filed and cross-linked. Linting detects **code drift** — packages added/renamed/deleted without the vault noticing. The vault compounds instead of rotting.

## What's in the box

| Piece | What it does |
|---|---|
| **SKILL.md** | Master skill — architecture, workflows, page categories, iron rules |
| **4 sub-agents** | `graph-works:scanner`, `graph-works:ingestor`, `graph-works:librarian`, `graph-works:linter` |
| **13 slash commands** | `/graph-works:onboard`, `/graph-works:scan`, `/graph-works:ingest`, `/graph-works:query`, `/graph-works:lint`, `/graph-works:log`, `/graph-works:file`, `/graph-works:archive`, `/graph-works:regen-index`, `/graph-works:status`, `/graph-works:next`, `/graph-works:proposals`, `/graph-works:auto-drive` |
| **Substrate operations** | Via `gw`: `bootstrap`, `scan`, `ingest`, `query`, `wiki lint` (+ code-drift) |
| **12 reference docs** | Schema, page formats, 4 workflows (scan/ingest/query/lint), Obsidian setup, cross-tool setup, monorepo principles, lifecycle rules, sidecar schema |
| **Wiki templates** | `CLAUDE.md`, `AGENTS.md`, `cursorrules`, `index.md`, `log.md`, plus entity templates (`entity-repository`, `entity-package`, `entity-app`, `entity-agent-plugin`, `entity-dependency`, `entity-test-suite`) and curated-page templates (`concept`, `concept-pattern`, `concept-architecture`, `source`, `adr`, `dependency`, `work`, `index`) |

## Quick start

```bash
# 1. Locate or create the workspace, then configure it (in Claude Code)
> /graph-works:onboard

# 2. Open the workspace in Obsidian (sidebar will show okf/ and its pages).
open -a Obsidian ~/my-repo/.works

# 3. Scan the repo — renders one entities/ page per admitted entity (package, app, dependency, …)
cd ~/my-repo
# in Claude Code:
> /graph-works:scan

# 4. Stage a source (article, spec, PR summary) under <workspace>/raw/ and ingest
> /graph-works:ingest graph-works/raw/specs/auth-migration.md

# 5. Ask questions
> /graph-works:query "which packages depend on common-context-node-ts?"

# 6. Health check (mechanical + semantic + code drift)
> /graph-works:lint
```

## Page categories

| Category | Example |
|---|---|
| `app` | `<workspace>/wiki/entities/app_web-next-ts.md` — Next.js app: platform, routes, deployment |
| `package` | `<workspace>/wiki/entities/pkg_common-aws-node-ts.md` — Lambda handlers, middleware, exports |
| `concept` | `<workspace>/wiki/concepts/global-context.md` — cross-cutting pattern; or `kind: architecture` for high-level syntheses, `kind: pattern` for reusable patterns |
| `dependency` | `<workspace>/wiki/entities/dep_react.md` — external lib: versions in use, upgrade notes, gotchas (`kind: package | service`) |
| `source` | `<workspace>/wiki/sources/2026-04-auth-migration-spec.md` — ingested spec with claims + citations |
| `adr` | `<workspace>/wiki/adrs/0012-move-to-esm.md` — dated decision with context + consequences |

## Cross-tool compatibility

Only the schema loader file changes per tool. The scripts run identically everywhere.

| Tool | Loader file |
|---|---|
| Claude Code | `<workspace>/CLAUDE.md` |
| Codex CLI (OpenAI) | `<workspace>/AGENTS.md` |
| Cursor (modern) | `<workspace>/AGENTS.md` |
| Cursor (legacy) | `<workspace>/.cursorrules` |
| Antigravity (Google) | `<workspace>/AGENTS.md` |
| OpenCode / Pi | `<workspace>/AGENTS.md` |
| Gemini CLI | `<workspace>/AGENTS.md` |

`gw bootstrap` does not generate these loader files — author whichever ones your tools need. Your repo's root `CLAUDE.md` (build/lint conventions) and a workspace-level one (vault conventions) are independent.

## Architecture

```
<repo>/graph-works/             # workspace; Obsidian vault opens here
├── workspace.yaml              # workspace manifest
├── CLAUDE.md                  # workspace-level schema (owned by gw)
├── raw/                       # source inbox; ingested sources move to _archive/
│   ├── articles/              # clipped web articles
│   ├── specs/                 # design docs, RFCs
│   ├── prs/                   # PR summaries
│   ├── tickets/               # issue exports
│   └── transcripts/           # meeting notes
├── work/                      # unified bugs / tech debt / features / initiatives / spikes (owned by gw)
├── knowledge/                 # other plugin-managed knowledge stores
└── wiki/                      # this plugin's curated knowledge base
    ├── index.md               # content catalog
    ├── log.md                 # append-only timeline
    ├── entities/              # one graph-derived page per admitted entity (pkg_*, app_*, dep_*, repo_*, *_tests_*)
    ├── concepts/              # cross-cutting concepts; kind: architecture for high-level syntheses
    ├── sources/               # one summary per ingested source
    ├── adrs/                  # decision records
    ├── CLAUDE.md              # wiki-local schema (Claude Code)
    └── AGENTS.md              # wiki-local schema (others)
```

**Iron rule:** the code is the source of truth. The LLM never edits file contents under `<workspace>/raw/` (ingested sources are moved to `raw/_archive/`); all wiki writes go under `<workspace>/wiki/`. Work items live at `<workspace>/work/` and are referenced from wiki pages via wikilinks (e.g. `[[../work/release-healthkit/children/epic-reliability/children/bug-flaky-healthkit-tests]]`).

## Four operations

- **Scan** — build the code graph from the repo (`package.json`, `pnpm-workspace.yaml`, `pyproject.toml`, `Cargo.toml`, `go.mod`) and write/update/delete one `entities/` page per admitted entity; surface deletions for human review
- **Ingest** — read a source, discuss with user, write summary, update 5-15 cross-referenced pages, update index, log
- **Query** — index-first read, drill into 3-10 pages, synthesize with inline citations, offer to re-file the answer
- **Lint** — mechanical checks (orphans, broken links, stale pages, missing frontmatter) + semantic checks (contradictions, cross-reference gaps) + **code-drift** (packages on disk vs. in vault)

## Why not just maintain READMEs?

| READMEs | Code Wiki |
|---|---|
| One per package, manually written | One per package, LLM-maintained and cross-linked |
| Go stale silently | `lint` detects drift mechanically |
| No cross-references | Every package links to concepts, sources, ADRs |
| No history of why decisions were made | ADRs capture decisions; log tracks every ingest/scan |
| Specs and articles live elsewhere | Ingested into `raw/` and summarized in `sources/` |

## License

MIT.

## Related
- [Karpathy's original gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- Vannevar Bush, "As We May Think" (1945) — the Memex
