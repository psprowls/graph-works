# okf-io

Read, derive from, and write back OKF v0.2 concept documents.

The OKF v0.2 specification is not checked into this repository; it is referenced
by name and version, the way ADR-0007 is referenced below.

## The two-layer model

`Document` owns the raw text and a round-trippable `CommentedMap` (`fm_raw`).
`Frontmatter` — reachable as `doc.fm` — is a frozen, memoized *view* built from
that map. Building a view never raises and never rejects: a malformed concept
still yields a `Document`, with the failure recorded in `parse_error` and lossy
fields listed in `fm.coercion_failures`. Validation is a separate concern.

## Byte fidelity

A document that has not been mutated serializes to its original bytes, exactly.

A document that *has* been mutated is rendered by diffing ruamel's rendering of
the pristine data against its rendering of the mutated data, and applying only
that delta to the original text, so an edit to one key leaves its neighbours
untouched — including in dialects ruamel cannot reproduce, such as the
pre-folded plain scalars a TypeScript `yaml` writer emits.

That splice requires the edited lines to be anchored in the original. When they
are not, the edit is one ruamel renders differently enough that no line
corresponds, and the document falls back to re-emitting the whole frontmatter.
The result is still correct and still re-parses; it simply carries a larger
diff, reformatting the block to ruamel's conventions. **A mutation is always
valid and never lossy; it is minimal in the common case, not in every case.**

## Dependency rule

**This package carries exactly two runtime dependencies: `ruamel.yaml` and
`markdown-it-py`.** Adding a third to the core requires its own ADR. The
constraint is a scope boundary, not an implementation detail.

## The validation pipeline

A bundle is read once with `load_bundle()`, yielding a `Bundle` object that
walks the directory exactly once. The markdown link graph is then derived from
that one walk via `build_link_graph()`, which computes backlinks — found never
written into a neighbour's frontmatter, keeping every file independent.

`validate()` runs a catalog of 29 conformance rules over the bundle and its
link graph. It takes a required `today=` keyword argument (no hidden clock) and
optional `strict=False` and `extra_rules=()` arguments. Nothing rejects a
bundle; every rule yields a `Finding`, and `validate()` returns a `Report`
listing all of them.

A `Finding` carries a dotted `code` (e.g., `"links.broken"`, `"trust.no-stamps"`),
a `Severity` of `"error"` or `"warn"`, a message, a spec citation, and the
path and line where the problem was found. The `Report` offers views via
`.errors`, `.warnings`, and `.ok` (no errors), and a `by_code()` method to
filter findings.

### A note on the `validate` export

`okf_io.validate` is the validation function, not the submodule. To import
names from the `validate` module (such as `Finding`, `Report`, `RuleContext`),
use `from okf_io.validate import Finding`. Do not use `import okf_io.validate
as m` or `from okf_io import validate as m` expecting `m.Finding` to work — both
bind the function, not the module. This is deliberate: the validation function
is the public interface, and the submodule is an implementation detail.

## The writers

`update_index()` reconciles a directory's `index.md` against the bundle.
**okf-io owns which entries appear; the human owns what they say.** An index
file is not derivable from the bundle — subdirectory summaries, asset
descriptions and curated one-liners that deliberately differ from a concept's
own `description` exist nowhere else — so regeneration is reconciliation, not
rendering. An entry whose target resolves to nothing is pruned, a concept or
content-bearing subdirectory with no entry is added — assets are never
proposed, only pruned when their entry goes dead — and every other byte is
copied through, prose and comments and line endings included. There is no marker syntax and no generated region:
markers are not in SPEC.md, and okf-io implements only what the spec defines
(ADR-0005).

Entry text is never rewritten under the default `descriptions="preserve"`;
text that has drifted from its concept's `description` is reported in
`IndexUpdate.drift` instead, so CI can surface staleness without a single word
being overwritten. `descriptions="refresh"` opts into the machine-generated
regime and rewrites it, reporting every rewrite as a change.

`parse_log()` reads `log.md` into dated sections, and `append_log_entry()`
adds one entry in date order. A log is append-only by construction, so
preserving what is already written follows from the operation.
`append_log_entry()` requires either `on=` or `today=`; okf-io never reads
the clock. If the log's dated sections are not newest-first, the append
refuses with a `ValueError` unless the new date is newer than every existing
section — see the docstring for the rationale.

Both writers default to **`dry_run=True`**: writing is something a caller asks
for, and only an explicit `dry_run=False` touches the disk. `update_index()`
also defaults to `create_missing=False`, which silently skips directories
without existing `index.md` — see the docstring for when to set it `True`.

`IndexUpdate` and `LogAppend` share one result vocabulary: `before`, `after`,
a `changed` property that renders nothing, and a `diff()` method that writes
nothing. Asking whether anything changed never produces a diff; producing a
diff never implies a write.

`update_index(..., describe=…)` lets a caller supply per-entry text, which is
how a future schema package drives index descriptions without okf-io knowing
schemas exist.

## Versioning

Static `version` field, never `hatch-vcs` (ADR-0007). Pre-1.0: minor = breaking,
patch = compatible.
