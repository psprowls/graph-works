# Monorepo Wiki Principles

> **Substrate ownership.** This document describes behavior that the graph-works rebuild is
> re-implementing. Identifiers and paths here are retargeted for the `graph-works` namespace, but
> the behavioral truth is owned by [`2026-08-13-epic-feature-scan-pipeline-vertical`](/work/2026-08-13-epic-feature-scan-pipeline-vertical.md) and is re-authored there, not here.
> Treat a disagreement between this page and that item as this page being stale.

Why this pattern works for a source code monorepo, and how it differs from the generic LLM Wiki.

## The problem the wiki solves

A medium-to-large monorepo has knowledge scattered across:

- `README.md` files (package-local, often stale)
- JSDoc / docstrings (reference docs, not narrative)
- Specs and RFCs (in Notion / Google Docs / Linear — not alongside the code)
- PR descriptions (ephemeral; hard to retrieve 6 months later)
- Slack threads and meeting notes (lost)
- The developers' heads (single points of failure)
- Clipped articles about libraries or patterns (on someone's desktop)

Querying this knowledge requires grepping, context-switching, and asking around. Agents have it worse — they can read files but can't ask people. The result: agents re-derive architecture on every session, and new humans take weeks to onboard.

A maintained wiki **compiles** this into one searchable, cross-referenced layer alongside the code.

## Why this works better than READMEs

| READMEs | Code Wiki |
|---|---|
| Written once, go stale silently | Incrementally updated on every scan/ingest; `lint` detects drift |
| One-directional (README describes package) | Bidirectional — packages ↔ concepts ↔ decisions |
| Manual maintenance | LLM does the bookkeeping |
| Can't capture decisions (those live in PR descriptions) | ADRs capture decisions with traceable history |
| Can't consolidate across tickets/PRs | Issues and architecture pages synthesize across sources |
| No home for ingested articles or external material | `raw/` + `sources/` keep everything alongside code |

## Why LLMs make this work now

The tedious part of a wiki isn't writing — it's the bookkeeping: updating cross-references when a package gets a new dependent, keeping summaries current, noticing when the code has drifted from the doc, maintaining consistency across dozens of pages. Humans abandon wikis because maintenance grows faster than value.

LLMs don't get bored. They don't forget to update a cross-reference. They can touch 15 files in one pass. They cost near-zero per maintenance operation. What collapses is the human's job:

- **Source curation** — deciding what specs/articles/PRs are worth ingesting
- **Direction** — asking good questions, steering the analysis
- **Judgment** — deciding when a contradiction matters, when to create an ADR
- **Taste** — knowing when the wiki's synthesis is wrong

Everything else — the 80% that killed human wikis — is delegated.

## Differences from the generic LLM Wiki

The generic [wiki](../../wiki) pattern (entities/concepts/sources/synthesis/comparisons) is designed for personal knowledge bases, research, book companions. Code Wiki adapts it for code:

| Generic LLM Wiki | Code Wiki |
|---|---|
| `entities/` (people, orgs, places) | `entities/` (one graph-derived page per admitted entity kind: repository, package, app, agent_plugin, dependency, test_suite) |
| `concepts/` | `concepts/` (cross-cutting patterns; `<a>-vs-<b>.md` comparisons live here) |
| `sources/` | Same, but source types are: spec, PR, ticket, article, transcript, RFC |
| `synthesis/` | `concepts/` (with `kind: architecture`) |
| *(none)* | `entities/dep_*` — one graph-derived page per dependency (`kind: package | service`) |
| *(none)* | `adrs/` — dated, citable decisions |
| Index-first retrieval | Same, plus **code-drift detection** (entities on disk vs. in wiki) |
| One-time curation | Continuous — every scan/merge picks up new entities automatically |

## The code is the source of truth

This is the most important principle and the biggest difference from the generic LLM Wiki:

> **If the wiki and the code disagree, the code wins. Update the wiki.**

The generic wiki treats ingested sources as authoritative. Code Wiki treats the code as authoritative. Sources are ingested as the **context around** the code — why something was built, what was proposed, what was decided — but a package's wiki page claim "exports X" is only as true as `packages/xxx/src/index.ts` says it is. The linter's **code-drift** pass mechanically checks this.

Consequence: after major refactors, run `/graph-works:scan` and `/graph-works:lint` to surface the drift. Don't trust the wiki's claims about code without spot-checking.

## When the wiki isn't enough

- **API reference** — the wiki gives you the high-level "what this package is for" and "how it's used"; for signature-level detail, read the code. JSDoc / typedoc still belongs in the code.
- **Current bug state** — the wiki captures synthesized issues; for up-to-the-minute bug tracking, use Linear/Jira/GitHub Issues.
- **Code style** — root `CLAUDE.md` / Biome config / eslint-config own this, not the wiki.

The wiki is a compiled *reading* layer. It doesn't replace the tools people use to *work*; it makes it easier to understand what the work is.

## Reading recommendations

- Karpathy's original LLM Wiki gist — the parent pattern
- Vannevar Bush, "As We May Think" (1945) — the Memex; why humans can't do this alone
- Michael Nygard, "Documenting Architecture Decisions" (2011) — the original ADR essay
- Ousterhout, *A Philosophy of Software Design* — "deep modules" / well-summarized pages beat shallow ones
