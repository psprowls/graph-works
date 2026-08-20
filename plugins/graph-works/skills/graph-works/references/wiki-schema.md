# Wiki Schema

> **Substrate ownership.** This document describes behavior that the graph-works rebuild is
> re-implementing. Identifiers and paths here are retargeted for the `graph-works` namespace, but
> the behavioral truth is owned by [`2026-08-11-epic-wiki-io-format-layer`](/work/2026-08-11-epic-wiki-io-format-layer.md) and is re-authored there, not here.
> Treat a disagreement between this page and that item as this page being stale.

The wiki sits inside a graph-works workspace alongside other workspace-level directories. The LLM must respect the boundaries.

## Layout

The wiki lives at `<workspace>/wiki/`. The workspace is resolved via `gw`, which prefers the `GRAPH_WORKS_DIR` env var — the supported way to point at a workspace, normally injected via the `env` block of the `.claude/settings.local.json` belonging to whichever directory the session runs from. There is no workspace-path key inside `workspace.yaml` itself. Without the env var, resolution falls back to `<repo>/graph-works/` — a `.git` walk-up plus the default name, never a search for `workspace.yaml` — so a workspace kept anywhere else is reachable only via the env var or an explicit `--workspace`. The Obsidian vault opens at `<workspace>/`, so `raw/` (source inbox; ingested sources move to `raw/_archive/`) sits at the workspace root as a sibling of `wiki/`, owned by `gw`. `work/` (work tracker) lives at `<workspace>/wiki/work/` — nested under `wiki/`, not a workspace-root sibling — so `[[work/foo]]` wikilinks resolve the same way as `[[concepts/foo]]`; schema owned by `gw`, lifecycle owned by this plugin.

```
<repo>/graph-works/               # workspace; Obsidian vault opens here
├── workspace.yaml                # workspace manifest (owned by gw)
├── CLAUDE.md                    # workspace-level schema (owned by gw)
├── raw/                         # source inbox; ingested sources move to _archive/
│   ├── articles/*.md            # Obsidian Web Clipper output
│   ├── specs/*.md               # design docs, RFCs
│   ├── prs/*.md                 # PR descriptions and review notes
│   ├── tickets/*.md             # issue exports
│   ├── transcripts/*.md         # meeting and design-session notes
│   └── assets/                  # images referenced by sources
├── knowledge/                   # other plugin-managed knowledge stores
└── wiki/                        # this plugin's curated knowledge base
    ├── index.md                 # content catalog — updated every ingest/scan
    ├── log.md                   # append-only timeline
    ├── work/                    # unified bugs, tech debt, features, initiatives, spikes
    │   └── _archive/            # terminal-status items; consider archiving when status is terminal
    ├── entities/                # one page per graph-derived entity (all kinds)
    │   └── <prefix>_<name>.md   # e.g. pkg_common-aws-node-ts.md, app_web-next-ts.md
    ├── concepts/                # cross-cutting technical concepts; optional kind: concept | pattern | architecture
    ├── sources/                 # one summary page per ingested source
    ├── adrs/                    # architecture decision records
    ├── .templates/              # page templates (reference only, not indexed)
    ├── CLAUDE.md                # wiki schema file for Claude Code
    ├── AGENTS.md                # same schema for Codex/Cursor/Antigravity
    └── .cursorrules             # (optional) legacy Cursor
```

`entities/` is the single flat folder for all graph-derived entity pages (kinds: `repository`, `package`, `app`, `agent_plugin`, `dependency`, `test_suite`). There are no separate `apps/` or `packages/` page folders. Bootstrap seeds `entities/.gitkeep`; `write_entities` removes it once real pages exist and restores it if all pages are swept.

## Iron rules

1. **The code is the source of truth.** If the wiki disagrees with the code, update the wiki — never the other way around.
2. **`<workspace>/raw/` contents are read-only.** The LLM never edits, renames within, or deletes staged sources — the single permitted operation is moving a successfully-ingested source to `raw/_archive/<same relative path>`.
3. **All wiki writes go under `<workspace>/wiki/`.** Work items go to `<workspace>/wiki/work/` (schema owned by `gw`; nested under `wiki/`, not the workspace root). No exceptions.
4. **Every scan or ingest updates ≥3 files:** the touched page(s), `index.md`, `log.md`. A typical ingest touches 5-15.
5. **Every wiki page carries YAML frontmatter.** Without frontmatter, index maintenance and `lint_wiki.py` can't see it.

