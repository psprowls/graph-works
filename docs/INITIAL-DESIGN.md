# okf-io — Design Document

**Status:** Living document. This is the "full picture" working doc; once the
long-term plan stabilizes, individual pieces will be broken out into
brainstorming/design sessions and implementation plans.

**Last updated:** 2026-08-02

---

## 1. What this is

This repository is a **uv-workspace monorepo** of Python packages built
around the Open Knowledge Format. At its center is **okf-io**: a pure,
spec-defining core library implementing **OKF v0.2**. Every other package
in the workspace layers on top of okf-io for a specific use case (schema
registry, content creation, attestation, consumption) without polluting
the core.

The authoritative spec is `knowledge-catalog/okf/SPEC.md` (v0.2) in the
archive at `/Users/pat/Personal/archive/okf/`.

No spec-faithful v0.2 *library* exists in any language today:

- **okfcli** (Go) is spec-faithful but CLI-only (its embeddable library is
  roadmap vaporware).
- **okf-go** is a library but diverges from the spec in load-bearing ways.
- **okf-schema** (Python) is a well-engineered library but implements v0.1
  only.

okf-io fills that slot: a typed, round-trip-safe, validating Python library
for reading, writing, validating, and migrating OKF bundles.

---

## 2. Ecosystem review (condensed)

Full surveys were done 2026-08-02 against the four archived repos. Key
takeaways each, since the survey detail lives only in that session:

### 2.1 SPEC.md v0.2 — the contract

- Bundle = directory tree of markdown files with YAML frontmatter. Reserved
  filenames: `index.md` (§8), `log.md` (§9). Concept ID = path minus `.md`.
- `type` is the **only required key** (§4.1, §11). Everything else is
  optional, and consumers MUST NOT reject unknown types, unknown keys,
  broken links, or missing optional families (§11).
- Four optional frontmatter families:
  - **Provenance** (§5.1): `sources[]` with `resource` (required per entry),
    `id`, `title`, and credibility signals `author`, `usage_count`,
    `last_modified`; sibling `usage_window: {from, to}`. Per-claim
    attribution via markdown footnotes whose label joins to `sources[].id`.
  - **Trust** (§5.2–5.3): `generated: {by, at}`, `verified: [{by, at}]`.
    A bare `verified` mapping MUST be treated as a one-element list.
    Derived trust tiers: no `verified` ⇒ unverified; non-`human:` actors
    only ⇒ machine-confirmed; any `human:` actor ⇒ human-reviewed.
  - **Lifecycle** (§5.4–5.5): `status: draft|stable|deprecated` (absent ⇒
    stable); `stale_after: YYYY-MM-DD` (stale when `today >= stale_after`).
  - **Attested computation** (§10): `type: Attested Computation` with
    `runtime` (required for the type), `parameters[] {name, type,
    required}`, `computation` (path) XOR inline body `# Computation` fence,
    `executor: {resource, receipt}`, `attester: {resource}`.
- Actor convention (§7): `<producer>/<version>` for agents, `human:<id>`,
  `process:<id>`. Trust-tier derivation keys off the `human:` prefix.
- Links (§6): standard markdown links; absolute (`/…` = bundle-root,
  recommended) or relative. All links are untyped directed edges. Broken
  links are not malformed.
- v0.1 → v0.2 breaking changes (§13.1): `timestamp` superseded by
  `generated.at`; body `# Citations` list superseded by `sources`.
  Consumers MAY fall back to both legacy forms.

### 2.2 okfcli (Go) — the best validation checklist

~1.6k LOC production Go, one dependency (yaml.v3), 117 test functions.
Agent-first design: JSON-only stdout, self-describing `okf schema` command,
stable exit codes (0 ok / 1 validation / 2 I/O / 3 internal / 4 usage),
structured error envelopes, findings that cite spec sections, repair hints
for broken links, deterministic ordering everywhere.

Its validation coverage is the best existing checklist (see §8 below).
Notable behaviors to replicate: injectable clock for staleness tests;
lenient "tolerate, don't fail" parsing so one bad concept never kills a
bundle load; broken-relative-link repair hints (suggest the absolute form
when it would resolve from the root).

Gaps/warts (avoid): no round-trip (extension keys parsed then silently
dropped); `lint` filters ERROR findings but still reports their counts;
no tests of the JSON response shapes; destructive `index` regeneration
with no dry-run.

