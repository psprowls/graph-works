---
name: lint
description: Use when the user says "lint the wiki" or "check the wiki", weekly, after batch ingests, or after a scan — or when they invoke /gw:lint. Runs mechanical checks (orphans, broken links, stale pages, missing frontmatter, log gaps, code drift), semantic checks (contradictions vault↔vault and vault↔code, stale claims, concept gaps, ADR chain health, cross-reference gaps, index drift), and the mechanical work-lifecycle catalog over every path-native item beneath the configured OKF bundle's work/ tree, then produces a markdown report with suggested actions.
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

Workspace and repo are resolved by `gw`.

## Dispatch

Prefer running this skill's body in a forked sub-agent with the tool set
`Read, Write, Edit, Bash, Grep, Glob`. A lint pass parses several large JSON
reports and reads many pages; its intermediate output would flood a caller's
context if run inline.

On a harness without sub-agent dispatch, run it inline in the current
context — the body below is written to work either way.

## Role

You audit the Code Wiki and surface problems for the user to fix. You do NOT silently auto-fix structural issues; you report and suggest. The user decides.

Code Wiki lint reports missing, stale, and orphaned graph-backed pages alongside wiki health and work lifecycle findings.

Spawned per-lint-pass.

## Frequency

| Trigger | Pass |
|---|---|
| Weekly | Four passes |
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
| Declared headings | `sections.missing`, `sections.unfilled`, `sections.unexpected`, `sections.no-declaration-for-type`, `sections.agent-oversize` (always warn); requires section declarations. |
| Wiki health | `health.uncited`, `health.log-gap`; always included. |
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

Read structural statistics separately from `/tmp/stats.json`; they are not lint
payload fields. See the workflow reference for the stats keys.

### Pass 2 — Residual semantic (read and think)

The `semantic` array captured in Pass 1 already covers contradictions (vault↔vault and vault↔code), stale claims, and ADR chain health (`page_quality`, `stale_claims`, `adr_chain`). Read those findings and present them in the report; Pass 2 is only what that array doesn't cover:

- **Concept gaps** — grep for concept-shaped phrases across 3+ pages without a dedicated page
- **Cross-reference gaps** — plain-text mentions of packages/deps that should have root-absolute markdown links
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

Use this report structure, including only findings actually returned or observed:

```markdown
# Code Wiki lint — <date>

**Total pages:** <stats total_pages>  **Components:** <stats component_count>
**Lint OK:** <ok>  **Open proposals:** <open_proposals.count>

## Wiki lint

### Found
- wiki [sync.missing-page]: <message; retain even without a path>
- wiki [sync.stale-page]: <page>: <message>
- wiki [sync.orphan-page]: <page>: <message>
- wiki [render.angle-bracket]: <page>:<line>: <message>
- wiki [sections.missing]: <page>: <message>
- work [<returned dotted code>]: <work-path>: <severity>: <message>
- semantic [<group>]: <page>: <message>
- Pipeline errors: <each errors entry, or none; name incomplete checks>
- Residual review: <concept gaps, cross-reference gaps, index drift>
- Curated drift review: <target>: <overtaken / still accurate, with reason>

### Suggested actions
1. Run `/gw:scan` to reconcile the reported graph-backed page drift.
2. Review render and declared-section findings at their reported locations.
3. Revise the affected work item based on its returned lifecycle finding.
4. Review semantic findings and proposed concept or link additions.

Want me to run these in order, or pick specific ones?
```

Then run `gw util log --op lint --title "<date> health check" --detail "<findings summary>"`
in the intended workspace. It appends a `- **lint** <title> — <detail>` list item
under the day's `## YYYY-MM-DD` heading in `log.md`.

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
