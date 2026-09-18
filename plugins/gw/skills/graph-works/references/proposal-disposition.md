# Proposal Disposition Workflow

How to review and dispose of curated-page **proposals** — the notes the ingest
pipeline and the cross-page drift producer drop into
`<workspace>/okf/proposals/`. Each note is a review artifact arguing for one
page, not a finished page.

## The one fact that changes everything

**`gw wiki proposal approve` does NOT write the page.** It flips the note's
`page_status` to `approved` and appends one `verified[]` entry naming the
deciding actor. Approval does not dispatch authorship. *You* author the
destination page — and the efficient way is to **fan out one subagent per
page**, in parallel.

Approve = bookkeeping flip. Authoring = a separate step you drive. There is no
promotion CLI command.

## The note's shape

Frontmatter, as `okf_ext.proposals` reads and writes it:

| Key | What it is |
|---|---|
| `type: Proposal` | the type the capability owns outright |
| `title`, `description` | the argument's name and one-line summary |
| `generated` | `{by, at}` — who filed it and when |
| `target` | **identity.** The bundle-relative path of the page being argued for. Two notes naming one target are one proposal and merge |
| `page_status` | `proposed` → `approved`/`rejected` → `created` |
| `sources[]` | each entry `{id, resource, ...}`. `resource` is the recorded source reference; `rationale` and `evidence` ride along when the producer supplied them |
| `verified[]` | appended by approve/reject: `{by, at}` |

Create-vs-update is **derived** from whether `target` is a member right now.
Read current declarations for destination types: fresh filing supports the four
Diataxis lanes and ADR. Preserve legacy recorded targets unless a retarget is
explicitly reviewed.

Bodies come in two shapes, and both are legitimate:

- **the review body** — `## Suggested Action`, `## Evidence From Source`,
  `## Existing Pages Considered`, `## Reasoning Summary`,
  `## Potential Conflicts`, `## Implementation Notes`, `## Origins`. This is
  what `gw wiki proposal file` writes today. (`## Origins` is a body heading
  rendered from `sources[]` — it is not a frontmatter key.)
- **the ledger body** — an `HTML` ownership comment, the description, and a
  `## Sources` list of footnoted bullets. This is what the generic renderer
  writes, and what drift-propagated notes carry.

Read whichever you are given. When a note carries neither, read `description`
and `sources[]` directly — the frontmatter is the contract, the body is a
convenience.

While a note is `proposed` its body is machine-owned and re-rendered on a
**changed** merge. Once it is decided the body is frozen and nothing rewrites
it. A merge that cannot first reproduce the existing body from the note's own
`description` and `sources[]` refuses with `unrenderable-body` rather than
replacing it. The mismatch can come from body-only prose, formatting, or
changed renderer context. Review the renderer and preserve or reconcile the unmatched prose
under a separate reviewed operation before retrying; there is no force flag.

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

**The argument is the `target`, not the filename.** `approve` and `reject`
resolve their argument by normalized target path — `adrs/0013-some-page.md`,
`explanations/byte-fidelity.md` — never by the note's own slug. If the user
hands you a filename, resolve it first: read `gw wiki proposals --json`, find
the record whose `member` matches, and call the command with that record's
exact `target`.

## Step-by-step

### 1. Review and decide disposition

Read each note's `description`, its `sources[]`, and whichever body shape it
carries. For each, **verify against ground truth** before recommending — does
the claim hold in the code and the cited sources, does every
`sources[].resource` still resolve, are the conflicts real? Follow
`sources[].resource` as written, preserving its actual path or URL; do not
synthesize one.

Then choose:

- **Accept** — `gw wiki proposal approve <target>`, then author the page (step 2).
- **Reject** — `gw wiki proposal reject <target>`. It is preserved as a
  tombstone, including after archiving, so a re-fire cannot resurrect it.
- **Supersede** — approve, then author with the supersession wiring (step 3).

