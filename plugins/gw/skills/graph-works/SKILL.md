---
name: graph-works
description: Use when building or maintaining a persistent wiki alongside any source-code project — single packages, monorepos, or hybrid shapes. Builds a code graph and renders one page per entity (repository, package, app, agent_plugin, dependency, test_suite) nested under repositories/<repo>/ (one folder per kind) and dependencies/<ecosystem>/ for external packages. Triggers include "wiki this repo", "document this codebase", "graph-works", "ingest this spec/PR/article into the wiki", or whenever the user wants a compounding, cross-referenced knowledge base alongside source code.
context: fork
version: 0.1.1
author: psprowls
license: MIT
tags: [monorepo, documentation, knowledge-management, obsidian, wiki, turborepo, pnpm, nx]
compatible_tools: [claude-code, codex-cli, cursor, antigravity, opencode, gemini-cli]
---

# Code Wiki — Maintained Documentation Alongside Any Source-Code Project

Adapts the LLM Wiki pattern ([graph-works](../graph-works/SKILL.md); Karpathy's [gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)) to a source code monorepo. The LLM incrementally builds and maintains a persistent, interlinked markdown vault that documents every package, app, and cross-cutting concept in the repo — plus ingested specs, PR summaries, articles, and design notes.

## Core principle

Code comments go stale. README files rot. Architecture diagrams drift from reality. The wiki is **compounding, cross-referenced, and kept current** — sources (code, specs, PRs, articles) are read once and integrated into package summaries, ADRs, and architecture syntheses. Every claim links to a source; contradictions with newer code get flagged; the index stays in sync with what the repo actually looks like.

> Obsidian is the reading room. The LLM is the maintainer. Your repo is the source of truth.

## When to use

- **Single-package repos** — libraries, services, or apps where a README isn't enough and you want per-module/area pages
- **Monorepos** — Turborepo, pnpm workspaces, Nx, Bazel, Rush, Lerna, Go workspaces, Cargo workspaces
- **Hybrid repos** — a primary package at the root with nested apps/packages
- **Onboarding** — a wiki that an LLM keeps up to date reduces the cost of new contributors (human or agent)
- **Agent-assisted development** — coding agents read the vault before making edits; they edit the vault as they go
- **Architecture bookkeeping** — ADRs, cross-package conventions, deprecation notices, migration plans
- **Spec/PR/article ingestion** — you clip an article, write a spec, review a PR — all feed into the vault

**Do NOT use when:** the team has a documentation CMS they prefer, or nobody will curate ingestion (vault quality = source quality).

## Architecture

The workspace root is `<repo>/.works/`. `gw` resolves it from an explicit
`--workspace`, then `GRAPH_WORKS_DIR`, then a `.git` walk-up to that default.
The OKF bundle lives at `<workspace>/okf/`; `.gw/` holds the control plane.
`gw ingest --source <path>` reads material directly from any filesystem path
and copies it into `okf/sources/references/`. Work identity is a canonical
extensionless path under `okf/work/`; every item owns the directory beside its
page, and managed artifacts live under that directory’s `references/` child.

```
<repo>/.works/                   # workspace; Obsidian vault opens at okf/
├── workspace.yaml               # workspace manifest
├── .gw/                         # control plane (cache/, worktrees/ nest here)
└── okf/                         # this plugin’s curated OKF bundle
    ├── index.md                 # Content catalog (LLM updates every ingest/scan)
    ├── log.md                   # Append-only timeline
    ├── work/                    # path-native work tree (owned by gw)
    │   ├── <release>.md
    │   └── <release>/children/<epic>/children/<feature>.md
    │       # every item has a sibling owned directory with references/
    ├── repositories/<repo>/
    │   ├── repository.md        # the repository’s own entity page
    │   ├── packages/<name>.md
    │   ├── apps/<name>.md
    │   ├── agent-plugins/<name>.md
    │   └── test-suites/<name>.md
    ├── dependencies/<ecosystem>/<name>.md   # sibling root, not nested under repositories/
    ├── docs/tutorials/ docs/how-tos/ docs/reference/ docs/explanations/   # Diátaxis lanes
    ├── sources/                 # One summary page per ingested source
    │   └── references/          # gw ingest’s copies of ingested material
    ├── adrs/                    # Architecture Decision Records
    ├── proposals/                # curated-page proposal ledger
    ├── .templates/              # Page templates (reference only, not indexed)
    ├── CLAUDE.md                # wiki schema + conventions (Claude Code)
    └── AGENTS.md                # same content for Codex/Cursor/Antigravity/OpenCode
```

Every workspace package and app — plus the repository, external dependencies, and
test suites — is rendered as a single page nested under `repositories/<repo>/`
(one folder per kind), except `dependency` pages, which live at the sibling root
`dependencies/<ecosystem>/<name>.md`. There is no filename-prefix scheme and no
`entities/` folder — the graph is the sole source for which entities exist.

**Source of truth is the code itself.** The wiki is a compiled layer above it. If the wiki disagrees with the code, the code wins — the wiki gets updated.

## Four core operations

1. **Scan** — build the code graph and render one page per admitted entity into `repositories/<repo>/` (and `dependencies/` for deps); the default scan then fills prose via a commit-gated **emit → fan-out → apply** pipeline (`## Narrative`, file/dir descriptions, `## Purpose`/`## Public API`). A bare `--no-narrate` invocation is the mechanical structural-only fast path (`## Narrative` placeholder + `— TODO` file-map rows). See `references/scan-workflow.md`.
2. **Ingest** — `gw ingest --source <any path>` reads material directly (article, spec, PR, transcript) and classifies it. By default (`claude_code` backend) it returns a brief and writes nothing — the `ingest` skill discusses with you, then drafts a source summary, links relevant pages, updates the index, and appends to the log. `--backend bedrock` (or `vercel`) runs the fully autonomous one-call pipeline instead.
3. **Query** — read `index.md`, drill into 3-10 pages, synthesize with inline root-absolute markdown links, offer to file the answer back. See `references/query-workflow.md`.
4. **Lint** — health check including **code-drift detection**: packages on disk missing from the vault, vault pages referencing deleted/renamed packages, stale package summaries whose exports have changed. See `references/lint-workflow.md`.

## Quick start

```bash
# 1. Locate or create the workspace, then configure it.
#    The workspace resolves from --workspace, then GRAPH_WORKS_DIR, then a
#    .git walk-up, defaulting to <repo>/.works. The OKF bundle lives at
#    <workspace>/okf/.
/gw:onboard

# 2. Scan the repo to render one repositories/<repo>/ page per admitted entity
/gw:scan

# 3. Ingest a source (article, spec, PR) from anywhere on disk
/gw:ingest ~/Downloads/auth-migration.md

# 4. Ask questions
/gw:query "which packages depend on common-context-node-ts?"

# 5. Health check (surfaces code-drift too)
/gw:lint
```

## Entry points

Every entry point is a skill under `skills/`. Claude Code namespaces plugin
skills as `<plugin>:<name>`, so each is invoked `/gw:<name>`. Codex
invokes skills with a `$` sigil and rejects unrecognised `/` tokens
client-side, so there the form is `$<name>` — or `$gw:<name>` if your
Codex build namespaces plugin skills rather than exposing them flat.

| Skill | Purpose |
|---|---|
| `onboard` | Locate or create the workspace (defaults to `<repo>/.works`), then configure it |
| `scan` | Build the code graph; create/update/delete one page per admitted entity under `repositories/<repo>/` (or `dependencies/`) |
| `ingest` | Read a source from any path, update vault, log it |
| `query` | Search vault, synthesize answer with citations, offer to file back |
| `lint` | Health check — orphans, broken links, stale claims, **code drift**, and the work-layer catalog |
| `log` | Show recent log entries (uses unix tools on `log.md`) |
| `file` | Interactively file a new work item (`gw work file`) |
| `archive` | Archive terminal-status work items (`gw work archive`) |
| `regen-index` | Reconcile Markdown indexes throughout the path-native work tree |
| `status` | One-screen work item rollup (`gw work status`) |
| `workflow` | Drive a work item to its next pipeline stage (`gw work next`/`advance`) |
| `proposals` | Review/accept/reject/supersede curated-page proposals |
| `auto-drive` | Drive a work item's full pipeline unattended via Orca-supervised workers |

`scan`, `ingest`, `query`, and `lint` each carry a `## Dispatch` section stating
that a forked sub-agent with `Read, Write, Edit, Bash, Grep, Glob` is preferred,
and that running inline is the supported fallback on a harness without sub-agent
dispatch.

## Cross-tool compatibility

Every substrate operation goes through the `gw` CLI — one boundary, no in-process imports. Run `gw <verb> --help` for flags. The full set of verbs these skills depend on is the CLI contract at `okf/explanations/graph-works-plugin-cli-contract.md`.

Schema lives in `<workspace>/okf/CLAUDE.md` (Claude Code) or `<workspace>/okf/AGENTS.md` (Codex/Cursor/Antigravity/OpenCode). The plugin ships both. The `gw` CLI runs identically everywhere. See `references/cross-tool-setup.md`.

**Note:** your repo's root `CLAUDE.md` is separate from the wiki's `CLAUDE.md`. The root file defines the repo's build/style conventions; the wiki file defines how the vault is structured. Both are active simultaneously when working from the repo root.

## Page types

`type:` in the frontmatter selects the contract; `.gw/schema/<Type>.schema.json` and `.gw/sections/<Type>.yaml` are authoritative (see `references/wiki-schema.md`).

| `type` | What it documents | Directory |
|---|---|---|
| `Repository`, `Package`, `App`, `AgentPlugin`, `TestSuite`, `File` | Graph-derived entity pages, one per admitted entity — written by `gw scan` | `<workspace>/okf/repositories/<repo>/…` |
| `Dependency` | An external package the repository depends on — written by `gw scan` | `<workspace>/okf/dependencies/<ecosystem>/<name>.md` |
| `Explanation` | Cross-cutting technical idea, pattern, or architecture synthesis. Comparisons (`<a>-vs-<b>.md`) live here too. | `<workspace>/okf/explanations/` |
| `Reference`, `HowTo`, `Tutorial` | The other Diátaxis lanes | `docs/reference/`, `docs/how-tos/`, `docs/tutorials/` |
| `Source` | Summary of an ingested spec, PR, article, transcript, etc. | `<workspace>/okf/sources/` |
| `Adr` | Architecture Decision Record — a dated, citable decision with context + consequences | `<workspace>/okf/adrs/` |
| `Proposal` | A proposed curated page awaiting disposition | `<workspace>/okf/proposals/` |
| `Release`, `Epic`, `Feature`, `Bug`, `TechDebt`, `TestGap`, `Spike` | Work items | `<workspace>/okf/work/` |

## Why this works (vs. just READMEs or generic docs)

| READMEs / generic docs | Code Wiki |
|---|---|
| Written once, go stale | Incrementally updated on every ingest/scan |
| One-directional (README describes package) | Bidirectional — packages link to explanations link to ADRs link to sources |
| Updates are manual chores | LLM does the cross-reference maintenance |
| Drift is invisible until you read | Lint surfaces drift mechanically |
| Searchable only by file | Indexed by type + frontmatter + BM25 |
| Specs/PRs/articles live in separate systems | Ingested and linked alongside code documentation |

## Related skills

- **`wiki`** — the generic personal-knowledge-base version of this skill. Same pattern, different page categories. Use `wiki` for non-code topics (research, books, journaling).
- **`para-memory-files`** — PARA memory; useful if you have personal memory feeding into a repo wiki.

## Reference docs

- `references/wiki-schema.md` — full vault layout, page frontmatter per type, taxonomies, body-table conventions
- `references/page-formats.md` — declared headings per type and annotated examples for entity, dependency, explanation, source, ADR, proposal, work pages
- `references/scan-workflow.md` — how the scanner builds the code graph and renders entity pages
- `references/ingest-workflow.md` — detailed ingest flow
- `references/proposal-disposition.md` — review/accept/reject/supersede curated-page proposals; approve only flips `page_status` and appends `verified[]`, then you fan out one subagent per page to author it at the note's `target`
- `references/query-workflow.md` — query patterns, citation format, re-filing answers
- `references/lint-workflow.md` — health-check heuristics including code-drift detection
- `references/obsidian-setup.md` — Obsidian plugins, hotkeys, vault config
- `references/cross-tool-setup.md` — per-tool setup (Codex, Cursor, Antigravity, etc.)
- `references/monorepo-principles.md` — why this pattern works for code, how it differs from the generic LLM Wiki
- `references/lifecycle-rules.md` — the work-layer lint catalog with severities and remediation, run by `/gw:lint` and `gw work lint`

## Iron rules

1. **The code is the source of truth.** If the vault contradicts the code, the code wins — update the vault.
2. **Ingested material is never edited.** `gw ingest --source <path>` copies material into `<workspace>/okf/sources/references/`; the original file is left untouched wherever it lives.
3. **All curated writes go under `<workspace>/okf/`.** Work items use canonical paths under `<workspace>/okf/work/`; managed work artifacts go only in the item’s owned `references/` directory.
4. **Every vault page has YAML frontmatter** whose `type:` selects a schema in `.gw/schema/`. Curated pages carry `type`, `title`, and `description`; graph-derived entity pages carry `type`, `title`, and `resource` plus scanner-owned keys and provenance (`generated`, `last_updated_commit`, `tokens`, …) that you never hand-edit. Set `updated:` when you touch a page. Do not copy graph-wiki-era keys (`category`, `summary`, `kind`, `uri`, `last_scan_at`) from older pages — they are retired.
5. **Every ingest or scan touches ≥3 files:** the changed/new page(s), `index.md`, `log.md`.
6. **Every claim on a package page cites** either a source page (`[…](/sources/xxx.md)`) or a code path (`packages/foo/src/bar.ts`).
7. **Good query answers get filed back** — explorations compound.
