# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A `uv` workspace (`members = ["packages/*"]`) for OKF tooling. The root is a
workspace root only — not distributable, `package = false`. It holds thirteen
packages today, plus one plugin tree (not a workspace member, not Python):
`plugins/gw`, the first-party Claude Code plugin this repo publishes and the
one Claude Code loads (name `gw`).

The OKF v0.2 spec is **not** in this repository. Code and docs cite it by
section (`§5.1`, `§11`) and expect you to reason from those citations.

### The package map

The suffix on a package name is a band, and a permission — what it may
couple to:


| Suffix               | Band           | May couple to                                                                                                                                                          |
| -------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-io`                | 1, foundation  | stdlib and declared third party only — never a sibling package, never a workspace path; `os` only where a package's own boundary test names a narrow, tested exemption |
| `-okf`               | 2, OKF-aware   | band 1 plus the OKF document model                                                                                                                                     |
| `-core`              | 3, application | everything below it; exactly one package (`graph-works-core`) knows what a workspace is                                                                                |
| `workflow-<backend>` | beside band 1  | vendor/system coupling lives here — one package per dispatch backend                                                                                                   |


Each package carries its own `AGENTS.md` (via a `CLAUDE.md` that just
`@`-imports it) with its module layout and gotchas. This file covers what
spans packages.


| Package            | Band     | What it is                                                                                                                                                     |
| ------------------ | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `okf-io`           | 1        | Read/derive/write-back for OKF v0.2 concept documents — the spec, nothing else. See "okf-io" below; this is the package the rest of the `-okf` band builds on. |
| `config-io`        | 1        | Schema-driven, git-like scoped configuration over YAML.                                                                                                        |
| `models-io`        | 1        | Guarded Bedrock / Vercel AI Gateway model constructors.                                                                                                        |
| `subagents-io`     | 1        | Bounded async fan-out pool for role-bound model dispatch.                                                                                                      |
| `code-graph-io`    | 1        | Code-graph core: SQLite store, tree-sitter parsing, manifest scanning, read-only queries.                                                                      |
| `workflow-local`   | beside 1 | Subprocess `DispatchBackend` for `subagents-io`.                                                                                                               |
| `workflow-orca`    | beside 1 | Orca-Run `DispatchBackend` for `subagents-io`.                                                                                                                 |
| `okf-ext`          | 2        | Beyond-spec capabilities over any OKF v0.2 bundle — extends `okf-io`, never modifies it.                                                                       |
| `code-wiki-okf`    | 2        | Generates/updates a standalone OKF v0.2 bundle from the shared code graph.                                                                                     |
| `doc-wiki-okf`     | 2        | The documentation-wiki lane: source reading, ingest briefs, proposals.                                                                                         |
| `work-tracker-okf` | 2        | Work-item tracking as an OKF v0.2 lane.                                                                                                                        |
| `graph-works-core` | 3        | What a workspace is: discovery, the layout object, the manifest, init.                                                                                         |
| `graph-works-cli`  | —        | `gw`: a thin Typer interface over `graph-works-core` (ADR-0013 — logic stays in core, this package routes/parses/formats/traces).                              |


Dependencies only point down the table (never up, never sideways within a
band except through the band-1 `DispatchBackend` seam). `graph-works-core` is
deliberately the only package that resolves a workspace root or reads
`workspace.yaml` — every package beneath it receives resolved paths as
arguments.

## Commands

Every `just` recipe is exactly what a future CI job will call. No CI workflow
exists yet — enforcement is local, by design (ADR-0010).


| Command                  | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `just` / `just check`    | `sync` + `normalization` + `text-io` + `line-endings` + `platform-declared` + `lint` + `types` + `contracts` + `cov` + `test-plugin` — the full gate                                                                                                                                                                                                                                                                                                      |
| `just sync`              | `uv sync --all-packages` — provisions every member's deps, not just the root's                                                                                                                                                                                                                                                                                                                                                                            |
| `just normalization`     | Unicode-normalization check on tracked filenames (cheap, run first)                                                                                                                                                                                                                                                                                                                                                                                       |
| `just text-io`           | Implicit text-IO defaults in shipped source — a missing `encoding=` on any text read/write, or a missing `newline=` on any text write. Not a `lint` addition deliberately: ruff's `PLW1514` is preview-only and covers `encoding=` alone, and `scripts`/`plugins` sit in ruff's `exclude`. Scope is `packages/*/src` and `scripts`, plus the three byte-exact test trees (`okf-io`, `okf-ext`, `scripts/tests`); the other nine still rely on a suite run |
| `just line-endings`      | A tracked file that would check out CRLF under Git for Windows' default `core.autocrlf=true`                                                                                                                                                                                                                                                                                                                                                              |
| `just platform-declared` | A package that imports a POSIX-only module, or reaches a POSIX-only process primitive, with no `## Platform` section declaring it — ADR-0021 rule 3a as a check                                                                                                                                                                                                                                                                                           |
| `just lint`              | `uv run ruff check . && uv run ruff format --check .`                                                                                                                                                                                                                                                                                                                                                                                                     |
| `just types`             | `uv run mypy --strict`, **twice per package** — once per `--platform` arm (`linux`, then `win32`), 24 invocations total — via `uv run --package <name> mypy --strict --platform <arm> packages/<name>/src` (okf-io and okf-ext share a bare `uv run` since they share the root `testpaths`); a POSIX host cannot otherwise see a Windows-only `mypy --strict` failure, or vice versa                                                                      |
| `just contracts`         | `uv run lint-imports` — the workspace's band/suffix boundaries plus okf-ext's internal capability boundaries                                                                                                                                                                                                                                                                                                                                              |
| `just test`              | `uv run pytest`, plus one `uv run --package <name> pytest packages/<name>/tests` per non-okf-io/okf-ext package                                                                                                                                                                                                                                                                                                                                           |
| `just cov`               | Branch coverage, gated per package (95% for most, 90% for `code-graph-io`) — see the justfile for exact invocations; a failure reports only a global percentage, so start with the lowest-covered module and read `term-missing`                                                                                                                                                                                                                          |
| `just test-plugin`       | The `gw` plugin's own test suites (bash, plus one `node --test` suite), under `plugins/gw/`                                                                                                                                                                                                                                                                                                                                                               |


Every suite `just check` runs is Python, bash, or — for the plugin's
`tests/pi` extension suite alone — `node --test`. Node 23.6+ is required for
that one suite; `npm` is not required by anything in the gate any more.

Single test / subset (pytest `testpaths` and `pythonpath` are preconfigured
for okf-io/okf-ext, so these work from the repo root):

```bash
uv run pytest packages/okf-io/tests/test_document.py
uv run pytest packages/okf-io/tests/test_roundtrip.py::test_mutation_touches_only_the_intended_line
uv run pytest -k "splice or crlf"
uv run pytest -x -q packages/okf-io/tests/test_catalog.py
```

Every other package resolves its own dependency closure and runs under
`--package`:

```bash
uv run --package graph-works-cli pytest packages/graph-works-cli/tests -k work
uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests
uv run --package code-graph-io mypy --strict --platform linux packages/code-graph-io/src
uv run --package code-graph-io mypy --strict --platform win32 packages/code-graph-io/src
```

`uv run mypy --strict` on its own only resolves the root's dependencies —
workspace members' own deps (e.g. `typer`) need `just sync` first, which is
why `types` and `cov` both depend on it.

## okf-io: the OKF v0.2 document model

`okf-io` is the foundational package the whole `-okf` band builds on. Its own
`AGENTS.md` is the module-level map; this is the architecture every consumer
needs to know exists.

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

`validate()` takes a **required** `today=` keyword — okf-io never reads the
clock — plus optional `strict=False` and `extra_rules=()`. Nothing rejects a
bundle: the catalog's rule functions yield `Finding`s and `validate()` returns
a `Report` (`.errors`, `.warnings`, `.ok`, `.by_code()`). The current catalog
size is pinned in `test_catalog.py` and quoted in `packages/okf-io/AGENTS.md`
— check there rather than trusting a number restated here, since it has
drifted before without anything noticing.

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
`sources[]`. **Its trigger is membership in** `doc.fm.fallbacks`, the same set
`_rules/legacy.py` keys off, so reader, validator and writer can never disagree
about what counts as v0.1; it never re-scans a document independently. Refusals
are all-or-nothing per document and come back as `Unmigrated` entries with a
closed `reason` vocabulary.

Two places where the shipped code is wider than the spec that describes it. A
**blank or non-instant** `timestamp` (any value the reader cannot coerce to a
datetime — an int, a bool, a list, a mapping, an unparseable string) fires the
read fallback but carries nothing to migrate, so the rewriter declines every
such value — migrating one would only launder a broken value into v0.2 shape
while silencing the warning that flags it. Only the blank/whitespace-only
string stays silent about it, matching `_rules/legacy.py`'s own exemption;
every other shape still fires `legacy.timestamp`, so the decline is reported
too, as an `Unmigrated` with reason `"not-an-instant"`. And a **partial**
`generated` — an authored `by` with no `at` — fires it too, so rewrite A
fills `at` in place rather than inserting a block over the author.

All three writers default to `dry_run=True`. `update_index()` also defaults
to `create_missing=False`. `IndexUpdate`, `LogAppend`, and `Migration` share
one vocabulary: `before`, `after`, a `changed` property that renders nothing,
and a `diff()` that writes nothing. (Every writer added since, in every other
`-okf`/`-core` package, follows the newer convention instead: a `plan_*`
function that writes nothing and an `apply_*` that does — not calling `apply_*`
is the dry run, with no `dry_run=` flag at all. `okf-io`'s three writers
predate that convention and were not retrofitted.)

## Invariants to respect

- **Exactly two runtime dependencies** in okf-io: `ruamel.yaml` and
`markdown-it-py`. A third requires its own ADR. This is a scope boundary, not
an implementation detail.
- **okf-io is the pure core** (ADR-0005). Every other package depends on it
(directly or via `okf-ext`), never the reverse. Schema validation, harvesters,
servers, wiki generation — none of those belong here. The core instead
exposes five extension points, all shipped and contract-tested before any
consumer exists: `extra_rules=` on `validate()`, `Document.fm_data(dates="iso")`
(plain-data projection, `json.dumps`-able with no encoder), `ignore=` on
`load_bundle()` (an ignored member is "not a concept", not "not there"),
`describe=` on `update_index()`, and `scope=` on `validate()` / `RuleContext`
(a `frozenset[str] | None` of bundle-relative member paths that constrains
per-document rule iteration). `validate(links=)` is a related optimisation
parameter, not an extension point — passing a pre-built `LinkGraph` skips
recomputing it and changes no behaviour.
- `okf_io.validate` **is the function, not the submodule.** Use
`from okf_io.validate import Finding, Report, RuleContext`. Both
`import okf_io.validate as m` and `from okf_io import validate as m` bind the
function. Deliberate.
- **Versions are static** (ADR-0007), never `hatch-vcs`, across every package
in the workspace. Tags `<pkg>-vX.Y.Z`. Pre-1.0: minor = breaking, patch =
compatible.
- **The band/suffix permission table above is enforced, not just documented.**
Most packages carry both an import-linter contract (`just contracts`) and a
hand-written AST boundary test, because import-linter's `grimp` backend
cannot see every violation shape (e.g. an ancestor-package import, or a
capability directory added without a matching contract entry). Read a
package's own `test_*_boundaries.py` before assuming `just contracts` alone
covers it.

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

## The plugin (`plugins/gw`)

`plugins/gw/` is **not** a workspace member and is not Python — it is the
first-party Claude Code plugin this repo publishes (skills and hooks, name
`gw`), registered by the root `.claude-plugin/marketplace.json`. It is
ordinary first-party code: edit it like any other tree here. It was once a
vendored `git subtree` of [obra/superpowers](https://github.com/obra/superpowers)
per ADR-0019; that fork, its ledger and its re-sync ritual were deleted by
`epic-unforked-plugin-skill-dispatch`, and pulling an upstream release is now
reading their diff and deciding, not a `git subtree pull`.

Its suites run under `just test-plugin`, which is enforcing and part of
`just check`. Every suite in the tree is named there explicitly — a new suite
gets a line in the same change, because a suite nothing invokes is not coverage.

## The knowledge base lives outside this repo

ADRs, concepts, entity pages, sources and work items live in a graph-works
workspace at `$GRAPH_WORKS_DIR`
(`/Users/pat/Personal/graph-works/gw-workspace`, exported by `.envrc` via
direnv and pinned in `.claude/settings.local.json`). ADRs referenced
throughout the code are `$GRAPH_WORKS_DIR/okf/adrs/`. Read them before
revisiting a settled decision — most surprising choices in this codebase are
documented there with their rejected alternatives.

(`GRAPH_WIKI_WORKSPACE` is an **old** layout's env var name — `graph-works-core`
explicitly does not honor it, and a workspace under that shape resolves as
"not a workspace" rather than falling back. If you see it referenced anywhere
outside a test asserting the old name is rejected, that reference is stale.)

Work items are driven stage-by-stage with `/gw:workflow <path>` (one stage per
session, fresh context between stages). `scripts/` is repo tooling, excluded
from ruff and not part of any package.

Feature work runs in git worktrees under `.claude/worktrees/` (gitignored).