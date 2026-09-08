---
name: log
description: Invoked explicitly as /gw:log [--last N] [--op scan|ingest|query|lint|...]. Shows recent entries from the workspace log at <workspace>/okf/log.md, which uses the standardized `## [YYYY-MM-DD] <op> | <title>` header format so grep plus tail works. Explicit invocation only.
---

# Show the wiki log

Show recent entries from `<workspace>/okf/log.md`. Every LLM operation on the wiki leaves a standardized entry:

```
## [YYYY-MM-DD] <op> | <title>
<optional detail>
```

## Usage

```
/gw:log                          # Claude Code — last 10 entries
$log                                       # Codex — last 10 entries
/gw:log --last 20
/gw:log --op scan --last 10      # only scan entries
/gw:log --op ingest              # recent ingests
/gw:log --since 2026-04-01
```

## What it does

Parses `<workspace>/okf/log.md` and prints matching entries. Essentially:

```bash
grep "^## \[" <workspace>/okf/log.md | tail -N
```

…plus optional filters for op type and date range.

## Valid ops

- `scan` — a `/gw:scan` pass ran
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
repositories/my-monorepo/packages/shared-aws-node-ts.md, adrs/0014-jwt-sessions (new).

## [2026-04-19] scan | detected 3 new packages
Added repositories/my-monorepo/packages/timeline-native-ts.md, repositories/my-monorepo/packages/timeline-data-node-ts.md, repositories/my-monorepo/packages/timeline-domain-ts.md.
```

## Reference

→ `../graph-works/SKILL.md`
