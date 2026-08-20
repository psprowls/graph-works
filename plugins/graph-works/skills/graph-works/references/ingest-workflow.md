# Ingest Workflow

> **Substrate ownership.** This document describes behavior that the graph-works rebuild is
> re-implementing. Identifiers and paths here are retargeted for the `graph-works` namespace, but
> the behavioral truth is owned by [`2026-08-13-epic-feature-ingest-pipeline-vertical`](/work/2026-08-13-epic-feature-ingest-pipeline-vertical.md) and is re-authored there, not here.
> Treat a disagreement between this page and that item as this page being stale.

The detailed flow the LLM follows when the user runs `/graph-works:ingest <path>` or dispatches the `graph-works:ingestor` sub-agent.

Sources in a graph-works vault are one of: **spec**, **article**, **PR summary**, **ticket**, **transcript**, **RFC**, **design doc**, or an **in-repo doc** (a `.md` that lives in the repo, passed by repo-relative path). The ingest flow is the same for all — only the summary template's framing changes.

## Source locations

Sources live in two places:

- **`<workspace>/raw/<...>`** — clipped articles, specs, PRs, transcripts you've staged. File contents are never edited; after a successful ingest the source is moved to `raw/_archive/<same relative path>`, so `raw/` (outside `_archive/`) only holds un-ingested material. Owned by `gw`.
- **`<repo>/<...>.md`** (in-repo design docs) — any `.md` that resolves under the repo but outside the wiki. Pass the repo-relative path straight to `/graph-works:ingest`; `ingest_source.py` detects it as an in-repo doc (`in_repo_doc`). The summary records `source_path` (repo-relative) and `last_sync_commit` so `/graph-works:lint` flags staleness when the file changes. The doc itself stays in the repo — the wiki does not duplicate it.

## Inputs

- Path to a source file. Either inside `raw/` or repo-relative for in-repo docs. If the file is somewhere else (e.g. `~/Downloads/`), prompt the user to stage it under `raw/` first.
- The current state of `<workspace>/wiki/` (especially `index.md`, relevant `entities/`, `concepts/`)

## Step-by-step

### 1. Prepare the brief

Run `gw ingest --source <path> --json` to get (wiki and repo discovered automatically via `gw`):
- title guess
- word count
- preview (first 1200 chars)
- source_type guess (spec / article / pr / ticket / transcript / example / doc / note — raw/<type>/ folders are authoritative; in-repo docs default to `doc`, loose files to `note`)
- suggested summary-page path (`<workspace>/wiki/sources/<YYYY-MM>-<slug>.md`)
- whether a summary page already exists (→ **merge mode**)
- `last_sync_commit`, `in_repo_doc` flag, and `state_gate` (`allowed`, `reason`, `head_commit`) — use `state_gate.allowed` to decide whether to write drift-detection frontmatter; use `state_gate.head_commit` as the value for `last_sync_commit`
- `entity_match` — `{ uri: <str|null>, entity_filename: <str|null> }` — the best-matching entity from `entities/` for this source (used to populate `entity_uri:` frontmatter); null when no match is found
- `is_batch`, `kind_folder`, `unit_count`, `total_count`, `limited`, `units[]` — when `--source` is a top-level `raw/<kind>` folder (specs, articles, prs, tickets, transcripts, examples, skills); see "Batch ingest (kind-folder roots)" below

### 2. Read the source

Use the Read tool on the source directly. For PDFs, use Read's PDF support. For images in `raw/assets/`, inspect them if the LLM has vision.

### 3. Discuss with the user

Before writing anything, tell the user:
- Title, authors, date, source type
- 2-3 sentence TL;DR
- Key claims (bulleted, 3-7 items)
- **Which code entities and concepts this source touches** — bulleted `[[entities/...]]` wikilinks
- Any **contradictions** with existing pages or with current code
- Whether this source proposes a decision worth capturing as an ADR
- **New pages** — REQUIRED enumeration: every NEW page this ingest would create
  (concept stubs, ADRs), one bullet each, e.g.
  `- NEW concepts/<slug>.md — <one-line justification>`. If none, state
  "New pages: none." Your single confirmation covers exactly this list — never
  create a page that was not enumerated.

