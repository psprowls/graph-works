# Page Formats

> **The shipped workspace tree wins.** If this page and the tree `gw bootstrap`
> actually builds (`<repo>/.works/okf/...`) disagree, this page is wrong: trust the
> tree, and file a TechDebt item for the drift.

Every page is YAML frontmatter plus a body whose headings match its `type`. Two
declarations are authoritative and installed in every workspace:

- `.gw/schema/<Type>.schema.json` — frontmatter keys, enums, required fields.
- `.gw/sections/<Type>.yaml` — required headings, their placeholders, and (for entity
  types) which frontmatter keys the scanner owns.

Read those rather than copying another page's shape; the examples below are
orientation, not a contract. The full key list per type lives in `wiki-schema.md`.
Keys from the graph-wiki era (`category`, `summary`, `kind`, `uri`, `graph_name`,
`last_scan_at`, `source_type`, `last_sync_*`, `packages`) are retired — do not write them.

Link with root-absolute markdown links (`[title](/explanations/x.md)`); never
`[[wikilinks]]`. Cite code as `` `path:line` ``.

## Section ownership

A declared section is one of two kinds:

- **Generated** (`ownership: generated`) — a deterministic projection of the code
  graph, rewritten on every scan. Never hand-edit. Examples: `## Files`, `## Commands`,
  `## Symbols`.
- **Prose** — written by an agent or a human. A prose section is refreshed only when
  the code diff since `prose_refreshed_commit` touches the entity's files, and the
  refresh treats existing text as the baseline to preserve unless the diff contradicts
  it.

An unfilled prose section keeps its `> TODO:` placeholder, which lint reports as
`sections.unfilled`.

## 1. Entity pages

Scanner-owned. Nested under `repositories/<repo>/`, one folder per kind; see
`wiki-schema.md` for paths and the owned/provenance key tables.

| `type` | Required headings | Optional headings |
|---|---|---|
| `Repository` | `Overview`, `Contents` (generated) | `Layout` |
| `Package` | `Purpose`, `Files` (generated) | `Public API` |
| `App` | `Purpose`, `Files` (generated) | `Platform & runtime`, `Routes / screens`, `Provider chain` |
| `AgentPlugin` | `Purpose`, then generated `Commands`, `Agents`, `Skills`, `Scripts`, `Hooks`, `MCP servers` | `How it fits together` |
| `TestSuite` | `Purpose`, `Files` (generated) | `How to run`, `Test conventions`, `Fixtures` |
| `File` | `Notes`, then generated `Symbols`, `Imports`, `Exports`, `Imported By` | — |

A package page:

```markdown
---
type: Package
title: code-wiki-okf
resource: pkg:org/repo/code-wiki-okf
language: python
version: 0.5.1
depends_on: [code-graph-io, okf-ext, okf-io]
test_suites: []
entry_points: []
generated:
  by: code-wiki-okf/0.5.1
  at: '2026-09-16T18:18:55.450584+00:00'
last_updated_commit: 3ff9f663
prose_refreshed_commit: 3ff9f663
---

# code-wiki-okf

## Purpose
What the package is for, in prose.

## Public API
The surface other packages import.

## Files
- [packages/code-wiki-okf/pyproject.toml](/repositories/<repo>/files/packages/code-wiki-okf/pyproject.toml.md)
- ... one link per tracked file, generated
```

Each `## Files` entry links to that file's own `File` page under
`repositories/<repo>/files/`. A package's `TestSuite` page is a sibling entity, linked
back through `tested_packages`.

## 2. Dependency page

`/gw:scan` writes one page per dependency into `dependencies/<ecosystem>/<name>.md`.

**Required frontmatter:** `type: Dependency`, `title`, `resource`, `ecosystem`.
**Optional:** `description`, `tags`, `implemented_by`, `used_by`, `versions_in_use`.
**Provenance, scanner-owned:** `generated` (`by`, `at`), `last_updated_commit`,
`prose_refresh_attempts`.
**Sections:** `## Why we depend on this` (required), then `## Gotchas / workarounds`.

```markdown
---
type: Dependency
title: React
resource: dependency:npm/react
ecosystem: npm
description: UI library used by the web application.
tags: [frontend, ui]
implemented_by: []
used_by: ["repo:acme/web"]
versions_in_use: ["react>=19"]
generated:
  by: code-wiki-okf/0.5.0
  at: '2026-09-09T21:37:46.643280+00:00'
---

# React

## Why we depend on this
React provides the component model for the web application.

## Gotchas / workarounds
Record known issues, version pins, or workarounds this dependency needs.
```

