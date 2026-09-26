# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Python ≥3.12 (the workspace floor). Tests are pytest. `doc-wiki-okf` is the
documentation-wiki lane over OKF v0.2: it reads arbitrary source material,
briefs an agent on what's about to be ingested, and manages the propose/decide/
promote lifecycle for wiki pages — all built on `okf-io` (the pure OKF reader/
writer/validator) and `okf-ext` (the tier-2 extension capabilities: `moves`,
`proposals`, `bundle`, `schemas`, `shape`).

## Commands

From the repo root (workspace-relative invocations; this package resolves its
own dependency closure so it runs under `--package`, per the root `justfile`):

```bash
uv run --package doc-wiki-okf mypy --strict --platform linux packages/doc-wiki-okf/src
uv run --package doc-wiki-okf mypy --strict --platform win32 packages/doc-wiki-okf/src
uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests
uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests --cov=doc_wiki_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
```

These are exactly the `types` / `test` / `cov` lines the root `justfile` runs
for this package — `just check` runs all workspace members together, but any
one of the lines above scopes to just this package. `types` runs twice, once
per `--platform` arm. A single test or subset:

```bash
uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests/test_migrate_fixtures.py
uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests -k "archive or migrate"
```

Coverage is gated at 95% (`just cov`), same floor as `okf-io`/`okf-ext`.

## Layout

- `src/doc_wiki_okf/reading/` — substrate-neutral file and format inspection.
  Five modules, each answering one question: `slug` normalizes text to an
  identifier, `extract` turns one file into text plus a title guess, `files`
  reports on a directory, `links` finds markdown link targets, `skills`
  assembles a skill directory into one blob.
- `src/doc_wiki_okf/reading/__init__.py` — the re-export surface. `links`'
  two functions are deliberately **not** on it: `reading/` is an internal
  subpackage and its `__init__` exports what the rest of the lane consumes.
- `src/doc_wiki_okf/ingest/` — ingest briefs: `document.py` (`plan_document_brief`),
  `folder.py` (`plan_folder_brief`), `batch.py` (`plan_batch_brief`), `layout.py`
  (`IngestLayout`, `GRAPH_WIKI_LAYOUT`, `resolve_source_path`), `seams.py` (the
  two optional-callable protocols below).
- `src/doc_wiki_okf/sources/` — `plan.py` builds the `Source`-page-plus-copy
  write plan (`plan_ingest`, `preflight_ingest`, `page_target`, `copy_target`,
  `source_kinds`). A sibling of `ingest/`, not a member of it: `ingest/` is
  boundary-locked to stdlib + `reading/`, and a writer needs `okf_io`/`okf_ext`.
  The dependency is one-way — `sources/` imports `ingest.layout` for the page
  template; nothing in `ingest/` imports `sources/`.
- `src/doc_wiki_okf/assets/` — the fourteen files this package installs into a
  bundle: seven `schema/` JSON schemas (a `_base-diataxis` `$ref` target plus
  `Adr`, the four Diátaxis types and `Source`) and seven `sections/` declarations
  (`_fragments.doc_wiki.yaml` plus the same six). Read through
  `importlib.resources`; installed by `okf_ext.bundle.plan_install`.
- `src/doc_wiki_okf/resources.py` — `SEED_RELATIVE_PATHS`, `assets_root()`,
  `seed_files()`. The asset list without the installer.
- `src/doc_wiki_okf/init.py` — `install_bundle()` / `plan_install()`: scaffold
  a bundle, install this package's fourteen files into it, log the arrival to
  `log.md`. Additive and idempotent — a re-run writes nothing and refuses
  nothing; the only refusal is a file this package owns existing with content
  it did not write.
- `src/doc_wiki_okf/diataxis/` — the four types as code. `rubric.py` is the
  taxonomy as data plus `brief()`; `classify.py` validates a typing decision the
  caller made and never makes one; `pages.py` reads `x-okf-directory` off the
  loaded `SchemaSet` for placement and composes a new page; `retype.py` is the
  `type:` rewrite plus an `okf_ext.moves` lane move, plan-and-apply.
