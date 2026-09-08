---
name: proposals
description: Invoked explicitly as /gw:proposals [slug...]. Reviews and disposes of curated-page proposals in okf/proposals/ — accept, reject, or supersede. Approving only flips a note's status; this skill then fans out one subagent per accepted proposal to author the destination page, flips notes to created, regenerates indexes, and archives. Mutates the workspace; explicit invocation only.
---

# Dispose of curated-page proposals

Dispose of the ADR/concept proposals the ingest pipeline drops into `<workspace>/okf/proposals/`.

## Usage

```
/gw:proposals          # Claude Code
$proposals                      # Codex
/gw:proposals adr-index-repository-grouping concept-subagent-roles
```

Without arguments: review all open (`proposed`) notes. With slug arguments: just those.

## Critical context

**`gw wiki proposal approve` does NOT write the wiki page — it only flips the note's
`status` to `approved`.** Authoring the destination ADR/concept page is a separate step
you drive by **fanning out one subagent per page, in parallel**. See the reference for the
full lifecycle, naming/numbering, supersession wiring, and index regeneration.

## What happens

1. `export GRAPH_WORKS_DIR=<workspace>`; list notes with `gw wiki proposals` (`--json` for evidence).
2. For each note, verify the decision against ground truth (code/sources/conflicts) and present a per-proposal recommendation. Disposition is the user's call.
3. **Accept** → `gw wiki proposal approve <slug>`. **Reject** → `gw wiki proposal reject <slug>` (preserved; never re-proposed).
4. For each accepted proposal, assign destination filename + ADR number + any supersedes links centrally, then **dispatch one subagent per page in a single message** to author it and flip its note to `status: created`.
5. Wire supersession on both pages (and mark the superseded page) yourself.
6. Regenerate indexes (`update_index` one-liner), then `gw wiki lint` to verify, then `gw wiki archive` to sweep spent notes.

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/proposal-disposition.md`
→ `../graph-works/references/page-formats.md`
