---
name: ingest
description: Ingest a source file from raw/ into the Code Wiki — read, discuss, write summary, link relevant code entities via [[entities/...]] and update concept/ADR pages, propose ADRs if decisions are captured, flag contradictions with code, update index, append to log. Usage /graph-works:ingest <path-to-source>
---

# /graph-works:ingest

Ingest a new source (spec, PR, article, ticket, transcript) into the Code Wiki.

The flow: read the source → discuss TL;DR and key claims with you → write a source summary → link relevant code entities via `[[entities/...]]` and update concept/ADR pages → propose an ADR if the source captures a decision → flag contradictions → update `index.md` → append to `log.md`.

A typical ingest touches **5-15 vault pages**. You're in the loop.

## Usage

```
/graph-works:ingest <path>
/graph-works:ingest raw/specs/auth-migration.md
/graph-works:ingest raw/articles/2026-04-react-19-blog.md
/graph-works:ingest raw/prs/842-healthkit-retry.md
/graph-works:ingest raw/transcripts/2026-04-arch-review.md
/graph-works:ingest raw/examples/expo-tanstack-query/   # folder ingest
/graph-works:ingest raw/specs/                           # batch: whole kind folder
```

## Source types

The script guesses the source type from the raw/ subdirectory. Supported:

| Path | Source type | Typical touches |
|---|---|---|
| `raw/specs/` | `spec` | `[[entities/...]]` links + concept pages (`kind: architecture`) / ADR pages |
| `raw/articles/` | `article` | Concept/dependency pages |
| `raw/prs/` | `pr` | `[[entities/...]]` links for every package modified |
| `raw/tickets/` | `ticket` | Source summary; light `[[entities/...]]` touches |
| `raw/transcripts/` | `transcript` | ADRs + `[[entities/...]]` links for relevant packages |
| `raw/examples/` | `example` | Concept pages (often pattern-flavored); `[[entities/...]]` `## Inspirations` bullets |
| skill dir (`SKILL.md`) | `skill` | Guidance pages under `guidance/<topic>/`; a `## Generates` source page |

## Batch mode

Pointing the command at a **top-level kind folder** ingests everything inside:
`raw/specs/`, `raw/articles/`, `raw/prs/`, `raw/tickets/`, `raw/transcripts/`,
`raw/examples/`, `raw/skills/`. A directory argument means batch; a file
argument means one unit. **You enumerate the units** — flat kinds: one unit per
file (recursive); `skills/`: one per immediate subdirectory (a loose file
directly in `raw/skills/` is NOT a unit — ingest it individually); `examples/`:
one per immediate subdirectory plus loose files; `_archive/`, `assets/`, and
dotfiles are excluded. Resolve `raw/<kind>` to an **absolute path** against the
workspace, not the repo cwd.

**Limit (default 10):** a batch ingests at most the first **10** units (by path
sort) unless told otherwise. `--limit N` and `--all` are arguments to *this
command* — `/graph-works:ingest <kind> --all` — and you apply them yourself when
you enumerate the units. `gw ingest` takes one `--source` and knows nothing about
batches; there is no prep script and no `--limit` to pass through to it. Report
`unit_count` (units to ingest), `total_count` (units found), and whether
truncation happened. `--all` overrides `--limit` when both are given.

1. **Detect + one confirm** — enumerate the units. If `total_count` is 0: report
   "nothing to ingest" and stop. Otherwise show the unit list and ask ONCE:
   - if truncated: _"raw/<kind>: <total_count> units found, ingesting
     first <unit_count> (pass `--all` for everything). NEW concept/ADR pages
     become proposals in `wiki/proposals/`, not real pages. Proceed?"_
   - else: _"raw/<kind>: <unit_count> units. Will ingest all; NEW concept/ADR
     pages become proposals in `wiki/proposals/`, not real pages. Proceed?"_

   After the go-ahead, run autonomously — no further questions.