## 3. Explanation page

A cross-cutting concept, convention, pattern, or high-level synthesis. Lives in
`explanations/`. Shared frontmatter with the other Diátaxis types: `type`, `title`,
`description` required; `status`, `updated`, `tags`, `sources` optional.

Declared headings: `## Context` (required), `## Trade-offs`, `## See also`. Extra
headings are allowed (`additional_sections: true`).

```markdown
---
type: Explanation
title: Global context
description: Request-scoped context via AsyncLocalStorage providing config, database, logger, and session.
tags: [context, middleware]
updated: 2026-04-20
---

# Global context

## Context
Precise, one-paragraph definition — the canonical form used across the codebase — and
why the pattern exists.

## Shape
Optional extra section: the interface or code sketch, cited as
`packages/common-context-node-ts/src/globalContext.ts:12`.

## Trade-offs
What choosing this costs, and the alternatives.

## See also
- [common-aws-node-ts](/repositories/<repo>/packages/common-aws-node-ts.md) — injects via middleware
- [middleware-pipeline](/explanations/middleware-pipeline.md)
- [2025-12-context-refactor-spec](/sources/2025-12-context-refactor-spec.md)
```

**Variants** are a naming and tagging convention, not a schema field:

- *Pattern* — prescriptive ("when to apply this"). Tag `pattern`; use extra sections
  such as `## When to apply`, `## Solution`, `## Tradeoffs`.
- *Architecture synthesis* — layers, flows, and components spanning many packages. Tag
  `architecture`; lead with a `## Thesis` section and link back to every entity and
  ADR it draws on, with a dated `## How this synthesis has changed` log.
- *Comparison* — `explanations/<a>-vs-<b>.md`, or `<topic>-options.md` for n-way.

Tags must exist in `.gw/tags.yaml`; curated lanes enforce it. Do not add a tag to the
vocabulary to make a page pass — pick an existing concept tag.

## 4. Reference, HowTo, Tutorial

Same base frontmatter as Explanation, plus:

| `type` | Extra frontmatter | Required headings |
|---|---|---|
| `Reference` | `applies_to` | `Summary` (and optional `See also`) |
| `HowTo` | `prerequisites`, `outcome` | `Goal`, `Assumptions`, `Steps`, `Result` |
| `Tutorial` | `prerequisites`, `outcome` | `What you will build`, `Before you start`, `Steps` (and optional `What you learned`) |

## 5. Source summary page

One per ingested source (article, spec, ticket, transcript, design doc). Summarized
**once**; other pages cite it. `gw ingest` writes the page and a verbatim copy of the
material at `sources/references/<YYYY-MM>-<slug>.<ext>`; the original is never edited.

Required frontmatter: `type`, `title`, `description`, `source_path`. Declared optional
keys: `source_kind` (`spec | article | ticket | skill | doc | transcript | code-review`),
`origin`, `ingested`, `updated`, `source_date`, `authors`, `entity_uri`, `tokens`,
`tags`, `sources`. Omit an optional key rather than writing `null` or an empty value.

Declared headings, all optional: `TL;DR`, `Key claims`, `Touches`,
`Evidence / rationale`, `Surprises / contradictions`, `Decisions triggered`,
`Where it's cited in this wiki`.

```markdown
---
type: Source
title: "Auth Migration Spec"
description: Move from opaque session tokens to JWTs; driven by compliance, affects 4 packages.
source_kind: spec
source_path: sources/references/2026-04-auth-migration-spec.md
origin: /abs/path/to/auth-migration.md
source_date: 2026-04-01
ingested: 2026-04-20
updated: 2026-04-20
authors: ["@psprowls"]
tags: [auth]
---

# Auth Migration Spec

## TL;DR
Two sentences max. What the source proposes, argues, or reports.

## Key claims
1. Session tokens stored in `sessions` must be retired by 2026-Q3 for compliance.
2. New approach: short-lived JWTs signed by Cognito, validated in `authProvider` middleware.

## Evidence / rationale
- Legal flagged the current storage pattern (`docs/compliance-2026Q1.pdf`).

## Surprises / contradictions
- Spec claims `session.session_id` is unchanged, but see [global-context](/explanations/global-context.md) — field shape differs.

## Touches
- [shared-aws-node-ts](/repositories/<repo>/packages/shared-aws-node-ts.md)

## Decisions triggered
- [0014-jwt-sessions](/adrs/0014-jwt-sessions.md) — stable
```

