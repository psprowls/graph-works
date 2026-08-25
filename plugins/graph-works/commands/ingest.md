---
name: ingest
description: Ingest a source file from any path into the Code Wiki — read, discuss, write summary, link relevant code entities via root-absolute markdown links and update explanation/reference/ADR pages, propose ADRs if decisions are captured, flag contradictions with code, update index, append to log. Usage /graph-works:ingest <path-to-source>
---

# /graph-works:ingest

Ingest a new source (spec, PR, article, ticket, transcript) into the Code Wiki.

The flow: `gw ingest --source <path> --json` returns a brief (title guess, preview, suggested summary path, entity match) and writes nothing → read the source → discuss TL;DR and key claims with you → write a source summary → link relevant code entities via root-absolute markdown links and update explanation/reference/ADR pages → propose an ADR if the source captures a decision → flag contradictions → update `index.md` → append to `log.md`.

A typical ingest touches **5-15 vault pages**. You're in the loop.

## Usage

```
/graph-works:ingest <path>
/graph-works:ingest ~/Downloads/auth-migration.md
/graph-works:ingest ~/Downloads/2026-04-react-19-blog.md
/graph-works:ingest ~/Downloads/842-healthkit-retry.md
/graph-works:ingest ~/Downloads/2026-04-arch-review.md
```

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

## What happens

1. **Prep** — `gw ingest --source <path> --json` returns a brief (`source_path`, title, source kind, slug, preview, word count, binary, suggested summary path, merge mode, entity match, state gate) and writes nothing
2. **Read** — reads the source directly
3. **Discuss** — TL;DR, key claims, touched pages, contradictions with vault or code
4. **Confirm** — waits for your go-ahead
5. **Write** — creates the source summary at `<workspace>/okf/sources/<YYYY-MM>-<slug>.md`
6. **Link entities** — add root-absolute markdown links under `## Touches` on the source page; do not edit entity pages (the scanner backfills the reciprocal reference on its next run)
7. **ADR** — if the source captures a decision, propose creating `<workspace>/okf/adrs/<NNNN>-<slug>.md`
8. **Contradictions** — flags vault↔vault and vault↔code contradictions
9. **Index** — `gw wiki index` reconciles `index.md` and the affected sub-indexes
10. **Log** — `gw util log` appends a `## [YYYY-MM-DD] ingest | <title>` entry
11. **Copy** — copies the source material to `<workspace>/okf/sources/references/<YYYY-MM>-<slug>.<ext>` (the original file is never moved or edited) — the source page's `source_path` frontmatter records that copy destination
12. **Report** — bulleted markdown links to every touched page

## Sub-agent

Dispatches the `ingestor` sub-agent. See `agents/ingestor.md`.

## Rules

- The source may be any filesystem path — there is no staging layer it must live inside
- Source file contents are never edited — the ingestor copies the material to `<workspace>/okf/sources/references/`, leaving the original untouched
- If a summary page exists, enters **merge mode** (appends a re-ingest section)

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/ingest-workflow.md`
