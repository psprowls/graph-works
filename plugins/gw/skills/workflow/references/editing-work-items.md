# Editing a work item by hand

## When to read this

Read this before changing an existing work item's frontmatter or body when the
change is not one of the pipeline's own writes (`gw work file`,
`gw work advance`, a stage skill saving its artifact). Typical requests:
backfill `affects`, retitle, re-size `effort`, discard an item as `wontfix`,
mark it `superseded`, repair a field a bug left wrong.

There is no `gw work edit` verb, by design. You edit the file directly, then
run the checks in [Procedure](#procedure-mandatory) — `gw work lint` and
`gw work regen-index` are the safety net, and skipping them is how a hand edit
goes wrong silently.

## Where the item lives

- The page: `<workspace>/okf/<work-path>.md` — for example
  `<workspace>/okf/work/feature-example.md`, or
  `<workspace>/okf/work/epic-example/children/feature-example.md` for a child.
- Its owned directory: `<workspace>/okf/<work-path>/`, with managed artifacts
  under `references/`.
- The format itself — declared headings, the example page, `depends_on` and
  `sources[]` shapes — is in
  [page-formats.md §8](../../graph-works/references/page-formats.md) and
  [wiki-schema.md, "Work pages"](../../graph-works/references/wiki-schema.md).
  This guide does not restate it; it says what editing each field *does*.

## Accepted values

<!-- packages/work-tracker-okf/tests/test_editing_guide_doc_sync.py parses this
section and asserts each list equals the code's closed vocabulary. Keep one line
per key, each value in backticks. -->

- `type`: `Release` · `Epic` · `Feature` · `Bug` · `TechDebt` · `TestGap` · `Spike`
- `status`: `draft` · `stable` · `deprecated`
- `work_status`: `open` · `accepted` · `in-progress` · `mitigated` · `resolved` · `wontfix` · `superseded`
- `terminal work_status`: `resolved` · `wontfix` · `superseded`
- `phase`: `design` · `plan` · `execute` · `finish` · `done`
- `effort`: `xtra-small` · `small` · `medium` · `large` · `xtra-large`
- `blast_radius`: `file` · `package` · `domain` · `system`

## Field table

Every edit ends with the [Procedure](#procedure-mandatory). The "Also" column
is what that field adds on top of it.

### Safe

| Key | Meaning / accepted values | Also |
|---|---|---|
| `description` | One-sentence summary, free text | — |
| `affects` | List of repo-relative paths the work touches | Each entry must resolve in a declared code repo, or lint fires `targets.affects-missing` |
| `tags` | List of tag names | — |
| `blast_radius` | One of the `blast_radius` values above | — |
| `owner` | Handle of whoever is executing the item | Required when `work_status: in-progress` (`state.in-progress-without-owner`, error) |
| `rationale` | Why the item was discarded, free text | Required when `work_status: wontfix` (`state.wontfix-without-rationale`, warn) |
| `mitigation` | What mitigates the item, free text | Required when `work_status: mitigated` (`state.mitigated-without-mitigation`, error) |
| `version` | Release version string | `Release` only. Every other type's schema sets `unevaluatedProperties: false`, so lint fires `schemas.invalid` (error) on the key — never add it to one (`gw work file` refuses it too) |
| `target_date` | `YYYY-MM-DD` | Same `Release`-only rule as `version` |
| `target` | Planning target: `YYYY-Qn` or `YYYY-MM` (not `target_date`) | — |
| `opened`, `updated` | `YYYY-MM-DD`; both required on every item | `updated` is set in the [Procedure](#procedure-mandatory); leave `opened` alone |
| `depends_on` | List of complete mappings: `path` (a canonical item path, active or archived), `blocks` (`design` · `plan` · `execute` · `finish`), `needs` (`design` · `plan` · `execute` · `finish` · `resolved`) | Never write a bare path string; every entry carries all three keys |

### Has consequences

Tell the user the consequence in your report — they asked for an edit, not
necessarily for what it does to the pipeline.

| Key | Meaning / accepted values | Also |
|---|---|---|
| `title` | Human title | The lane index shows it: run `gw work regen-index`. If the body has an H1 mirroring the title, change it too |
| `effort` | One of the `effort` values above | Changes routing: a `xtra-small`/`small` `Bug`, `TechDebt` or `TestGap` skips the plan stage. Say what `gw work next` will now route to. Unless `status` is `draft`, `effort` and `affects` are required |
| `work_status` | One of the `work_status` values above | Overrides the routing table; the lane index shows it: run `gw work regen-index`. `mitigated` blocks `gw work next` until a human changes it. Requires: `in-progress` → `owner` (error); `mitigated` → `mitigation` (error); `wontfix` → `rationale` (warn). Also `state.phase-status-incoherent` (warn) unless `phase` fits: `accepted` → `execute`/`finish`/`done`; `in-progress` → `execute`/`finish`; `resolved` → `done` |
| `phase` | One of the `phase` values above | Overrides the routing table; the lane index shows it: run `gw work regen-index`. Say what `gw work next` will now dispatch. Must stay coherent with `work_status` (see that row) |
| `status` | Document status, one of the `status` values above — independent of `work_status` | Routing does not read it. Leaving `draft` makes `effort` and `affects` required (`schemas.invalid`, error) |
| `work_status` → terminal | Any `terminal work_status` value | `wontfix` requires `rationale` (warn). `superseded` requires `superseded_by: <canonical path of the replacement>` (extensionless, e.g. `work/feature-replacement`). Set `phase: done` alongside. Hand-setting `resolved` skips the finish stage's merge evidence (`resolved_in`) — say so, and prefer `/gw:workflow <work-path>` when the work really was merged. Afterwards: for `resolved`, ingest the design (`gw:ingest` on the `design` source) as the workflow skill's Terminal handling does; for any terminal status, offer `/gw:archive <work-path>` |
| `sources[]` | Owned-artifact registry | `id` is the filename-derived kebab-case name (`01-design.md` → `design`); `resource` is root-absolute within the bundle, e.g. `/work/feature-example/references/01-design.md` |
| `worktree`, `branch`, `resolved_in`, `released_at` | Provenance the pipeline stamps | Edit only to correct a wrong value; never invent one |

### Not a frontmatter edit

| Change | Why | Do instead |
|---|---|---|
| `type` | The type fixes the basename's prefix (`feature-…`, `bug-…`), so it is part of the canonical path | Re-file with `gw work file`, then mark the old item `superseded` with `superseded_by` pointing at the new one |
| Basename | The basename *is* the identity | Same: re-file, then supersede |
| Parent | Physical placement under `children/` is ownership | `gw work reparent <work-path> --parent <new-parent-path>` |

## Body edits

The declared headings per `type` are the table in
[page-formats.md §8](../../graph-works/references/page-formats.md). Every type
requires `## Plan`, and it must stay a Markdown table with its header row
(`| Action | Done when | Rationale |` and the `| --- |` separator) even when it
has no rows. Append to `## Notes / log` rather than rewriting history in it;
date a new entry (`**YYYY-MM-DD** — …`).

## Procedure (mandatory)

1. **Read the item** before editing — the whole page, not just the key you
   were asked about.
2. **Make the edit.**
3. **Set `updated:`** to today (`YYYY-MM-DD`).
4. **If `title`, `work_status` or `phase` changed:** run `gw work regen-index`.
5. **Run `gw work lint`.** Fix every finding that names this item before
   reporting done. Findings on *other* items: relay them, do not fix them.
6. **Report** the edit and, for a consequence-class field, its consequence.

## Gotchas

- **Do not hand-edit while another `gw` write is in flight** for the same
  workspace (an advance, a filing, an archive). Its mutation checks a digest
  precondition taken before your edit and will refuse. Wait for it to finish,
  then re-read the item and retry.
- **Never add `parent` or `children` frontmatter.** Placement is ownership;
  those keys do not exist, and lint fires `schemas.invalid` (error) on them
  like any other unknown key.
- **Do not edit an item's owned `references/` artifacts from here.** A design
  or plan is rewritten by its stage (`/gw:workflow`), not by a field edit.
