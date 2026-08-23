# Lint Workflow

> **Substrate ownership.** This document describes behavior that the graph-works rebuild is
> re-implementing. Identifiers and paths here are retargeted for the `graph-works` namespace, but
> the behavioral truth is owned by [`feature-epic-feature-lint-drift-propagation-vertical`](/work/_archive/epic-graph-works-core/children/_archive/feature-epic-feature-lint-drift-propagation-vertical.md) and is re-authored there, not here.
> Treat a disagreement between this page and that item as this page being stale.

Periodic health check the LLM runs when the user runs `/graph-works:lint` or dispatches the `graph-works:linter` sub-agent. Run weekly, after batch ingests, and always after a repo scan.

## Goal

Keep the wiki healthy and **keep it in sync with the code**. Surface problems for the user to review. The graph-works linter adds **code-drift detection** on top of the generic wiki health check.

## Pass 1 — mechanical checks (script)

`gw wiki lint` runs each check group in turn; pass `--check <group>` to run an optional group on top of the defaults.

### Default check groups (always run)

```bash
gw wiki lint
```

(Workspace and repo are discovered automatically via `gw`.)

Default report:

- **Orphans** — pages with zero inbound `[[wikilinks]]`
- **Broken links** — wikilinks pointing to non-existent pages
- **Stale pages** — pages whose `updated:` frontmatter is older than 90 days (tune via `--stale-days`)
- **Missing frontmatter** — curated pages lacking `title`/`category`/`summary`; `entities/` pages lacking `uri`/`kind` (entity pages use the scanner-owned frontmatter contract, not `category`/`tokens`/`title`/`updated`)
- **Duplicate titles** — two or more pages sharing the same title
- **Log gap** — no log entry in the last 14 days (tune via `--log-gap-days`)
- **Code drift** (monorepo-specific) — packages/apps/agent_plugins on disk vs. `entities/` pages in the vault (matched by entity `kind` + `uri`; covers `kind: package`, `kind: app`, and `kind: agent_plugin`; legacy `packages/<slug>/` pages still recognized). Pages declaring `status: planned` in frontmatter are excluded from `orphaned_in_vault` and surfaced separately under `planned_in_vault`, so deliberately seeded pages don't drown the signal.
- **`package_sync` drift** (`lint/package_sync.py`) — for legacy/ingest-tracked package/app pages, runs `git diff --name-only <last_sync_commit>..HEAD` against `package_path` / `app_path`. Graph-derived `entities/` pages don't carry `last_sync_commit`, so code drift (above) is the entity-layout freshness signal; re-run `/graph-works:scan` to refresh them.
- **`file_map` drift** (`lint/file_map.py`) — `## File map` entries that no longer exist on disk.
- **Obsidian render** (`lint/obsidian_render.py`, JSON key `obsidian_render_findings`) — markdown that breaks Obsidian's renderer: bare angle-bracket placeholders, malformed callouts, malformed wikilinks/embeds, unescaped table pipes. Covers `index.md` files too.
- **Guidance frontmatter** (`guidance_io.lint`, JSON key `guidance_lint_findings`) — invalid frontmatter, non-allowlisted tags, keyword shape, and topic placement for pages under `wiki/guidance/`.
- **Work lifecycle** (`gw work lint`) — the current state, plan, graph, structure, target, and decision catalogs over every path-native item beneath the configured OKF bundle's `work/` tree.
- **Scanner heading drift** (`lint/scanner_heading.py`, JSON key `scanner_heading_drift`) — entity pages missing an expected deterministic section for their kind (e.g. a human renamed `## Referenced in wiki`).
- **Source path drift** (JSON key `source_path_drift`) — `sources/` pages whose workspace-relative `raw/` `source_path` no longer exists on disk (the file was archived).

The last five run fail-soft: an unexpected per-check exception is reported as `{"error": "<msg>"}` under that JSON key instead of killing the pass. These keys give `/graph-works:lint` mechanical parity with `gw wiki lint`; the parity regression test lives in `packages/graph-works-core/tests/unit/test_lint_parity.py`.

### Other helpers

Run `gw wiki stats` for structural stats — hubs, sinks, connected components. `--top N` sets
how many hubs each list carries (default 10); `--json` emits `total_pages`, `total_edges`,
`component_count`, `top_outbound_hubs`, `top_inbound_hubs`, `orphans`, `sinks`.

## Pass 2 — semantic checks (LLM)

The scripts can't catch these. The LLM must read and think.

### A. Contradictions between wiki pages

Scan pages whose `updated:` is recent. For each, check whether it contradicts any existing page. If so:
- Add a `> ⚠️ Contradiction:` callout to both pages
- Log with `op: note`
- Surface to user

### B. Contradictions between vault and code

For each recently-touched `entities/pkg_<name>.md` / `entities/app_<name>.md` page, spot-check the `## Narrative` prose and `## Public API` claims against the actual `package.json` and `src/index.ts`.

### C. Stale claims

For each flagged stale page, ask:
- Does newer code or a newer source now contradict this?
- Is a "Key patterns" bullet likely to be outdated?
- Suggest to user: "Page X says Y. This may be outdated — want me to re-read the code or find a newer source?"

### D. Concepts mentioned without their own page

Grep for concept-shaped phrases repeated across 3+ package/concept pages but without a dedicated concept page. Suggest creating one. Comparisons (`<a>-vs-<b>.md`) live under `concepts/`.

### E. ADR chain health

- Every `supersedes:` field should point to an existing ADR that has `superseded_by:` pointing back.
- Every ADR with `status: deprecated` should have `superseded_by:` or a reason.

### F. Cross-reference gaps

For each recently-touched page, check: do all package/dependency mentions have wikilinks? If something is referenced as plain text in 3+ places, promote it to a wikilink (and create a stub page if needed).

### G. Index drift

Compare `index.md` against actual `<workspace>/wiki/` contents. If out of sync after manual plugin edits, patch the relevant section inline.

## Pass 3 — report

Present findings to the user as a single markdown report:

```markdown
# Code Wiki lint — 2026-04-20

**Total pages:** 142  **Components:** 1  **Last log:** 2026-04-19
**Code drift:** 2 new packages un-documented, 1 package page orphaned

## Wiki lint

### Found
- ⚠️ 4 packages drifted since last sync: `common-aws-node-ts` (12 files), …
- ⚠️ 2 packages on disk missing wiki pages: `timeline-native-ts`, `timeline-data-node-ts`
- ⚠️ 1 dep-stub-detail-page: `entities/dep_lodash` has 3 body lines — flesh out or delete
- ⚠️ Work lifecycle: 2 findings across 14 items (1 error, 1 warn): `<work-path>: [<rule-id>] …`
- ⚠️ 1 Obsidian render finding: `<page>: [obsidian-render-angle-bracket] …`
- ⚠️ 1 guidance lint finding: `<topic>/<page>: [guidance-invalid-frontmatter] …`
- 3 orphan wiki pages
- 4 concepts mentioned across 3+ pages without their own page

### Suggested actions
1. Run `/graph-works:scan` to create stubs for missing packages
2. Re-read the drifted packages
3. Investigate orphans
```

Append a `lint` entry to `log.md` summarizing what was found and what was fixed.

## Frequency

- **Weekly** — light pass, default groups only
- **After every `/graph-works:scan`** — full code-drift pass
- **After batch ingests** — full pass with all `--check` groups enabled
- **Before sharing the wiki with onboarding devs / agents** — full pass plus extra review