**Wait for user to confirm or redirect.** The user is in the loop — the ingestor proposes, the user approves.

If the user declines specific pages from the list, do not drop them — file each
declined page to the proposals ledger instead:

```bash
gw wiki proposal file --lane <concept|adr> --title "<title>" \
  --id "<YYYY-MM>-<slug>" --resource "sources/<YYYY-MM>-<slug>.md" \
  --rationale "<why>" --evidence "<claim>" [--evidence "<claim>" ...]
```

`--lane`, `--title`, `--id` and `--resource` are all required. There is no `--target-slug`: the
target is derived from the lane and the title, so one page proposed twice under the same title
merges rather than forking. `--id` and `--resource` identify the **source making the argument** —
the source page's slug and its bundle-relative path — and are what a re-fired proposal merges on.

### 4. Create / merge the source summary page

Path: `<workspace>/wiki/sources/<YYYY-MM>-<slug>.md`. Use the **source summary** template from `references/page-formats.md`. Required frontmatter: `title`, `category: source`, `summary`, `source_path`, `source_type`, `ingested`, `updated`. For a source under `raw/`, set `source_path` to the post-archive location `raw/_archive/<rel-path>` (step 12 moves the file there). In-repo docs keep their repo-relative `source_path`.

For in-repo docs (`source_type: doc`), also set `last_sync_commit` (`state_gate.head_commit`) and `last_sync_at` (today) — but only when `state_gate.allowed` is true (working tree clean and HEAD on `main`). Otherwise omit both fields and warn the user that drift detection won't apply until the next clean-on-main ingest. `/graph-works:lint` uses these fields to flag drift on subsequent runs.

**Merge mode** (summary page already exists): append a new `## Re-ingest <date>` section at the bottom with what changed. Do not overwrite the original summary. Bump `last_sync_commit` to the new HEAD so drift detection resets (gate: clean tree on main).

### 5. Link the code entities (never edit entity pages)

For each code entity (package, app, dependency) the source touches, add a `[[entities/<prefix>_<name>]]` wikilink under the source summary's `## Touches` section. Entity pages are scanner-owned and live under `entities/` — **do not edit them**. The scanner regenerates each entity's `## Referenced in wiki` section from these forward-links on the next `/graph-works:scan`. Set the source page's `entity_uri:` frontmatter to the primary/canonical entity's URI from `entity_match.uri` in the brief (or `null` if none).

### 6. Update / create concept pages

For each cross-cutting concept mentioned:
- If a page exists: update `## Key claims` or `## Used in`; add to `## Sources`
- If not: create a stub concept page with the minimum (definition, one cited claim, link back to this source)

### 7. ADR capture (if applicable)

If the source proposes or documents a decision, the ADR must have appeared in
step 3's "New pages" list — consent comes from that single confirmation, not a
separate ask here. If confirmed: get the next ADR number (scan existing
`adrs/*.md` for highest `adr_id`), create the ADR using the template, and link
from the source page and from touched concept pages. If the user
declined it in step 3, file it to the proposals ledger via `gw wiki proposal file`
(see step 3) instead of dropping it.

### 8. Flag contradictions explicitly

If the source contradicts an existing wiki page OR current code, add a callout to BOTH the wiki page and (if code) note the code path:

```markdown
> ⚠️ **Contradiction** — [[sources/2026-04-auth-migration-spec]] claims
> `session.session_id` is preserved, but `packages/common-context-node-ts/src/globalContext.ts:23`
> defines it as required. Unresolved as of 2026-04-20.
```

Log contradictions in `log.md` with `op: note`.

### 9. Update concept pages (optional)

