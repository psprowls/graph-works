# Page Formats

> **Substrate ownership.** This document describes behavior that the graph-works rebuild is
> re-implementing. Identifiers and paths here are retargeted for the `graph-works` namespace, but
> the behavioral truth is owned by [`2026-08-11-epic-wiki-io-format-layer`](/work/2026-08-11-epic-wiki-io-format-layer.md) and is re-authored there, not here.
> Treat a disagreement between this page and that item as this page being stale.

Every wiki page has the same skeleton: YAML frontmatter + a section structure that matches its category. Below are the canonical formats. Templates live in `assets/page-templates/`. The full enum and per-category frontmatter spec lives in `wiki-schema.md`.

## File map convention (entity pages)

Each admitted entity is a single page under `entities/<prefix>_<name>[__<6hex>].md`. Package, app, and agent-plugin entity pages carry a `## File map - <name>` section composed of one H3 subsection per major folder, each containing a markdown table. Rules:

- The H2 heading carries the package or app name: `## File map - <name>`, followed by a one-line overview paragraph.
- Files at the workspace root live in a synthetic `### <name>/` H3 section directly under the H2 — uniform shape, no special-cased root.
- Each depth-1 subdirectory gets its own H3 section whose heading is the full path from the workspace root with a trailing slash (e.g. `### <name>/<sub>/`).
- Under each H3: a one-sentence paragraph describing the directory, then a markdown table with columns `Path | Kind | Description`. `Path` is relative to that section's root (e.g. `middleware/auth.ts` inside `### <name>/src/`). `Kind` is `file` or `dir`. `Description` starts as `— TODO` and is filled in by the agent later.
- Nested files (depth ≥ 2) flatten into rows inside their depth-1 parent's table. Directories deeper than the cutoff (default `max_depth=4`) are listed as `dir` rows in their depth-1 parent's table instead of getting their own section.
- The scanner pre-populates the tables via `git ls-files` (so `.gitignore` is respected) with `— TODO` Description placeholders. Per-row descriptions are filled in by the agent on a later pass.
- `/graph-works:lint`'s file-map drift check flags rows whose Path is no longer on disk; new files showing up on disk do not fail lint, since `dir`-row summarization is allowed.
- **Legacy heading+bullet pages on disk** (pre-2026-05) are parsed gracefully: directory entries from H3 headers are still extracted, but file-row entries are dropped. The next scan re-emits the block in the new table format when the page still shows the unfilled-template signature.
- **Prod vs testing split:** a package/app entity page's File map shows only **prod source + prod config**. Test files (any component named `tests/`, `__tests__/`, `test/`, or `spec/`), test config (`pytest.ini`, `tox.ini`, `conftest.py`, `jest.config.*`, `vitest.config.*`, `playwright.config.*`, `cypress.config.*`, `mocha.config.*`/`.mocharc.*`, `karma.conf.*`, `ava.config.*`), and test fixtures (typically under `tests/fixtures/`) do not appear here — they belong to that package's own `test_suite` entity page (see below). The prod/test split is implemented by `_is_test_path()` in `packages/wiki-io/src/wiki_io/scan_monorepo.py` — that helper is the single source of truth.
- **Fixtures at non-test paths:** workspaces that put fixtures outside a `tests/`-prefixed path (e.g. a root-level `fixtures/` directory used at runtime too) are classified as prod by the scanner. Document them by hand in the `test_suite` entity page's `## Fixtures` section if they are test-only.

## Test-suite entity pages

A package or app's test surface is its own admitted entity: a `test_suite` page under `entities/` (rendered from the `entity-test-suite.md` template, `kind: test_suite`). There is no companion `testing.md` sub-page — the test suite is a sibling entity page, linked back to the package it tests via the `tested_packages` edge. The scanner emits one `test_suite` entity per discovered suite and populates its File-map table from the graph's test-file node paths.

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

All entity pages live under `entities/` named `<prefix>_<name>[__<6hex>].md`. The frontmatter is scanner-owned (replaced every scan); human-preserved keys (`status`, `last_reviewed`, `owner`, `notes`) are never overwritten. `summary` is fill-when-empty.

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

App entity pages follow the same shape as package pages with the addition of `app_kind` and `app_signals` in the scanner-owned frontmatter. Filename prefix: `app_`.

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

All other admitted entity kinds (`repository`, `agent_plugin`, `dependency`, `test_suite`) also live in `entities/` with their respective filename prefixes (`repo_`, `agent-plugin_`, `dep_`, `unit_tests_` / `int_tests_` / `tests_`). Each carries the universal scanner-owned keys (`uri`, `kind`, `graph_name`, `last_scan_at`) plus kind-specific keys (see `wiki-schema.md` Entity pages). The authoritative templates are the packaged `entity-*.md` files in `packages/wiki-io/src/wiki_io/assets/page-templates/`.

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
- [[entities/pkg_common-aws-node-ts]] — injects via middleware
- [[entities/pkg_common-context-node-ts]] — defines the interface
- All `*-data-node-ts` packages — scope queries by `session.user_id`

