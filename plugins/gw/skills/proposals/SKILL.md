---
name: proposals
description: Invoked explicitly as /gw:proposals [target...]. Reviews and disposes of curated-page proposals in okf/proposals/ — accept, reject, or supersede. Approving only flips a note's page_status and appends a verified entry; this skill then fans out one subagent per accepted proposal to author the destination page, flips notes to created, regenerates indexes, and archives. Mutates the workspace; explicit invocation only.
---

# Dispose of curated-page proposals

Dispose of the proposals the ingest pipeline and the drift producer drop into
`<workspace>/okf/proposals/`.

## Usage

```
/gw:proposals          # Claude Code
$proposals                      # Codex
/gw:proposals adrs/0013-command-modules-are-libraries.md explanations/byte-fidelity.md
```

Without arguments: review every open (`page_status: proposed`) note. With
arguments: just those. **An argument is a proposal's `target` path**, not the
note's filename — that is what `approve` and `reject` resolve on. If the user
gives you a filename, resolve it through `gw wiki proposals --json` and use the
matching record's `target`.

## Critical context

**`gw wiki proposal approve` does NOT write the page.** It flips the note's
`page_status` to `approved` and appends one `verified[]` entry. Authoring the
destination page is a separate step you drive by **fanning out one subagent per
page, in parallel**. There is no `promote` command.

A note carries `target`, `page_status` and `sources[]`. The destination is
whatever `target` says.

## What happens

1. `export GRAPH_WORKS_DIR=<workspace>`; list notes with `gw wiki proposals`
   (`--json` for sources, rationale and evidence).
2. For each note, verify its claim against ground truth — the code, and every
   `sources[].resource` — and present a per-proposal recommendation.
   Disposition is the user's call.
3. **Accept** → `gw wiki proposal approve <target>`.
   **Reject** → `gw wiki proposal reject <target>` (preserved; never
   re-proposed).
4. For each accepted proposal, assign cross-page identity (any ADR number,
   any supersedes link) centrally, then **dispatch one subagent per page in a
   single message** to author the page at its recorded `target` and flip its
   own note to `page_status: created`.
5. Wire supersession on both pages, and mark the superseded page, yourself.
6. `gw wiki index`, then `gw wiki lint` to verify, then `gw wiki archive
   --dry-run` **before** `gw wiki archive` — the sweep takes `approved` notes
   too.

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/proposal-disposition.md`
→ `../graph-works/references/page-formats.md`
