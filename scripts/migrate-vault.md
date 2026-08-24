# migrate-vault — the graph-wiki-dialect vault to OKF v0.2 sweep

Carries a graph-wiki-dialect vault to an OKF v0.2 bundle, one phase per
subcommand: `decisions`, `frontmatter`, `titles`, `links`, `archive-shape`,
`work`, `entities`, `lanes`, `bundle`, `gate`. **C4 runs this sweep, not C2** —
C2 is the dry-run rehearsal against a scratch copy; C4 is the live vault.

Dry-run by default. Nothing is written without `--write`. Every subcommand is
idempotent — a run can be committed per phase, and a partially migrated vault
resumes where it left off.

## Use

```bash
# Read what a phase would change. Writes nothing.
uv run scripts/migrate_vault.py frontmatter "$GRAPH_WIKI_WORKSPACE/wiki"

# Apply it.
uv run scripts/migrate_vault.py frontmatter "$GRAPH_WIKI_WORKSPACE/wiki" --write
```

Run it through `uv run` from the repo root. **`uv run` is mandatory.** This
script imports `okf_io`, `okf_ext`, `doc_wiki_okf`, `code_wiki_okf` and
`graph_works_core` from the workspace; a `./migrate_vault.py` shebang
invocation cannot resolve any of them — the same trap
`scripts/convert-wikilinks.md` documents.

Exit code is `0` on success (no refusals), `1` if any phase refused (nothing
was written for the refused members — fix the cause and re-run the same
phase), `2` if `VAULT` is not a directory.

The vault is a git repo, so review with `git diff` before committing. Dry-run
every phase first — the report's refusals and notes are what a vault tells you
about itself before you commit to writing anything.

## Phase order

```
3   gw bootstrap                            # not this script -- the CLI already composes it
4a  decisions      <vault>                  # emits .gw/migration/decisions.yaml
    --- human reviews the decisions file ---
4b  frontmatter    <vault> --write
4c  titles         <vault> --write
5   links          <vault> --write
6a  archive-shape  <vault> --write
6   work           <vault> --write
7   entities       <vault> --write --repo-rename agent-workspace=graph-works
    --- human reviews .gw/migration/entities-unmatched.md, then deletes
        .gw/migration/entities-preimage/ by hand ---
8   lanes          <vault> --write
9   bundle         <vault> --write
10  gate           <vault> --today <ISO> --baseline .gw/migration/links-baseline.json
```

Three orderings are load-bearing, and each has a test that fails if it is
broken:

* **`titles` before `links`.** A page with no `title:` converts to link text
  derived from its stem: `[[entities/pkg_okf-io]]` becomes `pkg_okf-io`, not
  `okf-io`. `links` runs with its own title backfill turned off precisely
  because `titles` already owns that job, and two backfills that could
  disagree is exactly the failure the ordering exists to prevent.
* **`links` before every move.** `okf_ext.moves` repairs OKF markdown
  references and is deliberately blind to `[[wikilink]]` forms. It reports
  what it cannot reach on `MovePlan.stranded` rather than silently dropping
  it — so a stranded count in a move phase's report (`archive-shape`,
  `lanes`) means phase 5 did not run first.
* **`archive-shape` before `work`.** Without it, `plan_migration` refuses
  every archived item as `legacy-path-invalid` — `parse_item_path` cannot
  parse `work/_archive/<slug>/00-open-work` — and migrates nothing.

Run `gate` **before** the sweep too, with `--baseline`, so assertion 3 has a
pre-sweep broken-link count to measure a delta against. Without one it writes
the baseline file and reports "not evaluated" rather than passing vacuously.

## Subcommands

### `decisions` — phase 4a

Emits the reviewed judgment surface. Never writes to the vault itself,
`--write` or not — it only ever writes `.gw/migration/decisions.yaml`.
*Reads:* every `concepts/` page's legacy `kind:` and `status:`. *Writes:*
`.gw/migration/decisions.yaml` (merges by `(member, question)`, so a human's
prior `decision:` survives a re-run and only new rows are appended).

### `frontmatter` — phase 4b

Applies the closed frontmatter maps. *Reads:* the bundle, and
`.gw/migration/decisions.yaml`. *Writes:* mutated pages, byte-spliced. A
refusal anywhere aborts the whole phase — a partially applied contract is
worse than none — so fix the decisions file and re-run rather than expecting
a partial write.

### `titles` — phase 4c

Populates `title:` from the H1 on pages missing one. **Must run before
`links`** — see Phase order. *Reads:* the bundle. *Writes:* mutated pages.
Refuses a page with neither `title:` nor an H1 rather than guessing.

### `links` — phase 5

Converts `[[wikilink]]` forms to markdown links, delegated wholesale to
`scripts/convert_wikilinks.py` (see `scripts/convert-wikilinks.md`) with its
own title backfill turned off. *Reads:* the bundle. *Writes:* mutated pages.
Unresolved targets are notes, not refusals — a dangling wikilink is a
pre-existing vault defect this phase reports, not a reason to stop the sweep.

### `archive-shape` — phase 6a