## Related patterns
- [[concepts/middleware-pipeline]]
- [[concepts/repository-pattern]]

## Sources
- [[sources/2025-12-context-refactor-spec]]

## Open questions / gotchas
- Default `session.user_id` is `ObjectId(0)` — tests must call `updateSession()` before DB operations.
- ⚠️ Contradiction: `[[entities/pkg_shared-aws-node-ts]]` assumes `session.session_id` always populated, but `[[sources/auth-migration-spec]]` says pre-login requests have null.
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
- [[sources/2026-05-tanstack-suspense-example]] — minimal Expo demo.
- [[sources/2026-04-react-19-suspense-blog]] — conceptual write-up.

## Where this could apply in the codebase
- [[entities/pkg_web-next-ts]] — current isLoading-flag pattern in dashboard queries.
- [[entities/pkg_app-expo-ts]] — same.

## Related patterns
- [[concepts/error-boundary-pattern]]
- [[concepts/global-context]]

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
source_path: raw/_archive/specs/auth-migration.md
source_type: spec                # spec | article | pr | ticket | transcript | example | doc | note
source_date: 2026-04-01
last_sync_commit:                # set only for in-repo docs (source_type: doc) — full SHA at last ingest, used by /graph-works:lint to detect changes
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
- Spec claims `session.session_id` unchanged, but see `[[concepts/global-context]]` — field shape differs.

## Touches
- [[entities/pkg_shared-aws-node-ts]]
- [[entities/pkg_shared-native-ts]]
- [[entities/pkg_shared-domain-ts]]
- [[concepts/global-context]]

## Decisions triggered
- [[adrs/0014-jwt-sessions]] — accepted

## Where it's cited in this wiki
- [[concepts/global-context]]
- [[adrs/0014-jwt-sessions]]
```

`last_sync_commit` (40-char SHA) and `last_sync_at` (YYYY-MM-DD) record the repo commit this page was last verified against. `/graph-works:ingest` writes both when re-ingesting an in-repo doc (`source_type: doc`) with a clean working tree on `main`. `/graph-works:lint` compares HEAD against `last_sync_commit` to flag source files that have changed since the last ingest.

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

1. **Client** — React Native (`[[entities/pkg_app-expo-ts]]`) or Next.js (`[[entities/pkg_web-next-ts]]`) uses `[[entities/pkg_shared-domain-ts]]` client
2. **API Gateway / Lambda** — routes to `*-aws-node-ts` handlers; middleware pipeline establishes `[[concepts/global-context]]`
3. **Data layer** — handlers delegate to `*-data-node-ts` repositories scoped by `session.user_id`
4. **MongoDB** — per-domain database via `IDatabaseManager.getDatabase(name)`

## Diagrams
- See `raw/assets/request-flow.svg` (from `[[sources/2025-12-architecture-overview]]`)

## Key packages
- [[entities/pkg_shared-domain-ts]] — client
- [[entities/pkg_shared-aws-node-ts]] — auth
- [[entities/pkg_common-aws-node-ts]] — middleware base
- [[entities/pkg_common-context-node-ts]] — context
- [[entities/pkg_activities-data-node-ts]] — repo base classes

## Key concepts
- [[concepts/global-context]]
- [[concepts/middleware-pipeline]]
- [[concepts/repository-pattern]]

## Decisions shaping this
- [[adrs/0005-lambda-per-endpoint]]
- [[adrs/0008-middleware-pipeline]]
- [[adrs/0014-jwt-sessions]]

## How this synthesis has changed
- **2026-04-20** — added JWT flow from `[[sources/2026-04-auth-migration-spec]]`
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
**Supersedes:** [[adrs/0007-opaque-session-tokens]]

## Context
Compliance flagged the current session-token storage pattern. See [[sources/2026-04-auth-migration-spec]] for full context.

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
- Auth0 (rejected: see [[concepts/cognito-vs-auth0]])

## Impact
- [[entities/pkg_shared-aws-node-ts]] — middleware change
- [[entities/pkg_shared-native-ts]] — refresh logic
- [[entities/pkg_shared-domain-ts]] — header injection

## Follow-ups
- Roll out to staging 2026-05
- Deprecate opaque tokens 2026-Q3
```

## 7. Dependency page

`/graph-works:scan` writes one graph-derived dependency page per dep the monorepo touches into `entities/dep_<name>.md`, using the scanner-owned `entity-dependency.md` template shape (`uri`, `kind: dependency`, `graph_name`, `last_scan_at`, `ecosystem`, `used_by`, `versions_in_use`). The `kind: package | service` example below is a **legacy curated-page shape** that no lint group checks — the `dependency_layer` group it was gated behind does not exist in graph-works-core. It is not what the scanner writes, and its fate is open in `2026-08-20-tech-debt-revisit-dependency-layer-lint`. See `wiki-schema.md` for the `kind: service` variant.

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
| 19.0.0 | [[entities/pkg_web-next-ts]], [[entities/pkg_shared-ui-react-ts]] | Migrated 2026-Q1 |
| 18.3.1 | [[entities/pkg_app-expo-ts]], [[entities/pkg_shared-ui-native-ts]] | Pinned by RN 0.76 |

