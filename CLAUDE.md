# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A `uv` workspace (`members = ["packages/*"]`) for OKF tooling. The root is a
workspace root only — not distributable, `package = false`. One member today:
`packages/okf-io`, which reads, derives from, and writes back **OKF v0.2**
concept documents.

The OKF v0.2 spec is **not** in this repository. Code and docs cite it by
section (`§5.1`, `§11`) and expect you to reason from those citations.

## Commands

Every `just` recipe is exactly what a future CI job will call (CI is deferred —
no remote yet, ADR-0010).

| Command | What it does |
|---|---|
| `just` / `just check` | `lint` + `types` + `cov` — the full gate |
| `just lint` | `uv run ruff check . && uv run ruff format --check .` |
| `just types` | `uv run mypy --strict` over all four packages' `src` trees |
| `just test` | `uv run pytest` |
| `just cov` | pytest with branch coverage, **`--cov-fail-under=95`** |

Single test / subset (pytest `testpaths` and `pythonpath` are preconfigured, so
these work from the repo root):

```bash
uv run pytest packages/okf-io/tests/test_document.py
uv run pytest packages/okf-io/tests/test_roundtrip.py::test_mutation_touches_only_the_intended_line
uv run pytest -k "splice or crlf"
uv run pytest -x -q packages/okf-io/tests/test_catalog.py
```

Coverage is **gated at 95%**, and the margin over the floor is thin. A failure
reports only a global percentage; `models.py` and `document.py` carry the
coverage debt, so start there and read `term-missing`.

## Architecture

### The two-layer document model (ADR-0001)

`Document` owns the raw text plus a round-trippable ruamel `CommentedMap`
(`fm_raw`). `Frontmatter` — reached as `doc.fm` — is a **frozen, memoized view**
built from that map.

**Nothing on the content path raises.** A malformed concept still yields a
`Document`: the failure lands in `parse_error`, and fields that could not be
coerced are listed in `fm.coercion_failures`. This is spec §11's tolerance
requirement made structural. If you are adding a `raise` for a content reason,
you are fighting the design.

### Byte fidelity

An unmutated document serializes to its original bytes, exactly. A mutated one
is rendered by **diffing ruamel's render of the pristine data against its render
of the mutated data and splicing only that delta into the original text** — so
editing one key leaves its neighbours untouched, even in dialects ruamel cannot
reproduce (e.g. the pre-folded plain scalars a TypeScript `yaml` writer emits).

When the edited lines cannot be anchored in the original, it falls back to
re-emitting the whole frontmatter block. Still correct, still re-parses, just a
bigger diff. **A mutation is always valid and never lossy; it is minimal in the
common case, not in every case.** The two acceptance properties for this live in
`test_roundtrip.py` and run over every fixture.

`_yaml.py` subclasses `ruamel.yaml.emitter.Emitter`, which is not a stable
public contract. Bumping ruamel means re-running the round-trip suite.

### The read → derive → validate pipeline

```
load_bundle(root)  ->  Bundle        # ONE directory walk; every file read once
build_link_graph() ->  LinkGraph     # derived from that one walk; backlinks
validate(bundle, today=…)  ->  Report
```

`validate()` takes a **required `today=`** keyword — okf-io never reads the
clock — plus optional `strict=False` and `extra_rules=()`. Nothing rejects a
bundle: the catalog's 21 rule functions (31 codes, 9 topics) yield `Finding`s
and `validate()` returns a `Report` (`.errors`, `.warnings`, `.ok`,
`.by_code()`).

A `Finding` carries a dotted `code`, a `Severity` (`"error"` / `"warn"` as
*data*, not structure — ADR-0008), a message, a spec citation, and path/line.

`Bundle.assets` exists because non-markdown members are link targets; an
`rglob("*.md")` walk misses them and path validation then reports false
positives.

### The rule catalog — the module name IS the code prefix

Every rule in `_rules/trust.py` emits codes starting `trust.`, and
`test_catalog.py` asserts that mechanically. Adding a rule means adding it to
its topic module's `RULES` and `CODES`; a new topic means a new module plus an
entry in `_rules/__init__.py`'s two mappings.

Note `okf_io._rules.links` (Links-topic rules) vs `okf_io.links` (the graph) —
same last name, different things, a deliberate consequence of the invariant.

### The writers

`update_index()` **reconciles**, it does not regenerate (ADR-0009): okf-io owns
*which* entries appear, the human owns *what they say*. Dead entries pruned,
missing ones added, every other byte copied through — prose, comments, line
endings. No marker syntax, no generated region (markers are not in the spec, and
okf-io implements only what the spec defines — ADR-0005). Under the default
`descriptions="preserve"` drifted text is *reported* in `IndexUpdate.drift`, not
overwritten; `descriptions="refresh"` opts into rewriting.

`parse_log()` / `append_log_entry()` handle `log.md`; the append requires `on=`
or `today=` (again, no clock).