## Required page frontmatter

```yaml
---
title: common-aws-node-ts
category: package            # see enum below
summary: Lambda handlers, middleware, and AWS SDK client wrappers shared across all -aws-node-ts packages
tags: [aws, lambda, middleware]
sources: 2                   # optional — number of sources referencing this page
updated: 2026-04-20
---
```

Allowed `category` values: `app`, `package`, `concept`, `dependency`, `work`, `source`, `adr`. For concept pages, an optional `kind` field discriminates: `concept` (default), `pattern`, or `architecture` (for high-level syntheses — build system, module graph, request flow, deployment topology).

## Category-specific frontmatter

### Entity pages

Entity pages live under `<workspace>/wiki/entities/` — one page per graph-derived entity, regardless of kind. All entity frontmatter is split into two sets:

**Scanner-owned keys** (replaced every scan — do not hand-edit these):

| Key | Applies to | Notes |
|---|---|---|
| `uri` | all | graph node URI |
| `kind` | all | `repository \| package \| app \| agent_plugin \| dependency \| test_suite` |
| `graph_name` | all | name of the graph that sourced this entity |
| `last_scan_at` | all | YYYY-MM-DD of last scan |
| `depends_on` | package, app | list of dependency names |
| `test_suites` | package, app | associated test suite names |
| `entry_points` | package, app | detected entry-point paths |
| `language` | package, app | primary language string |
| `version` | package, app | version string from manifest |
| `app_kind` | app | app sub-type (web, mobile, cli, …) |
| `app_signals` | app | detected signals (framework, deployment, …) |
| `tested_packages` | test_suite | packages the suite covers |
| `suite_kind` | test_suite | `unit \| integration \| other` |
| `file_count` | test_suite | number of test files detected |
| `ecosystem` | dependency | `npm \| pypi \| cargo \| go \| …` |
| `used_by` | dependency | packages that declare this dependency |
| `versions_in_use` | dependency | version strings found across manifests |
| `package_count` | repository | total workspace packages detected |

**Human-preserved keys** (never overwritten by the scanner):

`status`, `last_reviewed`, `owner`, `notes`, and any key outside the scanner-owned set above.

**`summary`** is fill-when-empty: the scanner writes it only if the field is absent or empty. Once you write a summary, the scanner leaves it alone.

Minimal example (package):

```yaml
---
uri: pkg:org/repo/common-aws-node-ts
kind: package
graph_name: my-repo
last_scan_at: 2026-06-01
depends_on: []
test_suites: []
entry_points: []
language: typescript
version: "1.0.0"
---
```

Entity pages carry no `title` or `updated` key — the H1 carries the display name, and `last_scan_at` is the freshness signal.

### Concept pages

```yaml
---
title: Global Context
category: concept
summary: Per-request context object threaded through every Lambda handler
tags: [middleware, request-handling]
sources: 0
updated: 2026-04-20
---
```

Concepts are cross-cutting technical patterns — naming conventions, middleware shapes, contracts that span packages. A concept page is a one-paragraph definition, where the pattern appears in the code, and links to packages, dependencies, ADRs, and sources that motivate it. Comparisons live here too: `concepts/<a>-vs-<b>.md` for two-way, `concepts/<topic>-options.md` for n-way.

### Dependency pages

`/graph-works:scan` writes one graph-derived dependency page per dep into `entities/dep_<name>.md`, using the scanner-owned shape from `entity-dependency.md` (`uri`, `kind: dependency`, `graph_name`, `last_scan_at`, `ecosystem`, `used_by`, `versions_in_use` — no `category`, `provider`, or `load_bearing`). The `category: dependency` / `kind: package|service` shape below is a **legacy curated-page shape**, hand-authored and **currently checked by nothing** — the `dependency_layer` lint group that once gated it does not exist in graph-works-core, and `gw wiki lint` is a fixed pipeline with no group selection. It does not apply to scanner-generated `entities/dep_*.md` pages. `load_bearing: true` is recorded on these legacy pages but has no reader today; whether the shape survives at all is open in `2026-08-20-tech-debt-revisit-dependency-layer-lint`.