## Used by
- [[entities/pkg_web-next-ts]]
- [[entities/pkg_app-expo-ts]]
- [[entities/pkg_shared-ui-react-ts]]
- [[entities/pkg_shared-ui-native-ts]]

## Key patterns in this repo
- Functional components only; no class components.
- Suspense + Server Components in `[[entities/pkg_web-next-ts]]` (Next 15 App Router).
- `use client` directive boundaries — see [[concepts/nextjs-client-boundary]].

## Gotchas / workarounds
- ⚠️ React 19 `useEffect` runs twice in dev (Strict Mode) — see [[work/2026-02-08-double-mount-in-dev]].
- Expo pins React 18; can't bump until RN catches up.

## Upgrade history
- **2026-02** — bumped web surfaces to 19.0. See [[sources/2026-02-react-19-migration-pr]].
- **2025-09** — initial adoption of concurrent features.

## Decisions
- [[adrs/0011-react-19-on-web-only]]

## Related
- [[entities/dep_react-native]]
- [[concepts/server-state-vs-client-state]]
- [[work/rn-0-77-upgrade]]
```

## 8. Work page

Unified namespace for everything "to do, doing, or done" — bugs, tech debt, test gaps, security/perf items, features, initiatives, spikes. `kind:` discriminates; a single 7-state lifecycle covers all. The committed plan lives in a `## Plan` markdown table. Slugs are `<YYYY-MM-DD>-<kind>-<w1>-<w2>-<w3>-<w4>.md`, where the 4 words are filer-supplied via `gw work file --slug-words` (falling back to the first 4 words of the title when omitted); children filed under a parent epic get `epic-<kind>` instead of `<kind>`. No migration — pre-existing pages keep their old `<YYYY-MM-DD>-<short-slug>.md` filenames.

`graph-works` owns the schema, template, folder, lifecycle lint, and `<workspace>/wiki/work-index.json` sidecar.

Bug-shaped example (`kind: bug`):

```markdown
---
title: MONGO_DATABASE hardcoded to dev-pat-location in CDK
category: work
kind: bug
summary: cdk/location-service.ts:50 sets MONGO_DATABASE to a literal "dev-pat-location" — any prod deploy lands on the dev database.
status: open
severity: medium
effort: s
blast_radius: package
affects:
  - packages/location-aws-node-ts
opened: 2026-04-21
updated: 2026-05-03
tags: [location, infrastructure, configuration, mongodb]
---

# MONGO_DATABASE hardcoded to dev-pat-location in CDK

## Summary
The CDK deploy script for `location-aws-node-ts` sets `MONGO_DATABASE` to the literal string `dev-pat-location`. Any prod deploy lands on the dev database.

## Options considered
- Stage-prefix the database name from the existing `STAGE` variable in the same block.
- Move the DB name into env-vars-per-stage in `cdk.json` (overkill; one variable).

## Plan

| Action | Done when | Rationale |
|---|---|---|
| Stage-prefix the database name in CDK | `location-service.ts` has no literal `dev-pat-location` | Matches `STAGE` already in the same block |
| Drop the legacy adapter fallback | `legacyContextAdapter.ts` no longer falls back | Dead code once env var always set |

## Notes / log
- **2026-04-21** — filed; reproduced on dev deploy.
```

Feature-shaped example (`kind: feature`):

```markdown
---
title: Cloud LLM provider via AWS Bedrock
category: work
kind: feature
summary: Add Bedrock as a third provider behind getChatModel() so the agent can run against open-weight cloud-hosted models.
status: accepted
effort: l
blast_radius: system
target: 2026-Q2
owner: pat
affects:
  - src/llm/provider.ts
  - src/llm/bedrock.ts
  - src/config.ts
opened: 2026-05-02
updated: 2026-05-03
tags: [roadmap, llm, cloud, aws, bedrock]
---

# Cloud LLM provider via AWS Bedrock

## Summary
Add Bedrock to the provider seam so the agent can run against open-weight cloud-hosted models alongside the existing local and OpenAI providers.

## Plan

| Action | Done when | Rationale |
|---|---|---|
| Add `bedrock.ts` adapter to the provider seam | `getChatModel("bedrock-…")` returns a working client | Plug-in shape mirrors existing providers |
| Wire stage config | `BEDROCK_REGION` and `BEDROCK_MODEL` honored | Matches existing `OPENAI_*` shape |
| End-to-end smoke test against a small Bedrock model | One round-trip query returns a non-error completion | Catches IAM/credentials misconfig early |

## Notes / log
- **2026-05-03** — accepted after spike on IAM permissions.
```

Severity is allowed for `bug | security | perf | tech-debt | test-gap` and disallowed for `feature | epic | spike`. State-conditional fields (`resolved_in`, `mitigation`, `superseded_by`, `rationale`) are populated only in their corresponding state. See `wiki-schema.md` for the full taxonomy and lifecycle.
