# Wiki Schema

> **Substrate ownership.** `epic-wiki-io-format-layer` (the item this disclaimer used to
> point at) is `resolved`/`phase: done` — pointing readers there now sends them to closed
> history, not live truth. If this page and the shipped workspace tree
> (`<repo>/.works/okf/...`) disagree, treat this page as stale and file (or find) the
> TechDebt item that tracks the drift; see
> [tech-debt-plugin-docs-layout-claims](/work/tech-debt-plugin-docs-layout-claims.md) for the layout-claim sweep that
> last corrected this page.

The wiki sits inside a graph-works workspace alongside other workspace-level directories. The LLM must respect the boundaries.

## Layout

The workspace root lives at `<repo>/.works/`. Workspace resolution prefers an
explicit `--workspace`, then `GRAPH_WORKS_DIR`, then a `.git` walk-up to that
default. The OKF bundle lives at `<workspace>/okf/`; `.gw/` is the control plane
(cache and worktree state nest under it, not as top-level workspace siblings).
Ingest reads material directly from any filesystem path; there is no staging inbox and no separate knowledge store. The Obsidian vault opens at `<workspace>/okf/`.

```
<repo>/.works/                      # workspace root
├── workspace.yaml                  # workspace manifest (owned by gw)
├── .gw/                            # control plane
│   ├── cache/                      # nested under .gw/, not a top-level sibling
│   └── worktrees/                  # nested under .gw/, not a top-level sibling
└── okf/                            # bundle root; Obsidian vault root
    ├── index.md                    # content catalog — updated every ingest/scan
    ├── log.md                      # append-only timeline
    ├── tags.yaml
    ├── work/                       # unified bugs, tech debt, features, initiatives, spikes
    │   └── _archive/                # terminal-status items; consider archiving when status is terminal
    ├── repositories/<repo>/
    │   ├── repository.md           # the repository's own entity page
    │   ├── packages/<name>.md
    │   ├── apps/<name>.md
    │   ├── agent-plugins/<name>.md
    │   ├── test-suites/<name>.md
    │   └── files/<source-path>.md
    ├── dependencies/<ecosystem>/<name>.md   # sibling root, NOT nested under repositories/
    ├── tutorials/ how-tos/ references/ explanations/   # Diátaxis lanes
    ├── concepts/                   # cross-cutting technical concepts; optional kind: concept | pattern | architecture
    ├── sources/                    # one summary page per ingested source
    │   └── references/             # copies of ingested material (the ingest flow copies here; originals are never moved)
    ├── adrs/                       # architecture decision records
    ├── proposals/                  # curated-page proposal ledger
    ├── .templates/                 # page templates (reference only, not indexed)
    ├── CLAUDE.md                   # wiki schema file for Claude Code
    ├── AGENTS.md                   # same schema for Codex/Cursor/Antigravity
    └── .cursorrules                # (optional) legacy Cursor
```

Entity pages are nested under `repositories/<repo>/`, one folder per kind
(`packages/`, `apps/`, `agent-plugins/`, `test-suites/`), plus the repository's
own `repository.md`. `dependency` is the one entity kind that is NOT nested
under `repositories/` — it is a sibling root, `dependencies/<ecosystem>/<name>.md`.
There is no `entities/` folder and no filename-prefix scheme.

## Iron rules

