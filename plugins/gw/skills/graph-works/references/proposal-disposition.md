# Proposal Disposition Workflow

How to review and dispose of curated-page **proposals** — the notes the ingest pipeline and the cross-page drift producer drop into `<workspace>/okf/proposals/`. Each note is a review artifact arguing for one page, not a finished page.

## The one fact that changes everything

**`gw wiki proposal approve` does NOT write the destination.** It flips the note's `page_status` to `approved` and appends one `verified[]` entry naming the deciding actor. Approval does not dispatch authorship. Work types go through `gw work file`; other destination pages are authored by **fanning out one subagent per page**, in parallel.

Approve = bookkeeping flip. Filing or authoring = a separate step you drive. There is no promotion CLI command.

## The note's shape

Frontmatter, as `okf_ext.proposals` reads and writes it:

| Key | What it is |
|---|---|
| `type: Proposal` | the type the capability owns outright |
| `title`, `description` | the argument's name and one-line summary |
| `generated` | `{by, at}` — who filed it and when |
| `target` | **identity.** The bundle-relative path of the page being argued for. Two notes naming one target are one proposal and merge |
| `target_type` | the page type the filer named. Present on every note filed since the proposal pool; older notes lack it, and their type comes from the target's directory when exactly one pool type owns it |
| `page_status` | `proposed` → `approved`/`rejected` → `created` |
| `sources[]` | each entry `{id, resource, ...}`. `resource` is the recorded source reference; `rationale` and `evidence` ride along when the producer supplied them |
| `verified[]` | appended by approve/reject: `{by, at}` |

Create-vs-update is **derived** from whether `target` is a member right now. Fresh filing supports every type in the proposal pool: each schema under `.gw/schema/` that sets `x-okf-accept-proposals: true`. A type set to `false` is locked and can never be targeted; an absent key is outside the pool, and any other value refuses the type. Preserve legacy recorded targets unless a retarget is explicitly reviewed.

Before any authoring or filing, read `gw wiki proposals --json` and check `type_refusal` (null or `{path, kind, detail}`), then route on resolved `type` (the canonical destination type or null), not raw `target_type`. Legacy notes can resolve through a uniquely owned target directory. Stop on a non-null `type_refusal` or null `type`; report locked, refused or unknown types. Only an ambiguity asks which kind: explicitly record the chosen `target_type` on the note before retrying resolution.

Bodies come in two shapes, and both are legitimate:

- **the review body** — `## Suggested Action`, `## Evidence From Source`, `## Existing Pages Considered`, `## Reasoning Summary`, `## Potential Conflicts`, `## Implementation Notes`, `## Origins`. This is what `gw wiki proposal file` writes today. (`## Origins` is a body heading rendered from `sources[]` — it is not a frontmatter key.)
- **the ledger body** — an `HTML` ownership comment, the description, and a `## Sources` list of footnoted bullets. This is what the generic renderer writes, and what drift-propagated notes carry.

Read whichever you are given. When a note carries neither, read `description` and `sources[]` directly — the frontmatter is the contract, the body is a convenience.

While a note is `proposed` its body is machine-owned and re-rendered on a **changed** merge. Once it is decided the body is frozen and nothing rewrites it. A merge that cannot first reproduce the existing body from the note's own `description` and `sources[]` refuses with `unrenderable-body` rather than replacing it. The mismatch can come from body-only prose, formatting, or changed renderer context. Review the renderer and preserve or reconcile the unmatched prose under a separate reviewed operation before retrying; there is no force flag.

## Work-type proposals

A note whose resolved `type` is a work type — its schema declares `x-okf-directory: work/` — is not authored as a page. `/gw:proposals` files it with `gw work file --kind <type> --title "<title>" --summary "<description>" --repo <repo>` (plus `--affects` when the evidence names code), the one creator of work items, then manually appends the note's `sources[]` citation entries with their original `id` and `resource` (these are citations, not owned stage artifacts; never rename them to `design`/`plan` or replace their resources with artifact paths) onto the new item's frontmatter `sources:` following `skills/workflow/references/editing-work-items.md`, commits that one path by pathspec, and only then flips the note to `page_status: created` with `target` set to the new item's canonical path plus `.md`. A work-type note whose `target` already exists must preserve its recorded `target`, add its `sources[]` to that item instead of filing a second one, follow the same editing and commit protocol, and then flip to `created`. If `gw work file` fails, leave the note `approved` and report the failure. Missing `target_type` alone is not ambiguous; a resolution ambiguity requires a kind choice and recorded metadata before retrying.

