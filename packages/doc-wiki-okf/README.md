# doc-wiki-okf

The documentation-wiki lane over OKF v0.2 — see
`wiki/work/2026-08-11-epic-doc-ingestion-layer/01-design-spec.md` in the
graph-wiki workspace for the full architecture, and
`wiki/work/2026-08-11-epic-feature-scaffold-package-reading-core/01-design-spec.md`
for what this first slice ships.

## What is here today

`doc_wiki_okf.reading` — substrate-neutral file and format inspection, ported
from the legacy `wiki_io.ingest_source`:

| Symbol | What it does |
|---|---|
| `slugify(text)` | normalize free text to a ≤60-char identifier, `"untitled"` when empty |
| `extract(path)` | text plus a title guess for `.md` `.txt` `.html` `.htm` `.json` `.csv` |
| `language_for(path)` | extension → language name, `"unknown"` when unmapped |
| `list_folder_files(root)` | sorted `(rel_path, size)` for every regular file under `root` |
| `pick_representative(root, entries)` | `README.md` → `index.*` → largest |
| `resolve_skill_anchor(path)` | the `SKILL.md` a skill ingest anchors on, or `None` |
| `gather_skill_sources(anchor)` | one `SkillBundle` — `SKILL.md` plus transitively linked companion markdown |

## Boundaries

`reading/` imports the standard library and `doc_wiki_okf.reading.*`, and nothing
else — not `okf_io`, not `okf_ext`, not the rest of `doc_wiki_okf`. The check
walks every `.py` file under `reading/` with `ast` and takes its allowlist from
`sys.stdlib_module_names`, so a module added later is covered the moment it
exists. See `tests/test_reading_boundaries.py`.

The package declares `okf-io`, `okf-ext[schemas]` and `typer` because the later
slices of this lane use them. The reading layer uses none of the three.

`ingest/` takes the same treatment one layer out: it imports the standard
library and `doc_wiki_okf.reading`, and nothing else — not `okf_io`, not
`diataxis/`, not `proposals/`. Both contracts live in
`tests/test_reading_boundaries.py`, each deriving its module list from disk.

## The four Diátaxis types

`doc_wiki_okf.diataxis` declares the four types this lane knows, each with a
schema, a section declaration, and a lane directory:

| Type | Lane directory |
|---|---|
| `Tutorial` | `tutorials/` |
| `HowTo` | `how-tos/` |
| `Reference` | `references/` |
| `Explanation` | `explanations/` |

Per ADR-0012, each type's schema carries the directory as an `x-okf-directory`
annotation, and `pages.directory_for()` reads it to place a **new** page. The
annotation is a **writer-only hint** — it tells a writer where to *create*.
Nothing reads it to *locate* an existing page: `type` stays the sole source of
truth for what a page is, and a hand-moved page is still a legal page.

### `classify()`

`classify(schema_set, *, type_name, title, rationale, decided_by)` validates a
typing decision an agent already made; it never picks a type itself — Diátaxis
types differ by author intent, and text signals do not carry intent. It returns
either a `Classification` or an `Unclassified` naming one of five reasons:

| Reason | When |
|---|---|
| `undecided` | `type_name` is blank — the caller declined to choose |
| `unknown-type` | `type_name` is not one of the four |
| `undeclared-type` | the type is real but its schema was never installed into this bundle |
| `no-title` | a blank title derives no concept id |
| `no-rationale` | the type was chosen with no stated reason |

A blank rationale is a refusal, not a warning: an untyped page and a page typed
on purpose must stay distinguishable.

### Retyping is a move

`diataxis.plan_retype()` / `apply_retype()` change a page's `type:` **and**
relocate it into the new type's lane directory — a Diátaxis type is a design
decision, not metadata a validator patches in place. The `type:` rewrite lands
first, then the move, so a failure never leaves a page claiming a type its
directory disagrees with. Because retyping is a move rather than an edit,
`okf_ext.moves` is a permanent dependency of this lane, not a one-off migration
tool.

## Ingest briefs

`doc_wiki_okf.ingest` answers the question you ask before an ingest begins:
point at a path, learn what you are about to deal with.

| Builder | Given | Answers |
|---|---|---|
| `plan_document_brief` | one file | title, slug, source type, preview, word count, target source page, merge-or-create |
| `plan_folder_brief` | a directory | file manifest with sizes and languages, representative file, refusals, warnings |
| `plan_batch_brief` | `raw/<kind>/` | a manifest of ingest units, capped at a limit |

Each returns a frozen brief carrying the fields its own mode needs. There is no
abstract base and no `is_folder` / `is_batch` discriminator field — the type is
the discriminator, so a caller holding a `FolderBrief` cannot read `entity_match`
off it and get `None`, because the attribute is not there.