### 2.3 okf-go — feature catalog and cautionary tale

~13.5k LOC. Ecosystem features (harvesters, MCP/LSP servers, HTML portal,
hub client, sync) — all out of scope for okf-io, but candidates for
layered packages later. Its **graph + budgeted context assembly** (BFS
with MaxDepth/MaxCharacters/MaxTokens emitting XML/markdown for agent
context windows) is the one consumer feature worth stealing.

Spec divergences to avoid repeating:

- Attestation model `attestation: {query, executor, attester}` predates and
  contradicts §10's contract.
- Trust ladder `certified > human > automated` contradicts §5.3, and its
  `TrustTier()` can return an undocumented fifth value.
- `Diff`/`Merge` silently drop all v0.2 trust fields — the exact "agents
  rewrite constantly" failure mode v0.2 exists to prevent.
- No actor-convention parsing; no `index.md` generation; v0.1/v0.2 fields
  maintained as parallel unreconciled universes.

### 2.4 okf-schema (Python, v0.1) — lessons in both directions

**Keep:** ruamel.yaml round-trip editing ("human edits are sacred" — never
eat comments/quotes/key order); coded diagnostics (E1–E7/W1–W7) with
`--strict`; per-type JSON-Schema validation auto-discovered from a bundle's
`_schema/` dir (with `$ref` inlining and `x-okf-summary` driving index
text); index generation that preserves hand-written prose; 96% enforced
coverage; Diátaxis docs; the skills-evals A/B methodology.

**Avoid:** regex-only link extraction (known bug: `#fragment` anchors are
never stripped → false broken-link warnings); naive frontmatter splitter
(`find("\n---")` truncates on YAML scalars containing `---`; no CRLF);
public return types in `_internal`; no `py.typed`; every operation
re-walking the bundle instead of sharing one loaded model; library code
calling `click.echo`; `--check`/`--diff` conflation; denormalized
`links`/`backlinks` written into neighbors' frontmatter.

### 2.5 knowledge-catalog — reference behaviors and fixtures

- `okf/src/reference_agent/bundle/document.py` is a minimal spec-blessed
  core: `normalize_verified()` (bare mapping → list), `trust_tier()`,
  `is_stale()` (handles PyYAML pre-parsing `stale_after` into
  `datetime.date`), and a preferred frontmatter key order for
  diff-friendliness: `(type, resource, title, description, tags, status,
  generated, verified, stale_after, sources, usage_window)`, unknown keys
  appended after.
- `okf/bundles/acme_retail/` is the **only bundle anywhere exercising the
  full v0.2 surface** (attested computations, verified, stale_after,
  usage_window, deprecated concepts, a real Python attester file inside the
  bundle). It is the canonical test fixture. The three machine-generated
  bundles (ga4, stackoverflow, crypto_bitcoin) are "thin v0.2"
  (type/resource/title/description/tags/generated/sources only).
- `git show ee67a5c:okf/SPEC.md` recovers the v0.1 spec; commit `780fe9d`
  is the entire v0.1→v0.2 migration — the reference for the rewriter.

**Real-world edge cases the samples expose** (test cases, all of them):

1. `verified` as bare mapping or list (spec-mandated normalization).
2. YAML loaders pre-parse dates: `stale_after`/`last_modified` may arrive
   as `datetime.date`, timestamps as `datetime`, or as strings depending on
   quoting. Model fields must accept both.
3. Undeclared extension keys in shipped samples — including `not:` (a
   Python keyword) in `acme_retail/metrics/gross-margin.md`. Strict models
   reject shipped samples; `not` breaks naive attribute mapping.
4. Non-markdown files inside bundles (`attesters/sql_equality.py`,
   `viz.html`) referenced from frontmatter and linked from `index.md`.
   A `rglob("*.md")` walker misses them; path validation must not.
5. Three YAML dialects for the same logical content: PyYAML block-style
   with quoted timestamps, TS `yaml` with indented lists and unquoted
   timestamps, and hand-written flow-style (`generated: { by: …, at: … }`,
   `tags: [a, b]`). Round-trip must preserve whichever it finds.
6. Mixed link styles in one bundle: acme_retail mostly uses file-relative
   links but has root-absolute links in a `# Cited by` section — which the
   reference viewer silently discards (leading-`/` skip). okf-io must
   resolve both forms.
