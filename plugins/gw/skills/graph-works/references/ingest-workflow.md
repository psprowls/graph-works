# Ingest Workflow

The detailed flow the LLM follows when the user runs `/gw:ingest <path>`.

Sources in a graph-works bundle are one of the `source_kind` enum's seven values (`.gw/schema/Source.schema.json`): **spec**, **article**, **ticket**, **skill**, **doc**, **transcript**, **code-review**. The ingest flow is the same for all — only the summary's framing changes. (`skill` classifies but has no guidance-page flow of its own; it ingests like any other source.)

## Source locations

Sources are read from any filesystem path:

- **Staged or ad hoc material** (clipped articles, specs, PRs, transcripts, or anything else you point `/gw:ingest` at) — `gw ingest --source <path>` reads the file directly, wherever it lives; file contents are never edited. A copy of the material lands at `<workspace>/okf/sources/references/<YYYY-MM>-<slug>.<ext>`; the original is never moved. There is no staging inbox.
- **`<repo>/<...>.md`** (in-repo docs) — any `.md` that resolves under the repo but outside the bundle. Pass the repo-relative path straight to `/gw:ingest`. The summary records `source_path` as the reference copy's destination (same as any other source — no in-repo-doc exception) and classifies as `source_kind: doc`. The doc itself stays in the repo — the bundle does not duplicate it.

## Inputs

- Path to a source file. Any filesystem path, or repo-relative for in-repo docs.
- The current state of `<workspace>/okf/` (especially `index.md`, relevant `repositories/`, `explanations/`, `references/`)

## Step-by-step

### 1. Prepare the brief

Run `gw ingest --source <path> --json` (wiki and repo discovered automatically via `gw`). With no `--backend` flag and no `roles.ingestor.backend` workspace override, this returns `DocumentBrief`'s fields and writes nothing:
- `source_path` — the source file's path, as given
- `title` — title guess
- `source_kind` — the classification (see below)
- `slug` — the derived slug
- `preview` — first 1200 characters
- `word_count`
- `binary` — whether the source is binary
- `suggested_summary_path` (`<workspace>/okf/sources/<YYYY-MM>-<slug>.md`)
- `merge_mode` — whether a summary page already exists at that path
- `entity_match` — `{ uri: <str|null>, entity_filename: <str|null> }` — the best-matching entity for this source (used to populate `entity_uri:` frontmatter); `null` when no match is found
- `state_gate` — `{ allowed, reason, head_commit }` or `null` — informational only; there is no frontmatter field this stamps (see step 4)

Pass `--backend bedrock` or `--backend vercel` to opt back into the old always-write autonomous pipeline instead of this brief-then-discuss flow.

### 2. Read the source

Use the Read tool on the source directly. For PDFs, use Read's PDF support. For images, inspect them if the LLM has vision.

### 3. Discuss with the user

Before writing anything, tell the user:
- Title, authors, date, source kind
- 2-3 sentence TL;DR
- Key claims (bulleted, 3-7 items)
- **Which code entities and pages this source touches** — bulleted root-absolute markdown links
- Any **contradictions** with existing pages or with current code
- Whether this source proposes a decision worth capturing as an ADR
- **New pages** — REQUIRED enumeration: every NEW page this ingest would create
  (explanation/reference stubs, ADRs), one bullet each, e.g.
  `- NEW explanations/<slug>.md — <one-line justification>`. If none, state
  "New pages: none." Your single confirmation covers exactly this list — never
  create a page that was not enumerated.

**Wait for user to confirm or redirect.** The user is in the loop — the ingestor proposes, the user approves.

If the user declines specific pages from the list, do not drop them — file each
declined page to the proposals ledger instead:

```bash
gw wiki proposal file --lane <explanation|reference|how-to|tutorial|adr> --title "<title>" \
  --id "<YYYY-MM>-<slug>" --resource "sources/<YYYY-MM>-<slug>.md" \
  --rationale "<why>" --evidence "<claim>" [--evidence "<claim>" ...]
```

`--lane`, `--title`, `--id` and `--resource` are all required. There is no `--target-slug`: the
target is derived from the lane and the title, so one page proposed twice under the same title
merges rather than forking. `--id` and `--resource` identify the **source making the argument** —
the source page's slug and its bundle-relative path — and are what a re-fired proposal merges on.

### 4. Create / merge the source summary page

Path: `<workspace>/okf/sources/<YYYY-MM>-<slug>.md`. Required frontmatter per `.gw/schema/Source.schema.json`: `type: Source`, `title`, `description`, `source_path`, plus `source_kind` (the classification) and the `ingested` date. Set `source_path` to the copy destination `sources/references/<YYYY-MM>-<slug>.<ext>` (step 12 copies the file there) — this is unconditional, with no in-repo-doc exception. Also set `origin:` frontmatter to the resolved absolute path of the original source file — this is what the autonomous pipeline stamps, and what future re-ingest duplicate-detection relies on. Required section headings per `.gw/sections/Source.yaml`: `## TL;DR`, `## Key claims`, `## Touches`, `## Evidence / rationale`, `## Surprises / contradictions`, `## Decisions triggered`, `## Where it's cited in this wiki`.

**Merge mode** (brief reports `merge_mode: true`): append a new `## Re-ingest <date>` section at the bottom with what changed. Do not overwrite the original summary.