If the source meaningfully shifts a high-level synthesis, revise the relevant `concepts/` page (use `kind: architecture` for system-level syntheses, `kind: pattern` for reusable patterns, or omit `kind` for general concepts). On `kind: architecture` pages, revise the "Thesis" paragraph and append a dated entry under "How this synthesis has changed"; on other concept pages, update the relevant sections in place. Don't rewrite history; append.

### 10. Update `index.md`

If you edited wiki pages manually, edit the relevant category sections inline. Command-layer ingest/scan flows update indexes automatically.
If you wrote guidance pages manually, also refresh `guidance/index.md` and the affected `guidance/<topic>/index.md` (match the existing auto-generated bullet format).

### 11. Append to `log.md`

Append a `## [YYYY-MM-DD] ingest | <title>` entry with the touched pages.

### 12. Archive the raw source
If the source lives under `<workspace>/raw/` (and not already under `raw/_archive/`), `mkdir -p` the mirrored `_archive` parent and `mv` the source there (`raw/specs/x.md` → `raw/_archive/specs/x.md`). Skill directories move wholesale; a bare `SKILL.md` directly in a kind folder moves alone. Replace an existing destination (re-ingest semantics). Sources outside `raw/` are never touched. A failed move is a warning, not a failed ingest. The source page's `source_path` (step 4) must match this destination.

### 13. Report back to the user

Summary the user sees in chat:
- Source summary page created/updated
- Pages touched (bulleted wikilinks so the user can click through)
- Contradictions flagged (if any)
- ADRs created (if any)
- Suggested next sources to pursue (related PRs, follow-up specs)

## Source-type-specific notes

### Specs / RFCs / design docs
- Likely to produce an ADR. Include it in the step 3 "New pages" enumeration — consent comes from that single confirmation.
- Expect heavy updates to package pages and concept pages (especially `kind: architecture` syntheses).

### PR summaries
- Source type `pr`. Include the PR URL in `source_path` or a `pr_url` frontmatter field.
- Add `[[entities/<prefix>_<name>]]` links under `## Touches` for every package the PR modified.
- If the PR implements an ADR, link both ways.

### Articles (clipped with Obsidian Web Clipper)
- Often produce concept pages, not ADRs.
- May touch no packages if purely informational.
- Good source of comparison material — file as `concepts/<a>-vs-<b>.md`.

### Tickets
- Usually light ingest — a short source summary plus `[[entities/...]]` links for the relevant package entities.
- Multiple related tickets may roll up into a single `sources/` page.

### Transcripts
- Extract decisions (→ ADRs), action items, and technical context.
- Attribute claims to speakers where possible.

### In-repo docs (source_type: doc)
- An in-repo `.md` passed by repo-relative path; the file lives in the repo, not in `raw/`. Not auto-surfaced — point `/graph-works:ingest` at it directly.
- `source_path` is repo-relative (e.g. `docs/architecture.md`). The doc stays canonical — the wiki summary doesn't duplicate it; it cross-references concepts, packages, ADRs, etc. inferred from the doc's content.
- When `state_gate.allowed` is true, set `last_sync_commit` to `state_gate.head_commit` and `last_sync_at` to today; `/graph-works:lint` uses these to flag drift on subsequent runs. Otherwise omit both fields and warn the user that drift detection won't apply until the next clean-on-main ingest.
- Often produce concept pages (with `kind: architecture` for high-level syntheses) or ADRs depending on the doc's content. Treat them like specs/RFCs by default.