- `src/doc_wiki_okf/proposals/` — the two things `okf_ext.proposals` cannot hold.
  `lanes.py` is the lane map (four rows derived from the loaded `SchemaSet`, one
  `adr` row of new data); `render.py` is the seven-section `ReviewRenderer`,
  injected into `plan_propose` as its `render=`; `filing.py` and `promote.py`
  are the two compositions over the capability. `migrate.py` is the
  old-dialect rewriter: pure frontmatter key surgery on the parsed original,
  bodies carried verbatim by construction, refusals are data all-or-nothing
  per document, and re-placement is computed here but performed by
  `okf_ext.moves`.
- `src/doc_wiki_okf/archive.py` — archiving a wiki page: a filtered
  `okf_ext.moves` batch plus an index reconcile, addressed by
  `"<lane>/<slug>"` token rather than a bare slug (a wiki bundle has no single
  lane the way `work/` is for work items). **Not wired into `cli.py`** — there
  is no `doc-wiki-okf archive` command yet; `plan_archive`/`apply_archive` are
  library-only today, exercised by `tests/test_archive.py`.
- `src/doc_wiki_okf/cli.py` — the typer app behind `[project.scripts]`. The one
  module here that reads the clock. Commands: `init`, `proposals` (list),
  `proposal show|file|approve|reject|promote`, `migrate`, `ingest`,
  `source add`.
- `docs/cutover-key-mapping.md` — the old `--json` keys against the new ones,
  for the cutover epic that re-points `gw wiki proposals`.
- `tests/` — pytest tests, mostly `tmp_path`-based. One fixture directory:
  `tests/fixtures/proposals/` holds 13 live proposals copied **byte-exact**
  and marked `-text` in `.gitattributes`, exactly as okf-io's and okf-ext's
  corpora are. `test_migrate_fixtures.py` asserts migrated bodies are
  byte-identical to them, so EOL normalization would break the regression
  silently. Do not edit a fixture; change the assertion *with* the file.

## Key abstractions

**Source reading** (`reading/`) is purely mechanical: given a path, guess a
title, extract text, classify a language, list a directory, find link targets.
It knows nothing about OKF, proposals, or the wiki — it is the substrate-neutral
half that any consumer (this package, or something else entirely) could sit on.

**Ingest briefs** (`ingest/`) answer "what am I about to deal with?" before an
ingest happens — they read and predict, never write. `plan_document_brief`
(one file) → title, slug, source kind, preview, word count, target source page,
merge-or-create. `plan_folder_brief` (a directory) → file manifest, sizes,
languages, representative file, refusals/warnings. `plan_batch_brief` (a
directory + a `kind`) → a manifest of ingest units, capped by default. Each
brief is a distinct frozen type with only the fields its mode needs — no shared
base, no `is_folder`/`is_batch` discriminator to misread. `as_data()` on each
returns a dict shape pinned key-for-key by its mode's
`test_as_data_is_the_legacy_dict`; the classification key is `source_kind`, not
`source_type`. Refusals and warnings are two separate, closed vocabularies
(`FolderRefusal`/`FolderWarning`) rather than one untyped channel. Nothing on
this path raises; a refused folder still reports its file count and size.

**Proposals** (`proposals/` + `okf_ext.proposals`) are the propose → decide →
promote lifecycle for a wiki page. A proposal is identified by the **target
path** it argues for (not a `<kind>-<slug>` id — that scheme is retired with no
compat alias, see `docs/cutover-key-mapping.md`). `proposal file` upserts:
filing a second source against a page that already has an open proposal merges
into its `sources[]` and re-renders the body. `proposal approve`/`reject`
append an OKF §5.2 `verified` event rather than writing a `decided:` key.
`proposal promote` writes the real page (section skeleton via the bundle's own
`sections/`, not this package's asset copies — they diverge the moment a human
edits one) and flips the proposal to `created`, in one plan. `migrate` rewrites
old-dialect proposals (`kind`/`mode`/`target_slug`/`origins[]`, no `type`) into
current shape: rewrite in place, reload the bundle, then move via
`okf_ext.moves` — in that order, so a failure never leaves a page claiming a
type its directory disagrees with.

