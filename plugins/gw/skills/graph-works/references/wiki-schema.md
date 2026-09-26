# Wiki Schema

> **The shipped workspace tree wins.** If this page and the tree `gw bootstrap`
> actually builds (`<repo>/.works/okf/...`) disagree, this page is wrong: trust the
> tree, and file a TechDebt item for the drift.

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
│   ├── schema/                     # per-type JSON Schema — authoritative for frontmatter
│   ├── sections/                   # per-type required headings and placeholders
│   ├── tags.yaml                   # tag vocabulary
│   ├── cache/                      # nested under .gw/, not a top-level sibling
│   └── worktrees/                  # nested under .gw/, not a top-level sibling
└── okf/                            # bundle root; Obsidian vault root
    ├── index.md                    # content catalog — updated every ingest/scan
    ├── log.md                      # append-only timeline
    ├── work/                       # unified bugs, tech debt, features, initiatives, spikes
    │   └── _archive/                # archived top-level items, each with its whole subtree
    ├── code-graph/
    │   ├── index.md             # Repositories
    │   ├── <repo>.md            # the repository's own entity page
    │   └── <repo>/
    │       ├── index.md         # stub: Repository link + Subdirectories
    │       ├── entities/
    │       │   ├── index.md     # Packages, Apps, Agent Plugins, Test Suites, Dependencies
    │       │   ├── packages/<name>.md
    │       │   ├── apps/<name>.md
    │       │   ├── agent-plugins/<name>.md
    │       │   ├── test-suites/<name>.md
    │       │   └── dependencies/<ecosystem>/<name>.md   # one per (repository, dependency)
    │       └── file-system/<source-path>.md
    ├── docs/tutorials/ docs/how-tos/ docs/reference/ docs/explanations/   # Diátaxis lanes
    ├── sources/                    # one summary page per ingested source
    │   └── references/             # copies of ingested material (the ingest flow copies here; originals are never moved)
    ├── adrs/                       # architecture decision records
    ├── proposals/                  # curated-page proposal ledger
    ├── .templates/                 # page templates (reference only, not indexed)
    ├── CLAUDE.md                   # wiki schema file for Claude Code
    ├── AGENTS.md                   # same schema for Codex/Cursor/Antigravity
    └── .cursorrules                # (optional) Cursor