1. **The code is the source of truth.** If the wiki disagrees with the code, update the wiki — never the other way around.
2. **Ingested source material is never edited.** The ingest flow (either `gw ingest`'s `--backend bedrock`/`vercel` pipeline, or the `claude_code`-mode ingestor sub-agent per `/graph-works:ingest`) copies material into `<workspace>/okf/sources/references/` — the original file, wherever it lives, is left untouched. There is no staging inbox and no post-ingest move.
3. **All curated writes go under `<workspace>/okf/`.** Work items use canonical paths below `<workspace>/okf/work/`. No exceptions.
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

Entity pages live under `<workspace>/okf/repositories/<repo>/` (one folder per kind — `packages/`, `apps/`, `agent-plugins/`, `test-suites/` — plus the repository's own `repository.md`), except `dependency` pages, which live at the sibling root `<workspace>/okf/dependencies/<ecosystem>/<name>.md`. All entity frontmatter is split into two sets:

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

`/graph-works:scan` writes one graph-derived dependency page per dep into `dependencies/<ecosystem>/<name>.md`, using the scanner-owned shape from `entity-dependency.md` (`uri`, `kind: dependency`, `graph_name`, `last_scan_at`, `ecosystem`, `used_by`, `versions_in_use` — no `category`, `provider`, or `load_bearing`). The `category: dependency` / `kind: package|service` shape below is a **legacy curated-page shape**, hand-authored and **currently checked by nothing** — the `dependency_layer` lint group that once gated it does not exist in graph-works-core, and `gw wiki lint` is a fixed pipeline with no group selection. It does not apply to scanner-generated `dependencies/<ecosystem>/<name>.md` pages. `load_bearing: true` is recorded on these legacy pages but has no reader today; whether the shape survives at all is open in `2026-08-20-tech-debt-revisit-dependency-layer-lint`.

**`kind: package`** (e.g., `dependencies/npm/react.md`):

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

**`kind: service`** (e.g., `dependencies/mongodb-atlas.md`):

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

Work items are path-native OKF concepts. A permanent identity is an extensionless
bundle-relative path, not a page stem:

```text
work/<release>
work/<release>/children/<epic>
work/<release>/children/<epic>/children/<feature>
```

`Release` is root-only. `Release`, `Epic`, and `Feature` may own a `children/`
lane; `Bug`, `TechDebt`, `TestGap`, and `Spike` are leaves. Every item owns the
directory beside its page. Managed artifacts live under its `references/`
directory: `00-decisions.md`, `01-design.md`, `02-plan.md`,
`03-execute-results.md`, `03-execute-transcript.jsonl`, and
`04-finish-results.md`. Each lane has its own `index.md` and may have a local
`_archive/`. Physical placement defines ancestry; no `parent` or `children`
frontmatter aliases exist.

```yaml
---
type: Feature
title: Path-native filing
description: File and route by permanent path.
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
```

Every `depends_on` entry is a complete mapping. `path` names any active or
archived canonical item; `blocks` is `design | plan | execute | finish`; `needs`
is `design | plan | execute | finish | resolved`. The CLI form is equally
explicit: `--dep path=<canonical>,blocks=<phase>,needs=<phase>`.

The plan is the `## Plan` body table. `sources[]` registers owned artifacts by
filename-derived id; `resource` is root-absolute within the OKF bundle.

Archive commands move an item to the `_archive/` lane owned by its current
parent (or `work/_archive/` for roots), preserving its owned directory and
repairing path references. Indexes are Markdown filesystem projections
reconciled by `gw work regen-index`; there is no JSON sidecar.

### Source pages

```yaml
---
title: "Auth Migration Spec"
category: source
summary: Spec for moving from session tokens to JWTs; addresses compliance flags
source_path: sources/references/auth-migration.md   # ingest's copy destination: sources/references/<YYYY-MM>-<slug>.<ext>, always — no in-repo-doc exception
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
- **Entity pages** are nested under `repositories/<repo>/`, one folder per kind, no
  filename prefix:

  | Kind | Path | Example |
  |---|---|---|
  | `repository` | `repositories/<repo>/repository.md` | `repositories/my-monorepo/repository.md` |
  | `package` | `repositories/<repo>/packages/<name>.md` | `repositories/my-monorepo/packages/common-aws-node-ts.md` |
  | `app` | `repositories/<repo>/apps/<name>.md` | `repositories/my-monorepo/apps/web-next-ts.md` |
  | `agent_plugin` | `repositories/<repo>/agent-plugins/<name>.md` | `repositories/my-monorepo/agent-plugins/graph-works.md` |
  | `dependency` | `dependencies/<ecosystem>/<name>.md` (sibling root, not nested) | `dependencies/npm/react.md` |
  | `test_suite` | `repositories/<repo>/test-suites/<name>.md` | `repositories/my-monorepo/test-suites/common-aws-node-ts.md` |

- **Concepts:** `concepts/<concept-slug>.md` — e.g. `concepts/global-context.md`. Comparisons live here too: `concepts/<a>-vs-<b>.md` for two-way, `concepts/<topic>-options.md` for n-way.
- **Sources:** `sources/<YYYY-MM>-<short-slug>.md` — e.g. `sources/2026-04-auth-migration-spec.md`
- **ADRs:** `adrs/<NNNN>-<slug>.md` — e.g. `adrs/0012-move-to-esm.md`. Zero-padded ID, monotonically increasing.
- **Architecture syntheses:** `concepts/<topic>.md` with `kind: architecture` — e.g. `concepts/request-flow.md`
- **Dependencies:** `dependencies/<ecosystem>/<name>.md` — use the registry name (`dependencies/npm/react.md`, `dependencies/npm/react-native-maps.md`). For scoped npm packages, replace `/` with `__` (`dependencies/npm/@tanstack__react-query.md`). Service pages use a slug derived from the service name, under the `dependencies/` root (`dependencies/mongodb-atlas.md`).
- **Work:** `<work-path>.md`, where `<work-path>` is an extensionless canonical
  path such as `work/release-cutover/children/epic-migration/children/feature-parser`.
  Each basename is stable kebab-case; dates are lifecycle metadata, not identity.

## Taxonomies

The categorical vocabularies that frontmatter fields draw from. These apply across multiple categories (mainly `work`); per-category enums (e.g. ADR `status`, dependency `kind`) live with the category above.

### `type` (work)

Seven PascalCase values define placement and workflow behavior:

| Type | Placement | Typical shape |
|---|---|---|
| `Release` | root only; may own children | a dated delivery boundary containing Epics |
| `Epic` | root or child; may own children | a multi-feature effort |
| `Feature` | root or child; may own children | a user-driven capability |
| `Bug` | leaf | symptom, diagnosis, and fix |
| `TechDebt` | leaf | suboptimal pattern and refactor target |
| `TestGap` | leaf | missing coverage and test plan |
| `Spike` | leaf | time-boxed exploration with a question |

Security and performance are contributed tags (`security`, `perf`) on the
appropriate work type, not additional types. Schema/structure problems are
normally `type: Bug` plus a `data-model` tag; wiki-to-code drift is normally
`type: TechDebt` plus a `doc-drift` tag.

### `kind` (dependency)

Two values: `package | service`. Frontmatter shape diverges per kind — see [Dependency pages](#dependency-pages) above.

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

### Per-type field applicability (work)

| Field | Required for | Allowed for | Disallowed for |
|---|---|---|---|
| `target` | none | all types — usually meaningful for `Release`, `Epic`, and `Feature` | — |
| `target_date` | none | `Release` | every other type |
| `version` | none | `Release` | every other type |
| `owner` | every `in-progress` item | all types | — |
| `effort` | every stable document | all types | — |
| `blast_radius` | none | all types | — |

State-conditional fields (`resolved_in`, `mitigation`, `superseded_by`, `rationale`) are populated only in their corresponding state. Lint enforces.

### Status lifecycle (work)

Seven states. Replaces the two pre-existing enums (`open|investigating|mitigated|resolved|wontfix` and `proposed|planned|in-progress|done|cancelled`).

| State | Meaning | Required fields |
|---|---|---|
| `open` | filed; no committed plan | — |
| `accepted` | plan committed; `## Plan` table populated; ready to start | `## Plan` non-empty |
| `in-progress` | someone is implementing | `owner` |
| `mitigated` | symptom hidden, root cause persists | `mitigation` |
| `resolved` | done | `resolved_in` except for an Epic resolved by its children gate |
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
- `Done when` is required (lint `warn`) for `type: Feature` and `type: Epic`; optional otherwise.
- Pipes inside cell content escape as `\|`.
- File paths and `path:line` references in the `Action` cell are checked for existence by lint; line numbers are advisory.

## Linking

Use root-absolute markdown links — `okf_io.LinkGraph` parses `[text](/path.md)` and cannot see a `[[wikilink]]` at all:

```
[the AWS helpers package](/repositories/<repo>/packages/common-aws-node-ts.md)  # full path to entity page, custom display
[common-aws-node-ts](/repositories/<repo>/packages/common-aws-node-ts.md)       # full path, display matches the stem
```

Always use the full `/repositories/<repo>/<kind-folder>/<name>.md` path for entity pages — there is no stem-only resolution. Use full root-absolute paths for non-entity pages (concepts, sources, ADRs, etc.) too.

Code references — when citing actual code — use a plain code reference (not a link):

```
See `packages/common-aws-node-ts/src/handlers/baseApiHandler.ts:42`
```

## Cross-reference rules

- **Every package mentioned on an entity or concept page must be a link** to `/repositories/<repo>/packages/<name>.md`.
- **Every ADR referenced in entity/concept pages must be a link** to `/adrs/<id>-<slug>.md`.
- **Every claim on an entity page cites** either a source page (`[…](/sources/xxx.md)`) or a code path (backticked, with file:line).
- **Contradictions get flagged inline** with a `> ⚠️ Contradiction:` callout naming the conflicting sources or code paths.
- **Concept pages with `kind: architecture` link back to every entity and ADR they draw on.**

## Index discipline

`<workspace>/okf/index.md` is regenerated by command-layer scan/ingest flows. For manual plugin edits, update the relevant section inline.

The index groups pages by category, alphabetized by title. Each entry is one line with a wikilink, summary, and optional metadata.

## Log discipline

`<workspace>/okf/log.md` is append-only. Every entry starts with a standardized header so `grep "^## \[" log.md | tail -5` returns the last 5 entries.

```
## [2026-04-20] scan | detected 3 new packages
Added repositories/my-monorepo/packages/timeline-data-node-ts.md,
repositories/my-monorepo/packages/timeline-domain-ts.md,
repositories/my-monorepo/packages/timeline-native-ts.md. No renames or deletions.

## [2026-04-20] ingest | Auth Migration Spec
Added sources/2026-04-auth-migration-spec.md. Updated concepts/global-context,
repositories/my-monorepo/packages/shared-aws-node-ts.md,
repositories/my-monorepo/packages/shared-native-ts.md,
concepts/request-flow, adrs/0014-jwt-sessions (new). Flagged contradiction
with concepts/global-context on session shape.
```

Valid ops: `scan`, `ingest`, `query`, `lint`, `create`, `update`, `delete`, `note`.