## Quick reference

Set `export GRAPH_WORKS_DIR=<workspace>` first.

| Action | Command |
|---|---|
| List open proposals | `gw wiki proposals` |
| Full records (sources, evidence, rationale) | `gw wiki proposals --json` |
| Approve (flip + verify only) | `gw wiki proposal approve <target>` |
| Reject (preserve; never re-proposed) | `gw wiki proposal reject <target>` |
| Set `created` (no CLI) | edit the note's `page_status: approved` → `page_status: created` |
| Regenerate indexes | `gw wiki index` |
| Preview the archive sweep | `gw wiki archive --dry-run` |
| Archive spent notes | `gw wiki archive` |
| Verify | `gw wiki lint` |

**The argument is the `target`, not the filename.** `approve` and `reject` resolve their argument by normalized target path — `adrs/0013-some-page.md`, `docs/explanations/byte-fidelity.md` — never by the note's own slug. If the user hands you a filename, resolve it first: read `gw wiki proposals --json`, find the record whose `member` matches, and call the command with that record's exact `target`.

## Step-by-step

### 1. Review and decide disposition

Read each note's `description`, its `sources[]`, and whichever body shape it carries. For each, **verify against ground truth** before recommending — does the claim hold in the code and the cited sources, does every `sources[].resource` still resolve, are the conflicts real? Follow `sources[].resource` as written, preserving its actual path or URL; do not synthesize one.

Then choose:

- **Accept** — `gw wiki proposal approve <target>`, then file work types as described above or author other pages (step 2).
- **Reject** — `gw wiki proposal reject <target>`. It is preserved as a tombstone, including after archiving, so a re-fire cannot resurrect it.
- **Supersede** — approve, then author with the supersession wiring (step 3).

Disposition is the user's curation call. Present a per-proposal recommendation with the ground-truth evidence; never approve in bulk silently.

**Retargeting is an explicit, separate decision.** The target a note carries is the ledger's own data. If the right destination is a different path — say, an existing page it should update rather than a new one — say so, get the user's agreement, edit the note's `target` **before** approving, and reconcile any link that named the old path. Never rename a target silently to make it fit a convention. The declaration-driven dating of a new page in step 2 is part of promotion; it does not require a separate retarget decision. Updates retain their recorded identity.

### 2. Author the pages — FAN OUT ONE SUBAGENT PER PAGE (in parallel)

Before dispatch, read the resolved type's `<workspace>/.gw/schema/<type>.schema.json` (including `$ref`/`allOf` and applicable annotations) and `<workspace>/.gw/sections/<type>.yaml` (including referenced fragments). Use the declared fields and sections even for the first page of a custom type. An existing page may guide style but cannot supply a missing declaration; if sections are undeclared, leave the note `approved` and report the missing declaration rather than inventing a format.

Read `x-okf-proposal-promotion`: `dated` defaults to `false`, and an absent block supplies no extra fields. Carry the declared `frontmatter` string values and expand every `{on}` to one centrally chosen promotion date (`YYYY-MM-DD`). Only a new page with `dated: true` derives `<x-okf-directory><YYYY-MM-DD>-<slug-of-title>.md`; for updates and undated pages, preserve the recorded `target`, even when its filename differs from the title. Assign each author's exact final path, date, expanded metadata and any schema-supported supersession links centrally before dispatch.

Dispatch one subagent per remaining accepted proposal **in a single message** (so they run concurrently), each owning one destination and its note. Each subagent:

- reads its approved note, every `sources[].resource`, and the resolved schema and section declarations supplied in the brief;
- writes schema-valid frontmatter and the declared body sections at the centrally assigned final path, preserving existing provenance on updates and the note's citations with recorded resources;
- writes `about:`, `claims:` or `decisions:` only when the applicable declarations mandate them (including `x-okf-about` and its `entries` field), following **Curated-page claims** in [page-formats.md](page-formats.md) for those fields;
- only after successfully writing the page, flips its note's `page_status: approved` → `page_status: created`, setting `target` to the final path if new-page dating changed it; a failure leaves the note `approved`.