**`kind: package`** (e.g., `entities/dep_react.md`):

```yaml
---
title: React
category: dependency
kind: package
package_name: react
ecosystem: npm                  # npm | pypi | cargo | go | brew | system
versions_in_use: ["19.0.0", "18.3.1"]
used_by: [web-next-ts, app-expo-ts]
upstream_url: https://react.dev
load_bearing: true
quirks: []
tags: [frontend, ui]
updated: 2026-04-20
---
```

**`kind: service`** (e.g., `entities/dep_mongodb-atlas.md`):

```yaml
---
title: MongoDB Atlas
category: dependency
kind: service
service_name: MongoDB Atlas
provider: mongodb-atlas         # aws | gcp | azure | mongodb-atlas | cloudflare | github | …
used_by: [location-aws-node-ts, healthkit-aws-node-ts]
upstream_url: https://www.mongodb.com/atlas
load_bearing: true
quirks: [region-locked-us-west-2]
tags: [database, infra]
updated: 2026-04-20
---
```

Field divergences:

- `package` uses `ecosystem:`; `service` uses `provider:`.
- `versions_in_use` applies only to `package`. Services aren't versioned the same way.

### Work pages

Unified namespace replacing `issues/` + `roadmap/`. `category: work`. `kind:` discriminates between bug-shaped and feature-shaped items; a single status lifecycle covers both. Slugs follow `<YYYY-MM-DD>-<kind>-<w1>-<w2>-<w3>-<w4>.md`, where the 4 words are filer-supplied via `gw work file --slug-words` (falling back to the first 4 words of the title when omitted); children filed under a parent epic get `epic-<kind>` instead of `<kind>`. No migration — pre-existing pages keep their old `<YYYY-MM-DD>-<short-slug>.md` filenames; both formats coexist since every consumer reads slugs from file stems.

```yaml
---
title: <Title>
category: work
kind: bug                       # bug | tech-debt | test-gap | security | perf | feature | epic | spike
summary: <one-line>
status: open                    # open | accepted | in-progress | mitigated | resolved | wontfix | superseded
severity: medium                # bug | security | perf — leave blank for feature/epic/spike
effort: small                   # xtra-small | small | medium | large | xtra-large
blast_radius: package           # file | package | domain | system
affects:
  - packages/location-aws-node-ts
parent: 2026-07-01-epic-big-thing   # child side: owning epic/feature slug (source of truth)
depends_on: []                      # dependency edges — see the table below
children: []                        # DERIVED — tool-refreshed from children's parent keys; do not hand-edit
target: 2026-Q2                 # feature | epic — optional otherwise
owner: pat                      # populate when in-progress
opened: 2026-04-21
updated: 2026-05-03
related_tickets: []
related_prs: []
resolved_in: ""                 # required when resolved
superseded_by: ""               # required when superseded
mitigation: ""                  # required when mitigated
rationale: ""                   # required when wontfix
tags: [location, infrastructure]
---
```

Each `depends_on` entry is an **edge**. A bare slug string is accepted as legacy
shorthand for the defaults, but pages are rewritten to the mapping form on the
next write:

```yaml
depends_on:
  - slug: 2026-08-11-epic-feature-phase-granular-dependency-gating
    blocks: execute
    needs: resolved
  - slug: 2026-08-11-epic-feature-reconciling-spec-mode-routing
    blocks: execute
    needs: plan          # build against its written plan, not its merge
```

| Key | Values | Default | Meaning |
|---|---|---|---|
| `slug` | any work-item slug, active or archived | required | the dependency |
| `blocks` | `design` \| `plan` \| `execute` \| `finish` | `execute` | gates this phase **and every later one** |
| `needs` | `design` \| `plan` \| `execute` \| `resolved` | `resolved` | this phase of the dependency must be **complete** |

`needs` means *complete*, not *entered*. `phase` is stamped at dispatch, so an
item reading `phase: plan` is currently being planned and its `plan_doc` does
not exist yet — `needs: plan` is satisfied at `phase: execute`. Every blocker
message names both the required state and the observed one, so the rule never
has to be recalled from memory. There is no `needs: finish`: that state is
`phase: done, status: resolved`, which `resolved` already names.