7. Footnote labels (`[^rev-policy]`) joining to `sources[].id` — nothing in
   any implementation validates the join in both directions. Natural lint
   rule.
8. Filename conventions: `events_` (trailing underscore), `___` as a join
   separator in concept filenames.

---

## 3. Goals and non-goals

### Goals (okf-io, the core)

1. **Read** any OKF bundle (v0.2 or v0.1) into a typed object model without
   ever rejecting a bundle for the §11 tolerance list.
2. **Write** concepts and bundle artifacts back with full fidelity —
   comments, quote styles, key order survive.
3. **Validate** a bundle against the full v0.2 rule set with coded,
   spec-cited findings.
4. **Derive** the spec's computed signals: trust tiers, effective status,
   staleness, link graph, backlinks, footnote↔source joins.
5. **Migrate** v0.1 bundles to v0.2 with a comment-preserving rewriter.
6. **Stay pure**: okf-io implements only what SPEC.md defines. Anything
   beyond the spec lives in a downstream workspace package, enabled by
   deliberate extension points in the core (§7).
7. Be a **good citizen Python package**: typed (`py.typed`), documented,
   src-layout, minimal deps, high coverage.

### Non-goals (okf-io — many are in scope for *other* workspace packages)

- Per-type schema validation / schema registry → separate package (§7).
- Harvesters/content creation, MCP/LSP servers, hub clients, sync engines
  → future workspace packages (§5).
- Attestation *execution* (executors, receipts, running attesters) —
  static contract modeling and validation only (see D3).
- A full-featured CLI. Library-first; a thin CLI may come later and should
  follow okfcli's agent-first JSON conventions if/when it does.
- Denormalizing `links`/`backlinks` into frontmatter (okf-schema does this;
  it dirties neighbors on every edit — derived data stays derived).

---

## 4. Decisions

Decided 2026-08-02 in discussion; recorded with their consequences.

### D1. Full ruamel round-trip preservation

Bundles are read and hand-edited by both agents and humans. Loading a
concept, touching one field, and writing it back must not disturb comments,
quote styles, flow-vs-block style, or key order.

Consequences:

- ruamel.yaml (round-trip mode) is the storage layer for frontmatter.
- The serializer's preferred key order (§2.5) applies to **newly created**
  documents and newly added keys only. Existing documents are never
  reordered — reordering is a diff bomb even when comment-safe.