```

Entity pages are nested under `code-graph/<repo>/entities/`, one folder per kind
(`packages/`, `apps/`, `agent-plugins/`, `test-suites/`, `dependencies/`), beside the
repository's own `code-graph/<repo>.md` and its `file-system/` mirror. Dependencies are per repository, not global: one Dependency per (repository, dependency); `used_by` and `versions_in_use` describe that repository only. The implementing Package page aggregates across repositories.
There is no top-level `entities/` folder (only the per-repository one above) and no filename-prefix scheme.

## Iron rules

1. **The code is the source of truth.** If the wiki disagrees with the code, update the wiki — never the other way around.
2. **Ingested source material is never edited.** The ingest flow (either `gw ingest`'s `--backend bedrock`/`vercel` pipeline, or the `claude_code`-mode `ingest` skill per `/gw:ingest`) copies material into `<workspace>/okf/sources/references/` — the original file, wherever it lives, is left untouched. There is no staging inbox and no post-ingest move.
3. **All curated writes go under `<workspace>/okf/`.** Work items use canonical paths below `<workspace>/okf/work/`. No exceptions.
4. **Every scan or ingest updates ≥3 files:** the touched page(s), `index.md`, `log.md`. A typical ingest touches 5-15.
5. **Every wiki page carries YAML frontmatter.** Without frontmatter, index maintenance and `gw wiki lint` can't see it.

## Frontmatter

Every page opens with YAML frontmatter, and `type:` selects the contract. The schemas
installed at `.gw/schema/<Type>.schema.json` are authoritative for keys, enums, and
required fields; `.gw/sections/<Type>.yaml` for required headings and placeholders.
Read those rather than copying another page's shape.

| Lane | `type` | Directory | Required keys | Written by |
|---|---|---|---|---|
| Entities | `Repository`, `Package`, `App`, `AgentPlugin`, `TestSuite`, `File` | `code-graph/<repo>/…` | `type`, `title`, `resource` | `gw scan` |
| Dependencies | `Dependency` | `code-graph/<repo>/entities/dependencies/<ecosystem>/` | `type`, `title`, `resource`, `ecosystem` | `gw scan` |
| Diátaxis | `Explanation`, `Reference`, `HowTo`, `Tutorial` | `docs/explanations/`, `docs/reference/`, `docs/how-tos/`, `docs/tutorials/` | `type`, `title`, `description` | authors, via proposals |
| Sources | `Source` | `sources/` | `type`, `title`, `description`, `source_path` | `gw ingest` |
| ADRs | `Adr` | `adrs/` | `type`, `title`, `description`, `decision_date` | authors, or `gw wiki proposal promote` |
| Proposals | `Proposal` | `proposals/` | `type`, `title`, `target`, `page_status`, `sources` | `gw ingest`, `gw wiki proposal` |
| Work | `Release`, `Epic`, `Feature`, `Bug`, `TechDebt`, `TestGap`, `Spike` | `work/` | `type`, `title`, `description`, `work_status`, `opened`, `updated`; plus `effort` and `affects` unless `status: draft` | `gw work` |

Minimal example (a curated page):

```yaml
---
type: Explanation
title: Global context
description: Per-request context object threaded through every Lambda handler.
tags: [middleware, request-handling]
updated: 2026-04-20
---
```

**Retired keys.** The graph-wiki-era keys `category`, `adr_id`, `summary`, `kind`, `uri`, `graph_name`, `last_scan_at`,
`source_type`, `last_sync_commit`, `last_sync_at`, `packages`, `spec_doc`, `plan_doc`,
and `phase_started_commit` are not in any schema and nothing writes them. Older pages
still carry some; do not copy them onto new pages.

`tags:` draws from the vocabulary in `.gw/tags.yaml`. The work lane does not enforce
it; curated lanes do.

## Category-specific frontmatter

### Entity pages

Entity pages live under `<workspace>/okf/code-graph/<repo>/` (`entities/{packages,apps,agent-plugins,test-suites}/`, `entities/dependencies/<ecosystem>/`, and the `file-system/` mirror — plus the repository's own `code-graph/<repo>.md`).

Identity keys — `type`, `title`, `resource` — are on every page. `description` and
`tags` are optional. Each type also has **scanner-owned keys**, listed under
`frontmatter.owned` in `.gw/sections/<Type>.yaml` and rewritten every scan, and
**provenance keys**, listed under `frontmatter.provenance`. Do not hand-edit either;
keys a declaration does not list are left alone.

| Type | Scanner-owned keys |
|---|---|
| `Repository` | `package_count` |
| `Package` | `language`, `version`, `depends_on`, `test_suites`, `entry_points`, `used_by`, `versions_in_use` |
| `App` | `package` |
| `AgentPlugin` | `ecosystem`, `version`, `package` |
| `TestSuite` | `tested_packages`, `suite_kind`, `file_count` |
| `File` | `language`, `package`, `role_flags` |
| `Dependency` | `ecosystem`, `implemented_by`, `used_by`, `versions_in_use` |

Provenance keys, all scanner-written: `generated` (`by`, `at`), `last_updated_commit`
(the commit at the last structural pass), and — on pages with prose
sections — `prose_refreshed_commit` (the SHA the prose was last refreshed at) and
`prose_refresh_attempts` (the retry counter for a declined refresh; cleared on
success). `Dependency` pages carry only the counter; `File` pages carry neither.
`tokens` is declared but not scan-stamped: only the optional `gw util tokens` writes it, nothing runs that automatically, and pages here do not carry it.

Minimal example (package):

```yaml
---
type: Package
title: common-aws-node-ts
resource: pkg:org/repo/common-aws-node-ts
language: typescript
version: "1.0.0"
depends_on: []
test_suites: []
entry_points: []
generated:
  by: code-wiki-okf/0.5.0
  at: '2026-09-09T21:37:46.643280+00:00'