Drift on an in-repo doc is a diff against its `sources/references/` copy. There is no
`last_sync_commit` / `last_sync_at` stamp.

## 6. ADR page

A dated, citable decision in `adrs/`.

Required frontmatter: `type: Adr`, `title`, `description`, `category: adr`, `adr_id`
(four digits, quoted), `status` (`draft | stable | deprecated`), `decision_date`.
Optional: `deciders`, `supersedes`, `superseded_by`, `updated`, `tags`.

Declared headings: `Context`, `Decision`, `Consequences` (required), `Alternatives
considered`. Extra headings are allowed.

```markdown
---
type: Adr
title: "ADR-0014: JWT Sessions"
description: Adopt short-lived JWTs signed by Cognito in place of server-side session tokens.
category: adr
adr_id: "0014"
status: stable
decision_date: 2026-04-18
deciders: ["human:psprowls"]
supersedes: "0007"
superseded_by: null
tags: [auth, sessions]
updated: 2026-04-20
---

# ADR-0014: JWT Sessions

## Context
Compliance flagged the session-token storage pattern. See [2026-04-auth-migration-spec](/sources/2026-04-auth-migration-spec.md).

## Decision
Adopt short-lived JWTs signed by Cognito. Validate in middleware; refresh on the client.

## Consequences
**Positive:** meets compliance; simpler horizontal scaling.
**Negative:** token revocation gets harder (accepted trade-off).

## Alternatives considered
- Rotate opaque tokens with a short TTL (rejected: still server-side).
```

## 7. Proposal page

A proposed curated page, filed in `proposals/` by `gw ingest` and disposed of with
`/gw:proposals`. Required frontmatter: `type: Proposal`, `title`, `target` (the
bundle-relative path the page would land at), `page_status`
(`proposed | created | rejected`), and `sources` (each `id`, `resource`, and optional
`rationale` and `evidence[]`). Optional: `description`, `verified` (`by`, `at`),
`generated`, `tags`.

Required headings: `Suggested Action`, `Reasoning Summary`, `Evidence From Source`,
`Existing Pages Considered`, `Potential Conflicts`, `Implementation Notes`, `Origins`.
While `page_status: proposed` the body is regenerated from `sources[]` — edit the
frontmatter, not the body. See `proposal-disposition.md` for the review flow.

## 8. Work page

Work pages use PascalCase `type`, independent document `status`, and `work_status` for
the work lifecycle. Their extensionless bundle path is their identity; physical nesting
under `children/` defines ownership.

Declared headings per type — every type requires `## Plan`; the rest are optional:

| `type` | Other headings |
|---|---|
| `Release` | `Goal`, `Release criteria`, `Notes / log` |
| `Epic` | `Goal`, `Notes / log` |
| `Feature` | `Options considered`, `Notes / log` |
| `Bug` | `Steps to reproduce`, `Expected vs actual`, `Notes / log` |
| `TechDebt` | `Current state`, `Notes / log` |
| `TestGap` | `Coverage gap`, `Notes / log` |
| `Spike` | `Question`, `Findings`, `Notes / log` |

```markdown
---
type: Feature
title: Path-native filing
description: File work beneath its owning Epic.
status: stable
work_status: accepted
phase: execute
effort: medium
blast_radius: package
affects:
  - packages/work-tracker-okf
depends_on:
  - path: work/release-cutover/children/epic-migration/children/feature-parser
    blocks: execute
    needs: resolved
opened: 2026-08-23
updated: 2026-08-23
owner: pat
sources:
  - id: design
    resource: /work/release-cutover/children/epic-filing/children/feature-path-native/references/01-design.md
  - id: plan
    resource: /work/release-cutover/children/epic-filing/children/feature-path-native/references/02-plan.md
---

# Path-native filing

## Options considered

File and route every item by permanent canonical path, or keep page-stem identity.

## Plan

| Action | Done when | Rationale |
|---|---|---|
| File beneath the owner | The page and owned directory share one canonical path | Physical placement is ownership |
```

The page lives at `<workspace>/okf/<work-path>.md`. Managed artifacts live at
`<workspace>/okf/<work-path>/references/` and are registered in `sources[]` by
filename-derived id. `Release` is root-only; only `Release`, `Epic`, and `Feature`
may own child lanes. Every lane and local archive carries its own Markdown
`index.md`. There is no hierarchy frontmatter or JSON index sidecar. Live-state keys
(`phase`, `work_status`, `worktree`, `branch`, `released_at`, …) are written by
`gw work advance`; do not hand-edit them.
