---
name: lint
description: Use when the user says "lint the wiki" or "check the wiki", weekly, after batch ingests, or after a scan — or when they invoke /gw:lint. Runs mechanical checks (orphans, broken links, stale pages, missing frontmatter, duplicate titles, log gaps, code drift), semantic checks (contradictions vault↔vault and vault↔code, stale claims, concept gaps, ADR chain health, cross-reference gaps, index drift), and the mechanical work-lifecycle catalog over every path-native item beneath the configured OKF bundle's work/ tree, then produces a markdown report with suggested actions.
---

# Health-check the wiki

Health-check the wiki. Includes **code-drift detection** on top of generic wiki checks — surfaces when the vault has fallen out of sync with the code.

**Reports, doesn't silently fix.** You decide what to change.

Run weekly, after every `/gw:scan`, and after batch ingests.

## Usage

```
/gw:lint          # Claude Code
$lint                      # Codex
```

No arguments. The staleness and log-gap thresholds are `gw wiki lint`'s own, not
something this command threads through.

Workspace and repo are discovered automatically via `workspace_io`.

## Dispatch

Prefer running this skill's body in a forked sub-agent with the tool set
`Read, Write, Edit, Bash, Grep, Glob`. A lint pass parses several large JSON
reports and reads many pages; its intermediate output would flood a caller's
context if run inline.

On a harness without sub-agent dispatch, run it inline in the current
context — the body below is written to work either way.

## Role

You audit the Code Wiki and surface problems for the user to fix. You do NOT silently auto-fix structural issues; you report and suggest. The user decides.

Code Wiki lint adds **code-drift detection** to the generic wiki health check: packages on disk vs. in the vault, deleted packages with orphan vault pages, exports-frontmatter mismatch.

Spawned per-lint-pass.

## Frequency

| Trigger | Pass |
|---|---|
| Weekly | Mechanical only |
| After `/gw:scan` | Full — catches drift |
| After batch ingest | Full |
| Monthly | Full + structural review |
| Before sharing the wiki | Full + extra review |

## Workflow

Follow `../graph-works/references/lint-workflow.md`. Four passes.

### Pass 1 — Mechanical (`gw`)

```bash
gw wiki lint --json > /tmp/lint.json
gw wiki stats --json > /tmp/stats.json
```

(Workspace and repo are resolved by `gw`.)

Parse the JSON. Capture:
- Orphans, broken links, stale, missing frontmatter, duplicate titles, log gap
- Connected components, hubs, sinks (from `/tmp/stats.json`)
- **Code drift**: `missing_in_vault`, `orphaned_in_vault`, `exports_drift`
- **Semantic**: `semantic` — an array of `{group, message, page, model}`, already produced by a real LLM pass inside `gw wiki lint`; groups are `page_quality`, `adr_chain`, `stale_claims`. Report these directly rather than re-deriving them by hand.
- **Work lifecycle**: `work_lifecycle` — `{total_items, findings}`, all 32 lifecycle rules (same set as `gw work lint`)
- **Obsidian render**: `obsidian_render_findings` — markdown that breaks Obsidian's renderer
- **Guidance lint**: `guidance_lint_findings` — frontmatter/tag/placement findings for Diátaxis-lane pages
- **Scanner heading drift**: `scanner_heading_drift` — entity pages missing a deterministic section
- **Source path drift**: `source_path_drift` — `sources/` pages whose `sources/references/` copy is missing on disk

Any of the last five may fail-soft as `{"error": "<msg>"}` — report the error line, don't skip the section silently.

**New check:** Beyond mono-wiki's mechanical and semantic checks, `gw wiki lint` runs `check_package_sync_drift`. Package sync drift is actionable: a package/app page whose source code has changed since its `last_sync_commit` should be re-scanned.

- **Package sync drift** — package/app pages whose source code has changed since their `last_sync_commit`. Surface the count of changed files and one example path; suggest running `/gw:scan` on a clean main checkout.
- **Never-synced packages** — pages with no `last_sync_commit` (legacy or freshly-created stub). The first clean-on-main `/gw:scan` will record one.
- **Sync commit unreachable** — page records a `last_sync_commit` that isn't an ancestor of HEAD (typically means a feature-branch SHA, or main was rebased). Surface as: `<page>: last_sync_commit <sha> not reachable from HEAD`. Suggest re-running `/gw:scan` on a clean main checkout.

