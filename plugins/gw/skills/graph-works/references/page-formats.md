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

Scanner-owned. Nested under `code-graph/<repo>/`, one folder per kind; see
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
- [packages/code-wiki-okf/pyproject.toml](/code-graph/<repo>/file-system/packages/code-wiki-okf/pyproject.toml.md)
- ... one link per tracked file, generated
```

Each `## Files` entry links to that file's own `File` page under
`code-graph/<repo>/file-system/`. A package's `TestSuite` page is a sibling entity, linked
back through `tested_packages`.

## 2. Dependency page

`/gw:scan` writes one page per dependency into `code-graph/<repo>/entities/dependencies/<ecosystem>/<name>.md`.
It is one Dependency per (repository, dependency): `used_by` and `versions_in_use` describe that repository only; the implementing Package page aggregates across repositories.

**Required frontmatter:** `type: Dependency`, `title`, `resource`, `ecosystem`.
**Optional:** `description`, `tags`, `implemented_by`, `used_by`, `versions_in_use`.
**Provenance, scanner-owned:** `generated` (`by`, `at`), `last_updated_commit`,
`prose_refresh_attempts`.
**Sections:** `## Why we depend on this` (required), then `## Gotchas / workarounds`.

```markdown
---
type: Dependency
title: React
resource: dependency:acme/web/npm/react
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
`docs/explanations/`. Shared frontmatter with the other Diátaxis types: `type`, `title`,
`description` required; `status`, `updated`, `tags`, `sources` optional.
`about:` (scanner resource URIs, required by the `x-okf-about` mandate) and
`claims:` (entries `{id: C<n>, claim, about?, constrains?, phase?}`, at least
one on a live page) — see `wiki-schema.md` for the full contract.

Declared headings: `## Context` (required), `## Trade-offs`, `## See also`. Extra
headings are allowed (`additional_sections: true`).

```markdown
---
type: Explanation
title: Global context
description: Request-scoped context via AsyncLocalStorage providing config, database, logger, and session.
tags: [context, middleware]
updated: 2026-04-20
about: [pkg:acme/web/common-context-node-ts]
claims:
  - id: C1
    claim: Request context is carried by AsyncLocalStorage, never passed as an argument.
    about: [pkg:acme/web/common-context-node-ts]
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
- [common-aws-node-ts](/code-graph/<repo>/entities/packages/common-aws-node-ts.md) — injects via middleware
- [middleware-pipeline](/explanations/middleware-pipeline.md)
- [2025-12-context-refactor-spec](/sources/2025-12-context-refactor-spec.md)
```

**Variants** are a naming and tagging convention, not a schema field:

- *Pattern* — prescriptive ("when to apply this"). Tag `pattern`; use extra sections
  such as `## When to apply`, `## Solution`, `## Tradeoffs`.
- *Architecture synthesis* — layers, flows, and components spanning many packages. Tag
  `architecture`; lead with a `## Thesis` section and link back to every entity and
  ADR it draws on, with a dated `## How this synthesis has changed` log.
- *Comparison* — `docs/explanations/<a>-vs-<b>.md`, or `<topic>-options.md` for n-way.

Tags must exist in `.gw/tags.yaml`; curated lanes enforce it. Do not add a tag to the
vocabulary to make a page pass — pick an existing concept tag.

### Curated-page claims

Every ADR, Explanation, Reference and HowTo carries `about:`: a list of scanner URIs.
The rule is always the same — copy each URI **verbatim** from the `resource:` frontmatter
of the code-graph page it names; never retype or guess one. Shapes: `pkg:<org>/<repo>/<name>`,
`file:<org>/<repo>/<path>`, `agent_plugin:<org>/<repo>/<name>`,
`dependency:<org>/<repo>/<ecosystem>/<name>`, or `repo:<org>/<repo>` for a process or
convention page — that last one copied from the repository's own `code-graph/<repo>.md`
page. Each URI must resolve to exactly one code-graph page.

An ADR carries `decisions:`; an Explanation carries `claims:`; a Reference carries
`claims:` only when it is itself a list of rules. Keep to 1–5 decisions / 1–7 claims —
house style, not schema-enforced (the schema only requires `minItems: 1`). Each entry
requires `id` and `claim`; `about`, `constrains` and `phase` are schema-optional. The
authoring rule is stricter than the schema:

- `claim` is one sentence, present tense, no link or citation: the page is the citation.
- Always write an entry `about` that is a non-empty subset of the page's `about`.
- `constrains` only for repo-relative paths the page itself cites, and each must exist.
- Do not author `phase`, `status` or `superseded_by` on an entry; status is inherited
  from the page.