- Frontmatter splitting must be line-anchored and BOM/CRLF-tolerant (fixing
  okf-schema's splitter bugs), and must preserve the exact body bytes.

### D2. Plain dataclasses for the typed view (Pydantic rejected)

Pydantic was considered and dropped. The deciding argument: D1 forces a
**two-layer document model** in which serialization *never* comes from the
typed model —

- The ruamel `CommentedMap` (plus raw body text) is the **source of truth**
  for serialization.
- The typed model is a **validated, read-mostly view** derived from it.
- Mutations go through a write-back API that edits the `CommentedMap` in
  place (refreshing the view), never by re-emitting the model as YAML.

With the model reduced to a read-view, Pydantic's main payoffs
(serialization, model-as-truth parsing/coercion, JSON-schema export) are
neutralized, and dropping it removes both a heavy dependency from the pure
core and the standing temptation to `model_dump` back to YAML. Schema
generation belongs to the schema-registry package anyway (§7).

Consequences:

- Frozen, slotted dataclasses for `Frontmatter`, `Source`, `Generated`,
  `Verified`, `Parameter`, `Executor`, `Attester`, `Actor`, etc.
- Lenient normalizers are small hand-written functions applied when the
  view is built: bare-mapping `verified` → list, `str | date` date
  coercion, and (if adopted) okfcli's comma-separated-scalar-tags
  accommodation. Reference implementation: `normalize_verified()` in the
  knowledge-catalog reference agent.
- Unknown keys ride in an `extra: dict[str, Any]` field on the view
  (`not:` and other keyword-colliding keys included) — mirroring, not
  replacing, their presence in the `CommentedMap`.
- Shape/spec *validation* lives in the validate pipeline (§8), not in
  model constructors — building a view of a malformed concept must not
  raise (§11 tolerance).

### D3. Attestation: static contract only

Model and validate the §10 contract fields (`runtime`, `parameters`,
`computation` XOR inline fence, `executor`, `attester`); resolve and
existence-check path-valued fields. Do **not** bind parameters, execute,
or attest in okf-io. The consumer flow (§10.5) is a natural fit for a
future `okf-attest` workspace package — Python is uniquely placed to run
the (Python) attesters — but it waits, and it will not live in the core.

### D4. v0.1 compatibility: read fallbacks + a migration rewriter

- **Read-time fallbacks** (spec §13.1): expose `generated_at` falling back
  to legacy `timestamp`; parse a legacy body `# Citations` list into the
  sources view when `sources` is absent. Fallback-derived values are marked
  as such (consumers may want to distinguish).
- **`migrate` rewriter**: v0.1 → v0.2 in place — `timestamp` →
  `generated: {by, at}`, `# Citations` → `sources[]` with generated `id`s
  and footnote rewrites where feasible. Comment-preserving via D1.
  Reference diff: knowledge-catalog commit `780fe9d`.
- **Migration authorship**: v0.1 `timestamp` has no author but
  `generated.by` is required within `generated`. Default:
  `process:okf-io-migrate`, overridable via an `actor` argument.

### D5. Broken links are WARN

The spec is explicit that consumers MUST tolerate broken links (§6.1, §11)
— a broken link may represent not-yet-written knowledge. okfcli's
ERROR-severity precedent is rejected. Broken cross-links produce WARN
findings, with okfcli-style repair hints (suggest the absolute form when a
broken relative link would resolve from the bundle root).

### D6. Schema registry is a separate package

Per-type frontmatter schemas (okf-schema v0.1's `_schema/` idea) are
wanted eventually but are **beyond the spec**, so they live in a separate
workspace package that consumes okf-io. okf-io gains generic extension
points (§7) — never a jsonschema dependency. Design discussion in §7.

### D7. uv-workspace monorepo, okf-io as the pure core

This repo is a uv workspace. okf-io defines the spec and depends on no
other workspace package; every other package depends on okf-io. Existing
external code (an AST/filesystem→graph-db generator, a source-code→wiki
generator) will be imported into workspace packages over time (§5).

### D8. Naming

- Core distribution name: **okf-io**; import name **`okf_io`** — flat, one
  module root per package, and the same convention for every future
  workspace package. (PEP 420 namespace packaging under a shared `okf.*`
  was considered and rejected as needless friction.)
- Repo name: **agent-workspace** (renamed from okf-py; local directory move
  pending).

### D9. Versioning: independent, static, deferred publishing

Decided 2026-08-02. Publishing to PyPI is **deferred indefinitely** — the
workspace works fully unpublished (the single uv lockfile means every
package develops and tests against workspace HEAD of okf-io). But the
versioning constraints are built in from day one so publishing later is a
mechanical step, not a design session:

- **Independent versions per package** (no lockstep). Packages mature at
  different rates; okf-io's stability signal must not be tied to its
  youngest sibling.
- **Static `version = "x.y.z"`** in each package's pyproject — no
  hatch-vcs (VCS-derived versioning is fragile in monorepos: per-package
  tag patterns, shallow-clone breakage). Bumps via a release tool
  (commitizen / tbump per package) when the time comes.
- **Compatibility policy, effective immediately:** pre-1.0, an okf-io
  **minor** bump = breaking, **patch** = compatible. Downstream packages
  declare real bounds in their pyproject dependencies
  (`okf-io>=0.3,<0.4` style) even while the workspace source overrides
  them in development — the bounds document intent and make any future
  publish honest. Post-1.0, standard semver.
- **Reserved (dormant) release machinery:** tag scheme `<pkg>-vX.Y.Z`
  (e.g. `okf-io-v0.2.0`); a matching tag triggers per-package build +
  PyPI trusted publishing if/when publishing is switched on. No publish
  workflow is written until then.

### D10. Deferred

Which consumer-side features the depending project needs first (graph/
context assembly depth, search, etc.) — to be discussed on top of this
document.

---

## 5. Workspace and package layering

```
okf-py/                      # repo root = uv workspace
  pyproject.toml             # [tool.uv.workspace] members = ["packages/*"]
  uv.lock                    # single shared lockfile
  docs/                      # cross-cutting docs (this file)
  packages/
    okf-io/                  # THE CORE. Spec model, round-trip IO,
                             #   validation, derivation, index/log, migrate.
                             #   Deps: ruamel.yaml, markdown-it-py. Nothing else.
    okf-schemas/             # (name TBD) type-schema registry + jsonschema
                             #   validation rules plugged into okf-io. §7.
    okf-???/                 # content creation: fs/AST → graph db (existing
                             #   code to import; name once scoped)
    okf-???/                 # wiki generation: source → OKF bundles (existing
                             #   wiki-format generator to adapt; name once scoped)
    okf-attest/              # (future) attestation execution — §10.5 consumer
                             #   flow: bind, execute, receipt, attester runs
    okf-???/                 # (future) consumption: budgeted context assembly,
                             #   search, serving (MCP?)
```

**Dependency rule:** `okf-io` imports no workspace package. Every other
package imports `okf-io` (workspace dependency, `{ workspace = true }`).
Layered packages may depend on each other, but the graph stays acyclic and
shallow.

**Incoming assets** (existing code to be imported and adapted, timing TBD):

- **AST/filesystem graph generator** — parses source trees into a graph
  db. Kick-starts content creation: emitting code-derived concepts
  (modules, symbols, dependency edges) as OKF bundles, and/or backing a
  richer consumption graph than the in-memory link graph.
- **Source→wiki generator** — generates wiki pages from source code in a
  similar-but-different format. Kick-starts producer tooling: adapting its
  output to OKF concepts with proper `generated`/`sources` provenance.

Neither has been reviewed yet in this effort; each gets its own survey +
brainstorming session before a package is scoped around it.

---

## 6. Proposed architecture: okf-io

```
packages/okf-io/
  pyproject.toml
  src/okf_io/
    __init__.py        # public re-exports, __all__, __version__
    document.py        # Document: two-layer model (raw + view), parse/serialize
    models.py          # frozen dataclasses: Frontmatter, Source, UsageWindow,
                       #   Generated, Verified, Parameter, Executor, Attester, Actor
    bundle.py          # Bundle: walk/load, concept lookup, by-type/tag/status
    links.py           # markdown link + footnote extraction, resolution, graph
    validate.py        # rule pipeline -> Findings report; plugin registry (§7)
    derive.py          # trust tiers, effective status, staleness, joins
    index.py           # index.md parse + regenerate (prose-preserving)
    log.py             # log.md parse + append helpers
    migrate.py         # v0.1 -> v0.2 rewriter
    _yaml.py           # ruamel round-trip config, frontmatter split/join
    py.typed
```

Layer sketch (each layer depends only on those above it):

1. **`_yaml` / `document`** — one file in, `(CommentedMap, body)` out,
   byte-faithful back. `Document.parse_error` captured, never raised from a
   bundle walk.
2. **`models` / `derive`** — the validated view + computed signals.
   Injectable `today`/`now` for all time-dependent derivation.
3. **`bundle` / `links`** — one load pass shared by every operation (fixing
   okf-schema's re-walk-per-operation design). Real markdown parsing
   (markdown-it-py) for links and footnotes; fragments stripped; code
   blocks exempt (goldmark-AST lesson from okfcli/okf-go).
4. **`validate`** — coded findings (severity ERROR/WARN, spec citation,
   concept path), strict mode, deterministic ordering, external rule
   plugins (§7).
5. **`index` / `log` / `migrate`** — writers, all D1-safe.

Public API shape (illustrative, not final):

```python
import okf_io as okf

bundle = okf.load("path/to/bundle")          # never raises on bad concepts
concept = bundle["metrics/revenue"]           # by concept ID
concept.fm.type                               # typed dataclass view
concept.trust_tier                            # derived: "human-reviewed"
concept.is_stale(today=date(2026, 8, 2))      # injectable clock
concept.fm.extra["not"]                       # unknown keys preserved

report = okf.validate(bundle)                 # list[Finding], .is_conformant
graph = bundle.graph()                        # directed link graph + backlinks

concept.set("status", "deprecated")           # write-back through CommentedMap
concept.save()                                # comments/format intact

okf.migrate(bundle)                           # v0.1 -> v0.2, comment-preserving,
                                              #   generated.by=process:okf-io-migrate
```

---

## 7. Schema registry package — design discussion

The v0.1 okf-schema library's strongest beyond-spec idea: per-`type`
JSON-Schema validation, auto-discovered from a bundle's `_schema/`
directory, making bundles self-describing. We want it — but not in the
core (D6). Working name `okf-schemas` (TBD; also considered:
`okf-registry`, `okf-typeschemas`).

### What it does

- **Schema discovery**: load `<Type>.schema.{json,json5,yaml,yml}` from a
  bundle-local `_schema/` dir (v0.1 convention: type name = stem before
  `.schema`; `$ref` inlining with sibling-key override; `x-okf-summary`
  as a schema-level one-liner).
- **Registry beyond the bundle**: schema *sets* versioned and distributed
  independently of any bundle (an org's standard concept types), resolved
  by type name with a defined precedence (proposal: bundle `_schema/`
  overrides registry).
- **Validation**: for each concept whose `type` has a schema, validate the
  frontmatter (as plain data) against it, emitting findings through
  okf-io's normal report shape.
- **Schema-aware generation** (later): `x-okf-summary`-driven index text,
  scaffolding new concepts from a type schema.

### What okf-io must provide (extension points — these are core-design

requirements even before the schema package exists):

1. **Validation rule plugin API.** A rule is a callable
   `(bundle, concept) -> Iterable[Finding]`; `okf.validate(bundle,
   extra_rules=[...])` runs external rules through the same pipeline,
   findings indistinguishable in shape from built-ins (own code namespace,
   e.g. `S*` for schema findings vs core `E*`/`W*`).
2. **Plain-data projection of frontmatter.** jsonschema can't consume a
   `CommentedMap` with `date` objects. okf-io exposes
   `concept.fm_data(dates="iso")` — a plain dict/list/scalar projection
   with dates normalized to ISO strings (the v0.1 lesson: unquoted YAML
   dates break `type: string` schemas).
3. **Reserved-name awareness hook.** `_schema/` must be excluded from
   concept walking. Rather than hardcoding it in the core (it's not in the
   spec), `okf.load(..., ignore=("_schema/",))` — callers/packages declare
   extra non-concept paths.
4. **Writer hooks** (later, for index text): `index.regenerate(...,
   describe=callable)` so the schema package can supply
   `x-okf-summary`-based descriptions without the core knowing about
   schemas.

### Spec-tension note

§11 forbids consumers from *rejecting* unknown types or keys. Schema
validation is therefore a **producer/CI concern** — opt-in strictness for
bundles you own — not consumer-side gating. Schema findings default to
their schema-declared severity (proposal: WARN unless the schema set is
run in strict/CI mode), and a type with no schema is never a finding by
default (v0.1's W6 becomes opt-in).

### Open design questions for this package (to revisit when scoped)

- Registry distribution format: a Python package exporting schema sets? A
  git-fetched directory? Both?
- Precedence and composition: bundle `_schema/` vs registry vs multiple
  registries; `$ref` across sets.
- Draft pinning: v0.1 shipped draft-07 `$schema` headers validated with a
  2020-12 validator — pick 2020-12 and validate the schemas themselves.

---

## 8. Validation rule catalog (target, okf-io built-ins)

Adopt okf-schema's coded-diagnostic shape with okfcli's coverage (the most
complete existing rule set). Every finding carries a code, severity, spec
citation, and concept path. Draft catalog — codes to be finalized:

| Area | Rule (spec) | Severity |
|---|---|---|
| Frontmatter | parseable YAML frontmatter present (§11) | ERROR |
| Frontmatter | non-empty `type` (§4.1) | ERROR |
| Frontmatter | `title`/`description` recommended (§4.1) | WARN |
| Links | broken cross-link (with absolute-form repair hint) (§6.1, D5) | WARN |
| Provenance | `sources[].resource` required (§5.1) | ERROR |
| Provenance | `usage_count` without framing `usage_window` (§5.1) | WARN |
| Provenance | footnote label without matching `sources[].id`, and vice versa (§5.1) | WARN |
| Trust | `generated.by` required within `generated` (§5.2) | ERROR |
| Trust | non-ISO-8601 `at` values (§5.2) | WARN |
| Trust | `verified[]` entries need `by` and `at` (§5.2) | WARN |
| Trust | actor convention violations (§7) | WARN |
| Lifecycle | `status` outside draft/stable/deprecated (§5.4) | ERROR |
| Lifecycle | `stale_after` not `YYYY-MM-DD` (§5.5) | ERROR |
| Lifecycle | concept currently stale (§5.5) | WARN |
| Computation | `runtime` required for Attested Computation (§10.2) | ERROR |
| Computation | computation present exactly once: inline fence XOR `computation:` path (§10.3) | ERROR |
| Computation | `parameters[]` entries need `name` and `type` (§10.2) | WARN |
| Computation | path-valued fields (`computation`, `executor.resource`, `attester.resource`) missing from bundle (§6.2) | WARN |
| Reserved | non-root `index.md` with frontmatter; root `index.md` with keys beyond `okf_version` (§8, §12) | ERROR |
| Reserved | unknown `okf_version` (known: 0.1, 0.2) (§12) | WARN |
| Reserved | `log.md` `##` headings not ISO dates (§9) | ERROR |
| Legacy | v0.1 `timestamp` present (migration hint) (§13.1) | WARN |
| Legacy | body `# Citations` present (migration hint) (§13.1) | WARN |

---

## 9. Test strategy

- **Canonical fixture:** vendor a copy of `acme_retail` (full v0.2
  surface) plus one thin machine-generated bundle (e.g. `ga4`) and the TS
  re-serialization of the same bundle as a round-trip differential.
- **Edge-case fixtures:** one per item in §2.5's edge-case list.
- **Round-trip property:** for every fixture file, `parse → save` with no
  mutation is byte-identical; `parse → mutate one field → save` diffs only
  the intended lines.
- **Injectable clock** everywhere staleness is derived.
- **Migration golden tests:** v0.1 fixture in, expected v0.2 out, keyed to
  the `780fe9d` reference migration.
- **Plugin-API contract tests:** a dummy external rule exercises the
  validate plugin surface (§7) before the schema package exists.
- Coverage bar in the okf-schema spirit (≥95%, branch, enforced in CI).

---

## 10. Packaging

- Workspace: uv workspace at repo root, `packages/*` members, single
  lockfile. Shared dev tooling (ruff, mypy, pytest config) at the root.
- okf-io: distribution `okf-io`, import `okf_io` (D8). src-layout,
  `py.typed`, hatchling with a static version field (D9 — no hatch-vcs).
- Publishing deferred indefinitely (D9); version fields, intra-workspace
  bounds, and the `<pkg>-vX.Y.Z` tag scheme are in place from day one so
  a future publish is mechanical.
- okf-io runtime deps (minimal, final): `ruamel.yaml`, `markdown-it-py`.
- Python ≥3.11.
- Tooling: uv, ruff, mypy (strict), pytest (+coverage gate), CI matrix.

---

## 11. Open questions

1. **Names and scope for the content-creation packages** wrapping the
   incoming AST-graph and wiki-generator code — each needs its own survey
   session first (§5).
2. **Consumer features for the depending project** (D10): graph / context
   assembly (okf-go's budgeted BFS), search, tag views — which, and in
   what order, and in which package?
3. **CLI**: when one lands, adopt okfcli's agent-first conventions (JSON
   stdout, `schema` command, stable exit codes)? Any need before then?
   Which package owns it?
4. **Index generation policy**: okfcli's destructive regeneration vs
   okf-schema's prose-preserving merge. Proposal: prose-preserving, with
   `--check`/dry-run as separate, unconflated flags.
5. **Schema package design questions** listed at the end of §7 (registry
   distribution, precedence/composition, draft pinning).

---

## 12. Roadmap sketch

Rough sequencing; each phase becomes its own brainstorming + plan session:

0. **Workspace scaffolding** — root pyproject + uv workspace, git init,
   `packages/okf-io` skeleton, shared tooling/CI, fixture vendoring.
1. **Core document model** — `_yaml` + `document` + `models` + `derive`
   (D1/D2 two-layer design proven here; round-trip property tests).
2. **Bundle + links** — load pass, graph, footnote joins.
3. **Validation** — rule pipeline + plugin API + catalog (§8).
4. **Writers** — index.md, log.md.
5. **Migration** — v0.1 → v0.2 rewriter (D4).
6. **Schema package** — design session against §7, then implement.
7. **Survey + scope the incoming assets** (AST-graph, wiki generator) into
   content-creation packages.
8. **Then:** consumption features (D10), CLI, attestation execution
   (`okf-attest`).