### Pass 2 — Residual semantic (read and think)

The `semantic` array captured in Pass 1 already covers contradictions (vault↔vault and vault↔code), stale claims, and ADR chain health (`page_quality`, `stale_claims`, `adr_chain`). Read those findings and present them in the report; Pass 2 is only what that array doesn't cover:

- **Concept gaps** — grep for concept-shaped phrases across 3+ pages without a dedicated page
- **Cross-reference gaps** — plain-text mentions of packages/deps that should be wikilinks
- **Index drift** — `gw wiki index` already reconciles `index.md` mechanically (dead entries pruned, missing ones added); this pass is for drift it wouldn't catch — e.g. a page that should exist but doesn't

### Pass 3 — Drift (`gw wiki drift`)

```bash
gw wiki drift --json > /tmp/drift.json
```

`gw wiki drift` compares curated pages against the code graph and returns `{"targets": [...]}` — each a `Target` (one curated page) carrying a `candidates` list of `Candidate` (one drifted entity backlinking it):

```json
{"targets": [
  {"concept_id": "...", "title": "...", "kind": "...", "candidates": [
    {"concept_id": "...", "resource": "...", "title": "...", "narrative": "...", "last_updated_commit": "...", "changed_files": [...]}
  ]}
]}
```

For each target: open the cited entity narrative(s) named in `candidates` plus the curated page itself, and judge whether the page's claims are actually overtaken by what changed. Report the decision — don't silently rewrite the page; the user decides what to change.

The graph reflects the last `gw scan`, not necessarily HEAD. Running `gw scan` first gives more reliable results, but don't hard-block on it — note in the report if the graph looks stale.

### Pass 4 — Report

The report MUST be structured as:

```markdown
# Code Wiki lint — <date>

**Total pages:** N  **Components:** N  **Last log:** <date>
**Code drift:** <missing> new packages un-documented, <orphan> orphan package pages

## Wiki lint

### Found
- ⚠️ <N> packages on disk missing vault pages: <names>
- ⚠️ <N> vault package pages for non-existent packages: <names>
- ⚠️ <N> contradictions vault↔code
- ⚠️ Work lifecycle: <N> findings across <M> items (<E> error / <W> warn): <work-path>: [<rule_id>] …
- ⚠️ <N> Obsidian render findings: <page>: [<rule_id>] …
- ⚠️ <N> guidance lint findings: <slug>: [<rule_id>] …
- ⚠️ <N> scanner heading drift: <page> missing '<heading>'
- ⚠️ <N> source path drift: <page> → <raw path> gone
- <N> orphan vault pages
- <N> broken links
- <N> stale pages
- <N> concept gaps (mentioned across 3+ pages)
- <N> ADR chain issues
- <N> drift candidates reviewed: `<concept>` — <overtaken / still accurate, one line why>

### Suggested actions
1. Run `/gw:scan` to stub <package> and <package>
2. Re-run `/gw:scan` — it deletes the entity page for `<old-pkg>` automatically when its graph node is gone
3. Re-run `/gw:scan` to refresh `repositories/<repo>/packages/<pkg>.md` graph-derived frontmatter from current code
4. Revise the affected canonical `<work-path>` or update its `work_status`
5. Create concept pages for: <names>
6. Fix broken link in `[<page>](/<page>.md)`

Want me to run these in order, or pick specific ones?
```

Then append a `## [YYYY-MM-DD] lint | <date> health check` entry to `log.md` with the findings summary.

## Rules

- **Check link syntax** during the semantic pass — flag pages that use `[[wikilinks]]` instead of root-absolute markdown links to `.md` targets, malformed callouts, or properties duplicated between frontmatter and body.
- **Report, don't silently fix.** The user decides.
- **Prioritize by impact.** Code drift > contradictions > broken links > orphans > stale > style.
- **Use the scripts AND read pages.** Mechanical + semantic both reveal different problems.
- **Suggest actions** — never just dump findings.
- **Always log the pass.**

## Red flags

- Auto-fixing structural issues without asking → stop
- Silently rewriting a page off a `gw wiki drift` candidate → report the decision and let the user confirm the edit
- Skipping code-drift pass → always run it
- Skipping semantic pass because "mechanical looks clean" → do the read-and-think pass anyway
- Reporting without suggestions → add suggestions
- Not updating `log.md` → always log

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/lint-workflow.md`