2. **Fan out** — dispatch one `ingestor` sub-agent per unit, **at most 4
   concurrent**. Each dispatch prompt starts with **BATCH MODE** and includes
   the unit path, the workspace path, and the unit type. Workers follow the
   "Batch mode" contract in `agents/ingestor.md` and return a fenced JSON
   report. A worker that crashes, returns no parseable report, or reports
   `"status": "failed"` marks its unit **failed**; the batch continues.
3. **Serial commit phase** — for each successful unit, in unit order:
   - file each `proposals[]` entry:
     `gw wiki proposal file --lane <lane> --title "<title>" --id "<the unit report's source_page basename minus .md>" --resource "<the unit report's source_page>" --rationale "<rationale>" --evidence "<bullet>" [--evidence ...]`
     (the target is derived from lane + title, so duplicate targets across units
     merge into one ledger note's `origins[]` on their own)
   - apply the reported `existing_page_updates[]` (contradiction callouts on
     shared pages arrive inside these; `contradictions[]` is summary-only —
     surface it in the final report, don't apply it)
   - update `index.md`; refresh `guidance/index.md` + `guidance/<topic>/index.md`
     when the unit wrote guidance pages
   - append the unit's `log_line` as its own `## [YYYY-MM-DD] ingest | <title>`
     entry in `log.md` (one entry per unit); compose the entry body from the
     report fields — source page, guidance pages, proposal ledger paths,
     contradictions — matching single-mode log entries
   - archive the unit to `raw/_archive/<same relative path>`
   A **failed** unit gets none of this — its source stays in `raw/` (still
   un-ingested, re-runnable).
4. **Report** — source pages written, guidance pages, proposals filed (with
   ledger paths), existing pages updated, contradictions flagged, failed units.

## What happens

1. **Prep** — `gw ingest --json` — metadata, preview, suggested summary path
2. **Read** — reads the source directly
3. **Discuss** — TL;DR, key claims, touched pages, contradictions with vault or code
4. **Confirm** — waits for your go-ahead
5. **Write** — creates the source summary at `<workspace>/wiki/sources/<YYYY-MM>-<slug>.md` (for a **skill** directory: breaks it into guidance pages under `wiki/guidance/<topic>/`, plus a `## Generates` source page — see `references/ingest-workflow.md`)
6. **Link entities** — add `[[entities/...]]` under `## Touches` on the source page; do not edit entity pages (scanner backfills `## Referenced in wiki`)
7. **ADR** — if the source captures a decision, propose creating `<workspace>/wiki/adrs/<NNNN>-<slug>.md`
8. **Contradictions** — flags vault↔vault and vault↔code contradictions
9. **Index** — command-layer ingest updates this automatically; manual plugin edits update relevant sections inline
10. **Log** — append a `## [YYYY-MM-DD] ingest | <title>` entry for manual plugin edits
11. **Archive** — moves the raw source to `raw/_archive/<same relative path>` (skill directories move wholesale; an existing destination is replaced; sources outside `raw/` are never touched) — and the source page's `source_path` frontmatter records that archive destination
12. **Report** — bulleted wikilinks to every touched page

## Sub-agent

Dispatches the `ingestor` sub-agent. See `agents/ingestor.md`.

## Rules

- The source must be inside the wiki's `raw/` layer
- `raw/` file contents are never edited — after a successful ingest the source is moved to `raw/_archive/<same relative path>`, so anything left under `raw/` is un-ingested
- If a summary page exists, enters **merge mode** (appends a re-ingest section)
- Folders under `raw/examples/` are ingested as a single source summary. `ingest_source.py` warns at >50 files, errors at >200 (almost certainly the wrong directory), and warns when any file exceeds 200 KB.
- Batch mode: new curated pages are NEVER created directly — they become `wiki/proposals/` ledger notes; the per-unit ≥3-file-touches rule is split between worker (source page) and commit phase (index + log)

## Skill Reference

→ `graph-works/SKILL.md`
→ `graph-works/references/ingest-workflow.md`