`migrate()` is the third writer and the write half of ADR-0003: it rewrites a
v0.1 bundle into v0.2 form — `timestamp` → `generated`, body `# Citations` →
`sources[]`. **Its trigger is membership in `doc.fm.fallbacks`**, the same set
`_rules/legacy.py` keys off, so reader, validator and writer can never disagree
about what counts as v0.1; it never re-scans a document independently. Refusals
are all-or-nothing per document and come back as `Unmigrated` entries with a
closed `reason` vocabulary.

Two places where the shipped code is wider than the spec that describes it. A
**blank or non-instant `timestamp`** (any value the reader cannot coerce to a
datetime — an int, a bool, a list, a mapping, an unparseable string) fires the
read fallback but carries nothing to migrate, so the rewriter declines every
such value — migrating one would only launder a broken value into v0.2 shape
while silencing the warning that flags it. Only the blank/whitespace-only
string stays silent about it, matching `_rules/legacy.py`'s own exemption;
every other shape still fires `legacy.timestamp`, so the decline is reported
too, as an `Unmigrated` with reason `"not-an-instant"`. And a **partial
`generated`** — an authored `by` with no `at` — fires it too, so rewrite A
fills `at` in place rather than inserting a block over the author.

All three writers default to **`dry_run=True`**. `update_index()` also defaults
to `create_missing=False`. `IndexUpdate`, `LogAppend`, and `Migration` share
one vocabulary: `before`, `after`, a `changed` property that renders nothing,
and a `diff()` that writes nothing.

## Invariants to respect

- **Exactly two runtime dependencies** in okf-io: `ruamel.yaml` and
  `markdown-it-py`. A third requires its own ADR. This is a scope boundary, not
  an implementation detail.
- **okf-io is the pure core** (ADR-0005). Every future workspace package depends
  on it, never the reverse. Schema validation, harvesters, servers, wiki
  generation — none of those belong here. The core instead exposes four
  extension points, all shipped and contract-tested before any consumer exists:
  `extra_rules=` on `validate()`, `Document.fm_data(dates="iso")` (plain-data
  projection, `json.dumps`-able with no encoder), `ignore=` on `load_bundle()`
  (an ignored member is "not a concept", not "not there"), and `describe=` on
  `update_index()`.
- **`okf_io.validate` is the function, not the submodule.** Use
  `from okf_io.validate import Finding, Report, RuleContext`. Both
  `import okf_io.validate as m` and `from okf_io import validate as m` bind the
  function. Deliberate.
- **Versions are static** (ADR-0007), never `hatch-vcs`. Tags `<pkg>-vX.Y.Z`.
  Pre-1.0: minor = breaking, patch = compatible.

## Test fixtures are a byte-exact contract

`packages/okf-io/tests/fixtures/` is marked `-text` in `.gitattributes` —
`edge/encoding/crlf.md` is uniformly CRLF and `bom.md` carries a UTF-8 BOM on
purpose, and EOL normalization would silently break the regressions they guard.
The directory is also excluded from ruff.

- `bundles/acme_retail`, `bundles/ga4` — **vendored verbatim** from
  `GoogleCloudPlatform/knowledge-catalog` at a pinned commit. Do not edit; see
  `fixtures/FIXTURES.md` to re-vendor.
- `edge/` — ours, but each file is a named regression. Change the assertion
  *with* the file, never the file alone.
- `nonconformant/` — triggers every catalog code in one walk; its reviewed
  output is `nonconformant.golden.txt`. Regenerating the golden alone proves
  nothing: what holds it honest is the hand-written code set in
  `test_catalog.py` and the zero-errors property on the two vendored bundles.

Fixture discovery lives in `tests/helpers.py`, not `conftest.py`. Use
`helpers.has_frontmatter()` rather than re-deriving it — a local
`lstrip("﻿").startswith("---")` disagrees with the splitter on doubled-BOM
files (`lstrip` strips every BOM; the splitter strips exactly one).

## The knowledge base lives outside this repo

ADRs, concepts, entity pages, sources and work items live in a graph-wiki
workspace at `$GRAPH_WIKI_WORKSPACE`
(`/Users/pat/Personal/workspaces/agent-workspace`, exported by `.envrc` via
direnv and pinned in `.claude/settings.local.json`). ADRs referenced throughout
the code are `$GRAPH_WIKI_WORKSPACE/wiki/adrs/`. Read them before revisiting a
settled decision — most surprising choices in this codebase are documented
there with their rejected alternatives.

Work items are driven stage-by-stage with `/graph-wiki:next <slug>` (one stage
per session, fresh context between stages). `scripts/gw_dispatch.py` automates
the plan/execute stages in background `claude --bg` sessions — see
`scripts/gw-dispatch.md`. `scripts/` is repo tooling, excluded from ruff and not
part of any package.

Feature work runs in git worktrees under `.claude/worktrees/` (gitignored).