last_updated_commit: 3ff9f663
---
```

### Diátaxis pages (`Explanation`, `Reference`, `HowTo`, `Tutorial`)

All four share `type`, `title`, `description`, and optionally `status`
(`draft | stable | deprecated`), `updated`, `tags`, `sources`, and `about`.
`HowTo` and `Tutorial` add `prerequisites` and `outcome`.

**The claims contract.** `about:` is a non-empty list of scanner resource URIs
naming what the page is about — `repo:`, `pkg:`, `app:`, `agent_plugin:`,
`test_suite:`, `file:`, or `dependency:{org}/{repo}/{ecosystem}/{name}`. Each
must resolve to exactly one scanner-written page; a curated page's own
`resource:` never counts. `Adr` pages carry `decisions:` entries (ids `D1`,
`D2`, …); `Explanation`, `Reference` and `HowTo` carry `claims:` entries (ids
`C1`, `C2`, …). An entry is `{id, claim, about?, constrains?, phase?}` — only
`id` and `claim` are schema-required; `about` narrows the page's list,
`constrains` is repo-relative paths (no leading `/`, no `..`), and `phase` is
a list of any of `design | plan | execute | finish`. The schemas describe the
shape only — the authoring rules for what to actually write in an entry are
in `skills/graph-works/references/page-formats.md` → **Curated-page claims**.
The mandate is the top-level `x-okf-about` annotation — present
on `Adr`, `Explanation`, `Reference` and `HowTo` — which makes `about:`
required, and on `Adr`/`Explanation` names the entry list a **live** page
(any `status` but `draft`, `deprecated`, `superseded`; no `status` is live)
must fill. `gw wiki lint` reports gaps as `about.*` / `claims.*`, at error.
`Tutorial` carries no mandate.

An `Explanation` is a cross-cutting technical concept — a naming convention,
middleware shape, or contract that spans packages — or a high-level synthesis
(layers, components, flows). It is a one-paragraph definition, where the idea appears
in the code, and links to the packages, dependencies, ADRs, and sources that motivate
it. Comparisons live here too: `docs/explanations/<a>-vs-<b>.md` for two-way,
`docs/explanations/<topic>-options.md` for n-way. The old `kind: concept | pattern |
architecture` discriminator is gone; use `tags:` (for example `architecture`).

### Dependency pages

`/gw:scan` writes one graph-derived page per dependency into
`code-graph/<repo>/entities/dependencies/<ecosystem>/<name>.md`. One Dependency per (repository, dependency); `used_by` and `versions_in_use` describe that repository only. The implementing Package page aggregates across repositories. The `Dependency` shape is shipped by
`code-wiki-okf` and installed at `.gw/schema/Dependency.schema.json` and
`.gw/sections/Dependency.yaml`.

**Required frontmatter:** `type: Dependency`, `title`, `resource`, `ecosystem`.
**Optional:** `description`, `tags`, `implemented_by`, `used_by`, `versions_in_use`.
**Provenance, scanner-owned:** `generated` (`by`, `at`), `last_updated_commit`, `prose_refresh_attempts`.
**Sections:** `## Why we depend on this` (required), then `## Gotchas / workarounds`.

```yaml
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
```

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
`04-finish-results.md`. Each lane has its own `index.md`. Physical placement defines ancestry; no
`parent` or `children` frontmatter aliases exist.

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

Archive moves a top-level item, with its owned directory and whole `children/`
subtree unchanged, to `work/_archive/<name>`. A child is never archived on its
own. Path references are repaired. Indexes are Markdown filesystem projections
reconciled by `gw work regen-index`; there is no JSON sidecar.

### Source pages

