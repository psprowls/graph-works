---
name: log
description: Show recent entries from the Code Wiki log (<workspace>/wiki/log.md). Uses the standardized ## [YYYY-MM-DD] header format so grep + tail works. Usage /graph-works:log [--last N] [--op scan|ingest|query|lint|...]
---

# /graph-works:log

Show recent entries from `<workspace>/wiki/log.md`. Every LLM operation on the wiki leaves a standardized entry:

```
## [YYYY-MM-DD] <op> | <title>
<optional detail>
```

## Usage

```
/graph-works:log                          # last 10 entries
/graph-works:log --last 20
/graph-works:log --op scan --last 10      # only scan entries
/graph-works:log --op ingest              # recent ingests
/graph-works:log --since 2026-04-01
```

## What it does

Parses `<workspace>/wiki/log.md` and prints matching entries. Essentially:

```bash
grep "^## \[" <workspace>/wiki/log.md | tail -N
```

…plus optional filters for op type and date range.

## Valid ops

- `scan` — a `/graph-works:scan` pass ran
- `ingest` — a source was read and integrated
- `query` — a question was answered (when filed back)
- `lint` — a health check ran
- `create` — a new page was created outside an ingest
- `update` — a page was updated outside an ingest
- `delete` — a page was removed
- `note` — freeform note (contradictions flagged, thesis revisions)

## Example output

```
## [2026-04-20] lint | weekly health check
Code drift: 2 new packages un-documented. 3 orphans, 1 stale roadmap page.

## [2026-04-20] ingest | Auth Migration Spec
Added sources/2026-04-auth-migration-spec.md. Updated concepts/global-context,
entities/pkg_shared-aws-node-ts, adrs/0014-jwt-sessions (new).

## [2026-04-19] scan | detected 3 new packages
Added entities/pkg_timeline-native-ts, entities/pkg_timeline-data-node-ts, entities/pkg_timeline-domain-ts.
```

## Skill Reference

→ `graph-works/SKILL.md`