Each brief carries `as_data()`, which returns the dict the legacy
`wiki_io.ingest_source` module returned for the same input, key for key —
`_error` sentinel, bare-string `warnings`, `is_folder` / `is_batch` flags and
all — with two exceptions: `word_count` and `in_repo_doc` on `DocumentBrief`
use corrected computation, where the legacy formula was itself inconsistent
with its own fixtures. That is the parity contract the port is tested against.

### Refusals and warnings are two vocabularies

Legacy conflated two severities into one untyped channel. Here a folder over 200
files comes back as a `FolderBrief` carrying a `FolderRefusal`, and a folder over
50 files (or one holding a file over 200 KB) carries a `FolderWarning`:

    RefusalKind = Literal["folder-too-large"]
    WarningKind = Literal["folder-size", "large-file"]

The brief always comes back — nothing on the content path raises. A refused
folder still reports its file count and total size, the facts that explain the
refusal, where legacy returned the sentinel and nothing else.

### The layout is a value, the clock and the code graph are arguments

`IngestLayout` carries `raw_dir`, `archive_dir`, `source_types`, `batch_kinds`
and `source_page_template`; `GRAPH_WIKI_LAYOUT` is today's values and the
default for the two builders that take `layout=` — `plan_document_brief` and
`plan_batch_brief` (`plan_folder_brief` takes no `layout` at all).
`plan_document_brief` takes a **required** `today=` — the same
rule `okf_io.validate` and `append_log_entry` follow, and `date.today()` is
called in exactly one place in this package, the CLI.

The code graph arrives as two optional callables behind narrow protocols,
`StateGate` and `EntityMatcher`, because `compute_state_gate` and `match_entity`
live in `code-wiki-okf` — this package's sibling, not its dependency. Absent,
a brief reports `state_gate=None`, and a `DocumentBrief` reports an
`EntityMatch` with both fields `None`, exactly what legacy produced when its
reader was `None`.

### What is declined

The legacy `build_skill_ingest_brief` does not port. It builds a
guidance-shaped brief — chunking a skill directory into
`wiki/guidance/<topic>/<slug>.md` — and that is the guidance flow, a layer above
this one. The substrate-neutral half already lives in
`doc_wiki_okf.reading.skills` (`gather_skill_sources`, `SkillBundle`,
`resolve_skill_anchor`) and is ready for whoever claims that layer. `raw/skills/`
keeps mapping to source type `skill`, and `skills` stays a batch kind.

No brief carries a Diátaxis type, and `ingest/` does not import `diataxis/`. An
ingested document produces a source page — a record of a document, not a document
*of a type* — and the type is chosen when a proposal is filed against one of the
five lanes.

### The command

    doc-wiki-okf ingest SOURCE --workspace DIR         # human-readable
    doc-wiki-okf ingest SOURCE --workspace DIR --json  # `as_data()`
    doc-wiki-okf ingest DIR/raw/specs --workspace DIR --all   # no cap on the batch manifest
    doc-wiki-okf ingest SOURCE --today 2026-08-12      # brief as of a date

The command runs a batch → folder → single cascade, because a command taking a
path has no choice but to decide what the path is. The library offers no router:
the three builders are the surface. Skill detection is absent — a skill directory
briefs as a folder here.

## The proposal-state migrator

`doc-wiki-okf migrate ROOT` rewrites every old-dialect proposal in `ROOT` --
frontmatter carrying `kind`/`mode`/`target_slug`/`origins[]` and no `type` --
into `okf_ext.proposals` shape. A document already carrying `type: Proposal`
is skipped, so a re-run is an empty plan rather than an error.

The sequence is **rewrite, reload, move**: each old-dialect document is
rewritten in place first (frontmatter only, body carried verbatim), the
bundle is reloaded, and only then is each rewritten document relocated by
`okf_ext.moves`, which repairs every inbound reference on the way. Refusals
are all-or-nothing per document -- one unusable `kind`, `status` or
`target_slug` declines that proposal and its neighbours still migrate.

Unlike every other mutating command in this package, `migrate` **previews by
default and writes only under `--apply`** -- a whole-bundle rewrite plus a
batch move should not happen because someone typed a path:

    doc-wiki-okf migrate ROOT              # prints the plan, writes nothing
    doc-wiki-okf migrate ROOT --apply       # rewrites, reloads, and moves
    doc-wiki-okf migrate ROOT --json        # the plan as JSON

See `docs/cutover-key-mapping.md` for the full old-key-to-new-key mapping.

## Commands

    uv run --package doc-wiki-okf mypy --strict packages/doc-wiki-okf/src
    uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests

Coverage is gated at 95% (`just cov`).