`gw ingest` writes one summary page per ingested source, plus a verbatim copy of the
material under `sources/references/`. It stamps `title`, `description`, `source_kind`,
`source_path`, `origin`, and `ingested`; the optional keys below come from the ingest
brief. `updated` is declared but not stamped: set it when you touch the page, as
on every other lane.

```yaml
---
type: Source
title: "Auth Migration Spec"
description: Spec for moving from session tokens to JWTs; addresses compliance flags.
source_kind: spec              # spec | article | ticket | skill | doc | transcript | code-review
source_path: sources/references/2026-04-auth-migration-spec.md   # the copy: sources/references/<YYYY-MM>-<slug>.<ext>, always
origin: /abs/path/to/auth-migration.md   # where the material was read from; never edited
source_date: 2026-04-01
ingested: 2026-04-20
updated: 2026-04-20
authors: ["@psprowls"]
entity_uri: repo:acme/web      # optional; omit the key rather than writing null
tags: [auth]
---
```

Drift on an in-repo doc is a diff against its `sources/references/` copy; there is no
`last_sync_commit` stamp any more.

### ADR pages

```yaml
---
type: Adr
title: Move to ESM
description: Adopt ECMAScript modules in place of CommonJS across the package.
status: stable                   # draft | stable | deprecated
decision_date: 2026-02-14        # required; also the filename's date prefix
deciders: ["human:psprowls"]
supersedes: ["/adrs/2025-11-03-use-commonjs.md"]   # path(s) of the ADR(s) this replaces
superseded_by: null              # path(s) of the ADR(s) that replace this
tags: [build-system, modules]
updated: 2026-04-20
about: [pkg:acme/web/build-tools]   # scanner URIs this decision is about (required; see x-okf-about)
decisions:                       # at least one on a live ADR
  - id: D1
    claim: The package ships ESM only; CommonJS entry points are removed.
    about: [pkg:acme/web/build-tools]
    constrains: [packages/build-tools/package.json]
---
```

## Naming conventions

- **Filenames:** `kebab-case.md` — lowercase, hyphens, no spaces
- **Entity pages** are nested under `code-graph/<repo>/entities/`, one folder per kind, no
  filename prefix:

  | Kind | Path | Example |
  |---|---|---|
  | `repository` | `code-graph/<repo>.md` | `code-graph/my-monorepo.md` |
  | `package` | `code-graph/<repo>/entities/packages/<name>.md` | `code-graph/my-monorepo/entities/packages/common-aws-node-ts.md` |
  | `app` | `code-graph/<repo>/entities/apps/<name>.md` | `code-graph/my-monorepo/entities/apps/web-next-ts.md` |
  | `agent_plugin` | `code-graph/<repo>/entities/agent-plugins/<name>.md` | `code-graph/my-monorepo/entities/agent-plugins/graph-works.md` |
  | `dependency` | `code-graph/<repo>/entities/dependencies/<ecosystem>/<name>.md` | `code-graph/my-monorepo/entities/dependencies/npm/react.md` |
  | `test_suite` | `code-graph/<repo>/entities/test-suites/<name>.md` | `code-graph/my-monorepo/entities/test-suites/common-aws-node-ts.md` |

- **Explanations:** `docs/explanations/<slug>.md` — e.g. `docs/explanations/global-context.md`. Comparisons live here too: `docs/explanations/<a>-vs-<b>.md` for two-way, `docs/explanations/<topic>-options.md` for n-way.
- **Sources:** `sources/<YYYY-MM>-<short-slug>.md` — e.g. `sources/2026-04-auth-migration-spec.md`
- **ADRs:** `adrs/<YYYY-MM-DD>-<slug>.md` — e.g. `adrs/2026-02-14-move-to-esm.md`. Dated by the decision, never numbered: there is no id to allocate and nothing to collide on.
- **Architecture syntheses:** `docs/explanations/<topic>.md` tagged `architecture` — e.g. `docs/explanations/request-flow.md`
- **Dependencies:** `code-graph/<repo>/entities/dependencies/<ecosystem>/<name>.md` — use the registry name (`code-graph/web/entities/dependencies/npm/react.md`). For scoped npm packages, replace `/` with `__` (`code-graph/web/entities/dependencies/npm/@tanstack__react-query.md`). Service pages use a slug derived from the service name under the same `dependencies/` folder.
- **Work:** `<work-path>.md`, where `<work-path>` is an extensionless canonical
  path such as `work/release-cutover/children/epic-migration/children/feature-parser`.
  Each basename is stable kebab-case; dates are lifecycle metadata, not identity.

