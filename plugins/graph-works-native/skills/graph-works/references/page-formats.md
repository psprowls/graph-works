# Page Formats

> **Substrate ownership.** `epic-wiki-io-format-layer` (the item this disclaimer used to
> point at) is `resolved`/`phase: done` — pointing readers there now sends them to closed
> history, not live truth. If this page and the shipped workspace tree
> (`<repo>/.works/okf/...`) disagree, treat this page as stale and file (or find) the
> TechDebt item that tracks the drift; see
> [tech-debt-plugin-docs-layout-claims](/work/tech-debt-plugin-docs-layout-claims.md) for the layout-claim sweep that
> last corrected this page.

Every wiki page has the same skeleton: YAML frontmatter + a section structure that matches its category. Below are the canonical formats. Templates live in `assets/page-templates/`. The full enum and per-category frontmatter spec lives in `wiki-schema.md`.

## File map convention (entity pages)

Each admitted entity is a single page nested under `repositories/<repo>/`: packages at
`repositories/<repo>/packages/<name>.md`, apps at `repositories/<repo>/apps/<name>.md`,
agent plugins at `repositories/<repo>/agent-plugins/<name>.md`, test suites at
`repositories/<repo>/test-suites/<name>.md`, and the repository's own page at
`repositories/<repo>/repository.md`. Dependencies are a sibling root, not nested under
`repositories/`: `dependencies/<ecosystem>/<name>.md`. There is no filename prefix
scheme and no `entities/` folder. Package, app, and agent-plugin entity pages carry a
`## File map - <name>` section composed of one H3 subsection per major folder, each
containing a markdown table. Rules:

- The H2 heading carries the package or app name: `## File map - <name>`, followed by a one-line overview paragraph.
- Files at the workspace root live in a synthetic `### <name>/` H3 section directly under the H2 — uniform shape, no special-cased root.
- Each depth-1 subdirectory gets its own H3 section whose heading is the full path from the workspace root with a trailing slash (e.g. `### <name>/<sub>/`).
- Under each H3: a one-sentence paragraph describing the directory, then a markdown table with columns `Path | Kind | Description`. `Path` is relative to that section's root (e.g. `middleware/auth.ts` inside `### <name>/src/`). `Kind` is `file` or `dir`. `Description` starts as `— TODO` and is filled in by the agent later.
- Nested files (depth ≥ 2) flatten into rows inside their depth-1 parent's table. Directories deeper than the cutoff (default `max_depth=4`) are listed as `dir` rows in their depth-1 parent's table instead of getting their own section.
- The scanner pre-populates the tables via `git ls-files` (so `.gitignore` is respected) with `— TODO` Description placeholders. Per-row descriptions are filled in by the agent on a later pass.
- `/gw:lint`'s file-map drift check flags rows whose Path is no longer on disk; new files showing up on disk do not fail lint, since `dir`-row summarization is allowed.
- **Legacy heading+bullet pages on disk** (pre-2026-05) are parsed gracefully: directory entries from H3 headers are still extracted, but file-row entries are dropped. The next scan re-emits the block in the new table format when the page still shows the unfilled-template signature.
- **Prod vs testing split:** a package/app entity page's File map shows only **prod source + prod config**. Test files (any component named `tests/`, `__tests__/`, `test/`, or `spec/`), test config (`pytest.ini`, `tox.ini`, `conftest.py`, `jest.config.*`, `vitest.config.*`, `playwright.config.*`, `cypress.config.*`, `mocha.config.*`/`.mocharc.*`, `karma.conf.*`, `ava.config.*`), and test fixtures (typically under `tests/fixtures/`) do not appear here — they belong to that package's own `test_suite` entity page (see below). The prod/test split is implemented by `_is_test_path()` in `packages/wiki-io/src/wiki_io/scan_monorepo.py` — that helper is the single source of truth.
- **Fixtures at non-test paths:** workspaces that put fixtures outside a `tests/`-prefixed path (e.g. a root-level `fixtures/` directory used at runtime too) are classified as prod by the scanner. Document them by hand in the `test_suite` entity page's `## Fixtures` section if they are test-only.

