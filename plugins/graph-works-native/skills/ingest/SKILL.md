---
name: ingest
description: Use when the user says "ingest this", "add this spec/article/PR to the wiki", or invokes /gw:ingest <path>. Reads a source file from any path, proposes TL;DR and key claims, identifies which code entities and pages will be touched, flags contradictions with wiki or code, proposes ADRs when decisions are captured, and — after user confirmation — writes the source summary, links the relevant code entities via root-absolute markdown links, updates explanation/reference/ADR pages, regenerates the index, and logs the ingest.
---

# Ingest a source into the wiki

Ingest a new source (spec, PR, article, ticket, transcript) into the Code Wiki.

The flow: `gw ingest --source <path> --json` returns a brief (title guess, preview, suggested summary path, entity match) and writes nothing → read the source → discuss TL;DR and key claims with you → write a source summary → link relevant code entities via root-absolute markdown links and update explanation/reference/ADR pages → propose an ADR if the source captures a decision → flag contradictions → update `index.md` → append to `log.md`.

A typical ingest touches **5-15 vault pages**. You're in the loop.

## Usage

```
/gw:ingest <path>          # Claude Code
$ingest <path>                      # Codex
/gw:ingest ~/Downloads/auth-migration.md
/gw:ingest ~/Downloads/2026-04-react-19-blog.md
/gw:ingest ~/Downloads/842-healthkit-retry.md
/gw:ingest ~/Downloads/2026-04-arch-review.md
```

## Dispatch

Prefer running this skill's body in a forked sub-agent with the tool set
`Read, Write, Edit, Bash, Grep, Glob`. A typical ingest touches 5–15 vault
pages and reads the source in full; its output would flood a caller's context
if run inline.

On a harness without sub-agent dispatch, run it inline in the current
context — the body below is written to work either way.

## Role

You integrate a new source (spec, PR, article, ticket, transcript) into the `<workspace>/okf/` layer — writing a source summary, linking the relevant code entities via root-absolute markdown links, and updating explanation/reference/ADR pages — never editing entity pages (the scanner owns them); proposing ADRs for decisions; flagging contradictions with the code; updating the index and log. Spawned per-ingest.

## Inputs

- Path to a source file. Any filesystem path, or repo-relative for an in-repo doc (e.g. `docs/architecture.md`) passed directly to `/gw:ingest`.
- The current state of `<workspace>/okf/` (especially `index.md`)
- The repo's code (for contradiction checks)
- The bundle's `AGENTS.md` schema (`okf/AGENTS.md`) and `.gw/schema/Source.schema.json`

## Source kinds

`gw ingest` classifies the source kind from its content, not from a folder path. `source_kind` is a closed enum — see `.gw/schema/Source.schema.json` for the authoritative list: `spec`, `article`, `ticket`, `skill`, `doc`, `transcript`, `code-review`. (`skill` classifies but does not yet route to a guidance-page flow — see `work/epic-guidance-okf-port`.)

| Source kind | Typical touches |
|---|---|
| `spec` | Entity links + `explanations/`/`references/` pages, or an ADR |
| `article` | Explanation or reference pages |
| `ticket` | Source summary; light entity touches |
| `transcript` | ADRs + entity links for relevant packages |
| `code-review` | Entity links for every package the review touched |
| `doc` | An in-repo `.md`, ingested by repo-relative path |

## Workflow

Follow `../graph-works/references/ingest-workflow.md`. Summary:

### 1. Prep
```bash
gw ingest --source <path> --json
```

Returns these fields on `DocumentBrief` — `source_path`, `title`, `source_kind`, `slug`, `preview`, `word_count`, `binary`, `suggested_summary_path`, `merge_mode`, `entity_match` (`{uri, entity_filename}`), and `state_gate` (`{allowed, reason, head_commit}`, or `null` when no repo git state applies) — and writes nothing. (Workspace and repo are resolved by `gw`. Works for any filesystem path, staged or in-repo.)

### 2. Read the source
Use Read directly. PDF support for `.pdf`; vision for images. For a source read from inside the repo (`source_kind: doc`), the brief's `state_gate` object says whether the working tree was clean on the tracked branch at ingest time — use `state_gate.allowed`/`state_gate.reason` if you need to tell the user why drift-sensitive claims should be treated cautiously; there is no frontmatter field to stamp with it (see step 4).

### 3. Discuss (user in the loop)
Before writing:
- Title, authors, date, source kind
- 2-3 sentence TL;DR
- Key claims (3-7 bullets)
- **Which code entities and pages you'll touch** — bulleted root-absolute markdown links
- **Any contradictions** — with other wiki pages OR with current code (spot-check the files the source mentions)
- Whether this source captures a decision worth an ADR
- **New pages** — REQUIRED enumeration: every NEW page this ingest would create
  (explanation/reference stubs, ADRs), one bullet each, e.g.
  `- NEW explanations/<slug>.md — <one-line justification>`. If none, state
  "New pages: none." Your single confirmation covers exactly this list — never
  create a page that was not enumerated.

**Wait for confirmation before writing.**

If the user declines specific pages from the list, do not drop them — file each
declined page to the proposals ledger instead:

```bash
gw wiki proposal file \
  --lane <explanation|reference|how-to|tutorial|adr> --title "<title>" \
  --id "<YYYY-MM>-<slug>" --resource "sources/<YYYY-MM>-<slug>.md" \
  --rationale "<why>" --evidence "<claim>" [--evidence "<claim>" ...]
```