Disposition is the user's curation call. Present a per-proposal recommendation
with the ground-truth evidence; never approve in bulk silently.

**Retargeting is an explicit, separate decision.** The target a note carries is
the ledger's own data. If the right destination is a different path — most
often an ADR, where the shipped writer files to `adrs/<slug>.md` while this
workspace's corpus is numbered `adrs/NNNN-<slug>.md` and its `Adr` declaration
requires a four-digit `adr_id` — say so, get the user's agreement, edit the
note's `target` **before** approving, and reconcile any link that named the old
path. Never rename a target silently to make it fit a convention.

### 2. Author the pages — FAN OUT ONE SUBAGENT PER PAGE (in parallel)

This is the centerpiece. After approving N proposals, dispatch N subagents **in
a single message** (so they run concurrently), each owning exactly one page.
Each subagent:

- reads its approved note **and** every source it cites (`sources[].resource`);
- reads an existing page in the same lane as the format template;
- writes the destination file **at the note's `target`, exactly as recorded** —
  an existing target means update that page, a missing one means create it;
- flips its own note's `page_status: approved` → `page_status: created` (that
  one line, nothing else).

Destination frontmatter is the destination type's business, not the note's. A
page's own `status` and the note's `page_status` are different keys about
different documents — do not conflate them. Omit `tokens:` (stamped later by
`gw util tokens`).

Write real prose grounded in the note's evidence and its sources — never a
bullet dump, never invented facts. Cite each source by its recorded
`resource`, and link only pages you have verified exist.

Assign any cross-page identity — ADR numbers, supersession links — **centrally
before dispatch**; give each subagent its exact destination path and links so
parallel agents cannot collide or guess.

### 3. Supersession (when a new page replaces or amends an old one)

Wire **both** directions and mark the old page yourself (a subagent only sees
its own file):

- New page frontmatter: `supersedes: ["<old page path without .md>"]`.
- Old page frontmatter: `superseded_by: ["<new page path without .md>"]`.
- If fully replaced: set the old page's own `status: deprecated`.
- If superseded only **in part**: leave the old page's status alone and add an
  inline note scoping what changed and what still stands.

**If the supersession target is still a proposal, not a landed page**, there is
nothing to back-link. Do not emit a dangling `supersedes:` into `proposals/`.
Reconcile the two notes instead: reject or trim the superseded one so it is
never authored, and record the reconciliation in the new page's prose. If both
are being accepted this round, author them to be mutually consistent.

### 4. Regenerate indexes

New pages do not appear in the indexes until they are reconciled:

```bash
gw wiki index
```

It prints each index it rewrote, or `nothing to do`. Optionally run
`gw util tokens` afterward to stamp the `tokens:` keys you omitted.

### 5. Verify and archive

```bash
gw wiki lint
gw wiki archive --dry-run
gw wiki archive
```

**Run the dry run, every time.** The sweep selects every proposal whose
`page_status` is anything but `proposed` — `approved` included. A note you
approved but have not authored yet *will* be swept by a bare
`gw wiki archive`. Read the preview and confirm nothing on it is still owed a
page.

**When the workspace declaration omits `approved`:** `gw wiki lint` reports
`schemas.invalid … 'approved' is not one of ['proposed', 'created',
'rejected']`. The live workspace's `.gw/schema/Proposal.schema.json` omits
`approved` in its `page_status` enum, though the CLI writes it. Freshly
bootstrapped workspaces do not install that declaration and may not warn. The
window closes as soon as you flip the note to `created`. To close it permanently, add
`"approved"` to that enum in the workspace declaration.

Optionally append a `gw util log` entry to mirror ingest. Leave commit and push
to the user.

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
| Letting a subagent pick the ADR number or the supersedes link | The orchestrator assigns cross-page identity centrally, before dispatch. |
| Editing a `proposed` note's body to add evidence | A changed merge refuses if the renderer cannot reproduce it. Preserve evidence in `sources[]` and reconcile the body through a reviewed operation. |
