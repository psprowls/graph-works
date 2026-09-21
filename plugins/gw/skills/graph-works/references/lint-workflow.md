# Lint Workflow

Periodic health check the LLM runs when the user runs `/gw:lint`. Run weekly, after batch ingests, and always after a repo scan.

## Goal

Keep the wiki healthy and **keep it in sync with the code**. Surface problems for the user to review. The graph-works linter adds **code-drift detection** on top of the generic wiki health check.

## Pass 1 — mechanical checks (script)

```bash
gw wiki lint --json
```

Workspace and repo are resolved by `gw`.

`gw wiki lint --json` emits exactly five top-level keys:

| Key | How to read it |
| --- | --- |
| `ok` | True when there are no pipeline errors and every mechanical lane report is OK. Semantic findings are advisory. |
| `mechanical` | Array of `{lane, findings}`. Traverse each lane's findings and retain `code`, `severity`, `message`, `spec`, `path`, and `line`. Work lifecycle findings are in the `work` lane. |
| `semantic` | Array of `{group, message, page, model}`; groups include `page_quality`, `adr_chain`, and `stale_claims`. Present these model findings directly. |
| `open_proposals` | Backlog summary with `count`, `oldest` (ISO date or null), `malformed`, and `ages`. |
| `errors` | Array of pipeline error strings, including lane and semantic failures. Surface every error and identify incomplete checks. |

An empty findings array does not prove a failed or unavailable check succeeded.
If the command fails without a usable JSON report, report that failure explicitly.
For each mechanical finding, show its lane, dotted code, severity, message, and
available location. A `sync.missing-page` finding can have `path: null`; retain
its message instead of dropping the finding.

### Rule families and prerequisites

Built-in OKF validation accompanies each lane, including document, link, index,
and lifecycle checks. Wiki rules are composed as follows:

| Concern | Codes and conditions |
| --- | --- |
| Graph-backed page sync | `sync.missing-page`, `sync.stale-page`, `sync.orphan-page`; requires a graph reader. |
| Obsidian rendering | `render.angle-bracket`, `render.callout`, `render.wikilink`, `render.wikilink-target`, `render.table-pipe`; always included. |
| Declared headings | `sections.missing`, `sections.unfilled`, `sections.unexpected`, `sections.no-declaration-for-type`; requires section declarations. |
| Wiki health | `health.uncited`, `health.duplicate-title`, `health.log-gap`; always included. |
| Placement, schema, vocabulary | Code-wiki placement always applies; schema and vocabulary checks require their declaration files. |

Absent wiki declarations leave the corresponding checks inactive; malformed
ones produce lane errors. Work-lane schema and section declarations are required,
so missing ones also produce lane errors. Report the returned work findings
without assuming a fixed catalog size.

Commit-derived sync staleness uses `last_updated_commit`; it is distinct from
OKF `lifecycle.stale`. Suggest `/gw:scan` for graph-backed page drift, preserving
the finding's explanation. Sync findings do not carry changed-file counts or
an export-specific result.

The lint CLI exposes `--json`, `--workspace`, and `--help`. There are no optional
check-group or threshold flags.

### Other helpers

Run `gw wiki stats` for structural stats — hubs, sinks, connected components. `--top N` sets
how many hubs each list carries (default 10); `--json` emits `total_pages`, `total_edges`,
`component_count`, `top_outbound_hubs`, `top_inbound_hubs`, `orphans`, `sinks`.

## Pass 2 — residual semantic checks (LLM)

`gw wiki lint`'s `semantic` field (Pass 1) already runs an LLM pass over `page_quality`, `adr_chain`, and `stale_claims` — vault↔vault and vault↔code contradictions, stale-claim flags, and ADR chain health are already in that report. Read and present those findings; don't re-derive them here. What's left for this pass is what the CLI has no way to detect on its own:

### A. Concepts mentioned without their own page

Grep for concept-shaped phrases repeated across 3+ package/explanation pages but without a dedicated explanation page. Suggest creating one. Comparisons (`<a>-vs-<b>.md`) live under `explanations/`.

### B. Cross-reference gaps

For each recently-touched page, check: do package/dependency mentions have root-absolute markdown links? If something is referenced as plain text in 3+ places, suggest a root-absolute markdown link and, if needed, a stub page.

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

Present findings as a single markdown report. This is an illustrative structure; include only actual findings and use the returned messages and severities:

```markdown
# Code Wiki lint — 2026-04-20

**Total pages:** 142  **Components:** 1
**Lint OK:** false  **Open proposals:** 2

## Wiki lint

### Found
- wiki [sync.missing-page]: <message identifying an entity with no page; path is null>
- wiki [sync.stale-page]: <page>: <commit-derived staleness message>
- wiki [render.angle-bracket]: <page>:<line>: <message>
- wiki [sections.missing]: <page>: <missing declared section>
- work [<returned dotted code>]: <work-path>: <severity>: <message>
- semantic [page_quality]: <page>: <message>
- Pipeline errors: <errors entries; identify any incomplete checks>
- Residual review: 4 concepts mentioned across 3+ pages without their own page
- Curated drift review: `checkout-flow` — overtaken; `auth-model` — still accurate

### Suggested actions
1. Run `/gw:scan` to reconcile reported graph-backed page drift.
2. Review the cited render, section, and work findings.
3. Review semantic and curated-page findings before editing.
```

Append a `lint` entry to `log.md` summarizing what was found and what was fixed.

## Frequency

- **Weekly** — run the four passes
- **After every `/gw:scan`** — full code-drift pass
- **After batch ingests** — full pass
- **Before sharing the wiki with onboarding devs / agents** — full pass plus extra review
