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
/gw:proposals adrs/0013-command-modules-are-libraries.md docs/explanations/byte-fidelity.md
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
4. For each accepted proposal, assign cross-page identity (an ADR's
   `<decision_date>-<slug>` filename — ADRs are dated, never numbered — and
   any supersedes link) centrally, then **dispatch one subagent per page in a
   single message** to author the page at its recorded `target` and flip its
   own note to `page_status: created`. Each author's brief requires the
   destination page's `about:`, plus `decisions:` on an ADR or `claims:` on an
   explanation, written per `skills/graph-works/references/page-formats.md` →
   **Curated-page claims**, before the note flips. When the note's `sources[]`
   names a Source (`/sources/<slug>.md`), the author keeps the destination
   page's `sources[]` citation of that Source, and **returns** — never
   writes — one `(source, claim ordinal, landed ref)` triple per key claim its
   new entries carry: the Source's path, the claim's ordinal (the position of
   its top-level item under the Source's `## Key claims`, numbered from 1),
   and `/<target>#<entry id>`. Authors must not touch the Source: parallel
   read-modify-writes of one file lose entries, and two authors landing the
   same claim would each append it, leaving a duplicate ordinal that
   `gw wiki lint` reports as `sources.drain-invalid`.
5. After every author returns, write each touched Source's `drain:` ledger
   yourself, once, after the fan-out: one `{claim: <n>, landed: [...]}` entry
   per ordinal, merging every ref returned for that ordinal into its one
   `landed` list. Never add a second entry for an ordinal: one already in the
   ledger as `landed` gets the new refs appended to its list; one already
   `dropped` stays as is unless the user says otherwise.
6. Wire supersession on both pages, and mark the superseded page, yourself.
7. `gw wiki index`, then `gw wiki lint` to verify — it reports a missing
   `about:`/`decisions:`/`claims:` field at error — then `gw wiki archive
   --dry-run` **before** `gw wiki archive`: the no-target sweep takes
   `approved` notes too, and drained Sources (every key claim dispositioned)
   along with their `sources/references/` copies.

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/proposal-disposition.md`
→ `../graph-works/references/page-formats.md`