### Code examples (source_type: example)
- Source location: `raw/examples/`. The path passed to `/graph-works:ingest` may resolve to a single file or a folder; folder mode is the headline new capability and produces a single source summary (not one per file).
- `ingest_source.py` returns a folder brief (file listing, total size, language guesses, representative-file preview) when `--source` resolves to a directory under `raw/examples/`. Single files behave as today, with `source_type: example`. Caps: warn at >50 files or any file >200 KB; hard error at >200 files (almost certainly the wrong directory).
- `last_sync_commit` and `last_sync_at` are disallowed in frontmatter — examples are external; drift detection does not apply. The state-gate is a no-op for `source_type: example` in the brief output.
- **Step 3 (Discuss)** for examples covers: TL;DR, what patterns the example demonstrates, key takeaways, which existing concept pages map to those patterns, and which code entities the user wants to flag under `## Where this could apply`.
- **Step 5 (Link code entities)** for examples: add `[[entities/<prefix>_<name>]]` wikilinks under `## Touches` for the relevant entities. Do **not** edit entity pages. The scanner owns them and backfills `## Referenced in wiki`.
- **Step 6 (Update / create concept pages)** — if the example demonstrates a reusable pattern, include `concepts/<topic>-pattern.md` in the step 3 New-pages enumeration — consent comes from that single confirmation. Pattern pages use the body template in `page-formats.md` Section 3a; the `pattern` tag is recommended.
- **Step 7 (ADR capture)** is suppressed by default for examples — examples don't represent decisions in this codebase. The ingestor may still include an ADR in the step 3 "New pages" list if the example concretely motivates a decision the user is making *now*, but it should not appear proactively.
- **Step 8 (Contradictions)** still runs — an example can contradict an existing concept page's claim (e.g. "we said pattern X is bad but this example uses it well"). Flag both ways.
- The source summary uses `page-formats.md` Section 4a (example variant): no `## Key claims`, no `## Proposed changes`; instead `Origin / What's in it / Patterns demonstrated / Key takeaways / Where this could apply / Caveats / Related`.
- Each `[[entities/<prefix>_<name>]]` bullet under `## Where this could apply` on the source page is forward-linked; the scanner derives the reciprocal `## Referenced in wiki` backlink on entity pages automatically. Concept pages keep manual reciprocity (add `## Inspirations` bullets there by hand). `/graph-works:lint` cross-checks concept-page reciprocity and warns on drift.
- Frontmatter contract: see `wiki-schema.md` for `origin_url`, `origin_repo`, `license`, `attribution` (`origin_url` or `origin_repo` should be set; lint warns if both are empty).

## Skill → guidance pages

When the brief carries `is_skill: true`, this source is an agent **skill** (behavioral
guidance for an AI coding agent). Route it to this flow instead of the single
source-summary flow above: a skill is broken into one or more **guidance pages** under
`wiki/guidance/<topic>/<slug>.md`, plus one source page that links to them.

### Detection

The brief from `ingest_source.py --json` carries `is_skill: true`, `source_type: skill`,
`included_files` (skill-dir-relative markdown — `SKILL.md` first, then transitively-linked
companions in link order), `excluded_files` (non-markdown files under the skill dir),
`scripts_dominant`, and a `warnings` list. **Read the `included_files` yourself** (Read
tool, skill-dir-relative) before chunking — the brief is a manifest, not the content.

If `scripts_dominant` is true (or `warnings` contains `"scripts_dominant"`), the skill is
mostly non-markdown scripts — a weak guidance candidate. Surface this to the user and ask
whether to proceed before writing pages.

### Chunking rules

Choose the chunking from the content (mirrors the Bedrock skill planner):

- **Rules / atomic directives** — a skill that is a list of independent "do X" / "never Y"
  rules: write ONE guidance page per rule.
- **How-to / instructional flow** — a single coherent procedure or technique: write ONE
  guidance page for the whole skill.
- **Never split tightly-coupled steps** across pages. When in doubt, prefer fewer, larger
  pages over many fragments.
- **Multi-language sources** — when a source covers more than one language (e.g. a checklist
  with separate Python and TypeScript sections), write one single-language page per language
  using the slug convention `<base-slug>-<language>.md` under the same topic folder, each
  stamped with `language: <lowercase-scalar>` in frontmatter and containing only that
  language's content. When there is genuinely shared/cross-language advice, additionally write
  one agnostic page with no `language` key. Omit `language` for single-language or shared
  sources.