`children` is a derived, tool-refreshed projection maintained by `gw work regen-index` (which every filing/advance/archive runs): it lists this item's children — active *and* archived, sorted by `(opened, slug)` — and is omitted when empty. Hand-edits are overwritten; to detach a child, delete the *child's* `parent:` key and regen. Parents may be epics or features; feature children keep plain `<kind>-…` slugs (the `epic-<kind>-` prefix stays epic-only).

The committed plan does **not** live in frontmatter — it's a markdown table under `## Plan` in the body. See [Body-table conventions](#body-table-conventions) below.

The lifecycle lint rules (`accepted-without-plan`, `stuck-open`, `done-when-missing`, etc.) and the `<workspace>/wiki/work-index.json` sidecar generator live in **`graph-works`** (work_layer group). `gw` creates the `work/` directory (nested under `wiki/`). See `references/lint-workflow.md` for what lives where.

#### The `work/_archive/` sub-namespace

Items that have reached a terminal status (`resolved`, `wontfix`,
`superseded`) may be moved to
`<workspace>/work/_archive/<slug>.md`. They retain their full schema —
same frontmatter, same body convention, same wiki-page semantics —
but are excluded from:

- base structural lint (`/graph-works:lint`)
- the work-tracker sidecar (`<workspace>/wiki/work-index.json`)
- consumer commands that read the sidecar

Items under `_archive/` must already be in a terminal status; the
archive command (`/graph-works:archive`) enforces this on entry.
Restoring is a `git mv` from `_archive/` back to `work/` plus
`/graph-works:regen-index`.

### Source pages

```yaml
---
title: "Auth Migration Spec"
category: source
summary: Spec for moving from session tokens to JWTs; addresses compliance flags
source_path: raw/specs/auth-migration.md   # raw/<...> for staged sources, repo-relative (e.g. docs/auth.md) for in-repo docs
source_type: spec                # spec | article | pr | ticket | transcript | example | doc | note
source_date: 2026-04-01
last_sync_commit:                # set only for in-repo docs (source_type: doc) — full SHA at last ingest, used by /graph-works:lint to detect changes
last_sync_at:                    # YYYY-MM-DD when sync state was recorded
authors: [@psprowls]
ingested: 2026-04-20
updated: 2026-04-20
---
```

In-repo docs (an in-repo `.md` passed to `/graph-works:ingest` by repo-relative path) use `source_type: doc`, set `source_path` to the repo-relative path, and record `last_sync_commit` and `last_sync_at`. PDF/DOCX/etc. are deferred — only `.md` is supported today.

### Architecture pages (concept pages with `kind: architecture`)

High-level syntheses — the layers, components, and flows that span multiple packages — live in `concepts/` as concept pages with `kind: architecture`. The `## Thesis` body section is the load-bearing part; the rest (layers, diagrams, key concepts, decisions) supports the thesis and rotates as the codebase changes. `packages:` lists the workspaces the synthesis reasons about so lint can flag when a referenced package goes away.

```yaml
---
title: Request flow
category: concept
kind: architecture
summary: How a request flows from edge → API → service layer → datastore
packages: [web-next-ts, common-aws-node-ts, location-aws-node-ts]
tags: [architecture, request-flow]
sources: 0
updated: 2026-04-20
---
```

### ADR pages

```yaml
---
title: "ADR-0012: Move to ESM"
category: adr
adr_id: 0012
status: accepted                 # proposed | accepted | deprecated | superseded
decision_date: 2026-02-14
deciders: [@psprowls]
supersedes: null                 # ADR ID this replaces, if any
superseded_by: null              # ADR ID that replaces this, if any
tags: [build-system, modules]
updated: 2026-04-20
---
```

## Naming conventions

- **Filenames:** `kebab-case.md` — lowercase, hyphens, no spaces
- **Entity pages** live flat in `entities/` as `<prefix>_<name>[__<6hex>].md`. The `__<6hex>` SHA suffix is appended only on collision. Prefix per kind:

  | Kind | Prefix | Example |
  |---|---|---|
  | `repository` | `repo_` | `repo_my-monorepo.md` |
  | `package` | `pkg_` | `pkg_common-aws-node-ts.md` |
  | `app` | `app_` | `app_web-next-ts.md` |
  | `agent_plugin` | `agent-plugin_` | `agent-plugin_graph-works.md` |
  | `dependency` | `dep_` | `dep_react.md` |
  | `test_suite` (unit) | `unit_tests_` | `unit_tests_common-aws-node-ts.md` |
  | `test_suite` (integration) | `int_tests_` | `int_tests_common-aws-node-ts.md` |
  | `test_suite` (other) | `tests_` | `tests_common-aws-node-ts.md` |