**Diátaxis typing** (`diataxis/`) never guesses a type — `classify()` validates
a decision an agent already made (author intent isn't recoverable from text)
and returns `Unclassified` with one of five closed reasons otherwise. Retyping
a page (`retype.py`) is a move, not a metadata edit: the `type:` rewrite lands
before the directory relocation.

**Source pages** (`sources/`) are OKF's record of ingested material:
`source add` writes one `Source` page plus a byte-for-byte copy under
`sources/references/`, as a single two-write plan that commits together or not
at all. A second `source add` for existing material is refused, never merged.
`sources/references/*` is in every `ignore=` list here — ignored still means
present (`has_member` counts it), it's just not a concept.

## Data flow and gotchas

- **Only `cli.py` reads the clock.** Every other function takes
  `today`/`at`/`on` explicitly, matching `okf_io.validate()`'s own rule.
- **The code graph is a seam, not a dependency.** `ingest/seams.py` defines
  `StateGate` and `EntityMatcher` as narrow `Protocol`s because the real
  implementations (`compute_state_gate`, `match_entity`) live in
  `code-wiki-okf`, this package's *sibling* — importing them would stack two
  siblings into one layer. Absent, a brief reports `state_gate=None` and
  `NO_ENTITY` (both fields `None`), exactly what legacy produced with no reader.
- **`reading/` imports stdlib and itself only; `ingest/` imports stdlib +
  `reading/` only.** Both enforced mechanically by
  `tests/test_reading_boundaries.py`, which walks each directory with `ast`
  and allowlists from `sys.stdlib_module_names` — a new file is covered with
  no test edit, but a new import of `okf_io`, `typer`, `diataxis/`, or
  `sources/` from inside either boundary fails immediately.
- **`raw/` is gone.** This package does not read or write a `raw/` directory;
  `source_kind` and batch `kind` are always explicit arguments, and
  `sources/references/` is the durable copy location. Text extraction from
  binary material (PDF, image) is also out of scope — `source add` records
  such files byte-for-byte but the page body is just the section skeleton plus
  the caller's `--title`/`--description`.
- **The migrator never runs against the live vault.** `wiki/` is not yet an
  OKF bundle; no test or CLI default here reads `$GRAPH_WIKI_WORKSPACE`, and
  `test_migrate_fixtures.py` asserts it.
- **The curated-page claims contract lives in the seeds, its mandate in an
  annotation.** `_base-diataxis` declares `about:` (scanner-resource URIs) and
  the `$defs/entry` shape; `Adr` adds `decisions:` (ids `D<n>`) and
  `Explanation`/`Reference`/`HowTo` add `claims:` (ids `C<n>`). No schema
  *requires* them: the top-level `x-okf-about` annotation on those four types
  is the mandate, read by `okf_ext.schemas.declared_about` and enforced by
  `code_wiki_okf.about_rule` at its own severity. `applies_to` is gone.
  `Concept` has no schema; legacy `concept`/`pattern` pages are the cutover's job.
- **This layer supplies none of `OWNED_PROVENANCE_KEYS`.** `generated`,
  `sources`, and `verified` belong to the `okf_ext.proposals` capability;
  `plan_create` raises if a caller here supplies one.
- **`archive.py` predates its own CLI command.** The module is complete and
  tested (targeted-by-token and sweep-by-eligibility modes, index reconcile
  after move) but `cli.py` has no `doc-wiki-okf archive` entry point — check
  before assuming the feature is reachable from the command line.

## `reading/`

`reading/` is the substrate-neutral half of ingest: file and format inspection,
with no knowledge of briefs, lanes, or Diátaxis. `iter_link_targets` and
`resolve_companion` are public rather than underscore-prefixed because they
cross a module boundary. `SkillBundle` is frozen and its two file lists are
tuples.

There is no skill-ingest brief here — chunking a skill directory into guidance
pages is a layer above this one, and the substrate-neutral half it would need
already lives in `reading/skills`.

## Conventions

- Value types are frozen dataclasses with tuple fields, matching
  `okf_ext.proposals`, `moves`, and `generators`.
- Version is static (ADR 2026-08-02-versioning-independent-static). Tag `doc-wiki-okf-vX.Y.Z`.
- Module root is flat and singular (ADR 2026-08-02-naming-okf-io): one `src/doc_wiki_okf`, no
  namespace nesting.