Promotes `work/_archive/<slug>/00-open-work.md` to `work/_archive/<slug>.md`
through `okf_ext.moves`, repairing inbound references in the same pass.
*Reads:* the bundle. *Writes:* the renamed pages and every page that
referenced them. Refuses when the flattened destination already exists
(`ambiguous-archive-shape`) rather than choosing between the two forms.

### `work` — phase 6

The harvested path-native work migrator (`scripts/migrate_vault_work.py`).
*Reads:* the bundle, loaded with the migrator's own `LEGACY_IGNORE` (not this
sweep's `ignore_patterns`, so the 61 stage documents stay visible to it).
*Writes:* moves, writes and deletes applied transactionally through
`apply_mutation`.

**Slow.** Its `_projected_refusals` step copies the **whole vault** into a
tempdir and reloads it — not just the work lane — to compute refusals before
committing to a plan. Reported as a note so an attended run expects the wait.

`apply_mutation` can roll back without raising: postcondition validation runs
after the writes land, and a failed check restores the pre-mutation snapshot.
This phase checks `MutationApplication.ok` explicitly so a rolled-back run
never reports `changed=(...)` for writes that did not durably land.

### `entities` — phase 7

Retires the entity lane and remaps its references (D-011, D-044). Five
resumable steps: snapshot `entities/*.md` to
`.gw/migration/entities-preimage.json`; quarantine `entities/` to
`.gw/migration/entities-preimage/`; regenerate via `gw scan` (skip with
`--no-scan`); match old `uri:` values to the regenerated lane's `resource:`
identity and rewrite both reference surfaces (markdown links and
`sources/*.entity_uri:`); write `.gw/migration/entities-unmatched.md` for
human review. *Reads:* the bundle, `entities/*.md`. *Writes:* the quarantine
move, the regenerated `entities/` lane (via `gw scan`), rewritten reference
pages, and the two report files above.

Flags: `--repo-rename OLD=NEW` (repeatable) rewrites a repository name inside
every old `uri:` before parsing — for when C3 renamed the repo ahead of C4
(D-008). `--no-scan` skips invoking `gw scan`, for when the regenerated lane
already exists or a caller ran it separately.

**This subcommand never deletes `.gw/migration/entities-preimage/`.** See
Human gates below.

### `lanes` — phase 8

Moves each lane into the directory its schema declares
(`x-okf-directory`, read from the bundle's own `.gw/schema/`, never a
constant in this script). Must run after `links`, `archive-shape` and
`entities` — moves repair markdown references, which only exist once phase 5
has converted the wikilink forms that `okf_ext.moves` cannot see. *Reads:*
the bundle and its declared schema set. *Writes:* moved pages and repaired
inbound references.

### `bundle` — phase 9

Bundle-level cleanups, one family of edit: regroups `log.md`'s `## [DATE] op |
title` headings into one `## <ISO date>` section per date, newest first;
strips frontmatter from non-root `index.md` files (§8 permits none there);
adds `okf_version: 0.2` to the root `index.md`. *Reads:* `log.md` and every
`index.md`. *Writes:* the reformatted log and the touched index files.

### `gate` — phase 10

The four acceptance assertions that define "migrated": `validate(...).ok`;
zero `coercion_failures` across every document; `links.broken` did not
increase against the `--baseline` file (writes one if it does not exist, and
reports "not evaluated" rather than passing vacuously); zero unmatched entity
references still carried in `.gw/migration/entities-unmatched.md`. *Reads:*
the bundle, the baseline file, the unmatched-entities report. *Writes:* the
baseline file, only when `--write` is given and it did not already exist.

**Run this as the live acceptance gate in C4, and separately as a test over a
fixture — the real vault is the acceptance gate, not the test suite.**

Flags: `--today TODAY` (required, ISO date — okf-io never reads the clock).
`--baseline BASELINE` — the pre-sweep link-count file from an earlier `gate`
run.

## Human gates

Two points in the sequence hand off to a person, and the sweep will not
proceed past either on its own:

1. **Between `decisions` and `frontmatter`.** `decisions` only emits
   `.gw/migration/decisions.yaml`; a human reviews and fills in each row's
   `decision:`. `frontmatter` then refuses any `concepts/` page a row does
   not cover, so an unreviewed decisions file blocks the next phase rather
   than being silently accepted.

2. **Before deleting the entity quarantine.** After `entities` runs, a human
   reviews `.gw/migration/entities-unmatched.md` — the report of every old
   entity `uri:` that found no successor in the regenerated lane, with a
   stated reason for each absence. **This script never deletes
   `.gw/migration/entities-preimage/`; a human does, by hand, after signing
   off on the unmatched report** (D-011/D-044). The quarantine sits outside
   the bundle the whole time, so `load_bundle` never walks it and nothing
   later in the sweep can see or depend on it — the recovery path stays one
   `mv` away for as long as the review takes.

## Testing

```bash
uv run pytest scripts/tests
```

These live outside the repo's `testpaths` and coverage `source` list, so they
do not affect the 95% gate and `just check` does not run them.