## Test-suite entity pages

A package or app's test surface is its own admitted entity: a `test_suite` page under `repositories/<repo>/test-suites/<name>.md` (rendered from the `entity-test-suite.md` template, `kind: test_suite`). There is no companion `testing.md` sub-page — the test suite is a sibling entity page, linked back to the package it tests via the `tested_packages` edge. The scanner emits one `test_suite` entity per discovered suite and populates its File-map table from the graph's test-file node paths.

### Frontmatter (scanner-owned)
- `title`, `uri`, `kind: test_suite`, `graph_name`, `last_scan_at`
- `tested_packages: []` — the package(s) this suite covers
- `suite_kind`, `file_count` — edge-derived

### Sections
- `## Purpose` — one paragraph: what this suite covers, which frameworks, how to invoke
- `## How to run` — bullet list of commands (primary, secondary like smoke/e2e)
- `## File map - <name>` — same table format as the package/app entity page, but the rows are scoped to test files + test config + fixtures (see split rule above)
- `## Test conventions` — naming, structure, mocks, fixtures
- `## Fixtures` — bullet list of fixture paths and what each represents
- `## Coverage` — target threshold, measurement method, report location
- `## Open questions`

### Example worked output (test_suite entity page for common-aws-node-ts)

```markdown
# common-aws-node-ts — tests

## Purpose
Integration tests for the handler factories, exercised against a local DynamoDB and SNS stack via testcontainers.

## How to run
- `pnpm -F common-aws-node-ts test` — primary jest run
- `pnpm -F common-aws-node-ts test:e2e` — e2e suite (requires Docker)

## File map - common-aws-node-ts

### common-aws-node-ts/
Root: test config.

| Path | Kind | Description |
|---|---|---|
| `jest.config.ts` | file | jest config (ts-jest preset, coverage thresholds) |

### common-aws-node-ts/tests/
Integration tests + fixtures.

| Path | Kind | Description |
|---|---|---|
| `handlers.test.ts` | file | integration tests for the handler factories |
| `fixtures/` | dir | golden request/response bodies + signed JWTs |

## Test conventions
- Each handler has a `<handler>.test.ts` next to the integration suite.
- Mocks live under `tests/mocks/` and follow the `mock<Service>.ts` naming pattern.

## Fixtures
- `tests/fixtures/` — golden request/response bodies; refresh via `pnpm refresh-fixtures` when contracts change.

## Coverage
- Target: 80% statements, 70% branches. Measured by `jest --coverage`. Report at `coverage/index.html`.

## Open questions
- Whether to roll the e2e suite into the default `test` script once CI Docker support lands.
```

## 1. Entity page (package)

Package entity pages live at `repositories/<repo>/packages/<name>.md`. The frontmatter is scanner-owned (replaced every scan); human-preserved keys (`status`, `last_reviewed`, `owner`, `notes`) are never overwritten. `summary` is fill-when-empty.

```markdown
---
uri: pkg:org/repo/<name>
kind: package
graph_name: <graph-name>
last_scan_at: <YYYY-MM-DD>
depends_on: []
test_suites: []
entry_points: []
language: ""
version: ""
---

# <name>

## Narrative
_(scanner will populate on next scan)_

## File map - <name>
| Path | Kind | Description |
|---|---|---|
| `<file>` | file | — TODO |
```

Entity-page content splits into two classes by how it's produced. `## Referenced in wiki` and the `## File map - <name>` row set (the `Path`/`Kind` columns) are **deterministic**: pure graph projections, always regenerated fresh on every scan at zero model cost (`## File map - <name>` is pre-populated with `— TODO` Description placeholders even on structural-only scans; `## Referenced in wiki` is always regenerated from forward-links). `agent_plugin` pages additionally carry template-authoritative deterministic data tables (`## Commands`, `## Agents`, `## Skills`, `## Scripts`, `## Hooks`, `## MCP servers`), always regenerated from the graph, never sourced from the on-disk page.

