# Scan Workflow

> **Substrate ownership.** This document describes behavior that the graph-works rebuild is
> re-implementing. Identifiers and paths here are retargeted for the `graph-works` namespace, but
> the behavioral truth is owned by [`2026-08-13-epic-feature-scan-pipeline-vertical`](/work/2026-08-13-epic-feature-scan-pipeline-vertical.md) and is re-authored there, not here.
> Treat a disagreement between this page and that item as this page being stale.

## Purpose

Keep the wiki's single `entities/` folder in sync with the code graph. The scan builds the graph, renders one page per admitted entity, then refreshes model-maintained prose (`## Narrative`, `## Purpose`, `## Public API`, file/dir descriptions, overview) via a diff-gated Claude subagent fan-out: an entity is refreshed only when the commit range `last_updated_commit..HEAD` touches its files. A bare invocation runs the mechanical write only.

## Inputs

- Repo root + wiki path (resolved via `gw`).
- The code graph itself — entity discovery is purely graph-driven; there is no folder-shape input or pinned scoping. The graph (built from the repo by `cg`) is the sole source for which entities exist.

## What gets written

One page per admitted entity into `<workspace>/wiki/entities/`, across the **6 admitted kinds**: `repository`, `package`, `app`, `agent_plugin`, `dependency`, `test_suite`. Filenames are URI-derived (`pkg_<name>.md`, `app_<name>.md`, `dep_<name>.md`, `repo_<name>.md`, `agent-plugin_<name>.md`, suite-kind-aware `unit_tests_<pkg>.md` / `int_tests_<pkg>.md`), with a `__<6hex>` suffix on collision. See Appendix A in the plan / `wiki-schema.md` for the full vocabulary.

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
Bulleted wikilinks; suggest `/graph-works:lint` and `/graph-works:ingest` to flesh out narratives.

## Frontmatter contract

Data keys (`DATA_KEYS`, replaced every scan): `uri`, `kind`, `graph_name`, `last_scan_at`, plus per-kind edge/attr keys (`depends_on`, `test_suites`, `entry_points`, `language`, `version`, `app_kind`, `app_signals`, `tested_packages`, `suite_kind`, `file_count`, `ecosystem`, `used_by`, `versions_in_use`, `package_count`). Human keys preserved verbatim: `status`, `last_reviewed`, `owner`, `notes`. `summary` is fill-when-empty.

Provenance keys (scanner-stamped but deliberately NOT in `DATA_KEYS` — preserved verbatim across re-scan):
- `last_updated_commit` — HEAD at which prose sections (`## Narrative`, `## Purpose`, etc.) were last refreshed; gates the diff-driven prose-refresh pass.
- `drift_propagated_commit` — the entity's `last_updated_commit` value at which M4's drift producer last proposed against curated pages backlinking it; gates the M4 cross-page drift pass (proposal ledger) and keeps repeat runs idempotent.

The state gate (`last_updated_commit` stamping on scan/ingest) is configurable per-workspace via the `state_gate:` block in `<root>/workspace.yaml` (`enabled` + allowed `branches`); absent config gates on a clean `main`. See the workspace-io README for the schema.

## Contract

Artifacts live under `<root>/state/` across the emit/apply boundary:

**`worklist.json`** — written by `--emit-worklist`, consumed by `--apply-worklist` / `--results-dir`:
- `prose_tasks` — one diff-gated `ProseRefreshTask` per stale entity: `trigger` (`first_fill` | `diff`), the scoped `diff`, `changed_files`, the current `prose_sections`, `file_map_rows`, `graph_context`, and `owning_short_head`.
- `propagate_tasks` — cross-page drift propagation tasks (M4).
- `short_head` — abbreviated HEAD SHA at emit time; passed as `--short-head` to apply so anchors are stamped to the correct commit.
- `schema: 2`.

**`briefs/<page-stem>.md`** — one self-contained refresh prompt per `prose_tasks` entry, rendered from the same system prompt and work order the Bedrock provider sends. The fan-out follows these; nothing re-derives the contract from this document.

**`results/<page-stem>.json`** — one `ProseRefreshResult` per entity, written by that entity's subagent: `uri`, `sections` (keyed by full H2 headings), `file_map_descriptions`, `dir_descriptions`, `overview`, `error`.

**`results.json`** (optional) — a combined `{schema: 2, prose, propagate}` document; still accepted via `--apply-worklist`, and merged when both sources are given.

Every one of these is a transient workspace artifact: safe to delete, and `briefs/` + `results/` are emptied at the start of each emit.

## Anti-patterns

- Hand-writing `entities/*.md` pages (the graph renders them).
- Letting a prose-refresh subagent write anything but its own `results/<stem>.json` (page writes belong to the apply phase).
- Re-deriving the prose contract from this document instead of following the emitted brief.
- Silently accepting a large deletion set.
- Expecting `apps/` or `packages/` page folders — there are none; everything is in `entities/`.