`gw wiki lint` reports a missing or unresolved field at error.

## 4. Reference, HowTo, Tutorial

Same base frontmatter as Explanation, plus:

| `type` | Extra frontmatter | Required headings |
|---|---|---|
| `Reference` | `claims` (optional) | `Summary` (and optional `See also`) |
| `HowTo` | `prerequisites`, `outcome`, `claims` (optional) | `Goal`, `Assumptions`, `Steps`, `Result` |
| `Tutorial` | `prerequisites`, `outcome` | `What you will build`, `Before you start`, `Steps` (and optional `What you learned`) |

Every row but `Tutorial` also requires `about:` — see **Curated-page claims** in §3.

## 5. Source summary page

One per ingested source (article, spec, ticket, transcript, design doc). Summarized
**once**; other pages cite it. `gw ingest` writes the page and a verbatim copy of the
material at `sources/references/<YYYY-MM>-<slug>.<ext>`; the original is never edited.

Required frontmatter: `type`, `title`, `description`, `source_path`. Declared optional
keys: `source_kind` (`spec | article | ticket | skill | doc | transcript | code-review`),
`origin`, `ingested`, `updated`, `source_date`, `authors`, `entity_uri`, `tokens`,
`tags`, `sources`, `drain`. Omit an optional key rather than writing `null` or an empty value.

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
drain:
  - claim: 1
    landed: [/adrs/0014-jwt-sessions.md#D1]
  - claim: 2
    dropped: history
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
- [shared-aws-node-ts](/code-graph/<repo>/entities/packages/shared-aws-node-ts.md)

## Decisions triggered
- [0014-jwt-sessions](/adrs/0014-jwt-sessions.md) — stable
```

### Drain ledger

`drain:` covers every top-level item under `## Key claims`, numbered from 1 by
position. Each entry is `{claim: <n>, landed: [...]}` or `{claim: <n>, dropped:
<reason>}`. `landed` lists the root-absolute `<page>.md#<entry id>` of each
`decisions:` / `claims:` entry the claim produced. `dropped` is one of `history`
(what happened, not what is true), `evidence` (supports another claim),
`duplicate` (restates another claim or an existing entry), `superseded` (no longer
true). Leave an item out only on purpose: it stays pending, and `gw wiki lint`
reports the Source as `sources.undrained`.

A Source with at least one key claim, every one dispositioned, no ledger
findings and a non-empty `## Where it's cited in this wiki` is *drained*, and
`gw wiki archive` with no target moves it and its `sources/references/` copy
to `_archive/`. A Source with no `## Key claims` items is never drained — it
reports `sources.undrained` with reason `no-key-claims` and never sweeps.

Drift on an in-repo doc is a diff against its `sources/references/` copy. There is no
`last_sync_commit` / `last_sync_at` stamp.

## 6. ADR page

A dated, citable decision in `adrs/`, written in the Diátaxis explanation style: it
shares the base frontmatter of the other Diátaxis types and argues *why*, not what.

The filename is `adrs/<decision_date>-<slug>.md`. **ADRs are dated, never numbered**:
two branches can each add one without colliding on an allocated number, and nothing
has to hand out the next id. Cite an ADR by a link to its path, never by a number.

Required frontmatter: `type: Adr`, `title` (plain — no `ADR-NNNN:` prefix),
`description`, `decision_date`. Optional: `status` (`draft | stable | deprecated`; a
promoted proposal starts `stable`), `deciders`, `supersedes` and `superseded_by` (a
root-absolute path to another ADR, a list of them, or `null`), `updated`, `tags`,
`sources`. There is no `adr_id` and no `category`. `decisions:` holds the
load-bearing decisions as `{id: D<n>, claim, …}` entries — the `x-okf-about`
mandate requires at least one on a live ADR (see **Curated-page claims** in §3).

Declared headings: `Context`, `Decision`, `Consequences` (required), `Alternatives
considered`. Extra headings are allowed.

```markdown
---
type: Adr
title: JWT sessions
description: Adopt short-lived JWTs signed by Cognito in place of server-side session tokens.
status: stable
decision_date: 2026-04-18
deciders: ["human:psprowls"]
supersedes: ["/adrs/2026-01-10-opaque-session-tokens.md"]
superseded_by: null
tags: [auth, sessions]
updated: 2026-04-20
about: [pkg:acme/web/auth]
decisions:
  - id: D1
    claim: Sessions are JWTs signed with the rotating key.
    about: [pkg:acme/web/auth]
    constrains: [packages/auth/src/session.ts]
---

# JWT sessions

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
`gw work advance`; to override one by hand, follow the workflow skill's
`references/editing-work-items.md`.