## Taxonomies

The categorical vocabularies that frontmatter fields draw from. These apply across multiple categories (mainly `work`); per-type enums (e.g. ADR `status`, Source `source_kind`) live with the type above.

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
| `released_at` | `Release`, to resolve it | all types (declared in the base work schema; `gw work advance` stamps it) | — |

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
[the AWS helpers package](/code-graph/<repo>/entities/packages/common-aws-node-ts.md)  # full path to entity page, custom display
[common-aws-node-ts](/code-graph/<repo>/entities/packages/common-aws-node-ts.md)       # full path, display matches the stem
```

Always use the full `/code-graph/<repo>/entities/<kind-folder>/<name>.md` path for entity pages — there is no stem-only resolution. Use full root-absolute paths for non-entity pages (explanations, sources, ADRs, etc.) too.

Code references — when citing actual code — use a plain code reference (not a link):

```
See `packages/common-aws-node-ts/src/handlers/baseApiHandler.ts:42`
```

## Cross-reference rules

- **Every package mentioned on an entity or explanation page must be a link** to `/code-graph/<repo>/entities/packages/<name>.md`.
- **Every ADR referenced in entity or explanation pages must be a link** to `/adrs/<YYYY-MM-DD>-<slug>.md`.
- **Every claim on an entity page cites** either a source page (`[…](/sources/xxx.md)`) or a code path (backticked, with file:line).
- **Contradictions get flagged inline** with a `> ⚠️ Contradiction:` callout naming the conflicting sources or code paths.
- **Architecture explanations link back to every entity and ADR they draw on.**

## Index discipline

`<workspace>/okf/index.md` is regenerated by command-layer scan/ingest flows. For manual plugin edits, update the relevant section inline.

The index groups pages by lane, alphabetized by title. Each entry is one line with a root-absolute markdown link, summary, and optional metadata.

## Log discipline

`<workspace>/okf/log.md` is append-only. Use one `## YYYY-MM-DD` section per day,
newest dates first, and one `- **<op>** <title> — <detail>` list item per operation.
Indent continuation paragraphs and nested bullets under their entry. Bold labels
are a graph-works convention, not an OKF requirement.

Prefer `gw util log --op <op> --title <title> --detail <detail>` in the intended
workspace to append. For read-only retrieval and entry/date/operation filters,
follow the [log skill](../../log/SKILL.md#read-only-retrieval); headings count days,
not operations.

```
## 2026-04-20

- **scan** detected 3 new packages
  Added code-graph/my-monorepo/entities/packages/timeline-data-node-ts.md,
  code-graph/my-monorepo/entities/packages/timeline-domain-ts.md,
  code-graph/my-monorepo/entities/packages/timeline-native-ts.md. No renames or deletions.

- **ingest** Auth Migration Spec
  Added sources/2026-04-auth-migration-spec.md. Updated docs/explanations/global-context,
  code-graph/my-monorepo/entities/packages/shared-aws-node-ts.md,
  code-graph/my-monorepo/entities/packages/shared-native-ts.md,
  docs/explanations/request-flow, adrs/0014-jwt-sessions (new). Flagged contradiction
  with docs/explanations/global-context on session shape.
```

Valid ops: `scan`, `ingest`, `query`, `lint`, `create`, `update`, `delete`, `note`.
