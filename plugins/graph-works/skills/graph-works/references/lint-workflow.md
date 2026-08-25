# Lint Workflow

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
- **Missing frontmatter** — curated pages lacking `title`/`category`/`summary`; entity pages (under `repositories/<repo>/` or `dependencies/`) lacking `uri`/`kind` (entity pages use the scanner-owned frontmatter contract, not `category`/`tokens`/`title`/`updated`)
- **Duplicate titles** — two or more pages sharing the same title
- **Log gap** — no log entry in the last 14 days (tune via `--log-gap-days`)
- **Code drift** (monorepo-specific) — packages/apps/agent_plugins on disk vs. their pages under `repositories/<repo>/` (matched by entity `kind` + `uri`; covers `kind: package`, `kind: app`, and `kind: agent_plugin`; legacy `packages/<slug>/` pages still recognized). Pages declaring `status: planned` in frontmatter are excluded from `orphaned_in_vault` and surfaced separately under `planned_in_vault`, so deliberately seeded pages don't drown the signal.
- **Semantic** (JSON key `semantic`) — a real LLM pass `gw wiki lint` runs itself, not a script: an array of `{group, message, page, model}`, grouped `page_quality`, `adr_chain`, `stale_claims`. This already covers vault↔vault and vault↔code contradictions, stale-claim flags, and ADR chain health — Pass 2 reads and presents these findings rather than re-deriving them.
- **`package_sync` drift** (`lint/package_sync.py`) — for legacy/ingest-tracked package/app pages, runs `git diff --name-only <last_sync_commit>..HEAD` against `package_path` / `app_path`. Graph-derived entity pages don't carry `last_sync_commit`, so code drift (above) is the entity-layout freshness signal; re-run `/graph-works:scan` to refresh them.
- **`file_map` drift** (`lint/file_map.py`) — `## File map` entries that no longer exist on disk.
- **Obsidian render** (`lint/obsidian_render.py`, JSON key `obsidian_render_findings`) — markdown that breaks Obsidian's renderer: bare angle-bracket placeholders, malformed callouts, malformed wikilinks/embeds, unescaped table pipes. Covers `index.md` files too.
- **Guidance frontmatter** (`guidance_io.lint`, JSON key `guidance_lint_findings`) — invalid frontmatter, non-allowlisted tags, keyword shape, and topic placement for Diátaxis-lane pages.
- **Work lifecycle** (`gw work lint`) — the current state, plan, graph, structure, target, and decision catalogs over every path-native item beneath the configured OKF bundle's `work/` tree.
- **Scanner heading drift** (`lint/scanner_heading.py`, JSON key `scanner_heading_drift`) — entity pages missing an expected deterministic section for their kind (e.g. a human renamed `## Referenced in wiki`).
- **Source path drift** (JSON key `source_path_drift`) — `sources/` pages whose `sources/references/` copy no longer exists on disk.

The last five run fail-soft: an unexpected per-check exception is reported as `{"error": "<msg>"}` under that JSON key instead of killing the pass. These keys give `/graph-works:lint` mechanical parity with `gw wiki lint`; the parity regression test lives in `packages/graph-works-core/tests/unit/test_lint_parity.py`.

### Other helpers

Run `gw wiki stats` for structural stats — hubs, sinks, connected components. `--top N` sets
how many hubs each list carries (default 10); `--json` emits `total_pages`, `total_edges`,
`component_count`, `top_outbound_hubs`, `top_inbound_hubs`, `orphans`, `sinks`.

## Pass 2 — residual semantic checks (LLM)

`gw wiki lint`'s `semantic` field (Pass 1) already runs an LLM pass over `page_quality`, `adr_chain`, and `stale_claims` — vault↔vault and vault↔code contradictions, stale-claim flags, and ADR chain health are already in that report. Read and present those findings; don't re-derive them here. What's left for this pass is what the CLI has no way to detect on its own:

### A. Concepts mentioned without their own page

Grep for concept-shaped phrases repeated across 3+ package/concept pages but without a dedicated concept page. Suggest creating one. Comparisons (`<a>-vs-<b>.md`) live under `concepts/`.

### B. Cross-reference gaps

For each recently-touched page, check: do all package/dependency mentions have wikilinks? If something is referenced as plain text in 3+ places, promote it to a wikilink (and create a stub page if needed).

### C. Index drift

`gw wiki index` already reconciles `index.md` mechanically — it prunes dead entries, adds missing ones, and copies every other byte through. This pass isn't re-diffing `index.md` by hand; it's spotting drift that reconciliation wouldn't catch, e.g. a page that should exist (a concept, an ADR) but doesn't yet.

## Pass 3 — drift (`gw wiki drift`)

```bash
gw wiki drift --json
```

Compares curated pages against the code graph and returns `{"targets": [...]}` — each a `Target` (one curated page) carrying a `candidates` list of `Candidate` (one drifted entity backlinking it):

```json
{"targets": [
  {"concept_id": "...", "title": "...", "kind": "...", "candidates": [
    {"concept_id": "...", "resource": "...", "title": "...", "narrative": "...", "last_updated_commit": "...", "changed_files": [...]}
  ]}
]}
```

For each target, open the cited entity narrative(s) in `candidates` and the curated page itself, and judge whether the page's claims are actually overtaken by what changed — then report that decision. **Don't silently rewrite the page**; the user decides what to change.

`gw wiki drift` reads the graph as of the last `gw scan`, not necessarily HEAD. Run `gw scan` first for more reliable results, but don't hard-block the lint pass on it — note in the report if the graph looks stale.

## Pass 4 — report

Present findings to the user as a single markdown report:

```markdown
# Code Wiki lint — 2026-04-20

**Total pages:** 142  **Components:** 1  **Last log:** 2026-04-19
**Code drift:** 2 new packages un-documented, 1 package page orphaned

## Wiki lint

### Found
- ⚠️ 4 packages drifted since last sync: `common-aws-node-ts` (12 files), …
- ⚠️ 2 packages on disk missing wiki pages: `timeline-native-ts`, `timeline-data-node-ts`
- ⚠️ 1 dep-stub-detail-page: `dependencies/npm/lodash` has 3 body lines — flesh out or delete
- ⚠️ Work lifecycle: 2 findings across 14 items (1 error, 1 warn): `<work-path>: [<rule-id>] …`
- ⚠️ 1 Obsidian render finding: `<page>: [obsidian-render-angle-bracket] …`
- ⚠️ 1 guidance lint finding: `<topic>/<page>: [guidance-invalid-frontmatter] …`
- 3 orphan wiki pages
- 4 concepts mentioned across 3+ pages without their own page
- 2 drift candidates reviewed: `checkout-flow` — narrative overtaken by `payments-service` refactor; `auth-model` — still accurate

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
