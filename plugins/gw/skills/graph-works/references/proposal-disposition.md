# Proposal Disposition Workflow

How to review and dispose of curated-page **proposals** — the ADR/concept notes the
ingest pipeline and the cross-page drift producer drop into `<workspace>/okf/proposals/`. Each note is a
review artifact, not a finished page.

## The one fact that changes everything

**`gw wiki proposal approve` does NOT write the wiki page. It only flips the note's
`status` frontmatter to `approved`.** Nothing downstream consumes that flip to
create pages. *You* author the destination page — and the efficient way is to
**fan out one subagent per page**, in parallel.

Do not rediscover this by grepping the source every time. Approve = bookkeeping flip.
Authoring = a separate step you drive.

## Lifecycle

```
proposed ──review──> approve ──author page(s) via subagents──> status: created ──> archive
                 └──> reject (preserved; never re-proposed)
```

`proposed` notes have their body **regenerated from `origins[]` on every ingest**, so
edits to the body are pointless until a human decides. Once status leaves `proposed`
(`approved`/`rejected`/`created`), the body is frozen and `upsert_proposal` never stomps
it. `created` is the real terminal "page was authored" marker — it's what the archive
sweep and the notes under `_archive/` use, **not** `approved`.

## Quick reference

| Action | Command (set `export GRAPH_WORKS_DIR=<workspace>` first) |
|---|---|
| List open proposals | `gw wiki proposals` |
| Full records (origins, evidence) | `gw wiki proposals --json` |
| Approve (flip status only) | `gw wiki proposal approve <slug>` |
| Reject (preserve, won't re-propose) | `gw wiki proposal reject <slug>` |
| Set `created` (no CLI) | edit the note's `status: approved` → `status: created` |
| Regenerate indexes | `gw wiki index` |
| Archive spent notes | `gw wiki archive --dry-run` then `gw wiki archive` |
| Verify | `gw wiki lint` |

Slug = the note's filename without `.md` (e.g. `adr-index-repository-grouping`).

## Step-by-step

### 1. Review and decide disposition

Read each note (`## Suggested Action`, `## Evidence From Source`, `## Origins`, any
`## Potential Conflicts`). For each, **verify against ground truth** before recommending —
does the decision actually exist in the code/sources, do cited sources still exist, are
the conflicts real? Then choose:

- **Accept** — the decision is real and citable → `gw wiki proposal approve <slug>`, then author the page (step 2).
- **Reject** — duplicate, obsolete, or not decision-worthy → `gw wiki proposal reject <slug>`. Done; it stays in `proposals/` as a tombstone so re-ingest won't resurrect it.
- **Supersede** — accept, but it replaces/amends an existing page → approve + author with the supersession wiring (step 3).

Disposition is the user's curation call. Present a per-proposal recommendation with the
ground-truth evidence; don't approve in bulk silently.

### 2. Author the pages — FAN OUT ONE SUBAGENT PER PAGE (in parallel)

This is the centerpiece. After approving N proposals, dispatch N subagents **in a single
message** (so they run concurrently), each owning exactly one page. Each subagent:

- reads its approved note **and** the source page it cites (`origins[].ref`);
- reads an existing page of the same kind as the format template;
- writes the destination file (naming below);
- flips its own note `status: approved` → `status: created` (edit that one line, nothing else).

Destination naming, from the note's `kind` / `target_slug` / `mode`:

- **ADR, `create_new`** → `adrs/NNNN-<target_slug>.md`. ADR numbers are **human-assigned, monotonic, zero-padded** — read the current max off disk and assign the next free numbers. Set `adr_id`, the page's own `status: accepted` (this is the ADR page's status — distinct from the proposal note's `created`; don't conflate them), `decision_date` (default: today), `deciders` (default: the workspace owner, e.g. `["Pat"]` — the note carries none), `summary` (distil from the evidence), `tags`. Template: `.templates/adr.md`.
- **Concept, `create_new`** → `concepts/<target_slug>.md` (no number). If the note carries `concept_kind` (`pattern`/`architecture`), stamp `kind: <ck>` and use `.templates/concept-<ck>.md`.
- **Any kind, `update_existing`** → merge the evidence into the named existing page; do not create a new file.

Omit the `tokens:` key (stamped later by `gw util tokens`). Write real prose grounded in
the note's evidence + source — never a bullet dump, never invented facts. Cite the source
as `[<ref>](/sources/<ref>.md)` and link only pages under their real per-type lane (e.g. `/repositories/<repo>/packages/<name>.md`) that you've verified exist.

Assign numbers and supersession links **centrally before dispatch** (you, the orchestrator,
own cross-page identity); give each subagent its exact filename + any supersedes link so
parallel agents don't collide or guess.

### 3. Supersession (when a new page replaces/amends an old one)

Wire **both** directions and mark the old page yourself (a subagent only sees its own file):

- New page frontmatter: `supersedes: ["adrs/<old-id>-<old-slug>"]`.
- Old page frontmatter: `superseded_by: ["adrs/<new-id>-<new-slug>"]`.
- If fully replaced: set old page `status: superseded` (this makes it archivable via `gw wiki archive`).
- If only **superseded in part**: leave old `status: accepted`, add an inline note on its `**Status:**` line scoping what changed and what still stands. Don't set `superseded` — the core decision still holds.

**If the supersession target is still a proposal (not a landed page)**, there's nothing to back-link or mark. Don't emit a dangling `supersedes:` to a `proposals/…` path. Instead reconcile the two notes: `reject` (or trim) the superseded proposal so it won't be authored, and note the reconciliation inline in the new page's Context. If both are being accepted this round, author them to be mutually consistent rather than cross-linked.

### 4. Regenerate indexes

New pages won't appear in `index.md` / `adrs/index.md` / `concepts/index.md` until the
indexes are reconciled. Run it directly (graph-independent, pure frontmatter scan):

```bash
gw wiki index
```

It prints each index it rewrote, or `nothing to do`. Optionally run `gw util tokens`
afterward to stamp the `tokens:` keys you omitted.

### 5. Verify and archive

```bash
gw wiki lint            # 0 broken links / orphans / index drift
gw wiki archive --dry-run   # preview: sweeps created/approved/rejected notes
gw wiki archive             # moves spent notes to proposals/_archive/
```

Optionally append a `gw util log` entry to mirror ingest. Leave commit/push to the user.

## Common mistakes

| Mistake | Reality |
|---|---|
| "Approve created the ADR page" | No. Approve flips status only. You author the page. |
| Authoring pages one at a time | Fan out — one subagent per page, dispatched in a single message. |
| Leaving notes at `approved` after writing pages | Flip to `created` — that's the authored marker the archive sweep expects. |
| Hand-editing `index.md` | Run `gw wiki index`; hand edits drift. |
| Letting a subagent pick the ADR number / supersedes link | Orchestrator assigns numbers + cross-links centrally before dispatch. |
| Approving in bulk without ground-truth check | Verify each decision exists in code/sources first; disposition is the user's call. |
| Editing a `proposed` note's body | Pointless — regenerated from `origins[]` every ingest. Decide first. |