- **Concepts:** `concepts/<concept-slug>.md` — e.g. `concepts/global-context.md`. Comparisons live here too: `concepts/<a>-vs-<b>.md` for two-way, `concepts/<topic>-options.md` for n-way.
- **Sources:** `sources/<YYYY-MM>-<short-slug>.md` — e.g. `sources/2026-04-auth-migration-spec.md`
- **ADRs:** `adrs/<NNNN>-<slug>.md` — e.g. `adrs/0012-move-to-esm.md`. Zero-padded ID, monotonically increasing.
- **Architecture syntheses:** `concepts/<topic>.md` with `kind: architecture` — e.g. `concepts/request-flow.md`
- **Dependencies:** `entities/dep_<package-name>.md` — use the registry name (`dep_react.md`, `dep_react-native-maps.md`). For scoped npm packages, replace `/` with `__` (`dep_@tanstack__react-query.md`). Service pages use a slug derived from the service name (`dep_mongodb-atlas.md`).
- **Work:** `work/<YYYY-MM-DD>-<kind>-<w1>-<w2>-<w3>-<w4>.md` (`epic-<kind>` prefix for epic children) — e.g. `work/2026-07-11-feature-shorten-work-item-slugs.md`.

## Taxonomies

The categorical vocabularies that frontmatter fields draw from. These apply across multiple categories (mainly `work`); per-category enums (e.g. ADR `status`, dependency `kind`) live with the category above.

### `kind` (work)

Eight values, two origins:

| Kind | Origin | Typical shape |
|---|---|---|
| `bug` | discovered | symptom + root cause + fix |
| `tech-debt` | discovered | suboptimal pattern + refactor target |
| `test-gap` | discovered | missing coverage + test plan |
| `security` | discovered | exposure + remediation |
| `perf` | discovered or measured | regression + budget + fix |
| `feature` | intended | user-driven capability + scope |
| `epic` | intended | multi-feature effort spanning weeks/quarters |
| `spike` | intended | time-boxed exploration with a question |

Schema/structure problems are `kind: bug` + `tag: data-model`. Wiki↔code drift filed by lint is `kind: tech-debt` + `tag: doc-drift`. Lifecycle and required fields are identical to the underlying kind; the discriminating live in tags rather than spawning new kinds.

### `kind` (dependency)