### 5. Link the code entities (never edit entity pages)

For each code entity (package, app, dependency) the source touches, add a root-absolute markdown link — `[<name>](/repositories/<repo>/<kind-folder>/<name>.md)` — under the source summary's `## Touches` section. Entity pages are scanner-owned — **do not edit them**. The scanner regenerates each entity's reciprocal reference from these forward-links on the next `/gw:scan`. Set the source page's `entity_uri:` frontmatter to the primary/canonical entity's URI from `entity_match.uri` in the brief (or `null` if none).

### 6. Update / create explanation and reference pages

For each cross-cutting idea mentioned:
- If a page exists: update its claims section, or add to its citations
- If not: create a stub page in the appropriate Diátaxis lane (`explanations/` for why-shaped syntheses and patterns, `references/` for what-is-true-of-a-thing facts — see `okf/AGENTS.md`'s lane table) with the minimum (definition, one cited claim, link back to this source)

### 7. ADR capture (if applicable)

If the source proposes or documents a decision, the ADR must have appeared in
step 3's "New pages" list — consent comes from that single confirmation, not a
separate ask here. If confirmed: get the next ADR number (scan existing
`adrs/*.md` for the highest existing number), create the ADR using the template, and link
from the source page and from touched explanation/reference pages. If the user
declined it in step 3, file it to the proposals ledger via `gw wiki proposal file`
(see step 3) instead of dropping it.

### 8. Flag contradictions explicitly

If the source contradicts an existing wiki page OR current code, add a callout to BOTH the wiki page and (if code) note the code path:

```markdown
> ⚠️ **Contradiction** — [Auth migration spec](/sources/2026-04-auth-migration-spec.md) claims
> `session.session_id` is preserved, but `packages/common-context-node-ts/src/globalContext.ts:23`
> defines it as required. Unresolved as of 2026-04-20.
```

Log contradictions in `log.md` with `op: note`.

### 9. Update explanation/reference pages (optional)

If the source meaningfully shifts a high-level synthesis, revise the relevant `explanations/` or `references/` page. Append a dated note under an additional section (e.g. `## Changelog`) — both schemas allow additional sections beyond their required ones. Don't rewrite history; append.

### 10. Update `index.md`

Command-layer ingest/scan flows update indexes automatically. If you edited pages by hand, reconcile with `gw wiki index` rather than editing `index.md` yourself.

### 11. Append to `log.md`

`gw util log` appends a `## [YYYY-MM-DD] ingest | <title>` entry with the touched pages.

### 12. Copy the source material

The default `claude_code` backend writes nothing — perform this copy yourself: copy the source material to `<workspace>/okf/sources/references/<YYYY-MM>-<slug>.<ext>` (Bash `cp`, or the Write tool for text). The original file, wherever it lives, is never moved or edited. If a copy already exists at that destination, replace it (re-ingest semantics). The source page's `source_path` (step 4) must match this destination.

### 13. Report back to the user

Summary the user sees in chat:
- Source summary page created/updated
- Pages touched (bulleted markdown links so the user can click through)
- Contradictions flagged (if any)
- ADRs created (if any)
- Suggested next sources to pursue (related PRs, follow-up specs)

## Source-kind-specific notes

### Specs
- Likely to produce an ADR. Include it in the step 3 "New pages" enumeration — consent comes from that single confirmation.
- Expect heavy updates to package pages and explanation pages (especially system-level syntheses).

### Code reviews (PR summaries)
- `source_kind: code-review`. Include the PR URL in `source_path` or a `pr_url` frontmatter field.
- Add root-absolute markdown links under `## Touches` for every package the PR modified.
- If the PR implements an ADR, link both ways.

### Articles
- Often produce explanation pages, not ADRs.
- May touch no packages if purely informational.
- Good source of comparison material — file as `explanations/<a>-vs-<b>.md`.

### Tickets
- Usually light ingest — a short source summary plus root-absolute markdown links for the relevant package entities.
- Multiple related tickets may roll up into a single `sources/` page.

### Transcripts
- Extract decisions (→ ADRs), action items, and technical context.
- Attribute claims to speakers where possible.

### In-repo docs (source_kind: doc)
- An in-repo `.md` passed by repo-relative path; the file lives in the repo. Not auto-surfaced — point `/gw:ingest` at it directly.
- `source_path` is still the reference copy's destination, same as any other source — there is no in-repo-doc exception. The doc stays canonical — the summary doesn't duplicate it; it cross-references explanations, packages, ADRs, etc. inferred from the doc's content.
- Often produces explanation pages (for high-level syntheses) or ADRs depending on the doc's content. Treat like a spec by default.

## Formats

In-repo doc ingest handles `.md` files passed by path. Passing a path to `/gw:ingest` directly works for any format `gw ingest` understands; `.txt`, `.rst` and other markup go through that route and are not auto-surfaced. `.pdf`, `.docx` and `.odt` need a parser and are not supported.

## After-ingest tips

- **Big ingest?** Run `gw wiki lint` to check for new orphans or broken links.
- **New ADR?** Run `/gw:lint` to check the ADR chain (supersedes / superseded_by).
- **Graph check?** Run `gw wiki stats` to see if the new page is well-connected.
