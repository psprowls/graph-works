# Scan Workflow

## Purpose

Keep `repositories/<repo>/` (and `dependencies/`) in sync with the code graph. The scan builds the graph, renders one page per admitted entity, then refreshes model-maintained prose (`## Narrative`, `## Purpose`, `## Public API`, file/dir descriptions, overview) via a diff-gated Claude subagent fan-out: an entity is refreshed only when the commit range `last_updated_commit..HEAD` touches its files. A bare invocation runs the mechanical write only.

## Inputs

- Repo root + wiki path (resolved via `gw`).
- The code graph itself — entity discovery is purely graph-driven; there is no folder-shape input or pinned scoping. The graph (built from the repo by `cg`) is the sole source for which entities exist.

## What gets written

One page per admitted entity into `<workspace>/okf/repositories/<repo>/` (one folder per kind), across the **6 admitted kinds**: `repository`, `package`, `app`, `agent_plugin`, `dependency`, `test_suite`. `dependency` pages are the one exception, written to the sibling root `<workspace>/okf/dependencies/<ecosystem>/`. Filenames are `<name>.md`, no prefix. See `wiki-schema.md` for the full vocabulary.

## Step-by-step

### 1. Emit → fan-out → apply

The default scan runs as a three-phase pipeline:

**Phase 1 — Emit** (`--emit-worklist <path>`): builds the code graph (`cg update`, incremental), calls `write_entities`, injects deterministic file maps, computes the commit-gate, and serializes the worklist (`prose_tasks`, `propagate_tasks`, `short_head`) to `<root>/state/worklist.json`. It also writes `<root>/state/briefs/<page-stem>.md` — one rendered refresh prompt per stale entity — and resets an empty `<root>/state/results/`.

**Phase 2 — Fan-out**: one read-only subagent per `prose_tasks` entry follows its brief and writes a single `results/<page-stem>.json`. Subagents read with Read/Grep/Glob and write nothing but that one file — no wiki page, no repo file.

**Phase 3 — Apply** (`--results-dir <dir>` and/or `--apply-worklist <results.json>`, plus `--short-head <sha>`): sanitizes every result against its task's declared prose surface, injects it, runs the refill-gated anchor stamp, regenerates indexes and backlinks, and appends to `log.md`.

### 2. Report entities
From the `ScanResult` JSON: `entities_created`, `entities_updated`, `entities_deleted` (URIs), `entity_errors`.

### 3. Surface deletions
`write_entities` hard-deletes pages for vanished graph nodes. Report them; never silently. Offer a git undo when the wiki is versioned. >10 deletions is a red flag (bad repo path / failed graph build) — stop and ask.

### 4. Update cross-references / indexes
Already done by the script (`index.md`, per-folder sub-indexes). No separate step.

### 5. Append to log
Already done by the script.

### 6. Report back
Bulleted wikilinks; suggest `/gw:lint` and `/gw:ingest` to flesh out narratives.

## Frontmatter contract

Each entity type's `.gw/sections/<Type>.yaml` declares two key sets under `frontmatter:`. Identity keys (`type`, `title`, `resource`) come from the scanner on every page.

- **Owned keys** (`frontmatter.owned`, rewritten every scan): the per-kind graph edges and attributes — for example `depends_on`, `test_suites`, `entry_points`, `language`, `version` on a package, `tested_packages`, `suite_kind`, `file_count` on a test suite, `ecosystem`, `used_by`, `versions_in_use` on a dependency, `package_count` on a repository. `wiki-schema.md` has the table.
- **Provenance keys** (`frontmatter.provenance`, scanner-stamped): `generated` (`by`, `at`), `last_updated_commit`, `prose_refreshed_commit`, `prose_refresh_attempts`. (`tokens` is declared too but a scan never stamps it; only the optional `gw util tokens` does.)

Keys a declaration does not list — including any you add by hand — are left alone by a re-scan. The graph-wiki-era keys (`uri`, `kind`, `graph_name`, `last_scan_at`, `summary`) are gone.

The two commit keys mean different things:

- `last_updated_commit` — HEAD at the last **structural** pass. `entities.sync` advances it unconditionally, so it cannot gate prose.
- `prose_refreshed_commit` — the SHA at which the prose sections (`## Purpose`, `## Public API`, etc.) were last refreshed; stamped by the prose phase only. It gates the diff-driven refresh: the commit range since this anchor touching the entity's files earns a fresh attempt. `prose_refresh_attempts` bounds how many times a page that came back still holding a placeholder is retried, and is cleared when the anchor is stamped. `Dependency` pages carry only the counter.

The drift propagator's per-entity anchor is not frontmatter: it lives in `<cache_dir>/drift/propagated.json`.

The state gate (stamping on scan/ingest) is configurable per-workspace via the `state_gate:` block in `<root>/workspace.yaml` (`enabled` + allowed `branches`); absent config gates on a clean `main`.

## Contract

Artifacts live under `<root>/state/` across the emit/apply boundary:

**`worklist.json`** — written by `--emit-worklist`, consumed by `--apply-worklist` / `--results-dir`:
- `prose_tasks` — one diff-gated `ProseRefreshTask` per stale entity: `trigger` (`first_fill` | `diff`), the scoped `diff`, `changed_files`, the current `prose_sections`, `file_map_rows`, `graph_context`, and `owning_short_head`.
- `propagate_tasks` — cross-page drift propagation tasks.
- `short_head` — abbreviated HEAD SHA at emit time; passed as `--short-head` to apply so anchors are stamped to the correct commit.
- `schema: 2`.

**`briefs/<page-stem>.md`** — one self-contained refresh prompt per `prose_tasks` entry, rendered from the same system prompt and work order the Bedrock provider sends. The fan-out follows these; nothing re-derives the contract from this document.

**`results/<page-stem>.json`** — one `ProseRefreshResult` per entity, written by that entity's subagent: `uri`, `sections` (keyed by full H2 headings), `file_map_descriptions`, `dir_descriptions`, `overview`, `error`.

**`results.json`** (optional) — a combined `{schema: 2, prose, propagate}` document; still accepted via `--apply-worklist`, and merged when both sources are given.

Every one of these is a transient workspace artifact: safe to delete, and `briefs/` + `results/` are emptied at the start of each emit.

## Anti-patterns

- Hand-writing entity pages under `repositories/<repo>/` or `dependencies/` (the graph renders them).
- Letting a prose-refresh subagent write anything but its own `results/<stem>.json` (page writes belong to the apply phase).
- Re-deriving the prose contract from this document instead of following the emitted brief.
- Silently accepting a large deletion set.
- Expecting a flat `entities/` folder — there is none; pages are nested under `repositories/<repo>/apps/`, `repositories/<repo>/packages/`, etc.