Two values: `package | service`. Frontmatter shape diverges per kind — see [Dependency pages](#dependency-pages) above.

### Severity (work)

Values: `low | medium | high | critical`.

| Bucket | Kinds | Lint |
|---|---|---|
| Common | `bug`, `security`, `perf` | severity expected, not enforced |
| Possible | `tech-debt`, `test-gap` | severity allowed when known |
| Disallowed | `feature`, `epic`, `spike` | `severity-on-non-bug` (info) |

### Effort (work)

| Value | Anchor |
|---|---|
| `xtra-small` | minutes — one-line change, no test, no review needed |
| `small` | hours — single file, tests, single PR |
| `medium` | days — multiple files, possibly cross-package, single PR |
| `large` | weeks — multiple PRs, possibly an epic |
| `xtra-large` | months — multi-epic, large team or quarter-long scope |

Anchors are advisory. Missing field = unknown; no `unknown` value.

### Blast radius (work)

Blast-radius values: `file | package | domain | system`. **Practical impact, not source-code locality** — a one-line change to a shared library used by every domain is `system` even though the source is in one package.

### Per-kind field applicability (work)

| Field | Required for | Allowed for | Disallowed for |
|---|---|---|---|
| `severity` | none | `bug`, `security`, `perf`, `tech-debt`, `test-gap` | `feature`, `epic`, `spike` |
| `target` | none | all kinds — only meaningful for `feature`, `epic` | — |
| `owner` | none | all kinds — populated when `in-progress` | — |
| `effort` | none | all kinds | — |
| `blast_radius` | none | all kinds | — |

State-conditional fields (`resolved_in`, `mitigation`, `superseded_by`, `rationale`) are populated only in their corresponding state. Lint enforces.

### Status lifecycle (work)

Seven states. Replaces the two pre-existing enums (`open|investigating|mitigated|resolved|wontfix` and `proposed|planned|in-progress|done|cancelled`).

| State | Meaning | Required fields |
|---|---|---|
| `open` | filed; no committed plan | — |
| `accepted` | plan committed; `## Plan` table populated; ready to start | `## Plan` non-empty |
| `in-progress` | someone is implementing | `pr` or `branch` reference |
| `mitigated` | symptom hidden, root cause persists (mostly bug/security/perf) | `mitigation` |
| `resolved` | done | `resolved_in` |
| `wontfix` | closed without action | `rationale` |
| `superseded` | replaced by another work item | `superseded_by` |

Transitions are mostly forward; `accepted → open` (back) is allowed when a plan is invalidated by new evidence.

## Body-table conventions

Three categories use markdown tables in the body for structured rows. Header rows are exact; lint's table parser is strict.

### `## Plan` (work)

```markdown
## Plan

| Action | Done when | Rationale |
|---|---|---|
| Stage-prefix the database name in CDK | `location-service.ts` has no literal `dev-pat-location` | Matches `STAGE` already in the same block |
```

- Header row exact: `| Action | Done when | Rationale |`.
- One row per step. Order is significant.
- `Done when` is required (lint `warn`) for `kind: feature` and `kind: epic`; optional otherwise.
- Pipes inside cell content escape as `\|`.
- File paths and `path:line` references in the `Action` cell are checked for existence by lint; line numbers are advisory.

## Linking

Use Obsidian wikilinks. Three forms:

```
[[entities/pkg_common-aws-node-ts]]                         # full path to entity page
[[entities/pkg_common-aws-node-ts|the AWS helpers package]] # custom display
[[pkg_common-aws-node-ts]]                                  # stem — resolves if unique
```

For entity pages (packages, apps, etc.), prefer stem links when the name is unambiguous; use the full `entities/<prefix>_<name>` path only when disambiguation is needed. Use full paths for non-entity pages (concepts, sources, ADRs, etc.).

Code references — when citing actual code — use a plain code reference (Obsidian won't wikilink them but it's searchable):

```
See `packages/common-aws-node-ts/src/handlers/baseApiHandler.ts:42`
```

## Cross-reference rules

- **Every package mentioned on an entity or concept page must be a wikilink** to `entities/<prefix>_<name>`.
- **Every ADR referenced in entity/concept pages must be a wikilink** to `adrs/<id>-<slug>`.
- **Every claim on an entity page cites** either a source page (`[[sources/xxx]]`) or a code path (backticked, with file:line).
- **Contradictions get flagged inline** with a `> ⚠️ Contradiction:` callout naming the conflicting sources or code paths.
- **Concept pages with `kind: architecture` link back to every entity and ADR they draw on.**

## Index discipline

`<workspace>/wiki/index.md` is regenerated by command-layer scan/ingest flows. For manual plugin edits, update the relevant section inline.

The index groups pages by category, alphabetized by title. Each entry is one line with a wikilink, summary, and optional metadata.

## Log discipline

`<workspace>/wiki/log.md` is append-only. Every entry starts with a standardized header so `grep "^## \[" log.md | tail -5` returns the last 5 entries.

```
## [2026-04-20] scan | detected 3 new packages
Added entities/pkg_timeline-data-node-ts.md, entities/pkg_timeline-domain-ts.md,
entities/pkg_timeline-native-ts.md. No renames or deletions.

## [2026-04-20] ingest | Auth Migration Spec
Added sources/2026-04-auth-migration-spec.md. Updated concepts/global-context,
entities/pkg_shared-aws-node-ts.md, entities/pkg_shared-native-ts.md,
concepts/request-flow, adrs/0014-jwt-sessions (new). Flagged contradiction
with concepts/global-context on session shape.
```

Valid ops: `scan`, `ingest`, `query`, `lint`, `create`, `update`, `delete`, `note`.
