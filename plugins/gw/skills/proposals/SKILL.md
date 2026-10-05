---
name: proposals
description: Invoked explicitly as /gw:proposals [target...]. Reviews and disposes of curated-page proposals in okf/proposals/ — accept, reject, or supersede. Approving only flips a note's page_status and appends a verified entry; this skill files work types through gw work file and fans out one subagent per other accepted proposal to author the destination page, flips notes to created, regenerates indexes, and archives. Mutates the workspace; explicit invocation only.
---

# Dispose of curated-page proposals

Dispose of the proposals the ingest pipeline and the drift producer drop into `<workspace>/okf/proposals/`.

## Usage

```
/gw:proposals          # Claude Code
$proposals                      # Codex
/gw:proposals adrs/0013-command-modules-are-libraries.md docs/explanations/byte-fidelity.md
```

Without arguments: review every open (`page_status: proposed`) note. With arguments: just those. **An argument is a proposal's `target` path**, not the note's filename — that is what `approve` and `reject` resolve on. If the user gives you a filename, resolve it through `gw wiki proposals --json` and use the matching record's `target`.

## Critical context

**`gw wiki proposal approve` does NOT write the destination.** It flips the note's `page_status` to `approved` and appends one `verified[]` entry. Work types are filed through `gw work file`; other destination pages are authored by **fanning out one subagent per page, in parallel**. There is no `promote` command.

A note carries `target`, `target_type`, `page_status` and `sources[]`. Fresh filing supports schemas under `.gw/schema/` with `x-okf-accept-proposals: true`; `false` is locked, an absent key is outside the pool, and any other value refuses the type. Before any authoring or filing, read `gw wiki proposals --json`: its resolved `type` is the canonical destination type or null, and `type_refusal` is null or `{path, kind, detail}`. Route on resolved `type`, not raw `target_type`. A legacy note without `target_type` can resolve when exactly one pool type owns its target directory. Stop on a non-null `type_refusal` or null `type`; report locked, refused or unknown types. Only an ambiguity asks the user which kind, then explicitly record the chosen `target_type` on the note before retrying resolution.

## What happens

1. `export GRAPH_WORKS_DIR=<workspace>`; list notes with `gw wiki proposals` (`--json` for sources, rationale and evidence).
2. For each note, verify its claim against ground truth — the code, and every `sources[].resource` — and present a per-proposal recommendation. Disposition is the user's call.
3. **Accept** → `gw wiki proposal approve <target>`. **Reject** → `gw wiki proposal reject <target>` (preserved; never re-proposed).
4. For each accepted proposal with a resolved `type` and no `type_refusal`, inspect `<workspace>/.gw/schema/<type>.schema.json`. **A work type** — one declaring `x-okf-directory: work/` — is filed, not authored: when its `target` is not yet a page, run `gw work file --kind <type> --title "<title>" --summary "<description>" --repo <repo>` (plus `--affects` when the evidence names code), append the note's `sources[]` citation entries with their original `id` and `resource` to the new item's frontmatter `sources:` (these are citations, not owned stage artifacts; never rename them to `design`/`plan` or replace their resources with artifact paths) following `skills/workflow/references/editing-work-items.md`, commit that one path by pathspec, and only then flip the note to `page_status: created` with `target` set to the new item's canonical path plus `.md`. When its `target` already exists, preserve its recorded `target`, append the note's `sources[]` to that item instead of filing a second one, follow the same editing and commit protocol, and then flip the note to `created`. If `gw work file` fails, leave the note `approved` and report the failure.

4b. For every other accepted proposal, prepare each author's brief from the resolved type's `<workspace>/.gw/schema/<type>.schema.json` (including `$ref`/`allOf` and applicable annotations) and `<workspace>/.gw/sections/<type>.yaml` (including referenced fragments). These declarations govern frontmatter and body headings, including the first page of a custom type; an existing page is evidence of style, never a substitute for declarations. If the section declaration is missing, leave the note `approved` and report it; do not invent a template.

4c. Read `x-okf-proposal-promotion`: `dated` defaults to `false`, and an absent block supplies no extra fields. Supply its declared `frontmatter` string values, expanding every `{on}` to the centrally chosen promotion date (`YYYY-MM-DD`), alongside schema-valid type, title, description and provenance. Only a new page with `dated: true` derives `<x-okf-directory><YYYY-MM-DD>-<slug-of-title>.md`; for an existing page or any undated type, preserve the recorded `target` even when its filename differs from the title. Assign the exact final target and any schema-supported supersession links centrally before dispatch. Give each author that path, date, resolved declarations and expanded metadata, then **dispatch one subagent per page in a single message**. Authors write or update that destination first; only after successful writing do they flip their note to `page_status: created`, setting `target` to the final path if a new dated page changed it. A failure leaves the note `approved`.

4d. Write `about:`, `claims:` or `decisions:` only when the applicable declarations mandate them (including `x-okf-about` and its `entries` field); follow `skills/graph-works/references/page-formats.md` → **Curated-page claims** for those declared fields. Do not add unsupported fields to a custom type: for example, a Runbook may require promotion-supplied `service` and forbid `about`. Preserve existing provenance on updates and the note's recorded source citations; carry `sources[]` where the destination schema permits it, and cite recorded resources in prose. When the note names a Source (`/sources/<slug>.md`) and the destination has declared claim/decision entries, keep the destination's `sources[]` citation of that Source and **return** — never write — one `(source, claim ordinal, landed ref)` triple per key claim the new entries carry: the Source's path, the claim's ordinal (the position of its top-level item under the Source's `## Key claims`, numbered from 1), and `/<final target>#<entry id>`. A destination without declared entries returns no fabricated drain refs. Authors must not touch the Source: parallel read-modify-writes of one file lose entries, and two authors landing the same claim would each append it, leaving a duplicate ordinal that `gw wiki lint` reports as `sources.drain-invalid`.
5. After every author returns, write each touched Source's `drain:` ledger yourself, once, after the fan-out: one `{claim: <n>, landed: [...]}` entry per ordinal, merging every ref returned for that ordinal into its one `landed` list. Never add a second entry for an ordinal: one already in the ledger as `landed` gets the new refs appended to its list; one already `dropped` stays as is unless the user says otherwise.
6. Wire schema-supported supersession on both pages, and mark the superseded page, yourself; report unsupported fields rather than adding them.
7. `gw wiki index`, then `gw wiki lint` to verify the destination declarations, including mandated `about:`/`decisions:`/`claims:` fields where applicable, then `gw wiki archive --dry-run` **before** `gw wiki archive`: the no-target sweep takes `approved` notes too, and drained Sources (every key claim dispositioned) along with their `sources/references/` copies.

## Reference

→ `../graph-works/SKILL.md`
→ `../graph-works/references/proposal-disposition.md`
→ `../graph-works/references/page-formats.md`