- Extract reusable TECHNICAL knowledge; drop skill-harness scaffolding (activation phrases,
  tool-call mechanics, meta-instructions about being a skill).
- Preserve content verbatim where practical — the goal is smaller, targetable chunks, not
  rewrites.
- Infer `topic` from the skill's DOMAIN, not its filename (a React Native skill →
  `react-native`; a brainstorming skill → `brainstorming`). `topic` is a short kebab-case
  slug and becomes the folder under `wiki/guidance/`.

### Guidance page frontmatter (inline schema — no template file needed)

Each guidance page begins with this frontmatter block, then the body. Emit exactly these
keys:

```yaml
---
title: <human-readable page title>
category: guidance          # FIXED — always this literal value
summary: <one-line summary for the wiki spine>
topic: <kebab-case domain slug — the folder under guidance/>
applies_when: <when this guidance applies, one line>
triggers:                   # all sub-keys optional; emit empty lists when no signal
  globs: []
  keywords: []
  entities: []              # [[entities/...]] targets, or []
tags: []                    # optional coarse tags
impact: high                # critical | high | medium | low (lowercase)
source: "[[sources/<YYYY-MM>-<slug>]]"   # the skill's source page (see below)
language:                           # optional — omit for language-agnostic guidance.
                                    # lowercase scalar, e.g. python | typescript | javascript.
updated: <today, YYYY-MM-DD>
tokens: 0
---
```

`category` MUST be the literal `guidance`. `impact` MUST be lowercase and one of
critical/high/medium/low. Use the `suggested_summary_path` from the brief (minus the
`sources/` prefix and `.md` suffix) as the `source:` target. `language` is optional —
omit it for language-agnostic guidance; when set, use a lowercase scalar matching
source-parser language names (e.g. `python`, `typescript`, `javascript`), which lets the
page participate in code-graph/file matching during guidance suggestion.

Body sections:

1. `# <title>`
2. `## Guidance` — the prescriptive content: how to do it correctly and why. No padding, no
   restating the title.
3. `## Incorrect` / `## Correct` — optional code examples, only when they sharpen the point.
4. `## Applies to` — ONLY when `triggers.entities` is non-empty: one `- [[entities/...]]`
   bullet per entity. Omit the section entirely when there are no entities.

### Targets

Write each page to `<workspace>/wiki/guidance/<topic>/<slug>.md`. `<topic>` is the
kebab-case domain folder; `<slug>` is a kebab-case stem derived from the page title. Create
the topic folder if it doesn't exist. On re-ingest, overwrite the page in place.

### Source page

Write one source page at the brief's `suggested_summary_path`
(`<workspace>/wiki/sources/<YYYY-MM>-<slug>.md`) with `source_type: skill`:

- `## Summary` — one or two sentences: the skill was ingested into N guidance page(s).
- `## Generates` — a bullet list of `[[guidance/<topic>/<slug>]]` wikilinks, one per
  guidance page written.
- `## Excluded` — only when the brief's `excluded_files` is non-empty: a bullet list of the
  non-markdown files (as `` `path` ``) that were not ingested.

This matches the Bedrock source-page shape (`## Summary`, `## Generates`, `## Excluded`).

### Entity backlinks

`## Applies to` `[[entities/...]]` links **do** produce entity backlinks: `guidance` is in
the scanner's preserved-wiki-dirs list, so the next `/graph-works:scan` derives the reciprocal
`## Referenced in wiki` entry on each linked entity page from these forward links (the nested
`guidance/<topic>/<slug>` slug is rendered correctly). Write the links — the scanner backfills
the reciprocity, just as it does for source-page `## Touches` links.

## Batch ingest (kind-folder roots)

