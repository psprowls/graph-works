# packages/doc-wiki-okf

Python ≥3.12 (the workspace floor). Tests are pytest.

## Layout

- `src/doc_wiki_okf/reading/` — substrate-neutral file and format inspection.
  Five modules, each answering one question: `slug` normalizes text to an
  identifier, `extract` turns one file into text plus a title guess, `files`
  reports on a directory, `links` finds markdown link targets, `skills`
  assembles a skill directory into one blob.
- `src/doc_wiki_okf/reading/__init__.py` — the re-export surface. `links`'
  two functions are deliberately **not** on it: `reading/` is an internal
  subpackage and its `__init__` exports what the rest of the lane consumes.
- `src/doc_wiki_okf/assets/` — the twelve files this package installs into a
  bundle: six `schema/` JSON schemas (a `_base-diataxis` `$ref` target plus
  the four Diátaxis types plus `Source`) and six `sections/` declarations
  (`_fragments.doc_wiki.yaml` plus the same five). Read through
  `importlib.resources`; installed by `okf_ext.bundle.plan_install`, which is
  C3's call.
- `src/doc_wiki_okf/resources.py` — `SEED_RELATIVE_PATHS`, `assets_root()`,
  `seed_files()`. The asset list without the installer.
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
- `src/doc_wiki_okf/cli.py` — the typer app behind `[project.scripts]`. The one
  module here that reads the clock.
- `docs/cutover-key-mapping.md` — the old `--json` keys against the new ones,
  for the cutover epic that re-points `gw wiki proposals`.
- `tests/` — pytest tests, mostly `tmp_path`-based. One fixture directory:
  `tests/fixtures/proposals/` holds the 13 live proposals copied **byte-exact**
  and marked `-text` in `.gitattributes`, exactly as okf-io's and okf-ext's
  corpora are. `test_migrate_fixtures.py` asserts migrated bodies are
  byte-identical to them, so EOL normalization would break the regression
  silently. Do not edit a fixture; change the assertion *with* the file.

## Conventions

- **`reading/` imports stdlib and itself, nothing else.** Enforced by
  `tests/test_reading_boundaries.py`, which walks the directory with `ast` and
  allowlists from `sys.stdlib_module_names`. A new `.py` file under `reading/`
  is covered with no edit to that test; a new import of `okf_io`, of `typer`,
  or of a sibling `doc_wiki_okf` subpackage fails it.
- Value types are frozen dataclasses with tuple fields, matching
  `okf_ext.proposals`, `moves` and `generators`.
- Version is static (ADR-0007). Tag `doc-wiki-okf-vX.Y.Z`.
- Module root is flat and singular (ADR-0006): one `src/doc_wiki_okf`, no
  namespace nesting.
- **`diataxis/` may import `reading/`; `reading/` may not import `diataxis/`.**
  Already enforced — `tests/test_reading_boundaries.py` allowlists stdlib and
  `doc_wiki_okf.reading` only, so a new file under `reading/` reaching sideways
  fails it with no edit to the test.
- **The type list lives once.** `TYPE_NAMES` is unpacked from `RUBRIC`, and
  `tests/test_rubric.py` asserts every name in it has both a `schema/` and a
  `sections/` file in `SEED_RELATIVE_PATHS`. Adding a fifth type without its two
  declaration files fails there rather than at a consumer.
- **The classifier never guesses.** Diátaxis types differ by author intent and
  text signals do not carry intent, so `classify()` validates a decision and
  returns `Unclassified` with one of five closed reasons when it cannot. A blank
  rationale is a refusal, not a warning.
- **Only `cli.py` reads the clock.** Every other module takes `today`/`at`/`on`
  as an argument, which is okf-io's own rule for `validate()` and
  `append_log_entry`.
- **An ADR is an `Explanation` in `adrs/`, dated at promotion.** No fifth schema
  and no fifth section declaration: `x-okf-directory` is a writer-only hint and
  `type` is the sole source of truth (ADR-0012). That sentence is one row in
  `lanes.DIATAXIS_LANES`' neighbour, and nothing else restates it.
- **This layer supplies none of `OWNED_PROVENANCE_KEYS`.** `generated`,
  `sources` and `verified` are the capability's; `plan_create` raises on a
  caller that supplies one, and `test_promote.py` asserts we do not.
- **The migrator never runs against the live vault.** `wiki/` is not an OKF
  bundle yet, nothing in this package's tests or CLI defaults reads
  `$GRAPH_WIKI_WORKSPACE`, and `test_migrate_fixtures.py` asserts it.

## The port

`reading/` is a re-decomposition of the substrate-neutral half of the legacy
650-line `wiki_io/ingest_source.py`, and most of its tests came across
unchanged from `wiki-io`'s own suite — an unedited passing test is the
behaviour-parity evidence. Two names lost a leading underscore on the way
(`iter_link_targets`, `resolve_companion`) because they now cross a module
boundary. `SkillBundle` became frozen, and its two file lists became tuples.

## Not here

`raw/` itself. This package no longer reads or writes a `raw/` directory:
material is ingested from anywhere, `source_kind` and batch `kind` are explicit
arguments, and the copy in `sources/references/` is the material's durable
location. The three live vaults' own 610 `raw/` files are untouched — moving
them is a data migration and belongs to the live-vault migration item.

Text extraction from binary reference material. `source add` records a PDF or
image byte-for-byte (ADR-0031) but does not read it: the page body is the
section skeleton plus the caller's own `--title`/`--description`, and the
agent door's ingest brief goes content-blind rather than summarizing content
nobody read. Real extraction needs a new runtime dependency and its own work
item.

Re-pointing `gw wiki proposals` and editing `/graph-wiki:proposals` are the cutover epic's — `gw`
lives in `agent-research` and nothing in this workspace can keep those command names working. See
`docs/cutover-key-mapping.md`.

`Concept` and `Adr` schemas are deliberately absent: the fate of the existing
`concept`/`pattern`/`architecture` kinds belongs to the cutover epic, and
declaring `Concept` here would pre-empt it. Retrofitting the live vault's nine
concept pages is that epic's content sweep — this package makes it possible and
does not perform it. `Source` is the one type declared here that the cutover
epic did not defer: `sources/` has no contested legacy vocabulary, `source_kind`
takes three values across three vaults, and the migration's 173
`frontmatter.missing-type` source pages need a `type` value to be mapped *to*.