For example, a Runbook schema may forbid `about` and require `service` supplied by promotion `frontmatter`. Keep to that schema and its declared sections; do not copy ADR or Explanation fields into it. A destination page's own `status` and the note's `page_status` describe different documents. Write real prose grounded in the note and its sources, cite each recorded `resource`, and link only verified pages. Carry source citations in `sources[]` where the destination schema permits it.

For destinations with declared claim/decision entries, keep each cited Source in the destination's `sources[]`, and return `(source, claim ordinal, landed ref)` triples for the Source key claims those entries carry, using `/<final target>#<entry id>`. Destinations without declared entries supply no invented drain refs. Authors never edit Sources; the orchestrator merges returned refs into each Source's `drain:` ledger once, after the fan-out, as specified in `skills/proposals/SKILL.md`.

### 3. Supersession (when a new page replaces or amends an old one)

Use supersession fields only where both destination declarations support them; report an unsupported supersession rather than inventing fields. Wire **both** directions and mark the old page yourself (a subagent only sees its own file):

- New page frontmatter: `supersedes: ["<old page path without .md>"]`.
- Old page frontmatter: `superseded_by: ["<new page path without .md>"]`.
- If fully replaced: set the old page's own `status: deprecated`.
- If superseded only **in part**: leave the old page's status alone and add an inline note scoping what changed and what still stands.

**If the supersession target is still a proposal, not a landed page**, there is nothing to back-link. Do not emit a dangling `supersedes:` into `proposals/`. Reconcile the two notes instead: reject or trim the superseded one so it is never authored, and record the reconciliation in the new page's prose. If both are being accepted this round, author them to be mutually consistent.

### 4. Regenerate indexes

New pages do not appear in the indexes until they are reconciled:

```bash
gw wiki index
```

It prints each index it rewrote, or `nothing to do`.

### 5. Verify and archive

```bash
gw wiki lint
gw wiki archive --dry-run
gw wiki archive
```

**Run the dry run, every time.** The sweep selects every proposal whose `page_status` is anything but `proposed` — `approved` included. A note you approved but have not authored yet *will* be swept by a bare `gw wiki archive`. Read the preview and confirm nothing on it is still owed a page.

**When the workspace declaration omits `approved`:** `gw wiki lint` reports `schemas.invalid … 'approved' is not one of ['proposed', 'created', 'rejected']`. The live workspace's `.gw/schema/Proposal.schema.json` omits `approved` in its `page_status` enum, though the CLI writes it. Freshly bootstrapped workspaces do not install that declaration and may not warn. The window closes as soon as you flip the note to `created`. To close it permanently, add `"approved"` to that enum in the workspace declaration.

Optionally append a `gw util log` entry to mirror ingest. Leave commit and push to the user.

## Common mistakes

| Mistake | Reality |
|---|---|
| "Approve created the page" | No. Approve flips `page_status` and appends `verified[]`. You author the page. |
| Passing a filename slug to `approve` | The argument is the `target` path. Resolve the slug through `gw wiki proposals --json` first. |
| Synthesizing `/sources/<id>.md` | Use `sources[].resource` verbatim; it is already the path. |
| Renaming a target to fit a naming convention | A retarget is an explicit reviewed decision, made before approving, with links reconciled. |
| Leaving notes at `approved` after writing pages | Flip to `created` — and until you do, the note remains archive-eligible and may warn under its workspace schema. |
| Running `gw wiki archive` without the dry run | `approved` is swept too. Preview first. |
| Hand-editing an index | Run `gw wiki index`; hand edits drift. |
| Letting a subagent pick the dated path or the supersedes link | The orchestrator assigns the declared dated path and schema-supported supersession links centrally, before dispatch. |
| Editing a `proposed` note's body to add evidence | A changed merge refuses if the renderer cannot reproduce it. Preserve evidence in `sources[]` and reconcile the body through a reviewed operation. |