Pointing `/graph-works:ingest` at a top-level `raw/<kind>` folder ingests every
unit inside, fanned out to concurrent `ingestor` workers, with all NEW curated
pages deferred to the `wiki/proposals/` ledger (same queue and approval flow as
the Bedrock ingest path). Guidance pages are exempt — skill units keep writing
them directly; they are the deliverable.

### Detection & units

The brief carries `is_batch: true`, `kind_folder`, `unit_count`, `total_count`,
`limited`, and `units[]` (`{path, rel, unit_type}`). A batch caps to the first
`--limit N` units (default 10; `--all` ingests every unit), so `unit_count` is
the number of units in this brief, `total_count` is how many were found, and
`limited` is true when `total_count > unit_count`. Flat kinds (`specs`, `articles`, `prs`, `tickets`,
`transcripts`): each file is a unit, recursively. `skills/`: each immediate
subdirectory is a unit (processed exactly like a single skill ingest); a loose
file directly in `raw/skills/` is not a unit — ingest it individually.
`examples/`: each immediate subdirectory plus any loose files. `_archive/`,
`assets/`, and dotfiles are excluded. Any non-kind-folder path falls through to
the single-source flow. An empty folder → "nothing to ingest", stop.

### Orchestrator flow

One upfront confirmation (unit list + "new pages become proposals"), then
autonomous: dispatch `ingestor` workers (≤4 concurrent) with **BATCH MODE**
briefs; each worker writes only its uniquely-owned files (its source page; for
skill units the guidance pages + the `## Generates` page) and returns the fenced
JSON report defined in `agents/ingestor.md` → "Batch mode". Workers never touch
`concepts/`, `adrs/`, `proposals/`, `index.md`, `log.md`, or
the guidance indexes, and never archive.

The worker/orchestrator split exists because concurrent workers would collide
on shared files — `index.md`, `log.md`, and especially the ledger:
`upsert_proposal` merges multiple sources proposing the same target into one
note's `origins[]`, which only works if writes are serialized.

### Serial commit phase

After the workers return, commit each successful unit in unit order: file each
reported proposal via `gw wiki proposal file` (duplicate targets across
units merge origins — existing ledger behavior); apply the reported
`existing_page_updates[]` (contradiction callouts on shared pages arrive inside
these; `contradictions[]` is summary-only — surface it in the final report,
don't apply it); update `index.md` (and the guidance indexes for skill units);
append one `## [YYYY-MM-DD] ingest | <title>` log entry per unit; archive the
unit to `raw/_archive/<same relative path>`.

A failed unit (worker crash, malformed report) gets none of this — its source
stays in `raw/` (inbox semantics: still un-ingested, re-runnable) and is listed
in the final report. One failure does not stop the batch.

The iron rules hold per unit: ≥3 file touches (source page + index + log) split
between worker and commit phase; `raw/` contents are never edited, only
archived; existing per-folder file-count warnings for examples folders still
apply inside their units.

## Future formats

Today, in-repo doc ingest is limited to `.md` files passed by path. Other formats are deferred:

- **`.pdf`** — needs a parser (or rely on the LLM's PDF Read support).
- **`.docx` / `.odt`** — needs a parser.
- **`.html` / `.htm`** — `ingest_source.py` already handles these for `raw/` inputs; the scanner doesn't auto-surface them yet.
- **`.txt` / `.rst` / other markup** — same pattern; supported via direct `/graph-works:ingest <path>`, not auto-surfaced.

Manual ingest (passing the path to `/graph-works:ingest` directly) works today for any format `ingest_source.py` understands. The scanner's auto-discovery is intentionally md-only until the broader format support lands.

## After-ingest tips

- **Big ingest?** Run `gw wiki lint` to check for new orphans or broken links.
- **New ADR?** Run `/graph-works:lint` to check the ADR chain (supersedes / superseded_by).
- **Graph check?** Run `gw wiki stats` to see if the new page is well-connected.
- **Open Obsidian graph view** — the user should see the new page attached to the relevant cluster.