`## Narrative`, the File map's Description column, and any other hand-added H2 are **prose**: model-maintained, filled in on first scan, then updated only when the diff-driven refresh pass fires (the commit range since `last_updated_commit` touches the entity's files). Prose is never mechanically protected — the refresh prompt treats current page text as ground truth to preserve unless the code diff contradicts it. See the [File map convention](#file-map-convention-entity-pages) section for the full table rules.

## 2. Entity page (app)

App entity pages follow the same shape as package pages with the addition of `app_kind` and `app_signals` in the scanner-owned frontmatter, at `repositories/<repo>/apps/<name>.md`.

```markdown
---
uri: <app-uri>
kind: app
graph_name: <graph-name>
last_scan_at: <YYYY-MM-DD>
depends_on: []
test_suites: []
entry_points: []
language: ""
version: ""
---

# <name>

## Narrative
_(scanner will populate on next scan)_

## File map - <name>
| Path | Kind | Description |
|---|---|---|
| `<file>` | file | — TODO |
```

## Other entity kinds

All other admitted entity kinds live nested under `repositories/<repo>/`, one folder per
kind: `repository` at `repositories/<repo>/repository.md` itself, `agent_plugin` at
`repositories/<repo>/agent-plugins/<name>.md`, `test_suite` at
`repositories/<repo>/test-suites/<name>.md`. `dependency` is the one exception — a
sibling root, not nested under `repositories/`: `dependencies/<ecosystem>/<name>.md`.
Each carries the universal scanner-owned keys (`uri`, `kind`, `graph_name`,
`last_scan_at`) plus kind-specific keys (see `wiki-schema.md` Entity pages).

## 3. Concept page

A cross-cutting technical pattern, convention, or idea used across packages.

```markdown
---
title: GlobalContext
category: concept
summary: Request-scoped context via AsyncLocalStorage providing config, database, logger, session
tags: [context, middleware, patterns]
sources: 2
updated: 2026-04-20
---

# GlobalContext

## Definition
Precise, one-paragraph definition. The canonical form used across the codebase.

## Motivation
Why this pattern exists. What problem it solves vs. passing context explicitly.

## Shape
```typescript
interface IGlobalContext {
  config: IConfigurationManager;
  database: IDatabaseManager;
  logger: ILogger;
  session: SessionInfo;
}
```
From `packages/common-context-node-ts/src/globalContext.ts`.

## Used in
- [common-aws-node-ts](/repositories/<repo>/packages/common-aws-node-ts.md) — injects via middleware
- [common-context-node-ts](/repositories/<repo>/packages/common-context-node-ts.md) — defines the interface
- All `*-data-node-ts` packages — scope queries by `session.user_id`

## Related patterns
- [middleware-pipeline](/concepts/middleware-pipeline.md)
- [repository-pattern](/concepts/repository-pattern.md)

## Sources
- [2025-12-context-refactor-spec](/sources/2025-12-context-refactor-spec.md)

## Open questions / gotchas
- Default `session.user_id` is `ObjectId(0)` — tests must call `updateSession()` before DB operations.
- ⚠️ Contradiction: `[shared-aws-node-ts](/repositories/<repo>/packages/shared-aws-node-ts.md)` assumes `session.session_id` always populated, but `[auth-migration-spec](/sources/auth-migration-spec.md)` says pre-login requests have null.
```

## 3a. Concept page — pattern variant

A pattern is a prescriptive concept ("when to apply this, what to watch out for") rather than a descriptive one ("what this is in our codebase"). Naming convention only — no new `category` and no new `kind:` discriminator on concepts. Mirrors how comparison pages work today (`<a>-vs-<b>.md`).

Filename: `concepts/<topic>-pattern.md`. The `-pattern` suffix is the discriminator.

```markdown
---
title: "Suspense-driven query loading"
category: concept
summary: Pattern for loading data with React Suspense boundaries instead of isLoading flags
tags: [pattern, react, suspense, data-fetching]
sources: 1
updated: 2026-05-04
---

# Suspense-driven query loading

## Definition
One-paragraph definition of the pattern, in its general form (not tied to this codebase).

## When to apply (Forces)
- Bulleted list of conditions/forces that make this pattern a good fit.
- Each bullet is a constraint or pressure the pattern resolves.

## Solution
The shape of the pattern. Code sketch is fine; keep it minimal and language-agnostic where possible.

## Tradeoffs
**Positive:** …
**Negative:** …

## Example sources
- [2026-05-tanstack-suspense-example](/sources/2026-05-tanstack-suspense-example.md) — minimal Expo demo.
- [2026-04-react-19-suspense-blog](/sources/2026-04-react-19-suspense-blog.md) — conceptual write-up.

## Where this could apply in the codebase
- [web-next-ts](/repositories/<repo>/packages/web-next-ts.md) — current isLoading-flag pattern in dashboard queries.
- [app-expo-ts](/repositories/<repo>/packages/app-expo-ts.md) — same.

## Related patterns
- [error-boundary-pattern](/concepts/error-boundary-pattern.md)
- [global-context](/concepts/global-context.md)

## Open questions
- …
```

Notes:
- The `pattern` tag is recommended so the index can group these pages; not enforced.
- Body sections are recommended, not lint-enforced. Lint nudges (info-level) for naming/tag mismatch only.

## 4. Source summary page

One per ingested source (article, spec, PR, transcript, ticket). Summarized **once**; other pages cite it.

```markdown
---
title: "Auth Migration Spec"
category: source
summary: Move from opaque session tokens to JWTs; driven by compliance, affects 4 packages
source_path: sources/references/auth-migration.md
source_type: spec                # spec | article | pr | ticket | transcript | example | doc | note
source_date: 2026-04-01
last_sync_commit:                # set only for in-repo docs (source_type: doc) — full SHA at last ingest, used by /gw:lint to detect changes
last_sync_at:                    # YYYY-MM-DD when sync state was recorded
authors: [@psprowls]
ingested: 2026-04-20
updated: 2026-04-20
---

# Auth Migration Spec

## TL;DR
Two sentences max. What the source proposes / argues / reports.

## Key claims
1. Session tokens stored in `sessions` collection must be retired by 2026-Q3 for compliance.
2. New approach: short-lived JWTs signed by Cognito, validated in `authProvider` middleware.
3. `session.user_id` contract preserved; `session.session_id` becomes JWT `sub`.

## Proposed changes
- `packages/shared-aws-node-ts` — new `jwtAuthProvider` middleware
- `packages/shared-native-ts` — refresh token handling
- `packages/shared-domain-ts` — header injection from Cognito SDK

## Evidence / rationale
- Legal flagged current storage pattern (file cited: `docs/compliance-2026Q1.pdf`)
- Prototype in `packages/shared-aws-node-ts/src/auth/__prototype__.ts`

## Surprises / contradictions
- Spec claims `session.session_id` unchanged, but see `[global-context](/concepts/global-context.md)` — field shape differs.

## Touches
- [shared-aws-node-ts](/repositories/<repo>/packages/shared-aws-node-ts.md)
- [shared-native-ts](/repositories/<repo>/packages/shared-native-ts.md)
- [shared-domain-ts](/repositories/<repo>/packages/shared-domain-ts.md)
- [global-context](/concepts/global-context.md)

## Decisions triggered
- [0014-jwt-sessions](/adrs/0014-jwt-sessions.md) — accepted

## Where it's cited in this wiki
- [global-context](/concepts/global-context.md)
- [0014-jwt-sessions](/adrs/0014-jwt-sessions.md)
```

`last_sync_commit` (40-char SHA) and `last_sync_at` (YYYY-MM-DD) record the repo commit this page was last verified against. `/gw:ingest` writes both when re-ingesting an in-repo doc (`source_type: doc`) with a clean working tree on `main`. `/gw:lint` compares HEAD against `last_sync_commit` to flag source files that have changed since the last ingest.

## 5. Architecture concept page (`kind: architecture`)

High-level synthesis that draws on many packages and sources. Lives in `concepts/` with `kind: architecture`. Use the `concept-architecture.md` template.

```markdown
---
title: Request Flow
category: concept
kind: architecture
summary: End-to-end path of an authenticated API request from client through Lambda to MongoDB
packages: [shared-domain-ts, shared-aws-node-ts, common-context-node-ts, *-data-node-ts]
tags: [architecture, request-flow]
sources: 4
updated: 2026-04-20
---

# Request Flow

## Thesis
Two-three sentences capturing the current understanding of how requests flow through the system. Revised as new sources / ADRs arrive.

## Layers

1. **Client** — React Native (`[app-expo-ts](/repositories/<repo>/packages/app-expo-ts.md)`) or Next.js (`[web-next-ts](/repositories/<repo>/packages/web-next-ts.md)`) uses `[shared-domain-ts](/repositories/<repo>/packages/shared-domain-ts.md)` client
2. **API Gateway / Lambda** — routes to `*-aws-node-ts` handlers; middleware pipeline establishes `[global-context](/concepts/global-context.md)`
3. **Data layer** — handlers delegate to `*-data-node-ts` repositories scoped by `session.user_id`
4. **MongoDB** — per-domain database via `IDatabaseManager.getDatabase(name)`

## Diagrams
- See the diagram attached to `[2025-12-architecture-overview](/sources/2025-12-architecture-overview.md)` (the ingest flow copies attached material to `sources/references/`)

## Key packages
- [shared-domain-ts](/repositories/<repo>/packages/shared-domain-ts.md) — client
- [shared-aws-node-ts](/repositories/<repo>/packages/shared-aws-node-ts.md) — auth
- [common-aws-node-ts](/repositories/<repo>/packages/common-aws-node-ts.md) — middleware base
- [common-context-node-ts](/repositories/<repo>/packages/common-context-node-ts.md) — context
- [activities-data-node-ts](/repositories/<repo>/packages/activities-data-node-ts.md) — repo base classes

## Key concepts
- [global-context](/concepts/global-context.md)
- [middleware-pipeline](/concepts/middleware-pipeline.md)
- [repository-pattern](/concepts/repository-pattern.md)

## Decisions shaping this
- [0005-lambda-per-endpoint](/adrs/0005-lambda-per-endpoint.md)
- [0008-middleware-pipeline](/adrs/0008-middleware-pipeline.md)
- [0014-jwt-sessions](/adrs/0014-jwt-sessions.md)

## How this synthesis has changed
- **2026-04-20** — added JWT flow from `[2026-04-auth-migration-spec](/sources/2026-04-auth-migration-spec.md)`
- **2025-12-15** — initial write-up
```

## 6. ADR page

A dated, citable decision. Classic MADR-lite format.

```markdown
---
title: "ADR-0014: JWT Sessions"
category: adr
adr_id: 0014
status: accepted
decision_date: 2026-04-18
deciders: [@psprowls]
supersedes: 0007
superseded_by: null
tags: [auth, sessions]
updated: 2026-04-20
---

# ADR-0014: JWT Sessions

**Status:** accepted (2026-04-18)
**Supersedes:** [0007-opaque-session-tokens](/adrs/0007-opaque-session-tokens.md)

## Context
Compliance flagged the current session-token storage pattern. See [2026-04-auth-migration-spec](/sources/2026-04-auth-migration-spec.md) for full context.

## Decision
Adopt short-lived JWTs signed by Cognito. Validation in middleware; refresh on the client.

## Consequences

**Positive:**
- Meets compliance requirements (no server-side session storage)
- Simpler horizontal scaling

**Negative:**
- Token revocation becomes harder (accepted trade-off)
- Client-side refresh logic must be correct

## Alternatives considered
- Rotate opaque tokens with short TTL (rejected: still server-side)
- Auth0 (rejected: see [cognito-vs-auth0](/concepts/cognito-vs-auth0.md))

## Impact
- [shared-aws-node-ts](/repositories/<repo>/packages/shared-aws-node-ts.md) — middleware change
- [shared-native-ts](/repositories/<repo>/packages/shared-native-ts.md) — refresh logic
- [shared-domain-ts](/repositories/<repo>/packages/shared-domain-ts.md) — header injection

## Follow-ups
- Roll out to staging 2026-05
- Deprecate opaque tokens 2026-Q3
```

## 7. Dependency page

`/gw:scan` writes one graph-derived dependency page per dep the monorepo touches into `dependencies/<ecosystem>/<name>.md`, using the scanner-owned `entity-dependency.md` template shape (`uri`, `kind: dependency`, `graph_name`, `last_scan_at`, `ecosystem`, `used_by`, `versions_in_use`). The `kind: package | service` example below is a **legacy curated-page shape** that no lint group checks — the `dependency_layer` group it was gated behind does not exist in graph-works-core. It is not what the scanner writes, and its fate is open in `2026-08-20-tech-debt-revisit-dependency-layer-lint`. See `wiki-schema.md` for the `kind: service` variant.

```markdown
---
title: React
category: dependency
kind: package
package_name: react
ecosystem: npm
versions_in_use: ["19.0.0", "18.3.1"]
used_by: [web-next-ts, app-expo-ts, shared-ui-react-ts, shared-ui-native-ts]
upstream_url: https://react.dev
load_bearing: true
quirks: []
tags: [frontend, ui]
updated: 2026-04-20
---

# React

## What it is
One paragraph: what this library does, why we use it, which surfaces.

## Versions in use
| Version | Used in | Notes |
|---|---|---|
| 19.0.0 | [web-next-ts](/repositories/<repo>/packages/web-next-ts.md), [shared-ui-react-ts](/repositories/<repo>/packages/shared-ui-react-ts.md) | Migrated 2026-Q1 |
| 18.3.1 | [app-expo-ts](/repositories/<repo>/packages/app-expo-ts.md), [shared-ui-native-ts](/repositories/<repo>/packages/shared-ui-native-ts.md) | Pinned by RN 0.76 |

## Used by
- [web-next-ts](/repositories/<repo>/packages/web-next-ts.md)
- [app-expo-ts](/repositories/<repo>/packages/app-expo-ts.md)
- [shared-ui-react-ts](/repositories/<repo>/packages/shared-ui-react-ts.md)
- [shared-ui-native-ts](/repositories/<repo>/packages/shared-ui-native-ts.md)

## Key patterns in this repo
- Functional components only; no class components.
- Suspense + Server Components in `[web-next-ts](/repositories/<repo>/packages/web-next-ts.md)` (Next 15 App Router).
- `use client` directive boundaries — see [nextjs-client-boundary](/concepts/nextjs-client-boundary.md).

## Gotchas / workarounds
- ⚠️ React 19 `useEffect` runs twice in dev (Strict Mode) — see [bug-double-mount-in-dev](/work/release-web-platform/children/epic-react-19/children/bug-double-mount-in-dev.md).
- Expo pins React 18; can't bump until RN catches up.

## Upgrade history
- **2026-02** — bumped web surfaces to 19.0. See [2026-02-react-19-migration-pr](/sources/2026-02-react-19-migration-pr.md).
- **2025-09** — initial adoption of concurrent features.

## Decisions
- [0011-react-19-on-web-only](/adrs/0011-react-19-on-web-only.md)

## Related
- [react-native](/dependencies/npm/react-native.md)
- [server-state-vs-client-state](/concepts/server-state-vs-client-state.md)
- [rn-0-77-upgrade](/work/rn-0-77-upgrade.md)
```

## 8. Work page

Work pages use PascalCase `type`, independent document `status`, and
`work_status` for the work lifecycle. Their extensionless bundle path is their
identity. Physical nesting under `children/` defines ownership.

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

## Summary

File and route every item by permanent canonical path.

## Plan

| Action | Done when | Rationale |
|---|---|---|
| File beneath the owner | The page and owned directory share one canonical path | Physical placement is ownership |
```

The page lives at `<workspace>/okf/<work-path>.md`. Managed artifacts live at
`<workspace>/okf/<work-path>/references/`. `Release` is root-only; only
`Release`, `Epic`, and `Feature` may own child lanes. Every lane and local
archive carries its own Markdown `index.md`. There is no hierarchy frontmatter
or JSON index sidecar.