### 4. Write the source summary
`<workspace>/okf/sources/<YYYY-MM>-<slug>.md`. Required frontmatter per `.gw/schema/Source.schema.json`: `type: Source`, `title`, `description`, `source_path`. Also set `source_kind` (the classification — see below) and the `ingested` date. `source_path` always records the copy destination `sources/references/<YYYY-MM>-<slug>.<ext>` (step 12 copies the file there) — there is no in-repo-doc exception. Also set `origin:` frontmatter to the resolved absolute path of the original source file — this is what the autonomous pipeline stamps, and what future re-ingest duplicate-detection relies on.

`source_kind` is a closed enum: `spec`, `article`, `ticket`, `skill`, `doc`, `transcript`, `code-review`. `gw ingest` classifies from the source's content, not from a folder path. For in-repo docs and loose files, classify from the document's content; default to `doc` for in-repo docs. A PR or review-style source classifies as `code-review`.

Required section headings per `.gw/sections/Source.yaml`: `## TL;DR`, `## Key claims`, `## Touches`, `## Evidence / rationale`, `## Surprises / contradictions`, `## Decisions triggered`, `## Where it's cited in this wiki`. Additional sections are allowed.

Merge mode (page exists, per the brief's `merge_mode: true`): append a dated `## Re-ingest <date>` section at the bottom rather than overwriting the original summary.

### 5. Link the code entities (never edit entity pages)
For each code entity (package, app, dependency) the source touches, add a root-absolute markdown link — `[<name>](/repositories/<repo>/<kind-folder>/<name>.md)` — under the source summary's `## Touches` section. Entity pages are scanner-owned — **do not edit them**. The scanner regenerates each entity's reciprocal reference from these forward-links on the next `/gw:scan`. Set the source page's `entity_uri:` frontmatter to the primary/canonical entity's URI from `entity_match.uri` in the brief (or `null` if none).

### 6. Update explanation / reference / dependency pages
For each cross-cutting idea the source mentions: update the relevant `explanations/` page's claims (why-shaped syntheses and reusable patterns both live there) or the relevant `references/` page's facts (what-is-true-of-a-thing), add a citation, or create a stub page in the appropriate Diátaxis lane — see `okf/AGENTS.md`'s lane table for the full set (`explanations/`, `references/`, `how-tos/`, `tutorials/`). Dependency pages are graph-derived at `dependencies/<ecosystem>/*` and are scanner-owned — never hand-edited.

### 7. Capture ADRs for decisions
If the source proposes or documents a decision, the ADR must have appeared in
step 3's "New pages" list — consent comes from that single confirmation, not a
separate ask here. If confirmed: get the next ID, use the ADR template, link
both ways. If the user declined it, file it to the ledger via `gw wiki proposal file`
(see step 3) instead of dropping it.

### 8. Flag contradictions
Two kinds:
- **Vault↔vault** — add `> ⚠️ Contradiction:` callouts to both pages
- **Vault↔code** — note the code path and the conflicting vault claim

### 9. Update explanation/reference pages (optional)
If the source shifts a high-level synthesis, revise the relevant `explanations/` or `references/` page and append a dated note under an additional section (e.g. `## Changelog`) — `additional_sections: true` on both schemas permits this. Don't rewrite history; append.

### 10. Update index
Command-layer ingest/scan flows update indexes automatically. If you edited wiki pages by hand, reconcile with `gw wiki index` rather than editing `index.md` yourself.

### 11. Log
`gw util log` appends a `## [YYYY-MM-DD] ingest | <title>` entry to `log.md` with the touched pages and notable contradictions.

### 12. Copy the source material
The CLI's `claude_code` backend (the default) computes a brief and writes nothing — under it, you perform this copy yourself: copy the source material to `<workspace>/okf/sources/references/<YYYY-MM>-<slug>.<ext>` (Bash `cp`, or the Write tool for text). The original file, wherever it lives, is never moved or edited. If a copy already exists at that destination, replace it (re-ingest semantics; old versions are recoverable via workspace git). The source page's `source_path` frontmatter (step 4) must equal this copy destination.

### 13. Report
Bulleted markdown links to every touched page, plus contradictions flagged and ADRs created.

## Rules

- The source may be any filesystem path — there is no staging layer it must live inside
- Source file contents are never edited — the ingestor copies the material to `<workspace>/okf/sources/references/`, leaving the original untouched
- If a summary page exists, enters **merge mode** (appends a re-ingest section)
- **Links are root-absolute markdown links, not wikilinks.** `okf_io.LinkGraph` parses markdown links (`[text](/path.md)`) and cannot see a `[[wikilink]]` at all — see `okf/AGENTS.md`'s iron rule 2.
- **In-repo docs are also read-only.** The doc lives in the repo and the LLM never edits it through this skill — the canonical version stays where it is.
- **Code is the source of truth.** Vault↔code contradictions get flagged; vault gets updated, not code.
- **Discuss before writing.**
- **Minimum 3 file touches per ingest** (source summary + index + log).
- **Cite aggressively.** Every claim on an explanation/reference page links to a source page or a code path.
- **Entity pages are scanner-owned.** Add root-absolute markdown links under `## Touches` on the source page; never edit entity pages.
- **Flag contradictions** on both sides.
- **Propose ADRs** for captured decisions — don't just bury them in a source summary.
- **Md only for now.** PDF/DOCX/HTML auto-discovery is deferred. Direct `/gw:ingest <path>` works for any format `gw ingest` understands.

## Red flags

Stop and ask before proceeding if:
- The source is somewhere unexpected — not a readable file or directory the user named
- The source appears to duplicate an existing source exactly
- Ingesting would require deleting existing vault pages
- You detect >5 contradictions with the code (likely major drift — worth a separate conversation)

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/ingest-workflow.md`
